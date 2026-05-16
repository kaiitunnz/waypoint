"""Server-side terminal emulation for tmux-backed sessions.

The WebSocket terminal endpoint feeds raw bytes from a tmux pane's
``pipe-pane`` log into one of these renderers and forwards the
renderer's output to xterm.js. The renderer interprets the full
terminal protocol server-side (DECSTBM scroll regions, alt-screen
toggles, synchronized-output frames, complex CUP patterns) and emits
a simplified byte stream — full-line repaints positioned with CUP
plus cursor positioning — that xterm.js can render identically to a
native terminal.

The :class:`TerminalRenderer` protocol is implementation-agnostic so
the pyte backend can be swapped for libvterm/vterm later without
touching the WS handler.
"""

import re
from io import StringIO
from typing import Protocol

import pyte

# Private-CSI SGR-shape sequences like ``\x1b[>4;2m`` (kitty keyboard /
# modifyOtherKeys mode-set, emitted by claude_code at startup). The
# ``>`` / ``<`` / ``?`` intermediate is supposed to mark the CSI as
# device-private, but pyte ignores it and treats the ``4``/``2`` as
# regular SGR params — latching ``cursor.attrs.underscore = True`` on
# the entire subsequent stream. claude_code rarely emits a full
# ``\x1b[0m`` reset (it toggles individual attrs via ``\x1b[39m`` /
# ``\x1b[22m`` / …), so the latched underline survives indefinitely and
# every written cell renders underlined in xterm. Strip these before
# pyte sees them; xterm.js doesn't care about modifyOtherKeys either.
_PRIVATE_CSI_SGR_RE = re.compile(rb"\x1b\[[<>?][0-9;:]*m")
# Cap on a held partial CSI between feeds — anything longer is malformed
# and gets handed to pyte unchanged rather than buffered forever.
_MAX_PENDING_CSI = 64


def _csi_terminated(seq: bytes) -> bool:
    """True if ``seq`` (starting with ``ESC [``) contains a CSI final byte.

    CSI final bytes lie in ``0x40..0x7E``; bytes before that are params
    (``0x30..0x3F``) or intermediates (``0x20..0x2F``).
    """
    for byte in seq[2:]:
        if 0x40 <= byte <= 0x7E:
            return True
    return False


class TerminalRenderer(Protocol):
    """Server-side terminal emulator with diff emission."""

    cols: int
    rows: int

    def feed(self, data: bytes) -> None: ...
    def resize(self, cols: int, rows: int) -> None: ...
    def set_cursor(self, col: int, row: int) -> None: ...
    def render_full(self) -> str: ...
    def render_diff(self) -> str: ...


# Pyte represents named ANSI colors as strings; map them back to SGR
# parameters. Truecolor cells arrive as 6-digit hex strings.
_FG_NAMED: dict[str, str] = {
    "black": "30",
    "red": "31",
    "green": "32",
    "brown": "33",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "default": "39",
}
_BG_NAMED: dict[str, str] = {
    "black": "40",
    "red": "41",
    "green": "42",
    "brown": "43",
    "yellow": "43",
    "blue": "44",
    "magenta": "45",
    "cyan": "46",
    "white": "47",
    "default": "49",
}

# pyte stores DEC private modes as ``code << 5``; the runtime never
# exposes raw mode constants for the buffer-toggling modes, so derive
# them from the well-known numbers.
_ALT_SCREEN_MODES = {47 << 5, 1047 << 5, 1049 << 5}


def _cell_eq(cell: "pyte.screens.Char", mirror: "pyte.screens.Char | None") -> bool:
    """Compare a pyte cell against the mirror's record of the last emit.

    ``mirror`` is ``None`` for cells we have never emitted — those
    always count as different so the first paint covers them. After
    that, NamedTuple equality covers data + every SGR attribute.
    """
    if mirror is None:
        return False
    return cell == mirror


def _color_sgr(color: str, is_bg: bool) -> list[str]:
    table = _BG_NAMED if is_bg else _FG_NAMED
    if color in table:
        return [table[color]]
    if len(color) == 6:
        try:
            r = int(color[0:2], 16)
            g = int(color[2:4], 16)
            b = int(color[4:6], 16)
        except ValueError:
            return [table["default"]]
        return ["48" if is_bg else "38", "2", str(r), str(g), str(b)]
    return [table["default"]]


class PyteRenderer:
    """:class:`TerminalRenderer` backed by ``pyte``."""

    def __init__(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows
        self._screen = pyte.Screen(cols, rows)
        self._stream = pyte.ByteStream(self._screen)
        # pyte marks every row dirty on construction; discard since the
        # initial render emits a full frame regardless.
        self._screen.dirty.clear()
        # Tracks which buffer xterm.js currently has active. The first
        # frame we emit will toggle to match the pane's actual state.
        self._alt_emitted = False
        self._last_cursor: tuple[int, int, bool] | None = None
        # Mirror of what we've actually sent to xterm. pyte marks a row
        # dirty whenever it sees a write *or* a clear (CSI K), so its
        # ``dirty`` set is much broader than the cells that visibly
        # change. Codex's per-keystroke frame, for example, clears all
        # five textbox-border rows even though only one cell in row 16
        # actually changes — repainting all five rows every frame is
        # what produces the flicker. We diff each dirty row against
        # this mirror so the emitted ANSI only touches cells whose
        # final value differs from what xterm last drew.
        self._mirror: list[list[pyte.screens.Char | None]] = [
            [None] * cols for _ in range(rows)
        ]
        # Partial CSI held across ``feed`` boundaries so a private-SGR
        # split between chunks still gets stripped.
        self._pending: bytes = b""

    def feed(self, data: bytes) -> None:
        buf = self._pending + data
        self._pending = b""
        # If the buffer's trailing ``ESC [`` hasn't seen a final byte
        # yet, hold it for the next feed — otherwise the private-CSI
        # regex below would miss a sequence split across chunks.
        tail_idx = buf.rfind(b"\x1b[")
        if tail_idx != -1:
            tail = buf[tail_idx:]
            if not _csi_terminated(tail) and len(tail) <= _MAX_PENDING_CSI:
                self._pending = tail
                buf = buf[:tail_idx]
        buf = _PRIVATE_CSI_SGR_RE.sub(b"", buf)
        self._stream.feed(buf)

    def set_cursor(self, col: int, row: int) -> None:
        """Force the cursor to (col, row), zero-based.

        Used to seed the cursor from external state (``tmux
        display-message``) since ``capture-pane`` itself carries no
        positioning information.
        """
        self._screen.cursor.x = max(0, min(col, self.cols - 1))
        self._screen.cursor.y = max(0, min(row, self.rows - 1))

    def resize(self, cols: int, rows: int) -> None:
        if cols == self.cols and rows == self.rows:
            return
        # pyte takes (lines, columns) — opposite of the rest of this
        # module's (cols, rows) convention.
        self._screen.resize(rows, cols)
        self.cols = cols
        self.rows = rows
        # Drop the mirror so the next diff emits the new geometry from
        # scratch — both for cells pyte reflowed and for cells that
        # don't exist in the new size.
        self._mirror = [[None] * cols for _ in range(rows)]
        self._screen.dirty.update(range(rows))

    def render_full(self) -> str:
        out = StringIO()
        alt = self._alt_active()
        if alt != self._alt_emitted:
            out.write("\x1b[?1049h" if alt else "\x1b[?1049l")
            self._alt_emitted = alt
        # Reset SGR, clear viewport, home cursor — guarantees a known
        # starting state before we paint each row.
        out.write("\x1b[0m\x1b[2J\x1b[H")
        # Reset mirror to defaults since we just cleared xterm's view.
        for row in range(self.rows):
            for col in range(self.cols):
                self._mirror[row][col] = None
        for row in range(self.rows):
            self._emit_row_delta(out, row)
        self._paint_cursor(out)
        self._screen.dirty.clear()
        return out.getvalue()

    def render_diff(self) -> str:
        alt = self._alt_active()
        if alt != self._alt_emitted:
            # Buffer switch invalidates the entire prior frame; the
            # cheapest correct option is a full repaint of the new
            # buffer.
            return self.render_full()

        cursor = (
            self._screen.cursor.x,
            self._screen.cursor.y,
            self._screen.cursor.hidden,
        )
        dirty_rows = [r for r in self._screen.dirty if r < self.rows]
        if not dirty_rows and cursor == self._last_cursor:
            return ""

        out = StringIO()
        any_cell_change = False
        for row in sorted(dirty_rows):
            if self._emit_row_delta(out, row):
                any_cell_change = True
        # If pyte reported dirty rows but nothing actually differs from
        # the mirror, suppress the diff entirely as long as the cursor
        # didn't move either — that is exactly Codex's "clear the
        # textbox border every frame" case, and emitting an empty diff
        # would still trigger an xterm re-render.
        if not any_cell_change and cursor == self._last_cursor:
            self._screen.dirty.clear()
            return ""
        self._paint_cursor(out)
        self._screen.dirty.clear()
        return out.getvalue()

    def _alt_active(self) -> bool:
        return bool(self._screen.mode & _ALT_SCREEN_MODES)

    def _emit_row_delta(self, out: StringIO, row: int) -> bool:
        """Emit ANSI for the cells in ``row`` that differ from the mirror.

        Returns ``True`` if any cells were emitted.

        Walks the row left-to-right; each contiguous run of
        differing-from-mirror cells gets one ``CUP`` to the start of
        the run, then attribute-coalesced cell writes. Cells whose
        final value equals what xterm already has are skipped entirely
        — no ``CSI K``, no overwrite — so a frame that touches only
        one cell sends only that cell.
        """
        line = self._screen.buffer[row]
        mirror = self._mirror[row]
        emitted = False
        col = 0
        while col < self.cols:
            cell = line[col]
            if _cell_eq(cell, mirror[col]):
                col += 1
                continue
            run_start = col
            # Extend the run as long as cells differ from the mirror.
            while col < self.cols and not _cell_eq(line[col], mirror[col]):
                col += 1
            run_end = col  # exclusive
            out.write(f"\x1b[{row + 1};{run_start + 1}H")
            prev_attrs: tuple[object, ...] | None = None
            for c in range(run_start, run_end):
                cell = line[c]
                if cell.data == "":
                    # Right half of a wide character — its glyph was
                    # written with the previous cell, which advanced
                    # the cursor by two. Update the mirror so we don't
                    # diff it again, but emit nothing for it.
                    mirror[c] = cell
                    continue
                attrs = (
                    cell.fg,
                    cell.bg,
                    cell.bold,
                    cell.italics,
                    cell.underscore,
                    cell.strikethrough,
                    cell.reverse,
                    cell.blink,
                )
                if attrs != prev_attrs:
                    out.write(self._sgr(cell))
                    prev_attrs = attrs
                out.write(cell.data or " ")
                mirror[c] = cell
            out.write("\x1b[0m")
            emitted = True
        return emitted

    def _sgr(self, cell: pyte.screens.Char) -> str:
        params: list[str] = ["0"]
        if cell.bold:
            params.append("1")
        if cell.italics:
            params.append("3")
        if cell.underscore:
            params.append("4")
        if cell.blink:
            params.append("5")
        if cell.reverse:
            params.append("7")
        if cell.strikethrough:
            params.append("9")
        params.extend(_color_sgr(cell.fg, is_bg=False))
        params.extend(_color_sgr(cell.bg, is_bg=True))
        return "\x1b[" + ";".join(params) + "m"

    def _paint_cursor(self, out: StringIO) -> None:
        c = self._screen.cursor
        row = min(c.y, self.rows - 1) + 1
        col = min(c.x, self.cols - 1) + 1
        out.write(f"\x1b[{row};{col}H")
        out.write("\x1b[?25l" if c.hidden else "\x1b[?25h")
        self._last_cursor = (c.x, c.y, c.hidden)


def make_renderer(cols: int, rows: int) -> TerminalRenderer:
    """Factory for the default renderer.

    Centralises construction so future implementations can be selected
    via env/config without touching the WS handler.
    """
    return PyteRenderer(cols, rows)


class SyncFrameTracker:
    """Byte-level state machine for Codex's DECSET/DECRST 2026 markers.

    Codex (and other ratatui apps) brackets each render in
    ``\\x1b[?2026h`` … ``\\x1b[?2026l``. The WS handler uses this to
    avoid emitting partial frames to xterm. A naive substring scan on
    each chunk misses markers that straddle a chunk boundary; the
    state machine here matches one byte at a time, so the
    8-byte marker is detected no matter how the byte stream is split.
    """

    _PREFIX = b"\x1b[?2026"

    def __init__(self) -> None:
        self.in_frame = False
        # How many bytes of the prefix we've matched so far.
        self._idx = 0

    def feed(self, chunk: bytes) -> bool:
        """Process ``chunk`` and return the final ``in_frame`` state."""
        prefix = self._PREFIX
        plen = len(prefix)
        for byte in chunk:
            if self._idx == plen:
                if byte == 0x68:  # 'h'
                    self.in_frame = True
                elif byte == 0x6C:  # 'l'
                    self.in_frame = False
                # Reset; if the rejected byte is itself ESC we still
                # want to seed the next match below.
                self._idx = 1 if byte == 0x1B else 0
                continue
            if byte == prefix[self._idx]:
                self._idx += 1
            elif byte == prefix[0]:
                self._idx = 1
            else:
                self._idx = 0
        return self.in_frame

    def split_at_frame_ends(self, chunk: bytes) -> list[tuple[bytes, bool]]:
        """Split ``chunk`` at every in→out transition.

        Returns a list of ``(segment, ended_out_of_frame)`` pairs that
        together cover the chunk. Each segment ends at either a frame
        close marker (``ended_out_of_frame=True``, the caller should
        emit a diff after feeding the segment) or at the end of the
        chunk (``ended_out_of_frame=self.in_frame == False``). State
        carries across calls so split markers behave correctly.

        This is the API the streaming layer uses to interleave
        ``renderer.feed`` calls with ``render_diff`` emits: every fully
        closed frame in the chunk gets its own emit, and a trailing
        open frame holds until the next chunk closes it.
        """
        prefix = self._PREFIX
        plen = len(prefix)
        segments: list[tuple[bytes, bool]] = []
        seg_start = 0
        for i, byte in enumerate(chunk):
            if self._idx == plen:
                if byte == 0x68:
                    self.in_frame = True
                elif byte == 0x6C:
                    # Transition into out-of-frame: cut here so the
                    # closed frame's bytes (including this terminator)
                    # form their own segment.
                    self.in_frame = False
                    segments.append((chunk[seg_start : i + 1], True))
                    seg_start = i + 1
                self._idx = 1 if byte == 0x1B else 0
                continue
            if byte == prefix[self._idx]:
                self._idx += 1
            elif byte == prefix[0]:
                self._idx = 1
            else:
                self._idx = 0
        if seg_start < len(chunk):
            segments.append((chunk[seg_start:], not self.in_frame))
        return segments
