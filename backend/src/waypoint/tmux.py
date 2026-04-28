import asyncio
from dataclasses import dataclass
from pathlib import Path
import shlex


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
        return await self.describe_target(session_name)

    async def describe_target(self, target: str) -> TmuxTarget:
        template = "#{session_name}|#{window_index}|#{pane_id}|#{pane_current_path}|#{pane_dead}|#{pane_pid}"
        output = await self._run("display-message", "-p", "-t", target, template)
        session_name, window_index, pane_id, cwd, pane_dead, pane_pid = output.strip().split("|")
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

    async def interrupt(self, target: str) -> None:
        await self._run("send-keys", "-t", target, "C-c")

    async def resume(self, target: str) -> None:
        await self._run("send-keys", "-t", target, "Enter")

    async def pipe_output(self, target: str, path: Path) -> None:
        command = f"cat >> {shlex.quote(str(path))}"
        await self._run("pipe-pane", "-o", "-t", target, command)

    async def capture_snapshot(self, target: str, start_line: int = -200) -> str:
        return await self._run("capture-pane", "-p", "-J", "-e", "-t", target, "-S", str(start_line))

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
            raise TmuxError(stderr.decode().strip() or f"tmux command failed: {' '.join(args)}")
        return stdout.decode()

    async def _send_literal_text(self, target: str, text: str) -> None:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")
        for index, line in enumerate(lines):
            if line:
                await self._run("send-keys", "-t", target, "-l", "--", line)
            if index < len(lines) - 1:
                await self._run("send-keys", "-t", target, "Enter")
