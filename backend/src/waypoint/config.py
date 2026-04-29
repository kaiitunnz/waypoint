import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from waypoint.schemas import Backend, BackendModelOption
from waypoint.server_config import SshLaunchTargetConfig

# Canonical Claude Code model picker entries. Mirrors the per-model factory
# functions (WQ7/GQ7/RQ7/NQ7/...) baked into the CLI binary; Claude does not
# expose a runtime model-list RPC, so we maintain this list and bump it when
# the CLI ships new aliases. Free-text input is allowed via the API too, so
# any string the binary accepts works even if not listed here.
DEFAULT_CLAUDE_MODELS: tuple[BackendModelOption, ...] = (
    BackendModelOption(
        id="opus",
        label="Opus 4.7",
        description="Most capable for complex work",
    ),
    BackendModelOption(
        id="sonnet",
        label="Sonnet 4.6",
        description="Best for everyday tasks",
        is_default=True,
    ),
    BackendModelOption(
        id="haiku",
        label="Haiku 4.5",
        description="Fast and lightweight",
    ),
    BackendModelOption(
        id="opus[1m]",
        label="Opus 4.7 (1M context)",
        description="Long sessions with large codebases",
    ),
    BackendModelOption(
        id="sonnet[1m]",
        label="Sonnet 4.6 (1M context)",
        description="Long sessions with large codebases",
    ),
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CONFIG_PATH = BACKEND_ROOT / "waypoint.yaml"

DEFAULT_CORS_ORIGINS: tuple[str, ...] = ()
DEFAULT_CORS_ORIGIN_REGEX = (
    r"^https?://(localhost|127\.0\.0\.1|"
    r"100\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"\[fd7a:115c:a1e0(:[0-9a-fA-F]{0,4}){0,7}\])(:\d+)?$"
)


def default_data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "Waypoint"


def parse_cors_origins(raw: str | None) -> list[str]:
    if raw is None:
        return list(DEFAULT_CORS_ORIGINS)
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_cors_origin_regex(raw: str | None) -> str | None:
    if raw is None:
        return DEFAULT_CORS_ORIGIN_REGEX
    return raw or None


def parse_config_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    return Path(os.path.expandvars(raw)).expanduser()


class Settings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    password: str = "change-me"
    config_path: Path | None = None
    default_backend: Backend = Backend.CODEX
    default_cwd: str = "~/"
    data_dir: Path = Field(default_factory=default_data_dir)
    sessions_dir_name: str = "sessions"
    database_name: str = "waypoint.db"
    token_ttl_seconds: int = 60 * 60 * 24 * 30
    stream_poll_interval: float = 1.0
    tail_snapshot_lines: int = 200
    cors_origins: list[str] = Field(default_factory=lambda: list(DEFAULT_CORS_ORIGINS))
    cors_allow_origin_regex: str | None = DEFAULT_CORS_ORIGIN_REGEX
    ssh_targets: list[SshLaunchTargetConfig] = Field(default_factory=list)
    # Default model per backend, keyed by Backend value (e.g. "claude_code",
    # "codex"). Missing keys mean "let the backend pick" — no --model is
    # forwarded and the backend falls back to its built-in default.
    default_models: dict[str, str] = Field(default_factory=dict)
    claude_models: list[BackendModelOption] = Field(
        default_factory=lambda: list(DEFAULT_CLAUDE_MODELS)
    )

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_name

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / self.sessions_dir_name

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)


def load_settings(config_path_override: Path | None = None) -> Settings:
    explicit = config_path_override or parse_config_path(
        os.environ.get("WAYPOINT_CONFIG_PATH")
    )
    if explicit is not None:
        config_path: Path | None = explicit
        require_exists = True
    else:
        config_path = DEFAULT_CONFIG_PATH
        require_exists = False
    payload = _load_config_payload(config_path, require_exists=require_exists)
    expanded = config_path.expanduser() if config_path is not None else None
    payload["config_path"] = (
        expanded if expanded is not None and expanded.exists() else None
    )
    payload.update(_env_overrides())
    payload = _normalize_payload(payload)
    return Settings.model_validate(payload)


def _load_config_payload(
    config_path: Path | None, require_exists: bool = True
) -> dict[str, Any]:
    if config_path is None:
        return {}
    expanded = config_path.expanduser()
    if not expanded.exists():
        if require_exists:
            raise FileNotFoundError(f"waypoint config file not found: {expanded}")
        return {}
    data = yaml.safe_load(expanded.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("waypoint config file must contain a top-level mapping")
    return dict(data)


def _env_overrides() -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if "WAYPOINT_HOST" in os.environ:
        overrides["host"] = os.environ["WAYPOINT_HOST"]
    if "WAYPOINT_PORT" in os.environ:
        overrides["port"] = int(os.environ["WAYPOINT_PORT"])
    if "WAYPOINT_PASSWORD" in os.environ:
        overrides["password"] = os.environ["WAYPOINT_PASSWORD"]
    if "WAYPOINT_DATA_DIR" in os.environ:
        overrides["data_dir"] = Path(
            os.path.expandvars(os.environ["WAYPOINT_DATA_DIR"])
        ).expanduser()
    if "WAYPOINT_CORS_ORIGINS" in os.environ:
        overrides["cors_origins"] = parse_cors_origins(
            os.environ["WAYPOINT_CORS_ORIGINS"]
        )
    if "WAYPOINT_CORS_ORIGIN_REGEX" in os.environ:
        overrides["cors_allow_origin_regex"] = parse_cors_origin_regex(
            os.environ["WAYPOINT_CORS_ORIGIN_REGEX"]
        )
    return overrides


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    legacy_remote = normalized.pop("codex_remote", None)
    if legacy_remote and "ssh_targets" not in normalized:
        legacy_target = dict(legacy_remote)
        if legacy_target.pop("enabled", False):
            normalized["ssh_targets"] = [
                {
                    "id": "ssh-default",
                    "name": "SSH coding backend",
                    "supported_backends": [Backend.CODEX.value],
                    **legacy_target,
                }
            ]
    if "config_path" in normalized and normalized["config_path"] is not None:
        normalized["config_path"] = Path(normalized["config_path"]).expanduser()
    if "data_dir" in normalized and normalized["data_dir"] is not None:
        normalized["data_dir"] = Path(normalized["data_dir"]).expanduser()
    if "cors_origins" in normalized and normalized["cors_origins"] is None:
        normalized["cors_origins"] = list(DEFAULT_CORS_ORIGINS)
    if (
        "cors_allow_origin_regex" in normalized
        and normalized["cors_allow_origin_regex"] is None
    ):
        normalized["cors_allow_origin_regex"] = None
    return normalized
