from dataclasses import dataclass
from pathlib import Path

from waypointctl.config import load_stack_config
from waypointctl.services import ServiceResult
from waypointctl.stack import WaypointStack


@dataclass(slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class WaypointSupervisor:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.stack = WaypointStack(load_stack_config(home))

    def run(self, command: str, args: list[str]) -> CommandResult:
        stdout: list[str] = []
        stderr: list[str] = []

        def log(stream: str, line: str) -> None:
            (stdout if stream == "stdout" else stderr).append(line)

        result = self._dispatch(command, args, log)
        return CommandResult(
            returncode=0 if result.ok else 1,
            stdout=_join(stdout),
            stderr=_join(stderr)
            + (f"{result.message}\n" if not result.ok and result.message else ""),
        )

    def _dispatch(self, command: str, args: list[str], log) -> ServiceResult:  # type: ignore[no-untyped-def]
        if command == "start":
            return self.stack.start(log)
        if command == "stop":
            return self.stack.stop(log)
        if command == "restart":
            target = args[0] if args else "all"
            return self.stack.restart(target, log)
        if command == "status":
            return self.stack.status(log)
        return ServiceResult(ok=False, message=f"unknown command: {command}")


def _join(lines: list[str]) -> str:
    if not lines:
        return ""
    return "\n".join(lines) + "\n"
