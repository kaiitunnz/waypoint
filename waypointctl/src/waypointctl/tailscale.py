import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import typer


@dataclass(slots=True, frozen=True)
class ToolAvailability:
    docker: bool
    tailscale: bool


def detect_tool_availability() -> ToolAvailability:
    return ToolAvailability(
        docker=shutil.which("docker") is not None,
        tailscale=shutil.which("tailscale") is not None,
    )


def tailscale_helper_script(home: Path) -> Path:
    return home / "scripts" / "waypoint_tailscale.sh"


def preflight_tailscale_command(command: str) -> None:
    availability = detect_tool_availability()

    if availability.docker:
        if availability.tailscale and command == "up":
            if not sys.stdin.isatty():
                typer.echo(
                    "Docker and Tailscale are installed, but this command needs an "
                    "interactive confirmation.",
                    err=True,
                )
                raise typer.Exit(code=1)
            if not typer.confirm(
                "Docker and Tailscale are both installed. Proceed with Docker deployment?",
                default=False,
            ):
                raise typer.Exit(code=1)
        return

    # Docker is missing from here on. `status` is a read-only query — degrade
    # gracefully so users can ask "is there a container?" on hosts without
    # Docker installed.
    if command == "status":
        typer.echo("docker not installed; no tailscale container on this host.")
        raise typer.Exit(code=0)

    if command == "logs":
        typer.echo("Docker is required to read container logs.", err=True)
        raise typer.Exit(code=1)

    if availability.tailscale:
        typer.echo(
            "Tailscale is installed on this machine, but Docker is missing. "
            "This helper deploys the tailnet node in Docker.",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(
        "Install either Docker or Tailscale before running this command.",
        err=True,
    )
    raise typer.Exit(code=1)


def run_tailscale_helper(
    home: Path,
    command: str,
    profile: str,
) -> None:
    script = tailscale_helper_script(home)
    argv = ["bash", str(script), command, profile]
    completed = subprocess.run(argv, cwd=home, check=False)
    raise typer.Exit(code=completed.returncode)
