"""Codex backend plugin.

Codex differs from Claude on two big knobs the capability descriptor
captures: ``model_source=LIVE_RPC`` (models come from the App Server's
``model/list`` notification, not a static alias table) and
``supports_set_effort_inline=True`` (effort is per-turn via
``turn_steer``, no session restart required). ``/compact`` is also
Codex-only today; it surfaces here as a registered slash command.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status
from openai_codex.client import CodexClient
from pydantic import BaseModel, Field

from waypoint.backends.capabilities import (
    BackendCapabilities,
    ModelSource,
    SlashCommandSpec,
)
from waypoint.backends.codex.adapter import (
    ClientFactory,
    CodexAppServerAdapter,
    default_client_factory,
)
from waypoint.backends.codex.permission_modes import (
    CODEX_PERMISSION_MODE_IDS,
    CODEX_PERMISSION_MODE_SPECS,
    CODEX_PERMISSION_PRESETS,
    CODEX_PLAN_MODE,
    codex_turn_params_for,
)
from waypoint.backends.codex.rate_limits import (
    probe_codex_status,
    probe_codex_usage_remote,
)
from waypoint.backends.codex.remote import build_remote_codex_client_factory
from waypoint.backends.codex.schemas import (
    CodexThreadImportRequest,
    CodexThreadSummary,
)
from waypoint.backends.completions import static_slash_completions
from waypoint.backends.plugin_config import PluginConfig, PluginLaunchTargetConfig
from waypoint.backends.tmux.plugin import TmuxPlugin
from waypoint.git_meta import GitMeta
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import (
    CommandCompletion,
    CompletionDispatch,
    EventRecord,
    LaunchMode,
    SessionCreateRequest,
    SessionInputRequest,
    SessionRateLimitUsage,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.codex")


class CodexPluginConfig(PluginConfig):
    """Codex plugin configuration block.

    Codex discovers its model catalogue at runtime via ``model/list``,
    so no static model list lives here — only the per-plugin defaults
    inherited from :class:`PluginConfig`. Adds ``config_overrides`` —
    ``key=value`` strings forwarded to ``codex --config K=V``.
    """

    config_overrides: list[str] = Field(default_factory=list)


class CodexLaunchTargetConfig(PluginLaunchTargetConfig):
    """Per-target Codex configuration.

    Adds ``config_overrides`` — a list of ``key=value`` strings fed
    through to the remote ``codex --config K=V`` flag (typically used
    to pin reasoning effort or model on a specific dev box).
    """

    config_overrides: list[str] = Field(default_factory=list)


class CodexPlugin:
    id = "codex"
    transport_id = "codex_app_server"
    label = "Codex"
    import_request_schema: type[BaseModel] | None = CodexThreadImportRequest
    config_schema: type[PluginConfig] = CodexPluginConfig
    launch_target_schema: type[PluginLaunchTargetConfig] = CodexLaunchTargetConfig
    extra_env: dict[str, str] = {}
    capabilities = BackendCapabilities(
        is_structured=True,
        supports_resume=False,
        supports_reattach_after_exit=True,
        supports_set_model_inline=True,
        supports_set_effort_inline=True,
        supports_set_permission_mode_inline=True,
        supports_thread_discovery=True,
        supports_thread_import=True,
        supports_fork=True,
        supports_plan_approval=True,
        supports_slash_compact=True,
        supports_approval_note=False,
        supports_custom_cli_args=True,
        supports_config_overrides=True,
        # Codex honours "approve for session" on tool approvals
        # (adapter._map_decision) but has no persistent "always" decision.
        approval_decisions=("approve", "acceptForSession", "decline"),
        permission_modes=CODEX_PERMISSION_MODE_SPECS,
        effort_levels=(),  # discovered per-model from `model/list`
        model_source=ModelSource.LIVE_RPC,
        slash_commands=(
            SlashCommandSpec(name="status", description="Show session status"),
            SlashCommandSpec(name="compact", description="Compact the current thread"),
            SlashCommandSpec(
                name="plan",
                description="Switch to Codex Plan mode or plan the provided prompt",
                argument_hint="[prompt]",
            ),
        ),
        badges={"glyph": "X", "color": "#34d399"},
        cli_binary="codex",
        target_aliases=("codex",),
    )

    def __init__(self) -> None:
        self.adapter: CodexAppServerAdapter | None = None

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        # Lazy to avoid the same import-cycle pattern documented in
        # `backends/claude_code/plugin.py`.
        from waypoint.backends.codex.transport import CodexTransport

        return CodexTransport(runtime, self)

    def setup(self, runtime: "SessionRuntime") -> None:
        # Codex's adapter wires straight to the App Server SDK with no
        # external bootstrap. The plugin owns the adapter so every call
        # site reads ``self.adapter`` instead of reaching into the
        # runtime.
        self.adapter = CodexAppServerAdapter(
            runtime._emit_adapter_event,
            runtime.session_update_callback(),
        )

    async def shutdown(self, runtime: "SessionRuntime") -> None:
        if self.adapter is not None:
            await self.adapter.shutdown()
            self.adapter = None

    def _require_adapter(self) -> CodexAppServerAdapter:
        assert self.adapter is not None, "codex plugin adapter not initialized"
        return self.adapter

    async def _register_local_rate_limit_probe(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        cwd: str,
    ) -> None:
        if self.adapter is None:
            return
        binary = (
            self._config(runtime).local_bin or self.capabilities.cli_binary or "codex"
        )

        async def _probe() -> SessionRateLimitUsage | None:
            return await probe_codex_status(cwd=cwd, binary=binary)

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
        binary = self.remote_executable(launch_target) or "codex"

        async def _probe() -> SessionRateLimitUsage | None:
            return await probe_codex_usage_remote(launch_target, binary=binary)

        await self.adapter.register_rate_limit_probe(
            session_id, _probe, refresh_interval_seconds=300.0
        )

    async def _register_rate_limit_probe(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        cwd: str,
        launch_target: SshLaunchTargetConfig | None,
    ) -> None:
        if launch_target is None:
            await self._register_local_rate_limit_probe(runtime, session_id, cwd)
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
        await self._register_rate_limit_probe(
            runtime, session.id, session.cwd, launch_target
        )
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
        cwd: str,
    ) -> SessionRateLimitUsage | None:
        """Fetch the account's current rate-limit snapshot without a session.

        Exposed so the tmux fallback can populate ``rate_limit_usage`` for
        wrapped-codex sessions without wiring them through the structured
        adapter. ``cwd`` is forwarded to the local probe because codex's
        ``/status`` PTY fallback needs a working directory.
        """
        if launch_target is None:
            binary = (
                self._config(runtime).local_bin
                or self.capabilities.cli_binary
                or "codex"
            )
            return await probe_codex_status(cwd=cwd, binary=binary)
        binary = self.remote_executable(launch_target) or "codex"
        return await probe_codex_usage_remote(launch_target, binary=binary)

    def register_routes(self, app: Any, context: Any) -> None:
        return None

    def is_available_for_managed_launch(self, runtime: "SessionRuntime") -> bool:
        return True

    def remote_executable(self, launch_target: SshLaunchTargetConfig) -> str:
        return launch_target.remote_bin_for(self.id, self.capabilities.cli_binary) or ""

    async def terminate_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        # Soft path: runtime.terminate routes through here, so a session
        # whose adapter setup never completed (or was torn down during
        # shutdown) must still be disposable. Mirrors the opencode and
        # claude_code behaviour.
        if self.adapter is not None:
            await self.adapter.terminate_session(session.id)

    def native_thread_id(self, session: SessionRecord) -> str | None:
        thread_id = session.transport_state.get("thread_id")
        return thread_id if isinstance(thread_id, str) else None

    def on_session_deleted(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        return None

    def validate_permission_mode(self, mode: str | None) -> str | None:
        if mode is None or mode == "":
            return None
        if mode not in CODEX_PERMISSION_MODE_IDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"unsupported {self.id} permission mode: {mode}; "
                    f"expected one of {', '.join(CODEX_PERMISSION_MODE_IDS)}"
                ),
            )
        return mode

    @property
    def permission_mode_ids(self) -> tuple[str, ...]:
        return CODEX_PERMISSION_MODE_IDS

    def _session_model_for_turn(self, session: SessionRecord) -> str | None:
        if session.model:
            return session.model
        if self.adapter is None:
            return None
        session_model = getattr(self.adapter, "session_model", None)
        if not callable(session_model):
            return None
        return session_model(session.id)

    def _session_effort_for_turn(self, session: SessionRecord) -> str | None:
        if session.effort:
            return session.effort
        if self.adapter is None:
            return None
        session_effort = getattr(self.adapter, "session_effort", None)
        if not callable(session_effort):
            return None
        return session_effort(session.id)

    def turn_params_for(self, session: SessionRecord) -> dict[str, Any] | None:
        return codex_turn_params_for(
            session.permission_mode,
            model=self._session_model_for_turn(session),
            effort=self._session_effort_for_turn(session),
            pre_plan_mode=session.transport_state.get("pre_plan_mode"),
        )

    async def apply_permission_mode(
        self, runtime: "SessionRuntime", session: SessionRecord, mode: str
    ) -> None:
        if mode == CODEX_PLAN_MODE:
            pre_plan_mode = (
                session.permission_mode
                if session.permission_mode != CODEX_PLAN_MODE
                else session.transport_state.get("pre_plan_mode")
            )
            if pre_plan_mode not in CODEX_PERMISSION_PRESETS:
                pre_plan_mode = "default"
            state = {**session.transport_state, "pre_plan_mode": pre_plan_mode}
            runtime.storage.update_session(session.id, transport_state=state)
            return None
        if session.transport_state.get("pre_plan_mode") is not None:
            state = dict(session.transport_state)
            state.pop("pre_plan_mode", None)
            runtime.storage.update_session(session.id, transport_state=state)
        # Codex applies on next turn_start — no protocol round-trip here.
        return None

    async def apply_model(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        model: str | None,
    ) -> None:
        try:
            await self._require_adapter().set_model(session.id, model)
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
        try:
            await self._require_adapter().set_effort(session.id, effort)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        return False  # Codex doesn't surface a system-note for the swap

    def effort_swap_message(self, effort: str | None) -> str:
        return ""  # never published; apply_effort returns False

    async def answer_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        answer: str,
        tool_use_id: str | None,
        answers: list[dict[str, Any]] | None,
    ) -> SessionRecord:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="answer-question is only supported for Claude sessions",
        )

    async def post_approval(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        return None

    async def approve_plan(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        plan_item_id: str,
        decision: str,
        text: str | None,
    ) -> SessionRecord:
        if session.permission_mode != CODEX_PLAN_MODE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="codex session is not in plan mode",
            )
        if decision not in _PLAN_DECISIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unsupported plan decision: {decision}",
            )

        plan = _current_plan_text_for_item(runtime, session.id, plan_item_id)
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="codex plan item was not found or is no longer current",
            )

        accept = decision in {"accept", "acceptForSession"}
        # accept: exit plan mode, send approval prompt under restored mode.
        # decline / cancel: stay in plan mode, send rejection prompt under plan mode.
        if accept:
            target_mode = session.transport_state.get("pre_plan_mode")
            if target_mode not in CODEX_PERMISSION_PRESETS:
                target_mode = "default"
            prompt = _format_plan_approval_prompt(plan, text)
        else:
            target_mode = CODEX_PLAN_MODE
            prompt = _format_plan_rejection_prompt(decision, text)

        previous_status = session.status
        runtime.storage.update_session(session.id, status=SessionStatus.RUNNING)
        try:
            await self._require_adapter().send_input(
                session.id,
                prompt,
                turn_params=codex_turn_params_for(
                    target_mode,
                    model=self._session_model_for_turn(session),
                    effort=self._session_effort_for_turn(session),
                    pre_plan_mode=session.transport_state.get("pre_plan_mode"),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            runtime.storage.update_session(session.id, status=previous_status)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        if accept:
            await runtime.set_permission_mode(session.id, target_mode)
            running = runtime.storage.update_session(
                session.id, status=SessionStatus.RUNNING
            )
            await runtime._record_system_event(
                session.id,
                f"Plan approved; exited plan mode ({target_mode})",
                status=SessionStatus.RUNNING,
                metadata={"plan_item_id": plan_item_id, "plan_decision": decision},
            )
            return running

        running = runtime.storage.update_session(
            session.id, status=SessionStatus.RUNNING
        )
        rejection_label = "declined" if decision == "decline" else "cancelled"
        await runtime._record_system_event(
            session.id,
            f"Plan {rejection_label}; staying in plan mode",
            status=SessionStatus.RUNNING,
            metadata={"plan_item_id": plan_item_id, "plan_decision": decision},
        )
        return running

    async def maybe_handle_input(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        request: SessionInputRequest,
    ) -> SessionRecord | None:
        invocation = request.command
        if (
            invocation is not None
            and invocation.dispatch == CompletionDispatch.STRUCTURED_SKILL
        ):
            path = invocation.metadata.get("path")
            if not isinstance(path, str) or not path:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="selected Codex skill is missing a SKILL.md path",
                )
            items: list[dict[str, Any]] = [
                {"type": "skill", "name": invocation.name, "path": path}
            ]
            if invocation.arguments:
                items.append({"type": "text", "text": invocation.arguments})
            previous_status = session.status
            updated = runtime.storage.update_session(
                session.id, status=SessionStatus.RUNNING
            )
            await runtime._record_user_event(
                session.id,
                request.text,
                submit=request.submit,
                status=session.status,
            )
            try:
                await self._require_adapter().send_input_items(
                    session.id,
                    items,
                    turn_params=self.turn_params_for(updated),
                )
            except Exception as exc:  # noqa: BLE001
                runtime.storage.update_session(session.id, status=previous_status)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
            return updated

        command = request.text.strip()
        command_parts = command.split(None, 1)
        command_name = command_parts[0].lower() if command_parts else ""
        if command_name == "/status":
            await runtime._record_user_event(
                session.id,
                request.text,
                submit=request.submit,
                status=session.status,
            )
            await runtime._record_system_event(
                session.id,
                _format_codex_status(session),
                status=session.status,
                metadata={"builtin_command": "/status"},
            )
            return runtime.get_session(session.id)

        if command_name == "/plan":
            plan_prompt = command_parts[1].strip() if len(command_parts) > 1 else ""
            updated = await runtime.set_permission_mode(session.id, CODEX_PLAN_MODE)
            await runtime._record_user_event(
                session.id,
                request.text,
                submit=request.submit,
                status=session.status,
            )
            if not plan_prompt:
                await runtime._record_system_event(
                    session.id,
                    "Switched Codex to plan mode",
                    status=session.status,
                    metadata={"builtin_command": "/plan"},
                )
                return updated

            previous_status = updated.status
            running = runtime.storage.update_session(
                session.id, status=SessionStatus.RUNNING
            )
            try:
                await self._require_adapter().send_input(
                    session.id,
                    plan_prompt,
                    turn_params=self.turn_params_for(running),
                )
            except Exception as exc:  # noqa: BLE001
                runtime.storage.update_session(session.id, status=previous_status)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                ) from exc
            return running

        # Codex's app-server doesn't parse user text as control commands.
        # `/compact` takes effect through the thread/compact/start RPC; other
        # slash commands are left to the model unless Waypoint handles them
        # explicitly above.
        if command_name != "/compact":
            return None
        try:
            await self._require_adapter().compact_thread(session.id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        await runtime._record_user_event(
            session.id,
            request.text,
            submit=request.submit,
            status=session.status,
        )
        await runtime._record_system_event(
            session.id,
            "Compacting codex thread…",
            status=SessionStatus.RUNNING,
            metadata={"builtin_command": "/compact"},
        )
        return runtime.storage.update_session(session.id, status=SessionStatus.RUNNING)

    async def fork_session(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        new_session_id: str,
        title: str,
        raw_log: Path,
        structured_log: Path,
    ) -> SessionRecord:
        thread_id = session.transport_state.get("thread_id")
        if not thread_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="codex session has no thread id to fork from",
            )

        if (
            session.launch_target_id
            and runtime._find_launch_target(session.launch_target_id) is None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"codex session launch target {session.launch_target_id} is no longer configured",
            )

        effective_cli_args = self._effective_args(
            runtime, session.launch_target_id, session.args
        )
        effective_config_overrides = self._effective_config_overrides(
            runtime, session.launch_target_id, session.config_overrides
        )

        try:
            new_thread_id = await self._require_adapter().fork_session(
                new_session_id,
                session.cwd,
                thread_id,
                self.client_factory(
                    runtime,
                    session.launch_target_id,
                    custom_args=effective_cli_args,
                    custom_config_overrides=effective_config_overrides,
                ),
                model=session.model,
                effort=session.effort,
                custom_args=effective_cli_args,
                config_overrides=effective_config_overrides,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "codex fork failed",
                extra={
                    "session_id": session.id,
                    "thread_id": thread_id,
                    "cwd": session.cwd,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        now = datetime.now(UTC)
        raw_log.touch(exist_ok=True)
        forked_transport_state: dict[str, Any] = {"thread_id": new_thread_id}
        pre_plan_mode = session.transport_state.get("pre_plan_mode")
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
        )
        runtime.storage.create_session(new_session)
        runtime.storage.clone_events(session.id, new_session_id)
        await self._register_rate_limit_probe(
            runtime,
            new_session_id,
            session.cwd,
            runtime._find_launch_target(session.launch_target_id),
        )
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
        thread_id = session.transport_state.get("thread_id")
        if not thread_id:
            runtime.storage.update_session(session.id, status=SessionStatus.EXITED)
            await runtime._record_system_event(
                session.id,
                "Codex session has no thread id; marking exited",
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
                f"Codex session launch target {session.launch_target_id} is no longer configured",
                status=SessionStatus.ERROR,
            )
            return
        effective_cli_args = self._effective_args(
            runtime, session.launch_target_id, session.args
        )
        effective_config_overrides = self._effective_config_overrides(
            runtime, session.launch_target_id, session.config_overrides
        )
        try:
            await self._require_adapter().restore_session(
                session.id,
                session.cwd,
                thread_id,
                self.client_factory(
                    runtime,
                    session.launch_target_id,
                    custom_args=effective_cli_args,
                    custom_config_overrides=effective_config_overrides,
                ),
                model=session.model,
                effort=session.effort,
                custom_args=effective_cli_args,
                config_overrides=effective_config_overrides,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "codex restore failed",
                extra={
                    "session_id": session.id,
                    "thread_id": thread_id,
                    "cwd": session.cwd,
                },
            )
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"Codex session restore failed: {exc}",
                status=SessionStatus.ERROR,
            )
            return
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await self._register_rate_limit_probe(
            runtime,
            session.id,
            session.cwd,
            runtime._find_launch_target(session.launch_target_id),
        )
        await runtime._record_system_event(
            session.id,
            self.format_restore_message(runtime, session.cwd, session.launch_target_id),
            status=SessionStatus.IDLE,
        )

    def format_start_message(
        self,
        cwd: str | None,
        launch_target: SshLaunchTargetConfig | None,
    ) -> str:
        if launch_target is not None:
            return (
                f"Codex app-server session started via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd or launch_target.default_cwd})"
            )
        return "Codex app-server session started"

    def format_restore_message(
        self,
        runtime: "SessionRuntime",
        cwd: str | None,
        launch_target_id: str | None,
    ) -> str:
        launch_target = runtime._find_launch_target(launch_target_id)
        if launch_target is not None:
            return (
                f"Codex session restored via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd or launch_target.default_cwd})"
            )
        return "Codex session restored from previous backend process"

    def format_import_message(
        self,
        cwd: str,
        launch_target: SshLaunchTargetConfig | None,
    ) -> str:
        if launch_target is not None:
            return (
                f"Imported stored Codex thread via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd})"
            )
        return f"Imported stored Codex thread ({cwd})"

    # --- launch / discovery helpers ----------------------------------

    def _config(self, runtime: "SessionRuntime") -> CodexPluginConfig:
        config = runtime.settings.plugin_config(self.id)
        assert isinstance(config, CodexPluginConfig)
        return config

    def _effective_args(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None,
        custom_args: list[str],
    ) -> list[str]:
        """Yaml-derived raw cli_args (target if set, else global) + per-session args."""
        if launch_target_id:
            launch_target = runtime._find_launch_target(launch_target_id)
            if launch_target:
                target_config = launch_target.plugin_config(self.id)
                if target_config:
                    return list(target_config.cli_args) + list(custom_args)
            return list(custom_args)
        return list(self._config(runtime).cli_args) + list(custom_args)

    def _effective_config_overrides(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None,
        custom_overrides: list[str],
    ) -> list[str]:
        """Yaml-derived ``--config K=V`` overrides + per-session overrides."""
        if launch_target_id:
            launch_target = runtime._find_launch_target(launch_target_id)
            if launch_target:
                target_config = launch_target.plugin_config(self.id)
                if target_config is not None and isinstance(
                    target_config, CodexLaunchTargetConfig
                ):
                    return list(target_config.config_overrides) + list(custom_overrides)
            return list(custom_overrides)
        return list(self._config(runtime).config_overrides) + list(custom_overrides)

    def client_factory(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None,
        custom_args: list[str] | None = None,
        custom_config_overrides: list[str] | None = None,
    ) -> ClientFactory | None:
        launch_target = runtime._find_launch_target(launch_target_id)
        if launch_target is None:
            return None
        return build_remote_codex_client_factory(
            launch_target,
            cli_args=tuple(custom_args or ()),
            config_overrides=tuple(custom_config_overrides or ()),
        )

    def client_cwd(
        self, runtime: "SessionRuntime", launch_target_id: str | None
    ) -> str:
        launch_target = runtime._find_launch_target(launch_target_id)
        if launch_target is not None:
            return launch_target.default_cwd
        return str(Path(runtime.settings.default_cwd).expanduser())

    async def run_client_operation(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None,
        operation: Callable[[CodexClient], Awaitable[Any]],
    ) -> Any:
        default_cwd = self.client_cwd(runtime, launch_target_id)
        client_factory: ClientFactory = (
            self.client_factory(runtime, launch_target_id) or default_client_factory
        )
        client = client_factory(default_cwd, _deny_approval)
        try:
            await asyncio.to_thread(client.start)
            await asyncio.to_thread(client.initialize)
            return await operation(client)
        finally:
            with suppress(Exception):
                await asyncio.to_thread(client.close)

    async def _read_thread(
        self,
        runtime: "SessionRuntime",
        thread_id: str,
        launch_target_id: str | None,
    ) -> Any:
        runtime._resolve_launch_target(launch_target_id, self.id)

        async def operation(client: CodexClient) -> Any:
            response = await asyncio.to_thread(client.thread_read, thread_id, False)
            return response.thread

        try:
            return await self.run_client_operation(
                runtime, launch_target_id, operation=operation
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to read codex thread: {exc}",
            ) from exc

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

    def _thread_summary(self, thread: Any) -> CodexThreadSummary:
        cwd = _thread_cwd(thread)
        return CodexThreadSummary(
            id=thread.id,
            title=_thread_title(thread),
            cwd=cwd,
            repo_name=_thread_repo_name(thread),
            branch=_thread_branch(thread),
            preview=(thread.preview or "").strip() or None,
            created_at=datetime.fromtimestamp(thread.created_at, UTC),
            updated_at=datetime.fromtimestamp(thread.updated_at, UTC),
        )

    async def list_threads(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
    ) -> list[CodexThreadSummary]:
        runtime._resolve_launch_target(launch_target_id, self.id)
        imported: set[tuple[str | None, str]] = set()
        for session in runtime.storage.list_sessions():
            if session.backend != self.id:
                continue
            thread_id = session.transport_state.get("thread_id")
            if not thread_id:
                continue
            imported.add((session.launch_target_id, thread_id))

        async def operation(client: CodexClient) -> list[Any]:
            threads: list[Any] = []
            cursor: str | None = None
            while True:
                response = await asyncio.to_thread(
                    client.thread_list,
                    {"archived": False, "cursor": cursor, "limit": 100},
                )
                threads.extend(response.data)
                if response.next_cursor is None:
                    return threads
                cursor = response.next_cursor

        threads = await self.run_client_operation(
            runtime, launch_target_id, operation=operation
        )
        summaries = [
            self._thread_summary(thread)
            for thread in threads
            if not thread.ephemeral and (launch_target_id, thread.id) not in imported
        ]
        return sorted(summaries, key=lambda thread: thread.updated_at, reverse=True)

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
            permission_mode=permission_mode,
            model=resolved_model,
            effort=resolved_effort,
            args=list(request.args),
            config_overrides=list(request.config_overrides),
        )
        runtime.storage.create_session(session)
        effective_cli_args = self._effective_args(
            runtime, session.launch_target_id, request.args
        )
        effective_config_overrides = self._effective_config_overrides(
            runtime, session.launch_target_id, request.config_overrides
        )
        try:
            thread_id = await self._require_adapter().start_session(
                session_id,
                request.cwd,
                self.client_factory(
                    runtime,
                    session.launch_target_id,
                    custom_args=effective_cli_args,
                    custom_config_overrides=effective_config_overrides,
                ),
                model=resolved_model,
                effort=resolved_effort,
                custom_args=effective_cli_args,
                config_overrides=effective_config_overrides,
            )
        except Exception:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            raise
        runtime.storage.update_session(
            session.id,
            transport_state={**session.transport_state, "thread_id": thread_id},
            status=SessionStatus.IDLE,
        )
        await self._register_rate_limit_probe(
            runtime, session.id, session.cwd, launch_target
        )
        await runtime._record_system_event(
            session.id,
            self.format_start_message(request.cwd, launch_target),
            status=SessionStatus.IDLE,
        )
        return runtime.get_session(session.id)

    async def import_thread(
        self,
        runtime: "SessionRuntime",
        request: CodexThreadImportRequest,
    ) -> SessionRecord:
        launch_target = runtime._resolve_launch_target(
            request.launch_target_id, self.id
        )
        existing = self._find_imported_session(
            runtime, request.thread_id, request.launch_target_id
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="codex thread already imported",
            )
        thread = await self._read_thread(
            runtime, request.thread_id, request.launch_target_id
        )
        if thread.ephemeral:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ephemeral codex threads cannot be imported",
            )
        # Launch-mode dispatch mirrors create_session: TMUX_WRAPPER
        # always delegates; AUTO falls through when the structured
        # plugin isn't available for managed launch. DIRECT runs the
        # existing structured-resume path below.
        if request.launch_mode == LaunchMode.TMUX_WRAPPER or (
            request.launch_mode == LaunchMode.AUTO
            and not self.is_available_for_managed_launch(runtime)
        ):
            fallback = runtime.registry.fallback_for_managed_launch()
            if not isinstance(fallback, TmuxPlugin):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="tmux fallback launch is not available",
                )
            return await fallback.import_thread_via_resume(
                runtime,
                backend=self.id,
                thread_id=request.thread_id,
                cwd=_thread_cwd(thread),
                launch_target_id=request.launch_target_id,
                title=_thread_title(thread),
            )
        session_id = runtime._generate_session_id(self.id)
        session_dir = runtime._session_dir(session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"
        raw_log.touch(exist_ok=True)
        cwd = _thread_cwd(thread)
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=self.id,
            source=SessionSource.MANAGED,
            transport=self.transport_id,
            title=_thread_title(thread),
            cwd=cwd,
            launch_target_id=launch_target.id if launch_target else None,
            repo_name=_thread_repo_name(thread),
            branch=_thread_branch(thread),
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"thread_id": thread.id},
            permission_mode="default",
        )
        runtime.storage.create_session(session)
        # Imported sessions start with no per-session args/config_overrides;
        # the effective lists are just whatever the launch target's yaml
        # specifies (`_effective_*` will fold those in).
        effective_cli_args = self._effective_args(runtime, session.launch_target_id, [])
        effective_config_overrides = self._effective_config_overrides(
            runtime, session.launch_target_id, []
        )
        try:
            await self._require_adapter().restore_session(
                session.id,
                session.cwd,
                thread.id,
                self.client_factory(
                    runtime,
                    session.launch_target_id,
                    custom_args=effective_cli_args,
                    custom_config_overrides=effective_config_overrides,
                ),
                custom_args=effective_cli_args,
                config_overrides=effective_config_overrides,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "codex import failed",
                extra={
                    "session_id": session.id,
                    "thread_id": thread.id,
                    "launch_target_id": session.launch_target_id,
                },
            )
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"Codex thread import failed: {exc}",
                status=SessionStatus.ERROR,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to import codex thread: {exc}",
            ) from exc
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await self._register_rate_limit_probe(runtime, session.id, cwd, launch_target)
        await runtime._record_system_event(
            session.id,
            self.format_import_message(cwd, launch_target),
            status=SessionStatus.IDLE,
            metadata={"imported_thread_id": thread.id},
        )
        return runtime.get_session(session.id)

    async def list_models(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        config = self._config(runtime)
        default_model = config.default_model_id
        default_effort = config.default_effort
        cwd = self.client_cwd(runtime, launch_target_id)
        try:
            response = await self._require_adapter().list_models(
                cwd=cwd,
                client_factory_override=self.client_factory(runtime, launch_target_id),
                include_hidden=include_hidden,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"codex model discovery failed: {exc}",
            ) from exc
        models: list[dict[str, Any]] = []
        for entry in response.data:
            if entry.hidden and not include_hidden:
                continue
            supported_efforts = [
                option.reasoning_effort.value
                for option in (entry.supported_reasoning_efforts or [])
            ]
            models.append(
                {
                    "id": entry.model,
                    "label": entry.display_name or entry.model,
                    "description": entry.description or None,
                    "is_default": entry.is_default,
                    "hidden": entry.hidden,
                    "supported_efforts": supported_efforts,
                    "default_effort": (
                        entry.default_reasoning_effort.value
                        if entry.default_reasoning_effort is not None
                        else None
                    ),
                }
            )
            if default_model is None and entry.is_default:
                default_model = entry.model
        default_model_label: str | None = None
        if default_model:
            for model in models:
                if model.get("id") == default_model:
                    default_model_label = model.get("label")
                    break
        return {
            "backend": self.id,
            "models": models,
            "default_model_id": default_model,
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
        if trigger == "/":
            return static_slash_completions(self.id, self.capabilities, prefix=prefix)
        if trigger != "$":
            return []
        normalized_prefix = prefix if prefix.startswith("$") else f"${prefix}"
        try:
            skills = await self._require_adapter().list_skills(
                session.id,
                force_reload=force_refresh,
            )
        except Exception:
            return []
        completions: list[CommandCompletion] = []
        seen: set[str] = set()
        for skill in skills:
            name = skill.get("name")
            if not isinstance(name, str) or not name:
                continue
            command = f"${name}"
            if normalized_prefix != "$" and not command.startswith(normalized_prefix):
                continue
            if command in seen:
                continue
            path = skill.get("path")
            if not isinstance(path, str) or not path:
                continue
            description = skill.get("shortDescription") or skill.get("description")
            completions.append(
                CommandCompletion(
                    id=f"{self.id}:skill:{name}",
                    trigger="$",
                    replacement=f"{command} ",
                    name=name,
                    description=description if isinstance(description, str) else None,
                    kind="skill",
                    source="codex_skill",
                    dispatch=CompletionDispatch.STRUCTURED_SKILL,
                    metadata={"path": path},
                )
            )
            seen.add(command)
        return completions


def _deny_approval(_method: str, _params: dict[str, Any] | None) -> dict[str, Any]:
    return {"decision": "decline"}


def _current_plan_text_for_item(
    runtime: "SessionRuntime", session_id: str, plan_item_id: str
) -> str:
    decided_plan_ids: set[str] = set()
    for event in reversed(runtime.storage.list_events(session_id)):
        if event.metadata.get("item_type") != "plan":
            decision_plan_id = event.metadata.get("plan_item_id")
            if (
                isinstance(decision_plan_id, str)
                and event.metadata.get("plan_decision") in _PLAN_DECISIONS
            ):
                decided_plan_ids.add(decision_plan_id)
            continue
        current_plan_id = _plan_id_for_event(event)
        if not current_plan_id or current_plan_id in decided_plan_ids:
            return ""
        if current_plan_id != plan_item_id:
            return ""
        text = _plan_text_for_event(event)
        if text:
            return text
    return ""


def _plan_id_for_event(event: EventRecord) -> str | None:
    plan = event.metadata.get("plan")
    if isinstance(plan, dict):
        plan_id = plan.get("id")
        if isinstance(plan_id, str) and plan_id:
            return plan_id
    item_id = event.metadata.get("item_id")
    if isinstance(item_id, str) and item_id:
        return item_id
    payload = event.metadata.get("payload")
    if isinstance(payload, dict):
        item = payload.get("item")
        if isinstance(item, dict):
            item_id = item.get("id")
            if isinstance(item_id, str) and item_id:
                return item_id
    return None


def _plan_text_for_event(event: EventRecord) -> str:
    plan = event.metadata.get("plan")
    if isinstance(plan, dict):
        text = plan.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    payload = event.metadata.get("payload")
    if not isinstance(payload, dict):
        return ""
    item = payload.get("item")
    if not isinstance(item, dict):
        return ""
    text = item.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return ""


_PLAN_DECISIONS = frozenset({"accept", "acceptForSession", "decline", "cancel"})


def _format_plan_approval_prompt(plan: str, note: str | None = None) -> str:
    lines = [
        "User has approved your plan. You can now start coding. "
        "Start with updating your todo list if applicable.",
        "",
        "## Approved Plan:",
        plan,
    ]
    if note:
        lines.extend(["", "User note:", note])
    return "\n".join(lines)


def _format_plan_rejection_prompt(decision: str, note: str | None) -> str:
    if decision == "cancel":
        intro = "User has cancelled the plan; stay in plan mode and wait for further instructions."
    else:
        intro = (
            "User has declined the plan; stay in plan mode and revise the plan "
            "based on the note below before proposing a new plan."
        )
    lines = [intro]
    if note:
        lines.extend(["", "User note:", note])
    return "\n".join(lines)


def _format_codex_status(session: SessionRecord) -> str:
    lines = [
        "Codex session status",
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
    return "\n".join(lines)


def _thread_cwd(thread: Any) -> str:
    cwd = getattr(thread, "cwd", "")
    return getattr(cwd, "root", cwd)


def _thread_title(thread: Any) -> str:
    if thread.name:
        return thread.name
    preview = (thread.preview or "").strip()
    if preview:
        return preview.splitlines()[0][:80]
    return f"Codex {Path(_thread_cwd(thread)).name or thread.id}"


def _thread_branch(thread: Any) -> str | None:
    git_info = getattr(thread, "git_info", None)
    return git_info.branch if git_info is not None else None


def _thread_repo_name(thread: Any) -> str | None:
    git_info = getattr(thread, "git_info", None)
    if git_info is not None and git_info.origin_url:
        normalized = git_info.origin_url.rstrip("/").removesuffix(".git")
        name = normalized.rsplit("/", 1)[-1]
        if name:
            return name
    return Path(_thread_cwd(thread)).name or None


def build_plugin() -> CodexPlugin:
    return CodexPlugin()


__all__ = [
    "CodexLaunchTargetConfig",
    "CodexPlugin",
    "CodexPluginConfig",
    "build_plugin",
]
