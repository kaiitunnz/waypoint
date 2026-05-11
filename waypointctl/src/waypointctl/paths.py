import os
from pathlib import Path


def resolve_waypoint_home(raw: str | Path | None = None) -> Path:
    if raw is not None:
        return Path(raw).expanduser().resolve()

    env = os.environ.get("WAYPOINT_HOME")
    if env:
        return Path(env).expanduser().resolve()

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if _looks_like_waypoint_repo(candidate):
            return candidate

    raise RuntimeError(
        "set WAYPOINT_HOME to the Waypoint repository root "
        "(expected backend/, frontend/, and scripts/)"
    )


def waypoint_state_dir(home: Path) -> Path:
    return home / "tmp" / "waypointctl"


def waypoint_socket_path(home: Path) -> Path:
    return waypoint_state_dir(home) / "waypointd.sock"


def waypoint_pid_path(home: Path) -> Path:
    return waypoint_state_dir(home) / "waypointd.pid"


def waypoint_log_path(home: Path) -> Path:
    return waypoint_state_dir(home) / "waypointd.log"


def waypoint_script_path(home: Path) -> Path:
    return home / "scripts" / "waypoint.sh"


def _looks_like_waypoint_repo(path: Path) -> bool:
    return all((path / name).exists() for name in ("backend", "frontend", "scripts"))
