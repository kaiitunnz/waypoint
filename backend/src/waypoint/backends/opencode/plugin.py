import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from waypoint.backends.approvals import approval_request_id, open_approval_requests
from waypoint.backends.base import DefaultLaunchContract
from waypoint.backends.capabilities import (
    BackendCapabilities,
    ModelSource,
    PermissionModeSpec,
    SlashCommandSpec,
)
from waypoint.backends.completions import static_slash_completions
from waypoint.backends.opencode.adapter import (
    MigrationGate,
    OpenCodeAdapter,
    OpenCodeError,
)
from waypoint.backends.opencode.catalog import Model
from waypoint.backends.opencode.health import AdapterHealth
from waypoint.backends.opencode.normalize import (
    APPROVAL_DECISIONS,
    historical_events_from_messages,
)
from waypoint.backends.opencode.pane import composer_ready, composer_submitted
from waypoint.backends.plugin_config import PluginConfig, PluginLaunchTargetConfig
from waypoint.git_meta import GitMeta
from waypoint.launch_env import LaunchEnv
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.questions import (
    ASK_QUESTION_ANSWER,
    QuestionDecline,
    QuestionLiveness,
)
from waypoint.schemas import (
    AttachmentSpec,
    CommandCompletion,
    CompletionDispatch,
    EventKind,
    EventRecord,
    SessionCreateRequest,
    SessionInputRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
    SessionTransportId,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.backends.context_usage_source import ContextUsageSource
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.opencode")

OPENCODE_PERMISSION_MODES = (
    PermissionModeSpec(
        id="default",
        label="Default",
        description="Use OpenCode's built-in defaults (no rule attached)",
    ),
    PermissionModeSpec(
        id="ask", label="Ask", description="Ask for permission on every action"
    ),
    PermissionModeSpec(
        id="allow", label="Allow", description="Automatically approve actions"
    ),
    PermissionModeSpec(id="deny", label="Deny", description="Deny all actions"),
    PermissionModeSpec(
        id="plan",
        label="Plan",
        description="Draft and save an architecture plan before coding",
    ),
)
OPENCODE_PERMISSION_ACTIONS = {"ask", "allow", "deny"}

OPENCODE_SLASH_COMMANDS = (
    SlashCommandSpec(
        name="compact", description="Compact the session to reduce context"
    ),
    SlashCommandSpec(name="new", description="Start a new session"),
    SlashCommandSpec(name="status", description="Show session status"),
)

OPENCODE_REASONING_EFFORTS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "extra high",
    "max",
)

OpenCodeLaunchEnvKey = tuple[tuple[str, str], ...]
OpenCodeAdapterKey = tuple[str | None, str, tuple[str, ...], OpenCodeLaunchEnvKey]
OPENCODE_REASONING_EFFORT_LEVELS = dict(
    (level.lower(), i) for i, level in enumerate(OPENCODE_REASONING_EFFORTS)
)


def _normalize_remote_cwd(cwd: str) -> str:
    # Stale session.cwd values can carry an embedded `/~/` (the result of an
    # earlier path-concat bug). Bash on the remote only expands a *leading*
    # tilde, so the embedded one becomes a literal directory component and
    # every `cd` against the path fails. Re-anchor at the suffix following
    # the last `/~/` and prepend `~/` so the leading tilde does the right
    # thing on the remote shell.
    if "/~/" in cwd:
        suffix = cwd.rsplit("/~/", 1)[1]
        return f"~/{suffix}"
    return cwd


def _ruleset_for_mode(mode: str | None) -> list[dict[str, str]]:
    # No rules means "let OpenCode decide": its agents' own defaults apply.
    # The plan agent brings its own permissions too.
    if mode not in OPENCODE_PERMISSION_ACTIONS:
        return []
    return [{"action": "*", "resource": "*", "effect": mode}]


def _subagent_ruleset_for_mode(mode: str | None) -> list[dict[str, str]]:
    # Plan mode is the session's agent, which its subagents do not take: they
    # ask before acting.
    return _ruleset_for_mode("ask" if mode == "plan" else mode)


def _sorted_efforts(efforts: tuple[str, ...]) -> list[str]:
    def key(effort: str) -> tuple[int, int | float, str]:
        level = OPENCODE_REASONING_EFFORT_LEVELS.get(effort.lower())
        if level is not None:
            return (0, level, effort)
        try:
            return (1, float(effort), effort)
        except ValueError:
            return (2, 0, effort.lower())

    return sorted(efforts, key=key)


_STATUS_NOTES = {
    SessionStatus.IDLE: "Session idle",
    SessionStatus.INTERRUPTED: "Turn interrupted",
    SessionStatus.WAITING_INPUT: "Waiting for your input",
}


def _agent_process_env(
    runtime: "SessionRuntime", backend: str, launch_env: dict[str, str]
) -> dict[str, str]:
    return runtime._agent_process_env(backend, launch_env)


class OpenCodePluginConfig(PluginConfig):
    pass


class OpenCodeThreadImportRequest(BaseModel):
    thread_id: str
    launch_target_id: str | None = None
    cwd: str | None = None
    # Pins the transport the imported thread is driven over. OpenCode imports
    # only over its native ``opencode_http`` adapter; pinning any other
    # transport (e.g. the tmux wrapper) is rejected with 400 since there is no
    # resume-via-tmux path for OpenCode. ``None`` keeps the native behavior.
    transport: SessionTransportId | None = None
    launch_env: LaunchEnv = Field(default_factory=dict)
    # When true (default), the prior conversation is replayed into the new
    # session's transcript at import time; when false the transcript starts
    # empty and only the underlying agent resumes its own context.
    import_history: bool = True


class OpenCodeThreadSummary(BaseModel):
    id: str
    title: str
    directory: str | None = None
    created_at: int | None = None
    updated_at: int | None = None


class OpenCodePlugin(DefaultLaunchContract):
    id = "opencode"
    transport_id = "opencode_http"
    # Driven over its native REST + SSE adapter by default, or the generic
    # tmux pane wrapper as the managed-launch fallback.
    supported_transports = ("opencode_http", "tmux")
    default_transport = "opencode_http"
    label = "OpenCode"
    import_request_schema: type[BaseModel] | None = OpenCodeThreadImportRequest
    config_schema: type[PluginConfig] = OpenCodePluginConfig
    launch_target_schema: type[PluginLaunchTargetConfig] = PluginLaunchTargetConfig
    extra_env: dict[str, str] = {}

    question_parks_turn = True
    capabilities = BackendCapabilities(
        is_structured=True,
        supports_resume=False,
        supports_reattach_after_exit=True,
        supports_set_model_inline=True,
        supports_set_effort_inline=True,
        supports_set_effort_with_restart=False,
        supports_set_permission_mode_inline=True,
        supports_thread_discovery=True,
        supports_thread_import=True,
        supports_thread_delete=True,
        supports_fork=True,
        supports_slash_compact=True,
        supports_approval_note=True,
        supports_attachments=True,
        supports_custom_cli_args=True,
        # "Approve for session" is a rule on the session. OpenCode's own
        # "always" allows project-wide, so there is no acceptAlways.
        approval_decisions=APPROVAL_DECISIONS,
        permission_modes=OPENCODE_PERMISSION_MODES,
        effort_levels=(),
        model_source=ModelSource.LIVE_RPC,
        slash_commands=OPENCODE_SLASH_COMMANDS,
        badges={"glyph": "O", "color": "#cbd5e1"},
        cli_binary="opencode",
        target_aliases=("opencode",),
    )

    def launch_flags(
        self,
        *,
        model: str | None = None,
        effort: str | None = None,
        permission_mode: str | None = None,
    ) -> list[str]:
        return self.required_launch_flags()

    def required_launch_flags(self) -> list[str]:
        # Without it the TUI's tools run in a shared background service, which
        # has neither the session's launch env nor its WAYPOINT_SESSION_ID.
        return ["--standalone"]

    def __init__(self) -> None:
        # Adapter key is (launch_target_id, normalized_cwd, extra_args, env).
        # Sessions with distinct custom_cli_args or env get their own server process.
        self._adapters: dict[OpenCodeAdapterKey, OpenCodeAdapter] = {}
        # Per-adapter health (cooldown after death + circuit breaker after
        # repeated launch failures). Keyed identically to ``_adapters`` so
        # one entry survives adapter object replacement.
        self._health: dict[OpenCodeAdapterKey, AdapterHealth] = {}
        # Reconnect tasks keyed on adapter key so a single launch_target
        # never has more than one reconnect loop running concurrently.
        self._reconnect_tasks: dict[OpenCodeAdapterKey, asyncio.Task[None]] = {}
        # Sessions tracked by the reconnect loop per adapter key.
        self._reconnect_targets: dict[OpenCodeAdapterKey, set[str]] = {}
        self._lock = asyncio.Lock()
        # One per launch target, whose servers share one OpenCode database.
        self._migration_gates: dict[str | None, MigrationGate] = {}
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        self._shutting_down = False

    def _config(self, runtime: "SessionRuntime") -> OpenCodePluginConfig:
        config = runtime.settings.plugin_config(self.id)
        assert isinstance(config, OpenCodePluginConfig)
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

    def _default_cwd(
        self, runtime: "SessionRuntime", launch_target_id: str | None
    ) -> str:
        if launch_target_id is not None:
            launch_target = runtime._find_launch_target(launch_target_id)
            if launch_target is not None:
                return launch_target.default_cwd
        return str(Path(runtime.settings.default_cwd).expanduser())

    def _adapter_cwd(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None,
        cwd: str | None,
    ) -> str:
        chosen = cwd or self._default_cwd(runtime, launch_target_id)
        if launch_target_id is None:
            chosen = str(Path(chosen).expanduser())
        else:
            chosen = _normalize_remote_cwd(chosen)
        return os.path.normpath(chosen)

    def _adapter_key(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None,
        cwd: str | None,
        custom_args: tuple[str, ...] = (),
        launch_env: dict[str, str] | None = None,
    ) -> OpenCodeAdapterKey:
        return (
            launch_target_id,
            self._adapter_cwd(runtime, launch_target_id, cwd),
            custom_args,
            tuple(sorted((launch_env or {}).items())),
        )

    def _find_adapter_for_launch_target(
        self, launch_target_id: str | None
    ) -> OpenCodeAdapter | None:
        for key, adapter in self._adapters.items():
            if key[0] == launch_target_id:
                return adapter
        return None

    def _adapters_for_launch_target(
        self, launch_target_id: str | None
    ) -> list[OpenCodeAdapter]:
        return [
            adapter
            for key, adapter in self._adapters.items()
            if key[0] == launch_target_id
        ]

    async def _handle_agent_changed(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        old_agent: str | None,
        new_agent: str | None,
    ) -> None:
        # A switch Waypoint did not make, out of the plan agent: leave plan mode.
        if old_agent == "plan" and new_agent != "plan":
            session = runtime.get_session(session_id)
            if session.permission_mode == "plan":
                adapter = self.existing_session_adapter(runtime, session)
                target_mode = (
                    adapter.get_pre_plan_mode(session.id) if adapter else None
                ) or "default"

                if target_mode == "plan":
                    target_mode = "default"

                runtime.storage.update_session(session_id, permission_mode=target_mode)
                if adapter is not None and not await adapter.set_session_permission(
                    session.id,
                    _ruleset_for_mode(target_mode),
                    _subagent_ruleset_for_mode(target_mode),
                ):
                    await runtime._record_system_event(
                        session_id,
                        f"Could not apply the {target_mode} permission mode after "
                        "leaving plan mode; set the mode again",
                    )
                # Drop the persisted plan-agent footprint so a later restore
                # doesn't re-enter plan mode.
                self._persist_transport_state(
                    runtime, session, agent=None, pre_plan_mode=None
                )

                # Emit system note so transcript shows that plan mode exited
                await runtime._emit_adapter_event(
                    session_id,
                    EventKind.SYSTEM_NOTE,
                    "Exited plan mode",
                    {"status": SessionStatus.RUNNING},
                    SessionStatus.RUNNING,
                )

                await runtime.broadcast_session_list()

    def _health_for(self, key: OpenCodeAdapterKey) -> AdapterHealth:
        if key not in self._health:
            self._health[key] = AdapterHealth()
        return self._health[key]

    async def _get_or_create_adapter(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None,
        cwd: str | None,
        custom_args: tuple[str, ...] = (),
        launch_env: dict[str, str] | None = None,
        *,
        user_initiated: bool = False,
    ) -> OpenCodeAdapter:
        async with self._lock:
            key = self._adapter_key(
                runtime, launch_target_id, cwd, custom_args, launch_env
            )
            if key in self._adapters:
                return self._adapters[key]

            health = self._health_for(key)
            allowed, reason = health.can_attempt(user_initiated=user_initiated)
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=reason or "opencode adapter temporarily unavailable",
                )

            launch_target = None
            if launch_target_id is not None:
                launch_target = runtime._find_launch_target(launch_target_id)
                if launch_target is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"unknown launch target: {launch_target_id}",
                    )

            def _on_agent_changed(sid: str, old: str | None, new: str | None) -> None:
                if self._shutting_down:
                    return
                task = asyncio.create_task(
                    self._handle_agent_changed(runtime, sid, old, new)
                )
                self._pending_tasks.add(task)
                task.add_done_callback(self._pending_tasks.discard)

            def _on_server_died(active_session_ids: list[str]) -> None:
                self._handle_server_died(runtime, key, active_session_ids)

            def _on_questions_changed(sid: str) -> None:
                runtime.questions.mark_dirty(sid)
                runtime.asks.mark_dirty(sid)

            session_update_callback = getattr(runtime, "session_update_callback", None)
            token_usage_callback = getattr(runtime, "token_usage_callback", None)
            adapter = OpenCodeAdapter(
                emit_event=runtime._emit_adapter_event,
                on_session_update=(
                    session_update_callback()
                    if callable(session_update_callback)
                    else None
                ),
                on_token_usage=(
                    token_usage_callback() if callable(token_usage_callback) else None
                ),
                # A launch target names its own binary (``remote_bin``).
                binary=(
                    runtime.settings.plugin_config(self.id).local_bin
                    if launch_target is None
                    else None
                ),
                launch_target=launch_target,
                on_agent_changed=_on_agent_changed,
                on_server_died=_on_server_died,
                on_questions_changed=_on_questions_changed,
                migration_gate=self._migration_gates.setdefault(
                    launch_target_id, MigrationGate()
                ),
                workdir=key[1],
                extra_args=custom_args,
                launch_env=dict(key[3]),
            )
            self._adapters[key] = adapter
            return adapter

    def _handle_server_died(
        self,
        runtime: "SessionRuntime",
        key: OpenCodeAdapterKey,
        active_session_ids: list[str],
    ) -> None:
        # Synchronous bridge from the adapter's `_on_server_died` into the
        # plugin's health + reconnect machinery. Keep this fast: it runs
        # inside the adapter's teardown path.
        if self._shutting_down:
            return
        self._health_for(key).record_death()
        targets = self._reconnect_targets.setdefault(key, set())
        targets.update(active_session_ids)
        self._ensure_reconnect_task(runtime, key)

    def _ensure_reconnect_task(
        self,
        runtime: "SessionRuntime",
        key: OpenCodeAdapterKey,
    ) -> None:
        existing = self._reconnect_tasks.get(key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._reconnect_loop(runtime, key),
            name=f"opencode-reconnect-{key}",
        )
        self._reconnect_tasks[key] = task
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        task.add_done_callback(lambda _t: self._reconnect_tasks.pop(key, None))

    async def _reconnect_loop(
        self,
        runtime: "SessionRuntime",
        key: OpenCodeAdapterKey,
    ) -> None:
        # Capped exponential backoff that loops forever until either every
        # tracked session is gone (user deleted them) or the plugin is
        # shutting down. The cap is intentionally low (5 minutes) so a
        # VPN coming back hours later still recovers within five minutes
        # of the link healing.
        backoff_schedule = [5, 10, 30, 60, 120, 300]
        attempt = 0
        while not self._shutting_down:
            targets = self._reconnect_targets.get(key, set())
            if not targets:
                return
            try:
                # The loop is the dedicated retry mechanism — its own
                # backoff schedule already paces attempts, so it must
                # bypass the cooldown/quarantine gate. Without this,
                # `record_death()` having just fired means the very
                # first iteration is gated as a "failure", inflating
                # `consecutive_failures` toward quarantine without ever
                # really touching SSH. The gate exists to fail-fast
                # passive HTTP callers, not the loop.
                adapter = await self._get_or_create_adapter(
                    runtime,
                    key[0],
                    key[1] or None,
                    key[2],
                    dict(key[3]),
                    user_initiated=True,
                )
                await adapter.start()
                health = self._health_for(key)
                health.record_success()
            except Exception as exc:
                self._health_for(key).record_failure()
                wait = backoff_schedule[min(attempt, len(backoff_schedule) - 1)]
                attempt += 1
                log.warning(
                    "opencode reconnect for %s failed (%s); retrying in %ds",
                    key,
                    exc,
                    wait,
                )
                try:
                    await asyncio.sleep(wait)
                except asyncio.CancelledError:
                    raise
                continue

            attempt = 0
            await self._restore_after_reconnect(runtime, key)

            # Successful pass: drain tracked targets and exit. If new
            # deaths happen, `_handle_server_died` re-arms a fresh loop.
            self._reconnect_targets.pop(key, None)
            return

    async def _restore_after_reconnect(
        self,
        runtime: "SessionRuntime",
        key: OpenCodeAdapterKey,
    ) -> None:
        targets = list(self._reconnect_targets.get(key, set()))
        for session_id in targets:
            session = runtime.storage.get_session(session_id)
            if session is None:
                self._reconnect_targets.get(key, set()).discard(session_id)
                continue
            adapter = self._adapters.get(key)
            if adapter is None:
                # Slot was wiped between loop start and here; bail and let
                # the next death event re-arm the loop.
                return
            opencode_session_id = session.transport_state.get("opencode_session_id")
            if not opencode_session_id:
                self._reconnect_targets.get(key, set()).discard(session_id)
                continue
            try:
                status_after = await self._restore_into(
                    runtime, adapter, session, opencode_session_id
                )
            except Exception as exc:
                log.warning(
                    "opencode resurrect of %s failed after reconnect: %s",
                    session.id,
                    exc,
                )
                # Leave session ERROR; user can retry via /reattach.
                continue
            await runtime._record_system_event(
                session.id,
                "OpenCode connection restored",
                status=status_after,
            )
            await runtime.questions.reconcile(session.id)

    async def _restore_into(
        self,
        runtime: "SessionRuntime",
        adapter: OpenCodeAdapter,
        session: SessionRecord,
        opencode_session_id: str,
    ) -> SessionStatus:
        """Have ``adapter`` drive ``session`` again, and record the status the
        server's state gives it."""
        await adapter.restore_session(
            session.id,
            opencode_session_id,
            permissions=_ruleset_for_mode(session.permission_mode),
            subagent_permissions=_subagent_ruleset_for_mode(session.permission_mode),
            model=session.model,
            agent=session.transport_state.get("agent"),
            effort=session.effort,
            open_approvals=[
                request_id
                for event in open_approval_requests(
                    runtime.storage.list_approval_events(session.id)
                )
                if (request_id := approval_request_id(event))
            ],
            open_questions=runtime.storage.open_question_tool_use_ids(session.id),
        )
        pre_plan_mode = session.transport_state.get("pre_plan_mode")
        if pre_plan_mode is not None:
            adapter.set_pre_plan_mode(session.id, pre_plan_mode)
        current = adapter.session_status(session.id) or SessionStatus.IDLE
        runtime.storage.update_session(session.id, status=current)
        return current

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        from waypoint.backends.opencode.transport import OpenCodeTransport

        return OpenCodeTransport(runtime, self)

    def pane_ready_for_input(self, pane_text: str) -> bool:
        return composer_ready(pane_text)

    def pane_shows_blocking_dialog(self, pane_text: str) -> bool:
        # OpenCode's approvals run inline in its own TUI and are not surfaced to
        # Waypoint over the generic tmux transport, and there is no pane-dialog
        # parser to read them off the screen, so the transport's dialog guard is
        # a no-op here. Add detection if a collision is ever observed.
        return False

    def confirm_pane_submit(self, pane_text: str, sent_text: str) -> bool:
        # Over the tmux/Terminal transport the OpenCode TUI can absorb the
        # submit Enter while ingesting input; the composer box no longer
        # carrying the message is the signal it was sent.
        return composer_submitted(pane_text, sent_text)

    def setup(self, runtime: "SessionRuntime") -> None:
        log.info("setting up opencode plugin")

    async def shutdown(self, runtime: "SessionRuntime") -> None:
        self._shutting_down = True
        pending = list(self._pending_tasks)
        self._pending_tasks.clear()
        for task in pending:
            task.cancel()
        if pending:
            # Await the cancelled tasks so a late wake-up cannot fire after
            # the adapter (and its storage handle) is gone.
            await asyncio.gather(*pending, return_exceptions=True)
        for adapter in list(self._adapters.values()):
            await adapter.shutdown()
        self._adapters.clear()

    def is_available_for_managed_launch(self, runtime: "SessionRuntime") -> bool:
        return True

    def remote_executable(self, launch_target: SshLaunchTargetConfig) -> str:
        return launch_target.remote_bin_for(self.id, self.capabilities.cli_binary) or ""

    async def terminate_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        key = self._session_adapter_key(runtime, session)
        # Drop this session from any in-flight reconnect-loop target set so
        # an explicit terminate can't be silently undone by a later loop
        # tick resurrecting it.
        targets = self._reconnect_targets.get(key)
        if targets is not None:
            targets.discard(session.id)
            if not targets:
                self._reconnect_targets.pop(key, None)
        adapter = self._adapters.get(key)
        if adapter is not None:
            await adapter.terminate_session(session.id)
        await runtime.questions.reconcile(session.id)

    def clear_health_for_user_retry(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        # Hook surfaced via getattr from `runtime._reattach_session`. Clears
        # the cooldown / circuit breaker so the user-initiated reattach
        # bypasses backoff that the auto-reconnect loop is already
        # observing. Also re-arms a fresh reconnect attempt: cancel any
        # active loop so the next `_get_or_create_adapter` call drives
        # the SSH spinup synchronously instead of racing the loop.
        key = self._session_adapter_key(runtime, session)
        health = self._health.get(key)
        if health is not None:
            health.record_success()
        existing = self._reconnect_tasks.get(key)
        if existing is not None and not existing.done():
            existing.cancel()

    def native_thread_id(self, session: SessionRecord) -> str | None:
        # OpenCode keys its native conversation id as ``opencode_session_id``.
        session_id = session.transport_state.get("opencode_session_id")
        return session_id if isinstance(session_id, str) else None

    def native_thread_artifacts(
        self, session: SessionRecord, config_dir: str | None = None
    ) -> list[Path]:
        # No config-dir env var / account-profile support in phase 1.
        return []

    def native_thread_artifact_glob(self, session: SessionRecord) -> str | None:
        # No config-dir env var / account-profile support in phase 1.
        return None

    def on_session_deleted(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        if self._shutting_down:
            return
        adapter = self.existing_session_adapter(runtime, session)
        if adapter is not None:
            task = asyncio.create_task(adapter.terminate_session(session.id))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

    def create_context_usage_source(
        self, session: SessionRecord, runtime: "SessionRuntime"
    ) -> "ContextUsageSource | None":
        # No telemetry over the tmux transport: the TUI creates its session id
        # lazily after launch, so the only handle is the working directory —
        # and two sessions in one directory would report each other's usage.
        # Enable only once a native, unambiguous id handoff exists.
        return None

    def register_routes(self, app: FastAPI, context: Any) -> None:
        pass

    def validate_permission_mode(self, mode: str | None) -> str | None:
        if mode is None or mode == "":
            return None
        # "default" is a real, user-selectable mode for OpenCode — it clears
        # any attached ruleset so the upstream defaults apply. We pass it
        # through so set_permission_mode round-trips it (the runtime rejects
        # `None`), and apply_permission_mode handles it via _ruleset_for_mode.
        if mode == "default":
            return "default"
        if mode == "plan":
            return "plan"
        if mode not in OPENCODE_PERMISSION_ACTIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"unsupported opencode permission mode: {mode}; "
                    f"expected one of {', '.join(sorted(OPENCODE_PERMISSION_ACTIONS))} or plan"
                ),
            )
        return mode

    async def apply_permission_mode(
        self, runtime: "SessionRuntime", session: SessionRecord, mode: str
    ) -> None:
        adapter = self._live_adapter(runtime, session)
        try:
            if mode == "plan":
                pre_plan_mode = (
                    session.permission_mode
                    if session.permission_mode != "plan"
                    else session.transport_state.get("pre_plan_mode")
                )
                adapter.set_pre_plan_mode(session.id, pre_plan_mode)
                success = await adapter.set_agent(session.id, "plan")
                persisted: dict[str, Any] = {
                    "agent": "plan",
                    "pre_plan_mode": pre_plan_mode,
                }
            else:
                success = True
                if adapter.get_agent(session.id) == "plan":
                    success = await adapter.set_agent(session.id, "build")
                persisted = {"agent": None, "pre_plan_mode": None}
        except OpenCodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        if success:
            success = await adapter.set_session_permission(
                session.id,
                _ruleset_for_mode(mode),
                _subagent_ruleset_for_mode(mode),
            )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to set permission mode to {mode}",
            )
        self._persist_transport_state(runtime, session, **persisted)

    def _persist_transport_state(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        **updates: Any,
    ) -> None:
        merged = {**session.transport_state, **updates}
        session.transport_state = merged
        runtime.storage.update_session(session.id, transport_state=merged)

    def _live_adapter(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> OpenCodeAdapter:
        adapter = self.existing_session_adapter(runtime, session)
        if adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="opencode is not running for this session",
            )
        return adapter

    async def apply_model(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        model: str | None,
    ) -> None:
        adapter = self._live_adapter(runtime, session)
        try:
            success = await adapter.set_model(session.id, model, session.effort)
        except OpenCodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to set model to {model}",
            )

    async def apply_effort(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        effort: str | None,
    ) -> bool:
        adapter = self._live_adapter(runtime, session)
        try:
            success = await adapter.set_effort(session.id, effort)
        except OpenCodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to set effort to {effort}",
            )
        # OpenCode applies effort inline — no restart needed.
        return False

    def effort_swap_message(self, effort: str | None) -> str:
        # Never published — apply_effort returns False so the runtime skips
        # the announcement path entirely.
        return ""

    async def _reject_account_profile(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None,
        account_profile_id: str | None,
    ) -> None:
        # OpenCode has no config-dir env var, so a profile can't scope its
        # server-owned store. Route a selected profile through the generic
        # resolver so the rejection matches the launch path (400) rather than
        # being silently ignored; ``None`` is a no-op.
        if account_profile_id is None:
            return
        launch_target = runtime._resolve_launch_target(launch_target_id, self.id)
        await runtime.discovery_env(self.id, launch_target, account_profile_id)

    async def list_models(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        include_hidden: bool = False,
        account_profile_id: str | None = None,
    ) -> dict[str, Any]:
        await self._reject_account_profile(
            runtime, launch_target_id, account_profile_id
        )
        adapters = list(reversed(self._adapters_for_launch_target(launch_target_id)))
        if not adapters:
            adapters = [
                await self._get_or_create_adapter(runtime, launch_target_id, None)
            ]

        last_error: Exception | None = None
        for adapter in adapters:
            try:
                models = await adapter.list_models(include_deprecated=include_hidden)
                if not models:
                    continue
                providers = await adapter.provider_names()
                default_id = await adapter.default_model_id()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
            return self._model_listing(models, providers, default_id)

        if last_error is not None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"failed to list opencode models: {last_error}",
            ) from last_error
        return self._model_listing([], {}, None)

    def _model_listing(
        self,
        models: list[Model],
        providers: dict[str, str],
        default_id: str | None,
    ) -> dict[str, Any]:
        entries = sorted(
            (
                {
                    "id": model.id,
                    "label": (
                        f"{providers.get(model.provider_id, model.provider_id)}"
                        f" · {model.name}"
                    ),
                    "supported_efforts": _sorted_efforts(model.variants),
                    "default_effort": None,
                }
                for model in models
            ),
            key=lambda entry: str(entry["id"]),
        )
        label = next(
            (entry["label"] for entry in entries if entry["id"] == default_id), None
        )
        return {
            "backend": self.id,
            "models": entries,
            "default_model_id": default_id,
            "default_model_label": label,
            "default_effort": None,
            "supports_free_text": True,
        }

    async def list_command_completions(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        *,
        trigger: str = "/",
        force_refresh: bool = False,
    ) -> list[CommandCompletion]:
        if trigger != "/":
            return []
        completions = static_slash_completions(self.id, self.capabilities)
        adapter = self.existing_session_adapter(runtime, session)
        if adapter is None:
            return completions
        try:
            commands = await adapter.list_commands()
        except Exception:
            return completions

        seen = {f"/{item.name}" for item in completions}
        for item in commands:
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            command = f"/{name}"
            if command in seen:
                continue
            description = item.get("description")
            source = "skill" if item.get("source") == "skill" else "command"
            completions.append(
                CommandCompletion(
                    id=f"{self.id}:{source}:{name}",
                    trigger="/",
                    replacement=f"{command} ",
                    name=name,
                    description=description if isinstance(description, str) else None,
                    kind=source,
                    source=f"opencode_{source}",
                    dispatch=CompletionDispatch.BACKEND_COMMAND,
                    metadata={"source": source},
                )
            )
            seen.add(command)
        return completions

    async def _invoke(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        adapter: OpenCodeAdapter,
        request: SessionInputRequest,
        *,
        name: str,
        arguments: str,
        skill: bool,
    ) -> SessionRecord:
        """Run a slash command, or a prompt carrying a skill. Both bypass the
        runtime's input path, so the attachments are recorded here."""
        attachments = runtime.resolve_attachments(session.id, request.attachments)
        specs: list[AttachmentSpec] = [item.spec for item in attachments]
        updated = runtime.storage.update_session(
            session.id, status=SessionStatus.RUNNING
        )
        await runtime._record_user_event(
            session.id, request.text, submit=request.submit, attachments=specs
        )
        if specs:
            runtime.attachments.mark_sent(session.id, [spec.id for spec in specs])
        try:
            if skill:
                await adapter.send_input(
                    session.id, request.text, attachments, skills=[name]
                )
            else:
                await adapter.execute_command(session.id, name, arguments, attachments)
        except OpenCodeError as exc:
            runtime.storage.restore_status(session)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        return updated

    async def maybe_handle_input(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        request: SessionInputRequest,
    ) -> SessionRecord | None:
        text = request.text
        if not text.startswith("/"):
            return None

        rest = text[1:].split(maxsplit=1)
        command = rest[0] if rest else ""
        arguments = rest[1] if len(rest) > 1 else ""

        if command == "status":
            await runtime._record_user_event(
                session.id,
                text,
                submit=request.submit,
                status=session.status,
            )
            await runtime._record_system_event(
                session.id,
                _format_opencode_status(session),
                status=session.status,
                metadata={"builtin_command": "/status", "source": "waypoint"},
            )
            return runtime.get_session(session.id)

        adapter = self._live_adapter(runtime, session)

        if command == "compact":
            await runtime._record_user_event(session.id, text, submit=request.submit)
            await runtime._record_system_event(
                session.id,
                "Compacting session...",
                status=SessionStatus.RUNNING,
                metadata={"builtin_command": "/compact"},
            )
            try:
                await adapter.compact_session(session.id)
            except OpenCodeError as exc:
                runtime.storage.restore_status(session)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
            return runtime.get_session(session.id)

        invocation = request.command
        if (
            invocation is not None
            and invocation.dispatch == CompletionDispatch.BACKEND_COMMAND
        ):
            return await self._invoke(
                runtime,
                session,
                adapter,
                request,
                name=invocation.name,
                arguments=invocation.arguments,
                skill=invocation.metadata.get("source") == "skill",
            )

        if command not in {"new", "fork"}:
            cached = runtime.cached_command_completion(
                session.id, trigger="/", name=command
            )
            if (
                cached is not None
                and cached.dispatch == CompletionDispatch.BACKEND_COMMAND
            ):
                return await self._invoke(
                    runtime,
                    session,
                    adapter,
                    request,
                    name=cached.name,
                    arguments=arguments,
                    skill=cached.metadata.get("source") == "skill",
                )

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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="side-questions are only supported for Claude sessions",
        )

    async def dismiss_side_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        side_question_id: str,
    ) -> None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="side-questions are only supported for Claude sessions",
        )

    async def answer_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        answer: str,
        tool_use_id: str | None,
        answers: list[dict[str, Any]] | None,
    ) -> SessionRecord:
        adapter = self._live_adapter(runtime, session)
        request_id = tool_use_id or adapter.current_question_id(session.id)
        if not request_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="no pending question to answer",
            )
        with runtime.questions.operation(session.id, request_id):
            success = await adapter.answer_question(
                session.id, request_id, answer, answers
            )
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="failed to answer question",
                )
            # The turn may already be over by the time the reply returns.
            after = adapter.session_status(session.id) or SessionStatus.RUNNING
            updated = runtime.storage.update_session(session.id, status=after)
            metadata: dict[str, Any] = {"kind": ASK_QUESTION_ANSWER}
            if answers:
                metadata["answers"] = answers
            metadata["tool_use_id"] = request_id
            await runtime._record_user_event(
                session.id,
                answer,
                submit=True,
                status=after,
                extra_metadata=metadata,
            )
        return updated

    def question_liveness(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        tool_use_ids: list[str],
    ) -> dict[str, QuestionLiveness]:
        adapter = self.existing_session_adapter(runtime, session)
        if adapter is None:
            return dict.fromkeys(tool_use_ids, QuestionLiveness.UNAVAILABLE)
        return adapter.question_liveness(session.id, tool_use_ids)

    async def decline_question(
        self, runtime: "SessionRuntime", session: SessionRecord, tool_use_id: str
    ) -> QuestionDecline:
        adapter = self.existing_session_adapter(runtime, session)
        if adapter is None:
            return QuestionDecline.FAILED
        return await adapter.reject_question(session.id, tool_use_id)

    def _session_adapter_key(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> OpenCodeAdapterKey:
        return self._adapter_key(
            runtime,
            session.launch_target_id,
            session.cwd,
            tuple(
                self._effective_args(runtime, session.launch_target_id, session.args)
            ),
            _agent_process_env(runtime, self.id, session.launch_env),
        )

    async def session_adapter(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> OpenCodeAdapter:
        """The adapter serving ``session``, created if there is none."""
        launch_target_id, _, args, env = self._session_adapter_key(runtime, session)
        return await self._get_or_create_adapter(
            runtime,
            launch_target_id,
            session.cwd,
            custom_args=args,
            launch_env=dict(env),
        )

    def existing_session_adapter(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> OpenCodeAdapter | None:
        return self._adapters.get(self._session_adapter_key(runtime, session))

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
        # The runtime writes a status after the reply returns, which can be
        # after the stream reported the turn over: a rejection ends it at once.
        adapter = self.existing_session_adapter(runtime, session)
        if adapter is None:
            return
        actual = adapter.session_status(session.id)
        if actual is None or actual == runtime.get_session(session.id).status:
            return
        await runtime._record_system_event(
            session.id, _STATUS_NOTES.get(actual, "Session busy"), status=actual
        )

    async def restore_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        key = self._session_adapter_key(runtime, session)
        try:
            adapter = await self.session_adapter(runtime, session)
        except Exception as exc:
            self._health_for(key).record_failure()
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"OpenCode adapter unavailable; cannot restore: {exc}",
                status=SessionStatus.ERROR,
            )
            return
        opencode_session_id = session.transport_state.get("opencode_session_id")
        if not opencode_session_id:
            runtime.storage.update_session(session.id, status=SessionStatus.EXITED)
            await runtime._record_system_event(
                session.id,
                "OpenCode session has no opencode_session_id; marking exited",
                status=SessionStatus.EXITED,
            )
            return
        try:
            after = await self._restore_into(
                runtime, adapter, session, opencode_session_id
            )
        except Exception as exc:
            self._health_for(key).record_failure()
            log.exception("opencode restore failed", extra={"session_id": session.id})
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"OpenCode session restore failed: {exc}",
                status=SessionStatus.ERROR,
            )
            return
        self._health_for(key).record_success()
        await runtime._record_system_event(
            session.id,
            "OpenCode session restored from previous backend process",
            status=after,
        )
        await runtime.questions.reconcile(session.id)

    async def fork_session(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        new_session_id: str,
        title: str,
        raw_log: Path,
        structured_log: Path,
    ) -> SessionRecord:
        key = self._session_adapter_key(runtime, session)
        try:
            adapter = await self.session_adapter(runtime, session)
        except Exception as exc:
            self._health_for(key).record_failure()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"OpenCode adapter unavailable; cannot fork: {exc}",
            ) from exc

        opencode_session_id = session.transport_state.get("opencode_session_id")
        if not opencode_session_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OpenCode session has no opencode_session_id to fork from",
            )

        agent = session.transport_state.get("agent")
        try:
            new_opencode_session_id = await adapter.fork_session(
                new_session_id,
                opencode_session_id,
                _ruleset_for_mode(session.permission_mode),
                _subagent_ruleset_for_mode(session.permission_mode),
            )
            pre_plan_mode = session.transport_state.get("pre_plan_mode")
            if pre_plan_mode is not None:
                adapter.set_pre_plan_mode(new_session_id, pre_plan_mode)
        except Exception as exc:
            self._health_for(key).record_failure()
            log.exception("opencode fork failed", extra={"session_id": session.id})
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"OpenCode session fork failed: {exc}",
            ) from exc

        self._health_for(key).record_success()
        now = datetime.now(UTC)
        raw_log.touch(exist_ok=True)
        forked_transport_state: dict[str, Any] = {
            "opencode_session_id": new_opencode_session_id,
        }
        if agent:
            forked_transport_state["agent"] = agent
        if pre_plan_mode is not None:
            forked_transport_state["pre_plan_mode"] = pre_plan_mode
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
            transport_state=forked_transport_state,
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
        await runtime._record_system_event(
            new_session_id,
            f"OpenCode session forked from {session.title or session.id}",
            status=SessionStatus.IDLE,
        )
        return runtime.get_session(new_session_id)

    async def list_threads(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        account_profile_id: str | None = None,
    ) -> list[OpenCodeThreadSummary]:
        await self._reject_account_profile(
            runtime, launch_target_id, account_profile_id
        )
        adapters = self._adapters_for_launch_target(launch_target_id)
        if not adapters:
            adapters = [
                await self._get_or_create_adapter(runtime, launch_target_id, None)
            ]
        imported = {
            (s.transport_state.get("opencode_session_id"), s.launch_target_id)
            for s in runtime.storage.list_sessions()
            if s.backend == self.id
        }
        result = []
        seen: set[str] = set()
        failure: Exception | None = None
        listed = False
        for adapter in adapters:
            # One bad adapter (e.g. an ERROR session restored at boot whose
            # persisted cwd no longer resolves) must not take down thread
            # discovery for the rest. Skip and log.
            try:
                sessions = await adapter.list_sessions()
            except Exception as exc:  # noqa: BLE001
                log.exception(
                    "opencode list_sessions failed for adapter; skipping",
                    extra={"workdir": adapter.workdir},
                )
                failure = exc
                continue
            listed = True
            for sess in sessions:
                sess_id = sess.get("id")
                if not sess_id or sess_id in seen:
                    continue
                seen.add(sess_id)
                if (sess_id, launch_target_id) in imported:
                    continue
                time_data = sess.get("time", {})
                result.append(
                    OpenCodeThreadSummary(
                        id=sess_id,
                        title=sess.get("title") or "Untitled",
                        directory=(sess.get("location") or {}).get("directory"),
                        created_at=time_data.get("created"),
                        updated_at=time_data.get("updated"),
                    )
                )
        if failure is not None and not listed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"failed to list opencode threads: {failure}",
            ) from failure
        result.sort(
            key=lambda thread: (
                thread.updated_at if thread.updated_at is not None else -1,
                thread.created_at if thread.created_at is not None else -1,
                thread.id,
            ),
            reverse=True,
        )
        return result

    async def delete_thread(
        self,
        runtime: "SessionRuntime",
        thread_id: str,
        launch_target_id: str | None = None,
        account_profile_id: str | None = None,
    ) -> bool:
        await self._reject_account_profile(
            runtime, launch_target_id, account_profile_id
        )
        # OpenCode keeps sessions in its server's own store, so deletion is a
        # `DELETE /session/{id}` against that server (the adapter routes it
        # locally or over SSH) rather than a transcript unlink. A launch target
        # can have several adapters — distinct cwds / custom args, each its own
        # server — and the thread lives on exactly one, so try each and report
        # success on the first that removes it.
        adapters = self._adapters_for_launch_target(launch_target_id)
        if not adapters:
            adapters = [
                await self._get_or_create_adapter(runtime, launch_target_id, None)
            ]
        for adapter in adapters:
            if await adapter.delete_session(thread_id):
                return True
        return False

    async def import_thread(
        self,
        runtime: "SessionRuntime",
        request: OpenCodeThreadImportRequest,
        *,
        agent: str | None = None,
    ) -> SessionRecord:
        backend = agent or self.id
        # OpenCode only imports over its native HTTP adapter. A pinned transport
        # other than the native one (e.g. the tmux wrapper) has no resume path.
        transport = getattr(request, "transport", None)
        if transport is not None and transport != self.transport_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"thread import over transport {transport!r} "
                    f"is not supported for {backend}"
                ),
            )
        launch_target_id = getattr(request, "launch_target_id", None)
        requested_cwd = getattr(request, "cwd", None)
        opencode_session_id = request.thread_id
        for s in runtime.storage.list_sessions():
            if (
                s.backend == backend
                and s.transport_state.get("opencode_session_id") == opencode_session_id
                and s.launch_target_id == launch_target_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="session already imported",
                )

        request_launch_env = request.launch_env
        process_env = _agent_process_env(runtime, self.id, request_launch_env)
        # Fetch through any live adapter for this target first. The OpenCode
        # session already exists on that server with whatever directory it was
        # created in; after fetching metadata we cache the driving adapter
        # under the actual session cwd, where session lookups find it.
        fetch_adapter = self._find_adapter_for_launch_target(launch_target_id)
        if fetch_adapter is None:
            fetch_adapter = await self._get_or_create_adapter(
                runtime,
                launch_target_id,
                requested_cwd,
                launch_env=process_env,
            )
        try:
            sess = await fetch_adapter.get_session(opencode_session_id)
        except OpenCodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        if not sess:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="session not found in OpenCode",
            )
        raw_directory = (sess.get("location") or {}).get("directory")
        cwd = (
            raw_directory
            if isinstance(raw_directory, str) and raw_directory
            else (requested_cwd or ".")
        )
        adapter = await self._get_or_create_adapter(
            runtime, launch_target_id, cwd, launch_env=process_env
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
            title=sess.get("title") or "Imported session",
            cwd=cwd,
            launch_target_id=launch_target_id,
            repo_name=None,
            branch=None,
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"opencode_session_id": opencode_session_id},
            permission_mode="ask",
            launch_env=request_launch_env,
        )
        runtime.storage.create_session(session)
        try:
            after = await self._restore_into(
                runtime, adapter, session, opencode_session_id
            )
        except Exception as exc:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to restore session: {exc}",
            ) from exc

        async def read_history() -> list[EventRecord]:
            messages = await adapter.get_session_messages(opencode_session_id)
            return [
                EventRecord(
                    session_id=session.id,
                    ts=ts,
                    kind=kind,
                    text=text,
                    metadata=metadata,
                    sequence=0,
                )
                for ts, kind, text, metadata in historical_events_from_messages(
                    messages
                )
            ]

        await runtime.seed_thread_history(
            session.id,
            reader=read_history,
            enabled=request.import_history,
        )
        runtime.storage.update_session(session.id, status=after)
        await runtime._record_system_event(
            session.id,
            f"Imported OpenCode session ({opencode_session_id})",
            status=after,
        )
        return runtime.get_session(session.id)

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
        launch_target_id = launch_target.id if launch_target else None
        process_env = _agent_process_env(runtime, self.id, request.launch_env)
        try:
            adapter = await self._get_or_create_adapter(
                runtime,
                launch_target_id,
                request.cwd,
                custom_args=tuple(
                    self._effective_args(
                        runtime,
                        launch_target.id if launch_target else None,
                        request.args,
                    )
                ),
                launch_env=process_env,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"opencode adapter not configured: {exc}",
            ) from exc
        raw_log.touch(exist_ok=True)
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
            transport_state={},
            spawner_session_id=request.spawner_session_id,
            permission_mode=permission_mode,
            model=resolved_model,
            effort=resolved_effort,
            args=request.args,
            launch_env=request.launch_env,
        )
        runtime.storage.create_session(session)

        agent = "plan" if permission_mode == "plan" else None

        try:
            opencode_session_id = await adapter.start_session(
                session_id,
                model=resolved_model,
                effort=resolved_effort,
                agent=agent,
                title=title,
                permissions=_ruleset_for_mode(permission_mode),
                subagent_permissions=_subagent_ruleset_for_mode(permission_mode),
            )
            session.transport_state = {"opencode_session_id": opencode_session_id}
            if agent is not None:
                session.transport_state["agent"] = agent
                session.transport_state["pre_plan_mode"] = "default"
            runtime.storage.update_session(
                session.id, transport_state=session.transport_state
            )
        except OpenCodeError as exc:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        # The adapter already emits "OpenCode session started ({session_id})"
        # with the remote id; re-recording here would duplicate the note.
        model, effort = adapter.selected_model(session_id)
        if resolved_effort not in (None, "default", effort):
            runtime.storage.update_session(session.id, effort=None)
            await runtime._record_system_event(
                session.id,
                f"{model or 'The default model'} has no reasoning effort "
                f"{resolved_effort!r}; running without one",
            )
        return runtime.get_session(session.id)


def _format_opencode_status(session: SessionRecord) -> str:
    lines = [
        "OpenCode session status",
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
    opencode_session_id = session.transport_state.get("opencode_session_id")
    if isinstance(opencode_session_id, str) and opencode_session_id:
        lines.append(f"- Thread: {opencode_session_id}")
    return "\n".join(lines)


def build_plugin() -> OpenCodePlugin:
    return OpenCodePlugin()
