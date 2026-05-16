import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path


class TmuxError(RuntimeError):
    pass


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
        session_name, window_index, pane_id, cwd, pane_dead, pane_pid = (
            output.strip().split("|")
        )
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
        # `cat` switches to block buffering when stdout is a regular file,
        # holding up to ~4 KB before flushing — which traps the trailing
        # cursor-positioning bytes of every render in userland buffer
        # and breaks the live-stream view. `dd` reads and writes via raw
        # syscalls with no stdio buffer, so every chunk pipe-pane hands
        # us reaches the log file immediately. Shell-level >> append is
        # portable across BSD dd (macOS) and GNU dd (Linux); `oflag=` is
        # GNU-only and fails silently on BSD.
        command = f"dd 2>/dev/null >> {shlex.quote(str(path))}"
        await self._run("pipe-pane", "-o", "-t", target, command)

    async def stop_pipe(self, target: str) -> None:
        await self._run("pipe-pane", "-t", target)

    async def kill_session(self, name: str) -> None:
        await self._run("kill-session", "-t", name)

    async def capture_snapshot(self, target: str, start_line: int = -200) -> str:
        return await self._run(
            "capture-pane", "-p", "-J", "-e", "-t", target, "-S", str(start_line)
        )

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
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")
        for index, line in enumerate(lines):
            if line:
                await self._run("send-keys", "-t", target, "-l", "--", line)
            if index < len(lines) - 1:
                await self._run("send-keys", "-t", target, "Enter")
