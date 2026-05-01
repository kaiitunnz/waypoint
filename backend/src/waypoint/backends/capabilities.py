from dataclasses import dataclass, field
from enum import StrEnum


class ModelSource(StrEnum):
    STATIC = "static"
    LIVE_RPC = "live_rpc"
    NONE = "none"


@dataclass(frozen=True)
class PermissionModeSpec:
    id: str
    label: str
    description: str | None = None
    requires_session_restart: bool = False


@dataclass(frozen=True)
class SlashCommandSpec:
    name: str
    description: str | None = None


@dataclass(frozen=True)
class BackendCapabilities:
    is_structured: bool
    supports_resume: bool
    supports_terminate: bool = True
    supports_set_model_inline: bool = False
    supports_set_effort_inline: bool = False
    # The plugin can change effort by restarting the protocol process
    # (Claude: stop CLI + respawn with a new --effort) rather than
    # applying it mid-stream. Effectively widens the gate around
    # ``apply_effort`` so plugins that surface a "swap with restart"
    # path don't have to also claim ``supports_set_effort_inline``.
    supports_set_effort_with_restart: bool = False
    supports_set_permission_mode_inline: bool = False
    supports_thread_discovery: bool = False
    supports_thread_import: bool = False
    supports_slash_compact: bool = False
    permission_modes: tuple[PermissionModeSpec, ...] = ()
    effort_levels: tuple[str, ...] = ()
    model_source: ModelSource = ModelSource.NONE
    slash_commands: tuple[SlashCommandSpec, ...] = ()
    approval_decisions: tuple[str, ...] = ("approve", "decline")
    badges: dict[str, str] = field(default_factory=dict)
    # CLI binary used when this backend is launched in attached-tmux
    # fallback mode. ``None`` means the plugin doesn't ship a CLI
    # entry-point and can't be paired with the tmux transport.
    cli_binary: str | None = None
    # Substrings (case-insensitive) used to infer a backend from a tmux
    # target name when the user attaches to an existing pane without
    # specifying which CLI is running there.
    target_aliases: tuple[str, ...] = ()
