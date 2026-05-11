import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from waypoint.backends.plugin_config import PluginConfig
from waypoint.backends.registry import get_registry
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import BackendId

BACKEND_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CONFIG_PATH = BACKEND_ROOT / "waypoint.yaml"

DEFAULT_CORS_ORIGINS: tuple[str, ...] = ()
DNS_LABEL_PATTERN = r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
TAILSCALE_DNS_NAME_PATTERN = rf"(?:{DNS_LABEL_PATTERN}\.)+(?:ts\.net|tailscale\.net)"

DEFAULT_CORS_ORIGIN_REGEX = (
    r"^https?://(localhost|127\.0\.0\.1|"
    r"100\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"\[fd7a:115c:a1e0(:[0-9a-fA-F]{0,4}){0,7}\]|"
    rf"{DNS_LABEL_PATTERN}|{TAILSCALE_DNS_NAME_PATTERN})(:\d+)?$"
)


def default_data_dir() -> Path:
    return Path.home() / ".waypoint" / "backend-data"


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


def _default_backend_id() -> str:
    """Pick a default backend at validation time.

    Prefers ``codex`` when registered (the historical default),
    falling back to the first registered non-fallback plugin so a
    custom registry without Codex still validates without an explicit
    ``default_backend`` override. The fallback wrapper plugin (today:
    tmux) is the last resort — it should never be a user's default.
    """
    registry = get_registry()
    if registry.has_backend("codex"):
        return "codex"
    for plugin in registry.all():
        if not plugin.capabilities.is_fallback_for_managed_launch:
            return plugin.id
    plugins = registry.all()
    return plugins[0].id if plugins else "tmux"


class Settings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    password: str = "change-me"
    config_path: Path | None = None
    default_backend: BackendId = Field(default_factory=_default_backend_id)
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
    # Per-plugin configuration blocks keyed by plugin id. Each raw YAML
    # block is dispatched at validation time to the plugin's
    # ``config_schema`` so subclass fields (e.g. claude's curated model
    # catalogue) survive ``extra="forbid"``. Missing entries fall back
    # to the schema's defaults so plugin-specific YAML stays optional.
    plugin_configs: dict[BackendId, PluginConfig] = Field(default_factory=dict)
    # Default page size for `/api/sessions/{id}/events` measured in *logical
    # chat messages* (agent_output deltas with the same item_id collapse
    # into one, tool_call+tool_result pairs share an item_id, everything
    # else counts individually). Sized so that an initial chat-view paint
    # stays cheap on large transcripts and each "Load older" click reliably
    # surfaces N visible bubbles regardless of how many raw events the
    # backend emitted per message.
    chat_page_messages: int = Field(default=20, ge=1, le=200)

    @field_validator("plugin_configs", mode="before")
    @classmethod
    def _dispatch_plugin_configs(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        registry = get_registry()
        dispatched: dict[str, PluginConfig] = {}
        for plugin_id, raw in value.items():
            if not isinstance(plugin_id, str) or not registry.has_backend(plugin_id):
                raise ValueError(f"unknown backend: {plugin_id!r}")
            schema = registry.get(plugin_id).config_schema
            dispatched[plugin_id] = (
                raw if isinstance(raw, schema) else schema.model_validate(raw or {})
            )
        return dispatched

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_name

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / self.sessions_dir_name

    def plugin_config(self, plugin_id: str) -> PluginConfig:
        """Return the validated config for ``plugin_id``.

        Falls back to a default-constructed instance of the plugin's
        ``config_schema`` when the user hasn't supplied a block in
        ``waypoint.yaml``.
        """
        cfg = self.plugin_configs.get(plugin_id)
        if cfg is not None:
            return cfg
        return get_registry().get(plugin_id).config_schema()

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
