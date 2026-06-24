import subprocess
from pathlib import Path

import typer

from waypointctl.paths import resolve_waypoint_home


def _resolve_home(home: Path | None) -> Path:
    try:
        return resolve_waypoint_home(home)
    except RuntimeError:
        return Path.home() / ".waypoint" / "app"


def _latest_tag(home: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(home), "describe", "--tags", "--abbrev=0"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def run(home: Path | None = None, ref: str | None = None) -> None:
    """Fetch the latest release tag and restart the stack."""
    resolved = _resolve_home(home)
    typer.echo(f"Updating {resolved}")

    subprocess.run(["git", "-C", str(resolved), "fetch", "--tags"], check=True)

    target = ref if ref is not None else _latest_tag(resolved)
    typer.echo(f"Checking out {target}")
    subprocess.run(["git", "-C", str(resolved), "checkout", target], check=True)

    subprocess.run(["uv", "tool", "install", str(resolved / "waypointctl")], check=True)

    subprocess.run(["waypointctl", "--home", str(resolved), "restart"], check=True)
    typer.echo(f"Updated to {target}")
