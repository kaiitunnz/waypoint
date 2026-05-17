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

import base64
import binascii
import re
from io import StringIO
from typing import Protocol

import pyte

# Strip private-CSI SGR sequences like ``\x1b[>4;2m`` (modifyOtherKeys,
# emitted by claude_code at startup). Pyte ignores the ``>``/``<``/``?``
# intermediate and treats the params as regular SGR, latching
# ``underscore=True`` for the rest of the stream.
_PRIVATE_CSI_SGR_RE = re.compile(rb"\x1b\[[<>?][0-9;:]*m")
# Cap how much of a straddling escape we hold across feeds. CSIs are
# always well under 64 bytes; DCS/OSC payloads carry clipboard writes
# the size of a user selection, so they need the 1 MiB ceiling (tmux's
# own buffer-paste cap) — a 64-byte clip would chop them mid-base64.
_MAX_PENDING_CSI = 64
_MAX_PENDING_NON_CSI = 1 << 20  # 1 MiB
# Strip DCS sequences entirely. Pyte writes the payload to the screen
# as text, so tmux's DCS-passthrough OSC 52 (``ESC P tmux; … ESC \``)
# splatters base64 onto whatever the cursor is on. The middle group
# tolerates tmux's "double the inner ESC bytes" passthrough convention.
_DCS_RE = re.compile(rb"\x1bP[^\x1b]*(?:\x1b\x1b[^\x1b]*)*\x1b\\")

# DEC private modes we mirror from pane to xterm. Pyte doesn't surface
# these (mouse/focus/paste are input concerns), so without an explicit
# pass-through xterm never learns about mouse mode and scroll events
# fall on the floor.
_TRACKED_PRIVATE_MODES = frozenset(
    {
        1000,  # X10 mouse reporting
        1002,  # button-event mouse tracking
        1003,  # any-event mouse tracking
        1004,  # focus in/out events
        1006,  # SGR mouse encoding (modern, what xterm.js expects)
        1015,  # URXVT mouse encoding (fallback some apps still set)
        2004,  # bracketed paste
    }
)
_PRIVATE_MODE_SET_RESET_RE = re.compile(rb"\x1b\[\?([0-9;]+)([hl])")


def _csi_terminated(seq: bytes) -> bool:
    """True if ``seq`` (starting with ``ESC [``) contains a CSI final byte.

    CSI final bytes lie in ``0x40..0x7E``; bytes before that are params
    (``0x30..0x3F``) or intermediates (``0x20..0x2F``).
    """
    for byte in seq[2:]:
        if 0x40 <= byte <= 0x7E:
            return True
    return False


def _partial_escape_at_tail(buf: bytes) -> int:
    """Return the offset of an unterminated escape sequence at the tail.

    Walks forward through ``buf`` parsing each ``ESC`` it sees against
    its respective terminator rules. Returns the offset of the *last*
    escape that didn't reach its terminator before the buffer ended,
    or ``-1`` if every escape closed cleanly. The caller holds
    ``buf[offset:]`` until the next feed completes the sequence.

    Walking forward (rather than rfind) is necessary for DCS: tmux's
    passthrough doubles inner ``ESC`` bytes, so the last ``ESC`` in a
    truncated DCS isn't the start of a fresh escape — it's payload.
    """
    n = len(buf)
    i = 0
    while i < n:
        if buf[i] != 0x1B:
            i += 1
            continue
        start = i
        if i + 1 >= n:
            return start  # lone ESC at end
        kind = buf[i + 1]
        if kind == 0x5B:  # '[' — CSI
            j = i + 2
            terminated = False
            while j < n:
                if 0x40 <= buf[j] <= 0x7E:
                    j += 1
                    terminated = True
                    break
                j += 1
            if not terminated:
                return start
            i = j
        elif kind == 0x50:  # 'P' — DCS
            j = i + 2
            terminated = False
            while j < n:
                if buf[j] == 0x1B:
                    if j + 1 >= n:
                        return start  # ESC at end of buf
                    if buf[j + 1] == 0x1B:
                        # tmux-style doubled ESC inside the payload —
                        # skip both bytes, terminator can't be here.
                        j += 2
                        continue
                    if buf[j + 1] == 0x5C:  # '\\' — ST
                        j += 2
                        terminated = True
                        break
                    # Any other byte after a lone ESC is malformed
                    # inside a DCS; treat as terminator-adjacent and
                    # let the regex strip whatever it can.
                    j += 2
                    continue
                j += 1
            if not terminated:
                return start
            i = j
        elif kind == 0x5D:  # ']' — OSC
            j = i + 2
            terminated = False
            while j < n:
                if buf[j] == 0x07:  # BEL
                    j += 1
                    terminated = True
                    break
                if buf[j] == 0x1B:
                    if j + 1 < n and buf[j + 1] == 0x5C:
                        j += 2
                        terminated = True
                        break
                    if j + 1 >= n:
                        return start
                j += 1
            if not terminated:
                return start
            i = j
        else:
            # Two-byte ESC sequences (``ESC =``, ``ESC c``, …).
            i += 2
    return -1


class TerminalRenderer(Protocol):
    """Server-side terminal emulator with diff emission."""

    cols: int
    rows: int

    def feed(self, data: bytes) -> None: ...
    def resize(self, cols: int, rows: int) -> None: ...
    def set_cursor(self, col: int, row: int) -> None: ...
    def snoop_modes(self, data: bytes) -> None: ...
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
        # Mirror of what we've actually sent to xterm. pyte's ``dirty``
        # set is much broader than the cells that visibly change —
        # diffing against the mirror keeps emitted ANSI down to cells
        # whose final value actually differs.
        self._mirror: list[list[pyte.screens.Char | None]] = [
            [None] * cols for _ in range(rows)
        ]
        self._pending: bytes = b""
        self._modes: dict[int, bool] = {}
        self._emitted_modes: dict[int, bool] = {}

    def feed(self, data: bytes) -> None:
        buf = self._pending + data
        self._pending = b""
        # Hold any in-progress CSI/DCS/OSC at the very end of the
        # buffer so the strippers below can't miss a sequence that
        # straddles a chunk boundary.
        tail_idx = _partial_escape_at_tail(buf)
        if tail_idx != -1:
            tail = buf[tail_idx:]
            kind = tail[1:2] if len(tail) >= 2 else b""
            # CSIs are short by spec; DCS/OSC payloads (clipboard sets,
            # terminfo queries) can be much larger and need their own
            # window so a long sequence doesn't get truncated.
            cap = _MAX_PENDING_CSI if kind == b"[" else _MAX_PENDING_NON_CSI
            if len(tail) <= cap:
                self._pending = tail
                buf = buf[:tail_idx]
        buf = _PRIVATE_CSI_SGR_RE.sub(b"", buf)
        buf = _DCS_RE.sub(b"", buf)
        self._snoop_private_modes(buf)
        self._stream.feed(buf)

    def snoop_modes(self, data: bytes) -> None:
        """Update tracked private-mode state without driving the screen.

        The WS handler calls this on the raw_log prefix it's about to
        skip past (everything written before the client attached) so a
        mid-session reconnect still recovers mouse/focus/paste mode that
        the pane requested before we started reading the live tail.
        """
        self._snoop_private_modes(data)

    def _snoop_private_modes(self, data: bytes) -> None:
        for match in _PRIVATE_MODE_SET_RESET_RE.finditer(data):
            on = match.group(2) == b"h"
            for part in match.group(1).split(b";"):
                try:
                    mode = int(part)
                except ValueError:
                    continue
                if mode in _TRACKED_PRIVATE_MODES:
                    self._modes[mode] = on

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
        # Mirror tracked private modes to xterm.js as part of the
        # prelude so input encoding (mouse, focus, bracketed paste)
        # is in sync before the user can interact.
        self._emit_mode_changes(out, force_all=True)
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

        # Emit mode deltas before cells so xterm flips into mouse /
        # focus / paste mode in the same frame as whatever content
        # triggered the change. Compute this first because it's also
        # part of the "should we short-circuit?" decision below.
        mode_out = StringIO()
        self._emit_mode_changes(mode_out, force_all=False)
        mode_change_emitted = mode_out.tell() > 0

        if not dirty_rows and cursor == self._last_cursor and not mode_change_emitted:
            return ""

        out = StringIO()
        out.write(mode_out.getvalue())
        any_cell_change = False
        for row in sorted(dirty_rows):
            if self._emit_row_delta(out, row):
                any_cell_change = True
        # If pyte reported dirty rows but nothing actually differs from
        # the mirror, suppress the diff entirely as long as the cursor
        # didn't move either — that is exactly Codex's "clear the
        # textbox border every frame" case, and emitting an empty diff
        # would still trigger an xterm re-render. Mode changes count as
        # real work, so don't suppress when they're present.
        if (
            not any_cell_change
            and cursor == self._last_cursor
            and not mode_change_emitted
        ):
            self._screen.dirty.clear()
            return ""
        self._paint_cursor(out)
        self._screen.dirty.clear()
        return out.getvalue()

    def _alt_active(self) -> bool:
        return bool(self._screen.mode & _ALT_SCREEN_MODES)

    def _emit_mode_changes(self, out: StringIO, force_all: bool) -> None:
        """Emit ``\\x1b[?Nh`` / ``\\x1b[?Nl`` for tracked-mode changes.

        ``force_all`` re-emits the entire current mode state — used by
        ``render_full`` on a fresh attach so xterm starts out in the
        same modes the pane has. The diff form emits only modes whose
        state differs from what xterm was last told.
        """
        for mode, on in self._modes.items():
            if force_all or self._emitted_modes.get(mode) != on:
                out.write(f"\x1b[?{mode}{'h' if on else 'l'}")
                self._emitted_modes[mode] = on
        if force_all:
            # Cover the corner case where a mode was previously emitted
            # as "on" but the pane has since dropped it from its state
            # (e.g. via a reset). Without this the next render_full
            # would silently leave xterm with the stale "on".
            for mode in list(self._emitted_modes):
                if mode not in self._modes and self._emitted_modes[mode]:
                    out.write(f"\x1b[?{mode}l")
                    self._emitted_modes[mode] = False

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

    @staticmethod
    def _is_param_byte(byte: int) -> bool:
        # Accept 0x30-0x3B (digits, ':', ';') so multi-param forms like
        # ``\x1b[?2026;1h`` reach the h/l terminator. ECMA-48 also lists
        # 0x3C-0x3F (``< = > ?``) as parameter bytes, but those are
        # prefix indicators that can't legally reappear after the
        # ``?2026`` we've already matched — so deliberately excluded.
        return 0x30 <= byte <= 0x3B

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
                elif self._is_param_byte(byte):
                    continue
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
                elif self._is_param_byte(byte):
                    continue
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


class Osc52Extractor:
    """Pull ``OSC 52`` clipboard-write payloads out of a pane byte stream.

    Tmux-wrapped CLIs (Claude Code's ``/copy``, Codex's clipboard
    integration) emit ``\\x1b]52;<targets>;<base64>\\x07`` to ask the
    outer terminal to write to the system clipboard. Pyte sees those
    bytes and quietly drops them — they never reach xterm.js — so for
    the tmux transport the WS handler has to lift the payload out
    upstream and forward it to the session-state socket as a typed
    ``clipboard_copy`` envelope.

    The extractor is stateful so a sequence split across two reads (a
    20 ms poll boundary lands mid-base64 for large copies) still emits
    once the trailer arrives.
    """

    _PREFIX = b"\x1b]52;"

    def __init__(self) -> None:
        self._pending: bytes = b""

    def feed(self, chunk: bytes) -> list[str]:
        """Return decoded clipboard text for every complete OSC 52 in
        ``chunk`` (plus any sequence whose tail finally arrived).

        Reads that contain no OSC 52 cost one ``bytes.find`` per call;
        partial sequences are held verbatim across feeds, capped at
        ``_MAX_PENDING_NON_CSI`` so a malformed unterminated payload
        can't grow the buffer without bound.
        """
        buf = self._pending + chunk
        self._pending = b""
        results: list[str] = []
        i = 0
        n = len(buf)
        while i < n:
            start = buf.find(self._PREFIX, i)
            if start < 0:
                # No prefix in the remainder — but the very tail may be
                # the first few bytes of a prefix that completes next
                # feed, so hold up to ``len(prefix) - 1`` bytes.
                tail_keep = min(n - i, len(self._PREFIX) - 1)
                if tail_keep > 0:
                    self._pending = buf[n - tail_keep :]
                return results
            payload_start = start + len(self._PREFIX)
            j = payload_start
            end = -1
            next_i = -1
            while j < n:
                b = buf[j]
                if b == 0x07:  # BEL
                    end = j
                    next_i = j + 1
                    break
                if b == 0x1B:  # ESC — only valid terminator is ESC \
                    if j + 1 >= n:
                        # ST split across feeds; hold from ``start``.
                        break
                    if buf[j + 1] == 0x5C:  # '\\'
                        end = j
                        next_i = j + 2
                        break
                    # Malformed (ESC followed by non-ST inside OSC). Cut
                    # the sequence here so a stray ESC can't make us
                    # buffer until ``_MAX_PENDING_NON_CSI``.
                    end = j
                    next_i = j + 1
                    break
                j += 1
            if end < 0:
                # Incomplete — keep the whole sequence; drop on overflow
                # rather than letting a runaway tail wedge the buffer.
                tail = buf[start:]
                if len(tail) <= _MAX_PENDING_NON_CSI:
                    self._pending = tail
                return results
            inner = buf[payload_start:end]
            # Format is ``<targets>;<payload>``; the targets field can
            # be empty (defaults to ``c``). ``?`` is a clipboard *read*
            # query, which we ignore for security.
            sep = inner.find(b";")
            if sep >= 0:
                payload = inner[sep + 1 :]
                if payload and payload != b"?":
                    try:
                        decoded = base64.b64decode(payload, validate=False)
                    except (binascii.Error, ValueError):
                        decoded = b""
                    if decoded:
                        text = decoded.decode("utf-8", errors="replace")
                        if text:
                            results.append(text)
            i = next_i
        return results
