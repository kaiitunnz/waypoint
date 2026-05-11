import os
from pathlib import Path

DEFAULT_STATE_DIR = Path("~/.waypoint")


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


def resolve_state_dir() -> Path:
    raw = os.environ.get("WAYPOINTCTL_STATE_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_STATE_DIR.expanduser().resolve()


def state_run_dir() -> Path:
    return resolve_state_dir() / "run"


def state_log_dir() -> Path:
    return resolve_state_dir() / "logs"


def waypoint_socket_path() -> Path:
    return state_run_dir() / "waypointd.sock"


def waypoint_pid_path() -> Path:
    return state_run_dir() / "waypointd.pid"


def waypoint_log_path() -> Path:
    return state_log_dir() / "waypointd.log"


def pid_file_for(service: str) -> Path:
    return state_run_dir() / f"{service}.pid"


def log_file_for(service: str) -> Path:
    return state_log_dir() / f"{service}.log"


def started_marker_for(service: str) -> Path:
    return state_run_dir() / f"{service}.started-this-run"


def _looks_like_waypoint_repo(path: Path) -> bool:
    return all((path / name).exists() for name in ("backend", "frontend", "scripts"))
