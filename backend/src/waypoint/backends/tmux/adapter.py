import asyncio
import re
import shlex
import uuid
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from waypoint.backends.base import PaneTypingSpec

# tmux rejects a command over ~16 KB ("command too long").
_COMMAND_BUDGET_BYTES = 12_000
_TAB_AS_SPACES = "    "
# Control characters would be read as keys (ESC starts a sequence); LF stays.
_UNTYPEABLE_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")
_LEADING_BLANK_LINES_RE = re.compile(r"\A(?:[ \t]*\n)+")


class TmuxError(RuntimeError):
    pass


def _tmux_literal(text: str) -> str:
    # tmux treats an argument ending in ";" as a command separator; "\;" is a
    # literal one.
    return f"{text[:-1]}\\;" if text.endswith(";") else text


def _utf8_len(char: str) -> int:
    return len(char.encode())


def _utf16_len(char: str) -> int:
    return 2 if ord(char) > 0xFFFF else 1


def _split(text: str, limit: int, width: Callable[[str], int]) -> list[str]:
    pieces: list[str] = []
    start = size = 0
    for index, char in enumerate(text):
        char_width = width(char)
        if size + char_width > limit:
            pieces.append(text[start:index])
            start, size = index, 0
        size += char_width
    if start < len(text):
        pieces.append(text[start:])
    return pieces


def _paste_commands(target: str, text: str) -> tuple[str, list[list[str]]]:
    # A fresh name per paste: input is not serialized per pane (an HTTP send and
    # a terminal-WS submit can race), so a fixed name would let one send paste or
    # delete another's buffer.
    buffer_name = f"waypoint-input-{uuid.uuid4().hex}"
    fills = [
        ["set-buffer", *(["-a"] if index else []), "-b", buffer_name, "--"]
        + [_tmux_literal(chunk)]
        for index, chunk in enumerate(_split(text, _COMMAND_BUDGET_BYTES, _utf8_len))
    ]
    # -p brackets the paste when the app asked for it; -r keeps LF so the paste
    # never submits; -d drops the buffer afterward.
    paste = ["paste-buffer", "-d", "-p", "-r", "-b", buffer_name, "-t", target]
    return buffer_name, [*fills, paste]


@dataclass
class TmuxTarget:
    session: str
    window: str
    pane: str
    cwd: str
    pane_dead: bool
    pane_pid: int | None


class TmuxAdapter:
    async def start_managed_session(
        self,
        session_name: str,
        cwd: str,
        command: list[str],
    ) -> TmuxTarget:
        command_string = shlex.join(command)
        await self._run(
            "new-session",
            "-d",
            "-s",
            session_name,
            "-c",
            cwd,
            command_string,
        )
        # Pin window-size to whatever we explicitly resize to. Default
        # ("latest") tracks the most-recently-attached client's size —
        # we never have a client attached, so tmux can revert the pane
        # back to the global default and break our handshake.
        await self._run("set-option", "-t", session_name, "window-size", "manual")
        # Tmux reserves the bottom row of the window for its own status
        # bar by default; we don't render that bar to the user, so the
        # pane ends up one row short of the xterm viewport. Disabling
        # the status bar gives the pane the full window height and keeps
        # the cursor's parked position at the actual visual bottom.
        await self._run("set-option", "-t", session_name, "status", "off")
        return await self.describe_target(session_name)

    async def describe_target(self, target: str) -> TmuxTarget:
        template = "#{session_name}|#{window_index}|#{pane_id}|#{pane_current_path}|#{pane_dead}|#{pane_pid}"
        output = await self._run("display-message", "-p", "-t", target, template)
        fields = output.strip().split("|")
        # Unlike capture-pane/send-keys, `display-message -t` does not validate
        # the target: a missing pane expands the format with empty fields and
        # exits 0 (e.g. "|||||"), so an empty pane id means the target no longer
        # exists. Raise here so liveness checks (`_pane_alive`, `target_exists`,
        # `_refresh_state`) see a dead pane instead of a phantom one whose
        # ``pane_dead`` parses to False.
        if len(fields) != 6 or not fields[2]:
            raise TmuxError(f"can't find target: {target}")
        session_name, window_index, pane_id, cwd, pane_dead, pane_pid = fields
        return TmuxTarget(
            session=session_name,
            window=window_index,
            pane=pane_id,
            cwd=cwd,
            pane_dead=pane_dead == "1",
            pane_pid=int(pane_pid) if pane_pid.isdigit() else None,
        )

    async def send_input(self, target: str, text: str, submit: bool = True) -> None:
        if text:
            await self._send_literal_text(target, text)
        if submit:
            await self.submit(target)

    async def type_input(
        self,
        target: str,
        segments: Sequence[tuple[str, bool]],
        typing_spec: PaneTypingSpec,
        submit: bool = True,
    ) -> None:
        """Type ``(chunk, paste)`` segments into the pane, then press Enter if
        ``submit``.

        Text chunks are typed in pieces of at most ``typing_spec.max_event_units``
        UTF-16 units, with ``typing_spec.event_separator`` between pieces so the
        TUI reads each as its own key event even when tmux writes coalesce.
        ``paste`` chunks are pasted.

        Typed text is normalized: CR/CRLF become LF, tabs become spaces (a typed
        tab triggers completion), other control characters are dropped, and the
        first chunk's leading blank lines are removed.
        """
        separator = ["send-keys", "-t", target, "-H"] + [
            f"{byte:02x}" for byte in typing_spec.event_separator
        ]
        groups: list[tuple[list[list[str]], str | None]] = []
        for index, (chunk, paste) in enumerate(segments):
            if paste:
                name, commands = _paste_commands(target, chunk)
                groups.append((commands, name))
                continue
            text = chunk.replace("\r\n", "\n").replace("\r", "\n")
            text = _UNTYPEABLE_RE.sub("", text.replace("\t", _TAB_AS_SPACES))
            if index == 0:
                text = _LEADING_BLANK_LINES_RE.sub("", text)
            groups.extend(
                ([["send-keys", "-t", target, "-l", "--", _tmux_literal(piece)]], None)
                for piece in _split(text, typing_spec.max_event_units, _utf16_len)
            )

        # Separate invocations can coalesce into one read too, so the separator
        # also opens each later one.
        batch: list[str] = []
        batch_size = 0
        batch_buffers: list[str] = []
        for position, (commands, buffer_name) in enumerate(groups):
            if position:
                commands = [separator, *commands]
            args = [arg for command in commands for arg in (";", *command)][1:]
            size = sum(len(arg.encode()) + 1 for arg in args)
            if batch and batch_size + size > _COMMAND_BUDGET_BYTES:
                await self._run_dropping_buffers(batch, batch_buffers)
                batch, batch_size, batch_buffers = [], 0, []
            batch += [";", *args] if batch else args
            batch_size += size
            if buffer_name is not None:
                batch_buffers.append(buffer_name)
        if batch:
            await self._run_dropping_buffers(batch, batch_buffers)
        if submit:
            await self.submit(target)

    async def _run_dropping_buffers(
        self, args: Sequence[str], buffer_names: Sequence[str]
    ) -> None:
        try:
            await self._run(*args)
        except TmuxError:
            # paste-buffer -d only drops a buffer it pasted; clear any the
            # failed command left behind before propagating.
            for buffer_name in buffer_names:
                with suppress(TmuxError):
                    await self._run("delete-buffer", "-b", buffer_name)
            raise

    async def submit(self, target: str) -> None:
        """Send a bare Enter to submit the pane's current composer content.

        Separated from :meth:`send_input` so a caller can paste with
        ``submit=False`` and then drive the submit on its own — e.g. retrying it
        when the wrapped TUI swallowed the keystroke while ingesting the paste.
        """
        await self._run("send-keys", "-t", target, "Enter")

    async def send_bytes(self, target: str, data: bytes) -> None:
        """Forward arbitrary terminal input bytes to the pane.

        Uses ``send-keys -H`` so escape sequences (arrows, function keys,
        Ctrl combinations) and multibyte UTF-8 characters pass through
        without re-interpretation.
        """
        if not data:
            return
        hex_args = [f"{byte:02x}" for byte in data]
        await self._run("send-keys", "-t", target, "-H", *hex_args)

    async def resize_window(self, session: str, cols: int, rows: int) -> None:
        # Pin manual sizing and disable the status bar first; existing
        # sessions started before either became part of the create flow
        # would otherwise inherit "latest" sizing (which reverts our
        # explicit dimensions) and a one-row status bar (which steals
        # the bottom of the pane from Codex's render area).
        await self._run("set-option", "-t", session, "window-size", "manual")
        await self._run("set-option", "-t", session, "status", "off")
        await self._run(
            "resize-window", "-t", session, "-x", str(cols), "-y", str(rows)
        )

    async def resize_pane(self, pane: str, cols: int, rows: int) -> None:
        await self._run("resize-pane", "-t", pane, "-x", str(cols), "-y", str(rows))

    async def interrupt(self, target: str) -> None:
        await self._run("send-keys", "-t", target, "C-c")

    async def resume(self, target: str) -> None:
        await self._run("send-keys", "-t", target, "Enter")

    async def pipe_output(self, target: str, path: Path) -> None:
        # Plain ``cat`` is stdio-buffered (~4 KB) when stdout is a
        # regular file, so it traps Codex's per-keystroke frames. We
        # previously used ``dd`` because it bypasses stdio — but its
        # default ``bs=512`` accumulates short reads into a full block
        # before writing. Verified empirically: a 168-byte Codex frame
        # writes nothing to the log file until ~512 bytes are in
        # flight. The visible result is exactly the reported symptom —
        # typing one character produces no re-render until enough more
        # chars accumulate to fill the block. ``cat -u`` is the POSIX
        # unbuffered mode ("write bytes from the input file to the
        # standard output without delay as each is read") and writes
        # every pipe-pane delivery immediately regardless of size.
        # Supported on macOS BSD ``cat`` and GNU coreutils.
        command = f"cat -u >> {shlex.quote(str(path))}"
        await self._run("pipe-pane", "-o", "-t", target, command)

    async def stop_pipe(self, target: str) -> None:
        await self._run("pipe-pane", "-t", target)

    async def kill_session(self, name: str) -> None:
        await self._run("kill-session", "-t", name)

    async def capture_snapshot(self, target: str, start_line: int = -200) -> str:
        return await self._run(
            "capture-pane", "-p", "-J", "-e", "-t", target, "-S", str(start_line)
        )

    async def pane_dimensions(self, target: str) -> tuple[int, int]:
        """Return the pane's current (width, height) in columns and rows."""
        output = await self._run(
            "display-message", "-p", "-t", target, "#{pane_width}|#{pane_height}"
        )
        w_str, h_str = output.strip().split("|")
        return int(w_str), int(h_str)

    async def pane_screen_state(self, target: str) -> tuple[bool, int, int]:
        """Return whether the pane is on the alternate screen and the
        program's current cursor position (1-based row, col).

        ``capture-pane`` only dumps cell contents; it omits both the
        screen-buffer toggle and the cursor positioning sequence, so
        callers seeding xterm need this state to recreate the same
        visual context the program is running in.
        """
        output = await self._run(
            "display-message",
            "-p",
            "-t",
            target,
            "#{alternate_on}|#{cursor_x}|#{cursor_y}",
        )
        alt_str, x_str, y_str = output.strip().split("|")
        alt = alt_str == "1"
        # tmux reports cursor coordinates as 0-based; the ANSI CUP
        # sequence is 1-based.
        col = int(x_str) + 1 if x_str.isdigit() else 1
        row = int(y_str) + 1 if y_str.isdigit() else 1
        return alt, col, row

    async def list_sessions(self) -> list[str]:
        output = await self._run("list-sessions", "-F", "#{session_name}")
        return [line.strip() for line in output.splitlines() if line.strip()]

    async def target_exists(self, target: str) -> bool:
        try:
            await self.describe_target(target)
        except TmuxError:
            return False
        return True

    async def _run(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "tmux",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise TmuxError(
                stderr.decode().strip() or f"tmux command failed: {' '.join(args)}"
            )
        return stdout.decode()

    async def _send_literal_text(self, target: str, text: str) -> None:
        # Short single-line text is typed. Multi-line text is pasted: the
        # wrapped CLIs enable bracketed paste, and a raw multi-key burst can land
        # the caller's trailing Enter inside the composer instead of submitting.
        # A line too long for one tmux command is pasted too.
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if "\n" not in normalized and _utf8_len(normalized) <= _COMMAND_BUDGET_BYTES:
            await self._run(
                "send-keys", "-t", target, "-l", "--", _tmux_literal(normalized)
            )
            return
        buffer_name, commands = _paste_commands(target, normalized)
        for command in commands:
            await self._run_dropping_buffers(command, [buffer_name])
