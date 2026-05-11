from dataclasses import dataclass
from pathlib import Path

from waypointctl.legacy import run_legacy_command


@dataclass(slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class WaypointSupervisor:
    def __init__(self, home: Path) -> None:
        self.home = home

    def run(self, command: str, args: list[str]) -> CommandResult:
        completed = run_legacy_command(self.home, command, args)
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
