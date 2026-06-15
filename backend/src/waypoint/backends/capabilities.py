from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ModelSource(StrEnum):
    STATIC = "static"
    LIVE_RPC = "live_rpc"
    NONE = "none"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class PermissionModeSpec(_FrozenModel):
    id: str
    label: str
    description: str | None = None
    requires_session_restart: bool = False


class SlashCommandSpec(_FrozenModel):
    name: str
    description: str | None = None
    argument_hint: str | None = None


class AgentCapabilities(_FrozenModel):
    """What the agent (the CLI / protocol) can do, independent of how a
    session drives it.

    These are properties of the coding agent itself — its model catalogue,
    permission-mode vocabulary, thread/fork story, slash commands — and stay
    the same whether the agent is driven through a structured adapter or a
    tmux pane. ``supports_plan_approval`` and ``supports_config_overrides``
    are not in the original split brief but are agent traits (claude's plan
    gate, codex's ``--config`` wrapping), so they live here.
    """

    model_source: ModelSource = ModelSource.NONE
    permission_modes: tuple[PermissionModeSpec, ...] = ()
    effort_levels: tuple[str, ...] = ()
    slash_commands: tuple[SlashCommandSpec, ...] = ()
    approval_decisions: tuple[str, ...] = ("approve", "decline")
    supports_thread_discovery: bool = False
    supports_thread_import: bool = False
    supports_fork: bool = False
    supports_plan_approval: bool = False
    supports_approval_note: bool = False
    supports_attachments: bool = False
    supports_custom_cli_args: bool = False
    supports_config_overrides: bool = False
    supports_slash_compact: bool = False
    cli_binary: str | None = None
    target_aliases: tuple[str, ...] = ()
    badges: dict[str, str] = Field(default_factory=dict)


class TransportCapabilities(_FrozenModel):
    """What the transport (how the agent is driven) can do.

    These are properties of the channel — structured stream vs scraped pane,
    whether a detached session can be resumed/re-attached, which control
    knobs can be set inline vs require a restart, and whether this transport
    is the managed-launch fallback. ``supports_terminate`` is a transport
    trait (can the channel tear a session down) not in the original brief but
    placed here for that reason.
    """

    is_structured: bool
    supports_resume: bool
    supports_reattach_after_exit: bool = False
    supports_terminate: bool = True
    supports_set_model_inline: bool = False
    supports_set_effort_inline: bool = False
    supports_set_effort_with_restart: bool = False
    supports_set_permission_mode_inline: bool = False
    settings_change_interrupts_turn: bool = False
    is_fallback_for_managed_launch: bool = False


class BackendCapabilities(_FrozenModel):
    """Flat compatibility aggregate over the agent / transport split.

    A session is an (agent, transport) pair, and capabilities partition along
    that axis — see :class:`AgentCapabilities` and :class:`TransportCapabilities`.
    This flat model is retained as the single descriptor a plugin declares and
    the runtime/API read: its field set and order are frozen so the
    ``GET /api/backends`` payload stays byte-identical, and :meth:`split`,
    :meth:`agent_capabilities`, :meth:`transport_capabilities`, and
    :meth:`from_split` bridge to the two axis models. Migrating plugins to
    declare the two halves directly (and a per-axis registry) is left to a
    later phase; this is the thin compat layer.
    """

    is_structured: bool
    supports_resume: bool
    # The plugin's ``restore_session`` knows how to bring an EXITED or
    # ERROR record back to ``STARTING`` — by re-spawning the subprocess
    # (structured plugins) or by relaunching a fresh tmux session with
    # stored args (tmux fallback). Plugins without this capability are
    # rejected from the reattach endpoint with a 400.
    supports_reattach_after_exit: bool = False
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
    # Applying a model/permission-mode/effort change relaunches the session
    # process, so doing it mid-turn interrupts the running turn. claude_tty
    # is the only backend like this today — its TUI has no in-process knob,
    # so every swap kills the pane and respawns ``--resume``. The frontend
    # reads this to warn before changing settings on a running session.
    settings_change_interrupts_turn: bool = False
    supports_thread_discovery: bool = False
    supports_thread_import: bool = False
    supports_fork: bool = False
    supports_plan_approval: bool = False
    supports_slash_compact: bool = False
    supports_approval_note: bool = False
    # The plugin's transport accepts uploaded attachments alongside text
    # input. Every backend that sets this delivers images natively where it
    # can (inline content blocks / local image items / file parts) and falls
    # back to appending the host file path for other files; the universal
    # fallback is plain path-insertion (tmux). Drives whether the frontend
    # composer shows the upload affordance.
    supports_attachments: bool = False
    permission_modes: tuple[PermissionModeSpec, ...] = ()
    effort_levels: tuple[str, ...] = ()
    model_source: ModelSource = ModelSource.NONE
    slash_commands: tuple[SlashCommandSpec, ...] = ()
    approval_decisions: tuple[str, ...] = ("approve", "decline")
    badges: dict[str, str] = Field(default_factory=dict, exclude=True)
    # CLI binary used when this backend is launched in attached-tmux
    # fallback mode. ``None`` means the plugin doesn't ship a CLI
    # entry-point and can't be paired with the tmux transport.
    supports_custom_cli_args: bool = False
    # Plugin exposes a separate ``config_overrides`` input alongside ``cli_args``
    # whose entries are wrapped (e.g. as ``--config K=V`` for codex) rather than
    # passed through as raw flags. Only codex sets this today.
    supports_config_overrides: bool = False
    cli_binary: str | None = None
    # Substrings (case-insensitive) used to infer a backend from a tmux
    # target name when the user attaches to an existing pane without
    # specifying which CLI is running there.
    target_aliases: tuple[str, ...] = ()
    # Marks this plugin as the wrapper used when a structured plugin's
    # adapter isn't ready (or it isn't structured at all). The runtime
    # routes managed-session creation here when the requested plugin
    # opts out via ``is_available_for_managed_launch=False``. Exactly
    # one registered plugin should set this to ``True``; today only
    # the tmux fallback does.
    is_fallback_for_managed_launch: bool = False

    def agent_capabilities(self) -> AgentCapabilities:
        """Project the agent-axis subset (CLI/protocol traits)."""
        return AgentCapabilities(
            model_source=self.model_source,
            permission_modes=self.permission_modes,
            effort_levels=self.effort_levels,
            slash_commands=self.slash_commands,
            approval_decisions=self.approval_decisions,
            supports_thread_discovery=self.supports_thread_discovery,
            supports_thread_import=self.supports_thread_import,
            supports_fork=self.supports_fork,
            supports_plan_approval=self.supports_plan_approval,
            supports_approval_note=self.supports_approval_note,
            supports_attachments=self.supports_attachments,
            supports_custom_cli_args=self.supports_custom_cli_args,
            supports_config_overrides=self.supports_config_overrides,
            supports_slash_compact=self.supports_slash_compact,
            cli_binary=self.cli_binary,
            target_aliases=self.target_aliases,
            badges=self.badges,
        )

    def transport_capabilities(self) -> TransportCapabilities:
        """Project the transport-axis subset (how the agent is driven)."""
        return TransportCapabilities(
            is_structured=self.is_structured,
            supports_resume=self.supports_resume,
            supports_reattach_after_exit=self.supports_reattach_after_exit,
            supports_terminate=self.supports_terminate,
            supports_set_model_inline=self.supports_set_model_inline,
            supports_set_effort_inline=self.supports_set_effort_inline,
            supports_set_effort_with_restart=self.supports_set_effort_with_restart,
            supports_set_permission_mode_inline=self.supports_set_permission_mode_inline,
            settings_change_interrupts_turn=self.settings_change_interrupts_turn,
            is_fallback_for_managed_launch=self.is_fallback_for_managed_launch,
        )

    def split(self) -> tuple[AgentCapabilities, TransportCapabilities]:
        """The (agent, transport) pair this descriptor flattens."""
        return self.agent_capabilities(), self.transport_capabilities()

    @classmethod
    def from_split(
        cls, agent: AgentCapabilities, transport: TransportCapabilities
    ) -> "BackendCapabilities":
        """Recompose a flat descriptor from the two axis models.

        The path a future phase uses once plugins declare the halves
        directly; round-trips with :meth:`split`.
        """
        return cls(**transport.model_dump(), **agent.model_dump())
