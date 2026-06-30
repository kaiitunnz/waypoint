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


def _target_ref(home: Path, ref: str) -> str:
    # Branch refs track the remote tip (so nightly / --ref main actually
    # advance); tags and SHAs resolve as-is. Either way the caller detaches.
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
    return f"origin/{ref}" if remote.returncode == 0 else ref


def _rev(home: Path, rev: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(home), "rev-parse", rev],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _select_target(home: Path, ref: str | None, nightly: bool) -> str:
    if nightly:
        return DEFAULT_BRANCH
    if ref is not None:
        return ref
    return _latest_tag(home)


def _fetch(home: Path) -> None:
    subprocess.run(
        ["git", "-C", str(home), "fetch", "--force", "--tags", "origin"], check=True
    )


def _checkout(home: Path, ref: str) -> None:
    subprocess.run(
        ["git", "-C", str(home), "checkout", "--detach", _target_ref(home, ref)],
        check=True,
    )


def _check(home: Path, ref: str | None, nightly: bool) -> None:
    """Report whether a newer revision is available, without changing anything."""
    _fetch(home)
    target = _select_target(home, ref, nightly)
    # `^{commit}` dereferences annotated tags to the commit they point at, so
    # the comparison matches HEAD (always a commit) regardless of tag kind.
    if _rev(home, "HEAD") == _rev(home, f"{_target_ref(home, target)}^{{commit}}"):
        typer.echo(f"Up to date ({target}).")
    else:
        typer.echo(f"Update available ({target}). Run 'waypointctl update' to apply.")


def run(
    home: Path | None = None,
    ref: str | None = None,
    nightly: bool = False,
    check: bool = False,
) -> None:
    """Update Waypoint to the latest release (or --ref / --nightly).

    The stack is left untouched; run `waypointctl restart` afterward to apply.
    With check=True, only report whether an update is available and return.
    """
    if nightly and ref is not None:
        raise typer.BadParameter("--nightly cannot be combined with --ref")

    resolved = _resolve_home(home)

    # A check is read-only, so it runs before the managed-drift and dirty-tree
    # guards that only matter when we are about to rewrite the working tree.
    if check:
        _check(resolved, ref, nightly)
        return

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
    _fetch(resolved)

    target = _select_target(resolved, ref, nightly)

    typer.echo(f"Checking out {target}")
    _checkout(resolved, target)

    # --reinstall (not just --force): waypointctl's version comes from the git
    # tag via setuptools-scm, and a tag-only change isn't in uv's build-cache
    # key, so --force alone reuses the previous wheel and reports a stale version.
    subprocess.run(
        [
            "uv",
            "tool",
            "install",
            "--reinstall",
            "--force",
            str(resolved / "waypointctl"),
        ],
        check=True,
    )

    typer.echo(f"Updated to {target}. Run 'waypointctl restart' to apply.")
