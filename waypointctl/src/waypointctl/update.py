import os
import subprocess
from pathlib import Path

import typer

from waypointctl.paths import resolve_waypoint_home


def _resolve_home(home: Path | None) -> Path:
    try:
        return resolve_waypoint_home(home)
    except RuntimeError:
        # Fall back to the default install location only when it already exists
        # and looks like a real repo; otherwise re-raise the actionable error.
        cand = Path.home() / ".waypoint" / "app"
        if (cand / "backend").exists() and (cand / "frontend").exists():
            return cand
        raise


def _latest_tag(home: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(home), "tag", "--list", "--sort=-version:refname"],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        tag = line.strip()
        if tag:
            return tag
    raise RuntimeError(f"no tags found in {home}")


def run(home: Path | None = None, ref: str | None = None) -> None:
    """Fetch the latest release tag and restart the stack."""
    resolved = _resolve_home(home)
    typer.echo(f"Updating {resolved}")

    subprocess.run(["git", "-C", str(resolved), "fetch", "--tags"], check=True)

    target = ref if ref is not None else _latest_tag(resolved)
    typer.echo(f"Checking out {target}")
    subprocess.run(["git", "-C", str(resolved), "checkout", target], check=True)

    subprocess.run(
        ["uv", "tool", "install", "--force", str(resolved / "waypointctl")], check=True
    )

    restart_env = {**os.environ, "WAYPOINT_STACK_FORCE_FRONTEND_BUILD": "1"}
    subprocess.run(
        ["waypointctl", "--home", str(resolved), "restart"],
        check=True,
        env=restart_env,
    )
    typer.echo(f"Updated to {target}")
