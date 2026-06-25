import subprocess
from pathlib import Path

import typer

from waypointctl.paths import resolve_waypoint_home

DEFAULT_BRANCH = "main"

# Tracked files the frontend build regenerates; a prior `start`/`restart` leaves
# them modified, which would otherwise trip the dirty-tree guard. They are
# rewritten by the post-checkout build, so discarding the drift is safe.
_GENERATED_FILES = ("frontend/next-env.d.ts", "frontend/tsconfig.json")


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


def _is_dirty(home: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(home), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _is_managed(home: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(home), "config", "--get", "waypoint.managed"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "true"


def _discard_generated(home: Path) -> None:
    for rel in _GENERATED_FILES:
        subprocess.run(
            ["git", "-C", str(home), "checkout", "--", rel],
            check=False,
            capture_output=True,
        )


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


def _checkout(home: Path, ref: str) -> None:
    # Branch refs track the remote tip (so nightly / --ref main actually
    # advance); tags and SHAs detach. Either way we land in a detached HEAD.
    remote = subprocess.run(
        [
            "git",
            "-C",
            str(home),
            "rev-parse",
            "--verify",
            "--quiet",
            f"refs/remotes/origin/{ref}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    target = f"origin/{ref}" if remote.returncode == 0 else ref
    subprocess.run(["git", "-C", str(home), "checkout", "--detach", target], check=True)


def run(
    home: Path | None = None, ref: str | None = None, nightly: bool = False
) -> None:
    """Update Waypoint to the latest release (or --ref / --nightly).

    The stack is left untouched; run `waypointctl restart` afterward to apply.
    """
    if nightly and ref is not None:
        raise typer.BadParameter("--nightly cannot be combined with --ref")

    resolved = _resolve_home(home)
    # In an installer-managed checkout, clear build-generated drift first so a
    # prior start/restart doesn't block the update on "uncommitted changes".
    if _is_managed(resolved):
        _discard_generated(resolved)
    if _is_dirty(resolved):
        raise RuntimeError(
            f"refusing to update {resolved}: it has uncommitted changes; "
            "commit or stash them first"
        )

    typer.echo(f"Updating {resolved}")
    subprocess.run(
        ["git", "-C", str(resolved), "fetch", "--force", "--tags", "origin"], check=True
    )

    if nightly:
        target = DEFAULT_BRANCH
    elif ref is not None:
        target = ref
    else:
        target = _latest_tag(resolved)

    typer.echo(f"Checking out {target}")
    _checkout(resolved, target)

    subprocess.run(
        ["uv", "tool", "install", "--force", str(resolved / "waypointctl")], check=True
    )

    typer.echo(f"Updated to {target}. Run 'waypointctl restart' to apply.")
