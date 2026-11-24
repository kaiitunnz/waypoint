import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from waypointctl.paths import resolve_state_dir
from waypointctl.workspace import FrontendVariant, parse_variant


@dataclass(slots=True, frozen=True)
class StackConfig:
    home: Path
    state_dir: Path
    backend_host: str
    backend_port: int
    backend_config: Path
    backend_data_dir: Path
    frontend_port: int
    frontend_dev: bool
    start_timeout: int
    uv_cache_dir: Path
    force_frontend_build: bool
    caffeinate: bool
    control_host: str
    control_port: int
    child_env: dict[str, str]
    frontend_variant: FrontendVariant = "v1"
    frontend_preview_port: int | None = None


def load_env(home: Path) -> dict[str, str]:
    merged: dict[str, str] = dict(os.environ)
    env_file = home / ".env"
    if env_file.exists():
        for key, value in dotenv_values(env_file).items():
            if value is not None:
                merged[key] = value
    return merged


def apply_dotenv(home: Path) -> None:
    """Merge `$home/.env` into `os.environ` (dotenv wins, matching `set -a; source .env`)."""
    env_file = home / ".env"
    if not env_file.exists():
        return
    for key, value in dotenv_values(env_file).items():
        if value is not None:
            os.environ[key] = value


def load_stack_config(home: Path, env: dict[str, str] | None = None) -> StackConfig:
    env = env if env is not None else load_env(home)
    state_dir = resolve_state_dir()
    backend_port = int(env.get("WAYPOINT_STACK_BACKEND_PORT", "8787"))
    frontend_port = int(env.get("WAYPOINT_STACK_FRONTEND_PORT", "3000"))
    preview_port = _parse_preview_port(
        env.get("WAYPOINT_STACK_FRONTEND_PREVIEW_PORT"),
        taken={
            "WAYPOINT_STACK_BACKEND_PORT": backend_port,
            "WAYPOINT_STACK_FRONTEND_PORT": frontend_port,
        },
    )

    return StackConfig(
        home=home,
        state_dir=state_dir,
        backend_host=env.get("WAYPOINT_STACK_BACKEND_HOST", "0.0.0.0"),
        backend_port=backend_port,
        backend_config=_resolve_under_home(
            env.get("WAYPOINT_STACK_CONFIG"),
            home,
            default=home / "backend" / "waypoint.yaml",
        ),
        backend_data_dir=_resolve_under_home(
            env.get("WAYPOINT_STACK_BACKEND_DATA_DIR"),
            home,
            default=state_dir / "backend-data",
        ),
        frontend_port=frontend_port,
        frontend_dev=_parse_bool(env.get("WAYPOINT_STACK_FRONTEND_DEV"), default=False),
        start_timeout=int(env.get("WAYPOINT_STACK_START_TIMEOUT", "30")),
        uv_cache_dir=_resolve_under_home(
            env.get("WAYPOINT_STACK_UV_CACHE_DIR"),
            home,
            default=state_dir / "uv-cache",
        ),
        force_frontend_build=_parse_bool(
            env.get("WAYPOINT_STACK_FORCE_FRONTEND_BUILD"), default=False
        ),
        caffeinate=_parse_bool(env.get("WAYPOINT_STACK_CAFFEINATE"), default=True),
        control_host=env.get("WAYPOINT_STACK_CONTROL_HOST", "0.0.0.0"),
        control_port=int(env.get("WAYPOINT_STACK_CONTROL_PORT", "8799")),
        child_env=env,
        frontend_variant=parse_variant(env.get("WAYPOINT_STACK_FRONTEND")),
        frontend_preview_port=preview_port,
    )


def _parse_preview_port(raw: str | None, *, taken: dict[str, int]) -> int | None:
    if raw is None or not raw.strip():
        return None
    port = int(raw)
    if not 0 < port < 65536:
        raise ValueError(f"WAYPOINT_STACK_FRONTEND_PREVIEW_PORT out of range: {port}")
    for name, other in taken.items():
        if port == other:
            raise ValueError(
                f"WAYPOINT_STACK_FRONTEND_PREVIEW_PORT {port} collides with {name}"
            )
    return port


def _resolve_under_home(raw: str | None, home: Path, *, default: Path) -> Path:
    if not raw:
        return default
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate
    return (home / candidate).resolve()


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}
