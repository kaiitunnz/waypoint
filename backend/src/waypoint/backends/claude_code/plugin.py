"""Claude Code backend plugin.

Owns the per-backend invariants that the runtime previously hard-coded:
permission-mode catalogue, model catalogue, capability flags, transport
adapter wiring, lifecycle (start/restore/import), control surface
(set_model/effort/permission_mode), thread enumeration, and the
system-note formatters. The runtime delegates by id; backend literals
no longer leak into runtime.py.
"""

import asyncio
import logging
import shlex
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from waypoint.backends.base import (
    ConfigDirNotReadyError,
    DefaultLaunchContract,
    config_dir_for,
)
from waypoint.backends.capabilities import (
    BackendCapabilities,
    ModelSource,
)
from waypoint.backends.claude_code import side_question as _sq
from waypoint.backends.claude_code.adapter import ClaudeCliAdapter, ClaudeCliError
from waypoint.backends.claude_code.commands import (
    CLAUDE_BUILTIN_SLASH_COMMANDS,
    list_claude_command_completions,
)
from waypoint.backends.claude_code.history import read_local_claude_history
from waypoint.backends.claude_code.models import (
    CLAUDE_EFFORT_LEVELS,
    DEFAULT_CLAUDE_MODELS,
    claude_default_model_id,
    claude_models_for_version,
)
from waypoint.backends.claude_code.permission_modes import (
    CLAUDE_PERMISSION_MODE_SPECS,
    CLAUDE_PERMISSION_MODES,
    claude_permission_mode_label,
)
from waypoint.backends.claude_code.rate_limits import (
    invalidate_shared_probe_local,
    invalidate_shared_probe_remote,
    probe_claude_usage_remote_shared,
    probe_claude_usage_shared,
)
from waypoint.backends.claude_code.remote import build_remote_claude_launch_factory
from waypoint.backends.claude_code.schemas import (
    ClaudeThreadImportRequest,
    ClaudeThreadSummary,
)
from waypoint.backends.claude_code.support import (
    ClaudeSupportBundle,
    ensure_claude_support_bundle,
)
from waypoint.backends.claude_code.threads import (
    UUID_RE,
    ClaudeThreadInfo,
    claude_onboarding_complete,
    claude_projects_root,
    delete_local_claude_thread,
    find_local_claude_thread,
    list_local_claude_threads,
    local_claude_thread_artifacts,
)
from waypoint.backends.claude_code.threads_remote import RemoteClaudeThreadEnumerator
from waypoint.backends.claude_code.version import detect_claude_cli_version
from waypoint.backends.claude_tty.pane_dialog import (
    composer_is_empty,
    composer_ready,
    shows_blocking_dialog,
)
from waypoint.backends.completions import static_slash_completions
from waypoint.backends.plugin_config import (
    AccountProfileConfig,
    PluginConfig,
    PluginLaunchTargetConfig,
)
from waypoint.backends.tmux.plugin import TmuxPlugin
from waypoint.git_meta import GitMeta
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import (
    BackendModelOption,
    CommandCompletion,
    CompletionDispatch,
    EventKind,
    EventRecord,
    LaunchMode,
    SessionCreateRequest,
    SessionEnvelope,
    SessionInputRequest,
    SessionRateLimitUsage,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.backends.context_usage_source import ContextUsageSource
    from waypoint.runtime import SessionRuntime


log = logging.getLogger("waypoint.backends.claude_code")

_CLAUDE_ORG_PREFIX = "org: "
_CLAUDE_ORG_TIER_PREFIX = "org tier: "


def _find_prefixed(notes: list[str], prefix: str) -> str | None:
    for note in notes:
        if note.startswith(prefix):
            value = note[len(prefix) :].strip()
            if value:
                return value
    return None


class ClaudeCatalogueConfig(Protocol):
    """The config surface :func:`offered_claude_models` reads.

    Satisfied structurally by both ``ClaudeCodePluginConfig`` and
    ``ClaudeTtyPluginConfig`` (the two transports of the same agent), whose
    ``models`` field the base ``PluginConfig`` does not carry.
    """

    models: list[BackendModelOption]
    local_bin: str | None

    @property
    def model_fields_set(self) -> set[str]: ...


def offered_claude_models(
    config: ClaudeCatalogueConfig,
    cli_binary: str,
    launch_target: SshLaunchTargetConfig | None,
) -> tuple[list[BackendModelOption], tuple[int, ...] | None]:
    """The Claude model catalogue to offer for a new session on a target.

    Shared by claude_code and its claude_tty transport -- the offered models
    are an agent concern, identical across transports. A deployment that
    explicitly configured ``models`` (even to a list identical to the built-in
    default) opted out of the version gate; honor it verbatim (version is
    ``None`` then). Otherwise gate the built-in catalogue on the target's
    installed CLI version -- ``None`` when undetectable (missing binary, remote
    target) means "assume latest".

    Synchronous (spawns a subprocess on a cache miss): callers on the event
    loop should run it via ``asyncio.to_thread``.
    """
    if "models" in config.model_fields_set:
        return list(config.models), None
    version = detect_claude_cli_version(
        config.local_bin or cli_binary or "claude", launch_target
    )
    return list(claude_models_for_version(version)), version


def raise_for_unsupported_selection(
    models: list[BackendModelOption],
    version: tuple[int, ...] | None,
    model: str | None,
    effort: str | None,
) -> None:
    """Reject a model/effort combo the detected CLI can't honor.

    ``model`` is looked up in the version-gated catalogue ``list_models`` would
    offer for the target. A model that isn't in it is free text (or an id from
    a newer/unknown CLI) -- nothing to check it against, so it passes through
    unrejected. Only a *recognized* model paired with an effort that catalogue
    entry doesn't list is provably unsupported.
    """
    if effort is None:
        return None
    option = next((opt for opt in models if opt.id == model), None)
    if option is None or effort in option.supported_efforts:
        return None
    version_label = ".".join(str(part) for part in version) if version else "unknown"
    supported = ", ".join(option.supported_efforts) or "none"
    raise ValueError(
        f"model '{model}' does not support effort '{effort}' on the "
        f"detected Claude CLI (v{version_label}); supported efforts: {supported}"
    )


class ClaudeCodePluginConfig(PluginConfig):
    """Claude Code plugin configuration block.

    Owns the curated model catalogue (no live ``model/list`` RPC for
    Claude — the binary's per-model factory map is mirrored statically
    here).
    """

    models: list[BackendModelOption] = Field(
        default_factory=lambda: list(DEFAULT_CLAUDE_MODELS)
    )
    default_model_id: str | None = Field(default_factory=claude_default_model_id)
    # Deprecated no-op: tool approval moved from the PreToolUse HTTP hook to
    # the `can_use_tool` control protocol, which has no network timeout.
    # Retained so existing configs that still set it keep loading.
    hook_timeout_seconds: int = Field(default=3600, ge=1)
    # Named account/config-dir profiles (mapped to CLAUDE_CONFIG_DIR). Only
    # backends that own a config-dir env var carry this field; the base config
    # model rejects it for other backends via extra="forbid".
    account_profiles: dict[str, AccountProfileConfig] = Field(default_factory=dict)


class ClaudeCodeLaunchTargetConfig(PluginLaunchTargetConfig):
    """Per-target overrides for Claude Code on an SSH launch target."""

    # Deprecated no-op; see ``ClaudeCodePluginConfig.hook_timeout_seconds``.
    hook_timeout_seconds: int | None = Field(default=None, ge=1)
    # Target-level profiles merge field-by-field over the global set by id and
    # may introduce target-only ids (see account_profiles.resolve_account_profiles).
    account_profiles: dict[str, AccountProfileConfig] = Field(default_factory=dict)


class ClaudeCodePlugin(DefaultLaunchContract):
    id = "claude_code"
    transport_id = "claude_cli"
    # Defaults to the tty-tail driver (the faithful TUI), with the native
    # stream-json adapter and the generic tmux pane wrapper also available; the
    # wrapper doubles as the managed-launch fallback.
    supported_transports = ("claude_cli", "claude_tty", "tmux")
    default_transport = "claude_tty"
    label = "Claude Code"
    import_request_schema: type[BaseModel] | None = ClaudeThreadImportRequest
    config_schema: type[PluginConfig] = ClaudeCodePluginConfig
    launch_target_schema: type[PluginLaunchTargetConfig] = ClaudeCodeLaunchTargetConfig
    # Force the fullscreen Ink renderer. Claude's startup capability
    # probe (DA1 / XTVERSION / DECRQM 2026) races SSH latency on remote
    # tmux launches and falls back to an inline mode with no alt-screen
    # and no mouse-tracking. The flag is safe locally too: the fullscreen
    # renderer is what claude picks when detection succeeds.
    extra_env = {"CLAUDE_CODE_NO_FLICKER": "1"}
    capabilities = BackendCapabilities(
        is_structured=True,
        supports_resume=False,
        supports_reattach_after_exit=True,
        supports_set_model_inline=True,
        supports_set_effort_inline=False,
        supports_set_effort_with_restart=True,
        supports_set_permission_mode_inline=True,
        supports_thread_discovery=True,
        supports_thread_import=True,
        supports_thread_delete=True,
        supports_fork=True,
        supports_slash_compact=False,
        supports_approval_note=True,
        supports_attachments=True,
        supports_custom_cli_args=True,
        # One-shot approve/decline only. The binary ignores the can_use_tool
        # response's permission_updates in -p mode (verified against v2.1.157:
        # addRules/setMode neither suppress re-prompts nor persist to settings),
        # so "approve for session"/"always allow" would be dead buttons. Re-add
        # once in-session suppression is implemented adapter-side.
        approval_decisions=("approve", "decline"),
        permission_modes=CLAUDE_PERMISSION_MODE_SPECS,
        effort_levels=CLAUDE_EFFORT_LEVELS,
        model_source=ModelSource.STATIC,
        slash_commands=CLAUDE_BUILTIN_SLASH_COMMANDS,
        badges={"glyph": "C", "color": "#a78bfa"},
        cli_binary="claude",
        target_aliases=("claude",),
        config_dir_env_var="CLAUDE_CONFIG_DIR",
        native_thread_store="projects",
        supports_launch_settings_with_restart=True,
    )

    def __init__(self) -> None:
        self.adapter: ClaudeCliAdapter | None = None
        self.support: ClaudeSupportBundle | None = None
        self.thread_enumerator: RemoteClaudeThreadEnumerator | None = None
        self._sq_tasks: dict[str, asyncio.Task[None]] = {}

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        # Imported lazily to avoid the cycle: transport → adapter →
        # permission_modes → backends/claude_code/__init__ → plugin.
        from waypoint.backends.claude_code.transport import ClaudeTransport

        return ClaudeTransport(runtime, self)

    def pane_ready_for_input(self, pane_text: str) -> bool:
        return composer_ready(pane_text)

    def pane_shows_blocking_dialog(self, pane_text: str) -> bool:
        return shows_blocking_dialog(pane_text)

    def confirm_pane_submit(self, pane_text: str, sent_text: str) -> bool:
        # Over the tmux/Terminal transport the Claude TUI can absorb the submit
        # Enter while loading an image pasted by path; the composer clearing is
        # the signal the message was actually sent. The TUI collapses a pasted
        # message to an ``[Image]``/``[Pasted text]`` chip, so the sent text is
        # not literally on screen — emptiness is the only reliable signal.
        # Shared with claude_tty, which wraps the same TUI.
        return composer_is_empty(pane_text)

    def setup(self, runtime: "SessionRuntime") -> None:
        # Build the host-side support bundle, the CLI adapter, and the remote
        # thread enumerator — collectively the "claude side" of the runtime.
        # Resilient to ensure_claude_support_bundle failing (read-only data
        # dir, missing scripts, etc.); we log and leave self.adapter=None so
        # the runtime keeps working without Claude support and the tmux
        # fallback path takes over.
        try:
            support = ensure_claude_support_bundle(runtime.settings.data_dir)
        except Exception:  # noqa: BLE001
            log.exception("claude support bundle setup failed; claude support disabled")
            self.support = None
            self.adapter = None
            self.thread_enumerator = None
            return
        self.support = support
        self.adapter = ClaudeCliAdapter(
            runtime._emit_adapter_event,
            on_init=runtime.handle_completion_source_init,
            on_session_update=runtime.session_update_callback(),
            default_model_id=self._config(runtime).default_model_id,
        )
        self.thread_enumerator = RemoteClaudeThreadEnumerator(
            support.thread_enumerator_path
        )

    async def shutdown(self, runtime: "SessionRuntime") -> None:
        for task in list(self._sq_tasks.values()):
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._sq_tasks.clear()
        if self.adapter is not None:
            await self.adapter.shutdown()
            self.adapter = None
        self.support = None
        self.thread_enumerator = None

    async def start_background_tasks(self, runtime: "SessionRuntime") -> None:
        task = asyncio.create_task(
            _sq.recover_pending_side_questions(runtime, self),
            name="claude-sq-recovery",
        )
        self._sq_tasks["__recovery__"] = task

    async def cleanup_side_questions_on_delete(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        # No stale-snapshot guard: the helper re-reads fresh state under the
        # per-session lock (and returns cheaply when there is nothing to clean),
        # so a /btw persisted after this snapshot is still cleaned up.
        await _sq.delete_session_side_questions(runtime, self, session)

    def _require_adapter(self) -> ClaudeCliAdapter:
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="claude adapter is not initialized",
            )
        return self.adapter

    async def _register_local_rate_limit_probe(
        self, runtime: "SessionRuntime", session_id: str
    ) -> None:
        if self.adapter is None:
            return

        async def _probe() -> SessionRateLimitUsage | None:
            # Probe the account the session actually runs as: its launch_env
            # carries the profile's CLAUDE_CONFIG_DIR, which selects the
            # credentials/account (and the shared probe's cache key). Looked up
            # per probe so it tracks a live settings change.
            session = runtime.storage.get_session(session_id)
            env = (
                runtime.account_lookup_env(self.id, session.launch_env)
                if session is not None
                else None
            )
            return await probe_claude_usage_shared(env=env)

        await self.adapter.register_rate_limit_probe(
            session_id, _probe, refresh_interval_seconds=300.0
        )

    async def _register_remote_rate_limit_probe(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        launch_target: SshLaunchTargetConfig,
    ) -> None:
        if self.adapter is None:
            return

        async def _probe() -> SessionRateLimitUsage | None:
            return await probe_claude_usage_remote_shared(launch_target)

        await self.adapter.register_rate_limit_probe(
            session_id, _probe, refresh_interval_seconds=300.0
        )

    async def _register_rate_limit_probe(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        launch_target: SshLaunchTargetConfig | None,
    ) -> None:
        if launch_target is None:
            await self._register_local_rate_limit_probe(runtime, session_id)
            return
        await self._register_remote_rate_limit_probe(runtime, session_id, launch_target)

    async def refresh_rate_limit_usage(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        launch_target = (
            runtime._find_launch_target(session.launch_target_id)
            if session.launch_target_id
            else None
        )
        await self._register_rate_limit_probe(runtime, session.id, launch_target)
        # User asked for fresh data: drop any cached shared snapshot so the
        # forced probe below makes a real call instead of replaying the window.
        # Best-effort — a concurrent periodic probe for the same account can
        # repopulate the cache between here and the forced refresh, but that
        # snapshot is itself just-fetched, so the served data is still fresh.
        if launch_target is None:
            invalidate_shared_probe_local()
        else:
            invalidate_shared_probe_remote(launch_target)
        # Run the probe inline so the caller's HTTP response carries the
        # post-refresh snapshot — otherwise the response races the WS push
        # from the periodic loop and the UI sees stale data.
        if self.adapter is not None:
            await self.adapter.force_refresh_rate_limit_usage(session.id)

    async def probe_account_rate_limit(
        self,
        runtime: "SessionRuntime",
        launch_target: SshLaunchTargetConfig | None,
        *,
        cwd: str | None = None,
        launch_env: dict[str, str] | None = None,
        force: bool = False,
    ) -> SessionRateLimitUsage | None:
        """Fetch the account's current rate-limit snapshot without a session.

        The upstream probe (HTTP call to api.anthropic.com via cached OAuth
        creds) is account-scoped, not session-scoped — same call the
        per-session adapter probe makes. Exposed so the tmux fallback can
        populate ``rate_limit_usage`` for wrapped-claude sessions without
        wiring them through the structured adapter. ``cwd`` is accepted for
        a uniform probe signature across agents but unused — Claude's probe
        is independent of the working directory. ``force`` bypasses the shared
        TTL cache for a user-driven refresh so the click makes a live call.
        """
        _ = cwd
        if launch_target is None:
            env = (
                runtime.account_lookup_env(self.id, launch_env)
                if launch_env is not None
                else None
            )
            return await probe_claude_usage_shared(env=env, force=force)
        return await probe_claude_usage_remote_shared(launch_target, force=force)

    def rate_limit_account(
        self, snapshot: SessionRateLimitUsage
    ) -> tuple[str, str] | None:
        """Derive the usage-dashboard ``(account_key, account_label)``.

        Claude rate limits are scoped to an org; the snapshot's ``notes``
        carry ``org: <name>`` and ``org tier: <tier>``. Returns ``None``
        when no org note is present so the dashboard falls back to a
        session-scoped bucket.
        """
        org = _find_prefixed(snapshot.notes, _CLAUDE_ORG_PREFIX)
        if org is None:
            return None
        tier = _find_prefixed(snapshot.notes, _CLAUDE_ORG_TIER_PREFIX)
        label = f"{org} · {tier}" if tier else org
        return f"{self.id}:{org}", label

    def is_available_for_managed_launch(self, runtime: "SessionRuntime") -> bool:
        # The Claude adapter is wired up lazily by setup() — if the support
        # bundle failed to materialise we leave self.adapter=None and the
        # runtime falls through to the tmux plugin so the user still gets a
        # session.
        return self.adapter is not None

    def remote_executable(self, launch_target: SshLaunchTargetConfig) -> str:
        return launch_target.remote_bin_for(self.id, self.capabilities.cli_binary) or ""

    async def terminate_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        if self.adapter is not None:
            await self.adapter.terminate_session(session.id)

    def native_thread_id(self, session: SessionRecord) -> str | None:
        thread_id = session.transport_state.get("thread_id")
        return thread_id if isinstance(thread_id, str) else None

    def native_thread_artifacts(
        self, session: SessionRecord, config_dir: str | None = None
    ) -> list[Path]:
        thread_id = self.native_thread_id(session)
        if thread_id is None:
            return []
        return local_claude_thread_artifacts(thread_id, config_dir)

    def ensure_config_dir_ready(self, config_dir: str) -> None:
        # Setting CLAUDE_CONFIG_DIR moves .claude.json into the profile dir; if
        # that copy hasn't completed onboarding the CLI relaunches into its
        # first-run wizard, which a tmux/tty-driven turn can't dismiss (it hangs)
        # — reject up front instead. See ConfigDirValidating.
        if not claude_onboarding_complete(config_dir):
            raise ConfigDirNotReadyError(
                "claude onboarding is incomplete in its config dir; open a "
                "claude session there once to finish setup"
            )

    def on_session_deleted(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        # Claude caches per-launch-target thread listings remotely;
        # invalidate so a re-import after delete sees the freed slot.
        if session.launch_target_id and self.thread_enumerator is not None:
            self.thread_enumerator.invalidate(session.launch_target_id)
        # Drop any todo tracker stashed for a respawn that will never come.
        if self.adapter is not None:
            self.adapter.discard_session(session.id)

    def create_context_usage_source(
        self, session: SessionRecord, runtime: "SessionRuntime"
    ) -> "ContextUsageSource | None":
        if session.transport != "tmux":
            return None
        thread_id = session.transport_state.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            return None
        from waypoint.backends.claude_code.usage_source import (
            TranscriptContextUsageSource,
        )

        return TranscriptContextUsageSource(
            session_id=session.id,
            session_uuid=thread_id,
            cwd=session.cwd,
            runtime=runtime,
            config_dir=(
                session.launch_env.get(self.capabilities.config_dir_env_var)
                if self.capabilities.config_dir_env_var
                else None
            ),
        )

    def register_routes(self, app: FastAPI, context: Any) -> None:
        # Tool approval now rides the `can_use_tool` control protocol over the
        # CLI's stdio stream (see adapter._handle_can_use_tool), so the backend
        # no longer mounts a PreToolUse approval webhook.
        return

    def validate_permission_mode(self, mode: str | None) -> str | None:
        if mode is None or mode == "":
            return None
        if mode not in CLAUDE_PERMISSION_MODES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"unsupported {self.id} permission mode: {mode}; "
                    f"expected one of {', '.join(CLAUDE_PERMISSION_MODES)}"
                ),
            )
        return mode

    def _config(self, runtime: "SessionRuntime") -> ClaudeCodePluginConfig:
        config = runtime.settings.plugin_config(self.id)
        assert isinstance(config, ClaudeCodePluginConfig)
        return config

    def _effective_args(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None,
        custom_args: list[str],
    ) -> list[str]:
        if launch_target_id:
            launch_target = runtime._find_launch_target(launch_target_id)
            if launch_target:
                target_config = launch_target.plugin_config(self.id)
                if target_config:
                    return target_config.cli_args + custom_args
            return list(custom_args)
        return self._config(runtime).cli_args + custom_args

    def static_model_options(self, runtime: "SessionRuntime") -> list[Any]:
        # Plugin config carries the (configurable) Claude model catalogue.
        # Deployments patch the list via ``plugin_configs.claude_code.models``
        # in waypoint.yaml without forking this module.
        return list(self._config(runtime).models)

    @property
    def permission_mode_ids(self) -> tuple[str, ...]:
        return CLAUDE_PERMISSION_MODES

    async def apply_permission_mode(
        self, runtime: "SessionRuntime", session: SessionRecord, mode: str
    ) -> None:
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude adapter is not configured on this backend",
            )
        before_pending = self.adapter.pending_approval_ids(session.id)
        try:
            await self.adapter.set_permission_mode(session.id, mode)
        except Exception as exc:  # noqa: BLE001 — surface adapter errors as 400
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        after_pending = set(self.adapter.pending_approval_ids(session.id))
        cleared_pending = [
            approval_id
            for approval_id in before_pending
            if approval_id not in after_pending
        ]
        if not cleared_pending:
            return
        next_status = (
            SessionStatus.WAITING_INPUT if after_pending else SessionStatus.RUNNING
        )
        runtime.storage.update_session(session.id, status=next_status)
        mode_label = claude_permission_mode_label(mode)
        for approval_id in cleared_pending:
            await runtime._record_system_event(
                session.id,
                f"Pending approval cleared by permission mode change to {mode_label}",
                status=next_status,
                metadata={
                    "method": "approval.invalidated",
                    "approval_id": approval_id,
                    "permission_mode": mode,
                },
            )

    async def apply_model(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        model: str | None,
    ) -> None:
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude adapter is not configured on this backend",
            )
        try:
            await self.adapter.set_model(session.id, model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    async def apply_effort(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        effort: str | None,
    ) -> bool:
        """Returns True when the runtime should also publish a system
        note describing the effort swap; False signals "nothing changed".
        """
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude adapter is not configured on this backend",
            )
        # Claude has no in-process effort knob — set_effort terminates the
        # CLI and respawns it with `--resume <id> --effort <new>`. Skip
        # the swap when the value is unchanged so we don't restart for
        # nothing.
        if effort == (session.effort or None):
            return False
        try:
            await self.adapter.set_effort(session.id, effort)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        return True

    def _offered_models_with_version(
        self, runtime: "SessionRuntime", launch_target_id: str | None
    ) -> tuple[list[BackendModelOption], tuple[int, ...] | None]:
        launch_target = (
            runtime._find_launch_target(launch_target_id) if launch_target_id else None
        )
        return offered_claude_models(
            self._config(runtime),
            self.capabilities.cli_binary or "claude",
            launch_target,
        )

    def validate_new_session_selection(
        self,
        runtime: "SessionRuntime",
        model: str | None,
        effort: str | None,
        launch_target_id: str | None,
    ) -> None:
        if effort is None:
            return None
        options, version = self._offered_models_with_version(runtime, launch_target_id)
        raise_for_unsupported_selection(options, version, model, effort)

    async def list_models(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        config = self._config(runtime)
        default_model = config.default_model_id
        default_effort = config.default_effort
        models, _version = await asyncio.to_thread(
            self._offered_models_with_version, runtime, launch_target_id
        )
        options = [opt.model_dump(mode="json") for opt in models]
        default_model_id: str | None = None
        if default_model is None:
            for opt in models:
                if opt.is_default:
                    default_model_id = opt.id
                    break
        else:
            default_model_id = default_model
        default_model_label: str | None = None
        if default_model_id:
            for opt in models:
                if opt.id == default_model_id:
                    default_model_label = opt.label
                    break
        return {
            "backend": self.id,
            "models": options,
            "default_model_id": default_model_id,
            "default_model_label": default_model_label,
            "default_effort": default_effort,
            "supports_free_text": True,
        }

    async def list_command_completions(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        *,
        trigger: str = "/",
        prefix: str = "",
        force_refresh: bool = False,
    ) -> list[CommandCompletion]:
        if trigger != "/":
            return []
        runtime_commands = _session_slash_commands(
            self.adapter, session.id
        ) or _session_transport_slash_commands(session)
        waypoint_completions = _claude_waypoint_completions(prefix)
        # /status: drop our version when Claude has it natively; /btw is always ours
        if _commands_include_name(runtime_commands, "status"):
            waypoint_completions = [
                c for c in waypoint_completions if c.name != "status"
            ]
        completions = waypoint_completions
        completions.extend(_claude_runtime_slash_completions(runtime_commands, prefix))
        # Curated built-ins as a baseline for sessions with no live slash-command
        # stream (tmux transport), deduped against anything the runtime reported.
        present = {f"{item.trigger}{item.name}" for item in completions}
        for builtin in static_slash_completions(
            self.id, self.capabilities, prefix=prefix
        ):
            key = f"{builtin.trigger}{builtin.name}"
            if key not in present:
                completions.append(builtin)
                present.add(key)
        launch_target = (
            runtime._find_launch_target(session.launch_target_id)
            if session.launch_target_id
            else None
        )
        claude_bin = (
            self.remote_executable(launch_target)
            if launch_target is not None
            else self._config(runtime).local_bin or self.capabilities.cli_binary
        )
        if not claude_bin:
            return completions
        try:
            dynamic = await list_claude_command_completions(
                cwd=session.cwd,
                claude_bin=claude_bin,
                prefix=prefix,
                launch_target=launch_target,
                config_dir=config_dir_for(self.capabilities, session.launch_env),
            )
        except Exception:
            return completions
        seen = {f"{item.trigger}{item.name}" for item in completions}
        for item in dynamic:
            key = f"{item.trigger}{item.name}"
            if key in seen:
                _merge_completion_metadata(completions, key, item)
                continue
            completions.append(item)
            seen.add(key)
        return completions

    def effort_swap_message(self, effort: str | None) -> str:
        return _claude_effort_swap_message(effort)

    async def maybe_handle_input(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        request: SessionInputRequest,
    ) -> SessionRecord | None:
        command = _first_slash_command(request.text)
        if command == "/btw":
            question = request.text.strip()[len("/btw") :].strip()
            if question:
                await _sq.start_side_question(runtime, self, session, question)
            return runtime.get_session(session.id)
        runtime_commands = _session_slash_commands(
            self.adapter, session.id
        ) or _session_transport_slash_commands(session)
        if command == "/status" and not _commands_include_name(
            runtime_commands, "status"
        ):
            await runtime._record_user_event(
                session.id,
                request.text,
                submit=request.submit,
                status=session.status,
            )
            await runtime._record_system_event(
                session.id,
                _format_claude_status(session, self.adapter),
                status=session.status,
                metadata={"builtin_command": "/status", "source": "waypoint"},
            )
            return runtime.get_session(session.id)
        if command == "/copy":
            # Claude's native /copy emits OSC 52 from the interactive TUI;
            # the SDK's --print --output-format=stream-json mode never
            # surfaces that escape, so the slash command is a silent no-op
            # for structured sessions unless we intercept here. Tmux-
            # wrapped sessions take a different maybe_handle_input path
            # (tmux/plugin.py) where the CLI's OSC 52 reaches xterm
            # directly, so this branch is structured-only by construction.
            text = _last_assistant_text(runtime, session.id)
            await runtime._record_user_event(
                session.id,
                request.text,
                submit=request.submit,
                status=session.status,
            )
            if text:
                await runtime.broadcast.publish(
                    SessionEnvelope(
                        type="clipboard_copy",
                        payload={"text": text},
                    ),
                    session_id=session.id,
                )
                note = (
                    f"Copied last response to clipboard "
                    f"({len(text)} chars, {text.count(chr(10)) + 1} lines)"
                )
            else:
                note = "Nothing to copy — no assistant response yet."
            await runtime._record_system_event(
                session.id,
                note,
                status=session.status,
                metadata={"builtin_command": "/copy", "source": "waypoint"},
            )
            return runtime.get_session(session.id)
        return None

    async def fork_side_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        side_question_id: str,
        *,
        new_session_id: str,
        title: str,
        raw_log: Path,
        structured_log: Path,
    ) -> SessionRecord:
        adapter = self._require_adapter()

        async def _bring_up(new_session: SessionRecord, fork_thread_id: str) -> None:
            process_env = runtime._agent_process_env(
                self.id, session.launch_env, session_id=new_session.id
            )
            await adapter.restore_session(
                new_session.id,
                session.cwd,
                fork_thread_id,
                self.launch_factory(runtime, session.launch_target_id),
                permission_mode=session.permission_mode,
                model=session.model,
                effort=session.effort,
                launch_env=process_env,
            )

        return await _sq.fork_aside(
            runtime,
            session,
            side_question_id,
            new_session_id=new_session_id,
            transport_id=self.transport_id,
            title=title,
            raw_log=raw_log,
            structured_log=structured_log,
            bring_up=_bring_up,
        )

    async def dismiss_side_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        side_question_id: str,
    ) -> None:
        await _sq.dismiss_aside(runtime, self, session, side_question_id)

    async def answer_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        answer: str,
        tool_use_id: str | None,
        answers: list[dict[str, Any]] | None,
    ) -> SessionRecord:
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="answer-question is only supported for Claude sessions",
            )
        try:
            handled = await self.adapter.respond_to_ask_question(
                session.id, answer, tool_use_id
            )
        except ClaudeCliError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        if not handled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="no pending question for this session",
            )
        # Stash structured per-question answers + notes so the frontend
        # renders this user_input as a styled "answers" card instead of
        # the raw `"<question>"="<answer>" user notes: …` payload Claude
        # was tuned around.
        extra: dict[str, Any] = {"kind": "ask_user_question_answer"}
        if answers:
            extra["answers"] = answers
        if tool_use_id:
            extra["tool_use_id"] = tool_use_id
        # Same ordering as handle_input: flip status to RUNNING before
        # _record_user_event broadcasts the session_state snapshot,
        # otherwise the spinner stays off until Claude's next chunk.
        updated = runtime.storage.update_session(
            session.id, status=SessionStatus.RUNNING
        )
        await runtime._record_user_event(
            session.id, answer, submit=True, extra_metadata=extra
        )
        return updated

    async def approve_plan(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        plan_item_id: str,
        decision: str,
        text: str | None,
    ) -> SessionRecord:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"plan approval is not supported for {self.id}",
        )

    async def post_approval(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        # Side-effect of an ExitPlanMode approval: the Claude adapter
        # has already flipped the binary's permission mode to default
        # via set_permission_mode. Sync storage + broadcast so the UI
        # pill reflects the change instead of staying stuck on "plan".
        if self.adapter is None:
            return
        current = self.adapter.session_permission_mode(session.id)
        if current is None:
            return
        previous = session.permission_mode or "default"
        if current == previous:
            return
        runtime.storage.update_session(session.id, permission_mode=current)
        await runtime.broadcast.publish(
            SessionEnvelope(
                type="session_list_update",
                payload={
                    "sessions": [
                        item.model_dump(mode="json") for item in runtime.list_sessions()
                    ]
                },
            )
        )

    async def fork_session(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        new_session_id: str,
        title: str,
        raw_log: Path,
        structured_log: Path,
    ) -> SessionRecord:
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="claude adapter is not initialized",
            )
        launch_target = (
            runtime._find_launch_target(session.launch_target_id)
            if session.launch_target_id
            else None
        )
        thread_id = session.transport_state.get("thread_id")
        if not thread_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude session has no thread id to fork from",
            )
        if (
            session.launch_target_id
            and runtime._find_launch_target(session.launch_target_id) is None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"claude session launch target {session.launch_target_id} is no longer configured",
            )

        new_claude_session_id = self.generate_session_id()
        try:
            process_env = runtime._agent_process_env(
                self.id, session.launch_env, session_id=new_session_id
            )
            await self.adapter.start_session(
                new_session_id,
                session.cwd,
                new_claude_session_id,
                self.launch_factory(runtime, session.launch_target_id),
                permission_mode=session.permission_mode,
                model=session.model,
                effort=session.effort,
                custom_args=self._effective_args(
                    runtime, session.launch_target_id, session.args
                ),
                fork_from_claude_session_id=thread_id,
                launch_env=process_env,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "claude fork failed",
                extra={
                    "session_id": session.id,
                    "claude_session_id": thread_id,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        now = datetime.now(UTC)
        raw_log.touch(exist_ok=True)
        new_session = SessionRecord(
            id=new_session_id,
            backend=self.id,
            source=SessionSource.MANAGED,
            transport=self.transport_id,
            title=title,
            cwd=session.cwd,
            launch_target_id=session.launch_target_id,
            repo_name=session.repo_name,
            branch=session.branch,
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"thread_id": new_claude_session_id},
            permission_mode=session.permission_mode,
            model=session.model,
            effort=session.effort,
            args=session.args,
            config_overrides=session.config_overrides,
            launch_env=session.launch_env,
            account_profile_id=session.account_profile_id,
            account_profile_label=session.account_profile_label,
        )
        runtime.storage.create_session(new_session)
        runtime.storage.clone_events(session.id, new_session_id)
        await self._register_rate_limit_probe(runtime, new_session_id, launch_target)
        await runtime._record_system_event(
            new_session_id,
            self.format_restore_message(runtime, session.cwd, session.launch_target_id)
            + f" (forked from {session.title or session.id})",
            status=SessionStatus.IDLE,
        )
        return runtime.get_session(new_session_id)

    async def restore_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        if not _session_transport_slash_commands(session):
            slash_commands = _latest_stored_slash_commands(runtime, session.id)
            if slash_commands:
                state = dict(session.transport_state)
                state["slash_commands"] = list(slash_commands)
                session = runtime.storage.update_session(
                    session.id, transport_state=state
                )
        if self.adapter is None:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                "Claude adapter unavailable; cannot restore",
                status=SessionStatus.ERROR,
            )
            return
        thread_id = session.transport_state.get("thread_id")
        if not thread_id:
            runtime.storage.update_session(session.id, status=SessionStatus.EXITED)
            await runtime._record_system_event(
                session.id,
                "Claude session has no claude_session_id; marking exited",
                status=SessionStatus.EXITED,
            )
            return
        if (
            session.launch_target_id
            and runtime._find_launch_target(session.launch_target_id) is None
        ):
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"Claude session launch target {session.launch_target_id} is no longer configured",
                status=SessionStatus.ERROR,
            )
            return
        try:
            process_env = runtime._agent_process_env(
                self.id, session.launch_env, session_id=session.id
            )
            await self.adapter.restore_session(
                session.id,
                session.cwd,
                thread_id,
                self.launch_factory(runtime, session.launch_target_id),
                permission_mode=session.permission_mode,
                model=session.model,
                effort=session.effort,
                custom_args=self._effective_args(
                    runtime, session.launch_target_id, session.args
                ),
                launch_env=process_env,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "claude restore failed",
                extra={
                    "session_id": session.id,
                    "claude_session_id": thread_id,
                },
            )
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"Claude session restore failed: {exc}",
                status=SessionStatus.ERROR,
            )
            return
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await self._register_rate_limit_probe(
            runtime,
            session.id,
            runtime._find_launch_target(session.launch_target_id),
        )
        await runtime._record_system_event(
            session.id,
            self.format_restore_message(runtime, session.cwd, session.launch_target_id),
            status=SessionStatus.IDLE,
        )

    def format_start_message(
        self,
        claude_session_id: str,
        cwd: str | None,
        launch_target: SshLaunchTargetConfig | None,
    ) -> str:
        if launch_target is not None:
            return (
                f"Claude session started via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd or launch_target.default_cwd}) ({claude_session_id})"
            )
        return f"Claude session started ({claude_session_id})"

    def format_restore_message(
        self,
        runtime: "SessionRuntime",
        cwd: str | None,
        launch_target_id: str | None,
    ) -> str:
        launch_target = runtime._find_launch_target(launch_target_id)
        if launch_target is not None:
            return (
                f"Claude session restored via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd or launch_target.default_cwd})"
            )
        return "Claude session restored from previous backend process"

    def format_import_message(
        self,
        cwd: str,
        launch_target: SshLaunchTargetConfig | None,
    ) -> str:
        if launch_target is not None:
            return (
                f"Imported stored Claude thread via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd})"
            )
        return f"Imported stored Claude thread ({cwd})"

    # --- agent launch contract ---------------------------------------
    #
    # Pane-wrapper launch knowledge a generic transport drives without
    # knowing it's Claude. ``capture_thread_id`` stays the inert
    # ``DefaultLaunchContract`` no-op: Claude pregenerates its id via
    # ``--session-id``, so there's nothing to discover post-launch.

    def launch_flags(
        self,
        *,
        model: str | None = None,
        effort: str | None = None,
        permission_mode: str | None = None,
    ) -> list[str]:
        flags: list[str] = []
        if model:
            flags += ["--model", model]
        if effort:
            flags += ["--effort", effort]
        if permission_mode:
            flags += ["--permission-mode", permission_mode]
        return flags

    def pregenerate_thread_id(self) -> str | None:
        return str(uuid.uuid4())

    def resume_args(self, thread_id: str, prior_args: list[str]) -> list[str]:
        # prior_args may carry ``--session-id <uuid>`` (the initial create
        # form) or ``--resume <uuid>`` (a prior reconnect's output). Strip
        # both so the new prefix doesn't compound on repeated reconnects.
        scrubbed: list[str] = []
        skip = 0
        for arg in prior_args:
            if skip:
                skip -= 1
                continue
            if arg in ("--session-id", "--resume"):
                skip = 1
                continue
            scrubbed.append(arg)
        return ["--resume", thread_id, *scrubbed]

    async def conversation_exists(
        self,
        thread_id: str,
        cwd: str,
        launch_target: SshLaunchTargetConfig | None,
        config_dir: str | None = None,
    ) -> bool:
        # <config-dir>/projects/<dashed-absolute-cwd>/<uuid>.jsonl — but the
        # dashed key uses claude's view of the absolute cwd, which may not
        # match what we have on hand (SSH sessions carry the raw ``~/foo``
        # form; ``cd`` symlinks resolve on the remote). UUIDs are globally
        # unique under projects/, so glob across all project dirs and pick
        # the file by name. ``config_dir`` (the session's CLAUDE_CONFIG_DIR,
        # e.g. a switched account profile's) overrides the default ~/.claude.
        needle = f"{thread_id}.jsonl"
        if launch_target is None:
            projects = claude_projects_root(config_dir)
            if not projects.is_dir():
                return False
            return any(projects.glob(f"*/{needle}"))
        # A remote CLAUDE_CONFIG_DIR (unusual, but honor it) roots the search;
        # otherwise the shell expands the default ``$HOME/.claude``. ``$HOME``
        # / ``$CLAUDE_CONFIG_DIR`` stay outside the quoted needle so the remote
        # shell expands them; quoting the whole path would look for a literal.
        root = shlex.quote(config_dir) if config_dir else "$HOME/.claude"
        stdout = await launch_target.ssh_capture(
            f"ls {root}/projects/*/{shlex.quote(needle)} 2>/dev/null | head -n 1",
        )
        return bool(stdout.strip())

    # --- launch / discovery helpers ----------------------------------

    def launch_factory(self, runtime: "SessionRuntime", launch_target_id: str | None):
        launch_target = runtime._find_launch_target(launch_target_id)
        if launch_target is None or self.adapter is None:
            return None
        return build_remote_claude_launch_factory(launch_target)

    def generate_session_id(self) -> str:
        return str(uuid.uuid4())

    def _thread_summary(self, info: ClaudeThreadInfo) -> ClaudeThreadSummary:
        return ClaudeThreadSummary(
            id=info.id,
            title=info.title,
            cwd=info.cwd,
            repo_name=info.repo_name,
            branch=info.branch,
            preview=info.preview,
            created_at=info.created_at,
            updated_at=info.updated_at,
        )

    def _find_imported_session(
        self,
        runtime: "SessionRuntime",
        thread_id: str,
        launch_target_id: str | None,
    ) -> SessionRecord | None:
        for session in runtime.storage.list_sessions():
            if session.backend != self.id:
                continue
            if session.transport_state.get("thread_id") != thread_id:
                continue
            if session.launch_target_id != launch_target_id:
                continue
            return session
        return None

    async def list_threads(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
    ) -> list[ClaudeThreadSummary]:
        if self.adapter is None:
            return []
        imported: set[tuple[str | None, str]] = set()
        for session in runtime.storage.list_sessions():
            if session.backend != self.id:
                continue
            thread_id = session.transport_state.get("thread_id")
            if not thread_id:
                continue
            imported.add((session.launch_target_id, thread_id))
        if launch_target_id is None:
            infos = await asyncio.to_thread(list_local_claude_threads)
        else:
            target = runtime._resolve_launch_target(launch_target_id, self.id)
            if target is None or self.thread_enumerator is None:
                return []
            infos = await self.thread_enumerator.list(target)
        return [
            self._thread_summary(info)
            for info in infos
            if (launch_target_id, info.id) not in imported
        ]

    async def delete_thread(
        self,
        runtime: "SessionRuntime",
        thread_id: str,
        launch_target_id: str | None = None,
    ) -> bool:
        if launch_target_id is None:
            return await asyncio.to_thread(delete_local_claude_thread, thread_id)
        target = runtime._resolve_launch_target(launch_target_id, self.id)
        if target is None:
            return False
        if not UUID_RE.match(thread_id):
            return False
        stdout = await target.ssh_capture(
            'f=$(ls "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/"*/'
            f"{thread_id}.jsonl 2>/dev/null | head -n1); "
            '[ -n "$f" ] && rm -f "$f" && echo deleted'
        )
        if "deleted" not in stdout:
            return False
        if self.thread_enumerator is not None:
            self.thread_enumerator.invalidate(launch_target_id)
        return True

    async def create_session(
        self,
        runtime: "SessionRuntime",
        request: SessionCreateRequest,
        *,
        session_id: str,
        launch_target: SshLaunchTargetConfig | None,
        title: str,
        raw_log: Path,
        structured_log: Path,
        git_meta: GitMeta,
        permission_mode: str | None,
        resolved_model: str | None,
        resolved_effort: str | None,
    ) -> SessionRecord:
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude adapter is not configured on this backend",
            )
        raw_log.touch(exist_ok=True)
        claude_session_id = self.generate_session_id()
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=request.backend,
            source=SessionSource.MANAGED,
            transport=self.transport_id,
            title=title,
            cwd=request.cwd,
            launch_target_id=launch_target.id if launch_target else None,
            launch_mode=request.launch_mode,
            repo_name=git_meta.repo_name,
            branch=git_meta.branch,
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"thread_id": claude_session_id},
            spawner_session_id=request.spawner_session_id,
            permission_mode=permission_mode,
            model=resolved_model,
            effort=resolved_effort,
            args=request.args,
            launch_env=request.launch_env,
        )
        runtime.storage.create_session(session)
        try:
            process_env = runtime._agent_process_env(
                self.id, session.launch_env, session_id=session.id
            )
            await self.adapter.start_session(
                session_id,
                request.cwd,
                claude_session_id,
                self.launch_factory(runtime, session.launch_target_id),
                permission_mode=session.permission_mode,
                model=session.model,
                effort=session.effort,
                custom_args=self._effective_args(
                    runtime, session.launch_target_id, session.args
                ),
                launch_env=process_env,
            )
        except (ClaudeCliError, FileNotFoundError, OSError) as exc:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await self._register_rate_limit_probe(runtime, session.id, launch_target)
        await runtime._record_system_event(
            session.id,
            self.format_start_message(claude_session_id, request.cwd, launch_target),
            status=SessionStatus.IDLE,
        )
        return runtime.get_session(session.id)

    async def import_thread(
        self,
        runtime: "SessionRuntime",
        request: ClaudeThreadImportRequest,
        *,
        agent: str | None = None,
    ) -> SessionRecord:
        backend = agent or self.id
        launch_target = runtime._resolve_launch_target(
            request.launch_target_id, self.id
        )
        existing = self._find_imported_session(
            runtime, request.thread_id, request.launch_target_id
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude thread already imported",
            )
        # Resolve thread metadata first; cwd is needed for both the
        # direct (Claude SDK) and tmux_wrapper paths.
        if launch_target is None:
            info = await asyncio.to_thread(find_local_claude_thread, request.thread_id)
        else:
            if self.thread_enumerator is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="claude thread enumerator is not initialized",
                )
            info = await self.thread_enumerator.find(launch_target, request.thread_id)
        if info is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="claude thread not found",
            )
        if launch_target is None:
            cwd_path = Path(info.cwd).expanduser()
            if not cwd_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"claude thread cwd {info.cwd} no longer exists; cannot resume"
                    ),
                )
            cwd = str(cwd_path)
        else:
            # Remote cwd lives on the SSH host; we can't stat it from here.
            cwd = info.cwd
        # A pinned transport supersedes launch_mode (mirrors create_session):
        # the agent's native transport takes the structured path below, any
        # other resolves to the tmux wrapper here (the tty-tail driver is
        # dispatched by the runtime before reaching this plugin). With no
        # pinned transport, launch_mode decides: TMUX_WRAPPER always delegates;
        # AUTO falls through when the structured plugin isn't available.
        if request.transport is not None:
            use_resume_wrapper = request.transport != self.transport_id
        else:
            use_resume_wrapper = request.launch_mode == LaunchMode.TMUX_WRAPPER or (
                request.launch_mode == LaunchMode.AUTO
                and not self.is_available_for_managed_launch(runtime)
            )
        if use_resume_wrapper:
            fallback = runtime.registry.fallback_for_managed_launch()
            if not isinstance(fallback, TmuxPlugin):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="tmux fallback launch is not available",
                )
            return await fallback.import_thread_via_resume(
                runtime,
                backend=backend,
                thread_id=request.thread_id,
                cwd=cwd,
                launch_target_id=request.launch_target_id,
                title=info.title,
                launch_env=request.launch_env,
            )
        # Direct (structured-SDK) path requires the adapter.
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="claude adapter is not initialized",
            )
        session_id = runtime._generate_session_id(self.id)
        session_dir = runtime._session_dir(session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"
        raw_log.touch(exist_ok=True)
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=backend,
            source=SessionSource.MANAGED,
            transport=self.transport_id,
            title=info.title,
            cwd=cwd,
            launch_target_id=launch_target.id if launch_target else None,
            repo_name=info.repo_name,
            branch=info.branch,
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"thread_id": info.id},
            permission_mode="default",
            launch_env=request.launch_env,
        )
        runtime.storage.create_session(session)
        try:
            process_env = runtime._agent_process_env(
                self.id, session.launch_env, session_id=session.id
            )
            await self.adapter.restore_session(
                session.id,
                cwd,
                info.id,
                self.launch_factory(runtime, session.launch_target_id),
                permission_mode=session.permission_mode,
                model=session.model,
                effort=session.effort,
                launch_env=process_env,
            )
        except (ClaudeCliError, FileNotFoundError, OSError) as exc:
            log.exception(
                "claude import failed",
                extra={
                    "session_id": session.id,
                    "claude_session_id": info.id,
                    "launch_target_id": session.launch_target_id,
                },
            )
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"Claude thread import failed: {exc}",
                status=SessionStatus.ERROR,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to import claude thread: {exc}",
            ) from exc
        if launch_target is not None and self.thread_enumerator is not None:
            self.thread_enumerator.invalidate(launch_target.id)
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await self._register_rate_limit_probe(runtime, session.id, launch_target)

        async def _read_thread_history() -> list[EventRecord]:
            if launch_target is not None:
                # Remote transcripts aren't fetched today: RemoteClaudeThread-
                # Enumerator only surfaces thread-list metadata (via
                # claude_thread_enumerator.sh), not the full JSONL transcript,
                # and there's no existing remote-file-read primitive cheap
                # enough to reuse here. Degrade to a plain resume — an empty
                # read is a no-op for seed_thread_history, not a failure.
                return []
            return await read_local_claude_history(session.id, info.id)

        await runtime.seed_thread_history(
            session.id,
            reader=_read_thread_history,
            enabled=request.import_history,
        )
        await runtime._record_system_event(
            session.id,
            self.format_import_message(cwd, launch_target),
            status=SessionStatus.IDLE,
            metadata={"imported_thread_id": info.id},
        )
        return runtime.get_session(session.id)


def _claude_effort_swap_message(effort: str | None) -> str:
    if effort:
        return f"Restarted Claude session with --effort {effort}"
    return "Restarted Claude session with default effort"


def _claude_waypoint_completions(prefix: str) -> list[CommandCompletion]:
    specs = [
        (
            "/status",
            "status",
            "Show Waypoint session status",
            "claude_code:waypoint:status",
        ),
        (
            "/btw",
            "btw",
            "Ask a side-question without interrupting the session",
            "claude_code:waypoint:btw",
        ),
    ]
    return [
        CommandCompletion(
            id=cid,
            trigger="/",
            replacement=f"{command} ",
            name=name,
            description=description,
            kind="command",
            source="waypoint",
            dispatch=CompletionDispatch.PLAIN_TEXT,
            metadata={"builtin_command": command},
        )
        for command, name, description, cid in specs
        if _slash_prefix_matches(command, prefix)
    ]


def _claude_runtime_slash_completions(
    commands: tuple[str, ...], prefix: str
) -> list[CommandCompletion]:
    completions: list[CommandCompletion] = []
    seen: set[str] = set()
    for raw in commands:
        name = _slash_command_name(raw)
        if not name:
            continue
        command = f"/{name}"
        if command in seen or not _slash_prefix_matches(command, prefix):
            continue
        is_plugin_command = ":" in raw
        completions.append(
            CommandCompletion(
                id=f"claude_code:runtime:{raw}",
                trigger="/",
                replacement=f"{command} ",
                name=name,
                description=None,
                kind="skill" if is_plugin_command else "command",
                source="plugin_skill" if is_plugin_command else "claude_builtin",
                dispatch=CompletionDispatch.PLAIN_TEXT,
                metadata={"runtime_command": raw},
            )
        )
        seen.add(command)
    return completions


def _merge_completion_metadata(
    completions: list[CommandCompletion], key: str, incoming: CommandCompletion
) -> None:
    for existing in completions:
        if f"{existing.trigger}{existing.name}" != key:
            continue
        if not existing.description and incoming.description:
            existing.description = incoming.description
        if incoming.metadata:
            existing.metadata = {**existing.metadata, **incoming.metadata}
        if existing.source == "plugin_skill" and incoming.source == "plugin_skill":
            existing.kind = incoming.kind
        return


def _slash_prefix_matches(command: str, prefix: str) -> bool:
    if not prefix:
        return True
    normalized = prefix if prefix.startswith("/") else f"/{prefix}"
    return normalized == "/" or command.startswith(normalized)


def _slash_command_name(raw: str) -> str:
    candidate = raw.rsplit(":", 1)[-1].strip()
    if candidate.startswith("/"):
        candidate = candidate[1:]
    return candidate


def _first_slash_command(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    return stripped.split(maxsplit=1)[0].lower()


def _last_assistant_text(runtime: "SessionRuntime", session_id: str) -> str:
    """Concatenate the assistant's most recent bubble into a single string.

    ``list_events_by_message_count(message_limit=1)`` walks events
    backward and returns events for exactly one logical anchor, so a
    long-running transcript doesn't get fully loaded just to read the
    last bubble. Streaming chunks within the bubble share ``item_id`` →
    same anchor → all chunks are in the slice and join in order.
    Returns empty when the latest anchor isn't an assistant message.
    """
    events = runtime.storage.list_events_by_message_count(session_id, message_limit=1)
    return "".join(
        event.text
        for event in events
        if event.kind == EventKind.AGENT_OUTPUT and event.text
    )


def _session_slash_commands(
    adapter: ClaudeCliAdapter | None, session_id: str
) -> tuple[str, ...]:
    if adapter is None:
        return ()
    getter = getattr(adapter, "session_slash_commands", None)
    if not callable(getter):
        return ()
    commands = getter(session_id)
    return commands if isinstance(commands, tuple) else tuple(commands or ())


def _session_transport_slash_commands(session: SessionRecord) -> tuple[str, ...]:
    commands = session.transport_state.get("slash_commands")
    if not isinstance(commands, list):
        return ()
    return tuple(command for command in commands if isinstance(command, str))


def _latest_stored_slash_commands(
    runtime: "SessionRuntime", session_id: str
) -> tuple[str, ...]:
    for event in reversed(runtime.storage.list_events(session_id)):
        if event.metadata.get("method") != "system.init":
            continue
        payload = event.metadata.get("payload")
        if not isinstance(payload, dict):
            continue
        commands = payload.get("slash_commands")
        if isinstance(commands, list):
            return tuple(command for command in commands if isinstance(command, str))
    return ()


def _commands_include_name(commands: tuple[str, ...], name: str) -> bool:
    return any(_slash_command_name(command) == name for command in commands)


def _format_claude_status(
    session: SessionRecord, adapter: ClaudeCliAdapter | None
) -> str:
    lines = [
        "Claude Code session status",
        f"- Status: {session.status.value}",
        f"- Backend: {session.backend}",
        f"- Transport: {session.transport}",
        f"- CWD: {session.cwd}",
    ]
    if session.launch_target_id:
        lines.append(f"- Launch target: {session.launch_target_id}")
    if session.repo_name:
        repo = session.repo_name
        if session.branch:
            repo = f"{repo} ({session.branch})"
        lines.append(f"- Repo: {repo}")
    if session.model:
        lines.append(f"- Model: {session.model}")
    if session.effort:
        lines.append(f"- Effort: {session.effort}")
    if session.permission_mode:
        lines.append(f"- Permission mode: {session.permission_mode}")
    thread_id = session.transport_state.get("thread_id")
    if isinstance(thread_id, str) and thread_id:
        lines.append(f"- Thread: {thread_id}")
    commands = sorted(
        {
            f"/{name}"
            for command in _session_slash_commands(adapter, session.id)
            if (name := _slash_command_name(command))
        }
    )
    if commands:
        preview = ", ".join(commands[:12])
        suffix = f", +{len(commands) - 12} more" if len(commands) > 12 else ""
        lines.append(f"- Runtime slash commands: {preview}{suffix}")
    return "\n".join(lines)


def build_plugin() -> ClaudeCodePlugin:
    return ClaudeCodePlugin()


__all__ = [
    "CLAUDE_EFFORT_LEVELS",
    "CLAUDE_PERMISSION_MODES",
    "DEFAULT_CLAUDE_MODELS",
    "ClaudeCodePlugin",
    "ClaudeCodePluginConfig",
    "build_plugin",
]
