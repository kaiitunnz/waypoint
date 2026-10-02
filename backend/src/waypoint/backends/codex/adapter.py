from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from openai_codex.client import CodexClient, CodexConfig, _resolve_codex_bin
from openai_codex.generated.v2_all import ModelListResponse, SkillsListResponse

from waypoint.backends.codex._sdk_compat import start_compaction_turn
from waypoint.backends.codex.event_registry import (
    REASONING_DELTA_METHODS,
    REASONING_ITEM_KIND,
    extract_tool_name,
    is_known_method,
    persisted_item,
    render_notification,
)
from waypoint.backends.codex.normalize import (
    diff_preview_for_approval,
    diff_preview_for_notification,
    error_text,
    extract_item,
    extract_item_id,
    format_approval_text,
    is_retryable_error,
    payload_to_dict,
    plan_metadata_for_item,
    plan_todo_items,
    set_completed_outcome,
)
from waypoint.backends.codex.questions import apply_async_question
from waypoint.backends.diff_preview import DiffPreviewPayload, preview_to_metadata
from waypoint.backends.events import (
    INTERACTION_METADATA_KEY,
    InteractionEnvelope,
)
from waypoint.schemas import (
    EventKind,
    SessionContextUsage,
    SessionRateLimitUsage,
    SessionStatus,
    TokenUsageRecord,
)

log = logging.getLogger("waypoint.codex")

# How long a manual compaction may take to surface its turn before the session
# settles without progress.
COMPACTION_TURN_TIMEOUT_SECONDS = 10.0
_TOOL_RESULT_DELTA_METHODS = frozenset(
    {"item/commandExecution/outputDelta", "item/fileChange/outputDelta"}
)


class CodexCompactingError(RuntimeError):
    """Input arrived while a manual compaction owns the thread."""

    def __init__(self) -> None:
        super().__init__(
            "Codex is compacting the conversation; send again when it finishes"
        )


# Refusals for each server request when the turn is interrupted. `cancel` on
# command/file approvals also ends the turn.
_INTERRUPT_RESPONSES: dict[str, dict[str, Any]] = {
    "item/commandExecution/requestApproval": {"decision": "cancel"},
    "item/fileChange/requestApproval": {"decision": "cancel"},
    "applyPatchApproval": {"decision": "abort"},
    "execCommandApproval": {"decision": "abort"},
    "mcpServer/elicitation/request": {"action": "cancel"},
    "item/tool/requestUserInput": {"answers": {}},
    "item/permissions/requestApproval": {"permissions": {}},
}


def _interrupt_response(method: str) -> dict[str, Any]:
    return dict(_INTERRUPT_RESPONSES.get(method, {"decision": "decline"}))


ApprovalDecisionHandler = Callable[
    [str, EventKind, str, dict[str, Any], SessionStatus | None],
    Coroutine[Any, Any, None],
]
ApprovalCallback = Callable[[str, dict[str, Any] | None], dict[str, Any]]
ClientFactory = Callable[[str, ApprovalCallback], CodexClient]
SessionUpdateCallback = Callable[[str, dict[str, Any], bool], Awaitable[Any]]
TokenUsageCallback = Callable[[str, TokenUsageRecord, bool], Awaitable[Any]]


def default_client_factory(cwd: str, approval_handler: ApprovalCallback) -> CodexClient:
    return CodexClient(
        config=CodexConfig(
            cwd=cwd,
            client_name="waypoint",
            client_title="Waypoint",
        ),
        approval_handler=approval_handler,
    )


def _apply_codex_args(
    base: ClientFactory | None,
    cli_args: tuple[str, ...],
    config_overrides: tuple[str, ...],
    launch_env: dict[str, str] | None = None,
    local_bin: str | None = None,
) -> ClientFactory | None:
    """Wrap *base* so it injects *cli_args* / *config_overrides* into local launches.

    Remote factories use ``launch_args_override`` (the entire SSH command),
    so the lists were already baked in by ``build_remote_codex_client_factory``
    before this function is called — return remote factories as-is.

    Local launches normally use ``CodexConfig`` (which only exposes a
    ``config_overrides`` slot, not raw flags). When *cli_args* is non-empty
    we have to fall back to ``launch_args_override`` and assemble the argv
    ourselves so the raw flags reach codex; otherwise we use the simpler
    ``config_overrides`` slot.

    *local_bin* is the resolved absolute path to a system codex binary
    (already existence-checked by the caller). ``None`` means "use the binary
    bundled with the pinned openai-codex SDK". A non-None value forces the
    local factory to be built even with no other args so the configured
    binary is honored.
    """
    if not cli_args and not config_overrides and not launch_env and not local_bin:
        return base
    if base is not None:
        # Remote factory — args were baked in at construction; return as-is.
        return base

    def _local(cwd: str, approval_handler: ApprovalCallback) -> CodexClient:
        if cli_args:
            argv: list[str] = [local_bin or str(_resolve_codex_bin(CodexConfig()))]
            argv.extend(cli_args)
            for kv in config_overrides:
                argv.extend(["--config", kv])
            argv.extend(["app-server", "--listen", "stdio://"])
            return CodexClient(
                config=CodexConfig(
                    launch_args_override=tuple(argv),
                    cwd=cwd,
                    env=launch_env,
                    client_name="waypoint",
                    client_title="Waypoint",
                ),
                approval_handler=approval_handler,
            )
        return CodexClient(
            config=CodexConfig(
                codex_bin=local_bin,
                cwd=cwd,
                client_name="waypoint",
                client_title="Waypoint",
                config_overrides=config_overrides,
                env=launch_env,
            ),
            approval_handler=approval_handler,
        )

    return _local


@dataclass
class PendingApproval:
    method: str
    params: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None


@dataclass
class CodexSessionState:
    session_id: str
    cwd: str
    client: CodexClient
    # Serializes Waypoint-initiated requests. Notification reads skip it: the
    # SDK routes responses on its own reader thread, so a stream parked on the
    # next notification must not block a steer or interrupt.
    request_lock: asyncio.Lock
    thread_id: str
    active_turn_id: str | None = None
    stream_task: asyncio.Task[None] | None = None
    pending_approval: PendingApproval | None = None
    # Async question ids the active turn asked.
    turn_question_ids: set[str] = field(default_factory=set)
    # Set while an interrupt is in flight; read on the SDK reader thread.
    interrupting: bool = False
    streamed_tool_result_ids: set[str] = field(default_factory=set)
    streamed_reasoning_ids: set[str] = field(default_factory=set)
    # Drains notifications that carry no turn id for the client's lifetime.
    notification_task: asyncio.Task[None] | None = None
    # A manual compaction owns the thread from /compact until its turn ends.
    compacting: bool = False
    # An interrupt that arrived before the compaction turn's id was known.
    interrupt_pending: bool = False
    unknown_methods_logged: set[str] = field(default_factory=set)
    file_diff_previews: dict[str, DiffPreviewPayload] = field(default_factory=dict)
    # Most recent model selection. Codex's protocol exposes model as a per-turn
    # override that persists, so we apply it on every turn_start to keep the
    # waypoint contract — "set once, apply going forward" — even across
    # restarts.
    model: str | None = None
    # Same shape as `model` for reasoning-effort: re-emit on each turn_start so
    # the override survives restarts and turn reuse.
    effort: str | None = None
    context_usage_signature: (
        tuple[int, int | None, tuple[tuple[str, int], ...]] | None
    ) = None
    rate_limit_usage_snapshot: SessionRateLimitUsage | None = None
    rate_limit_usage_signature: str | None = None
    rate_limit_probe: Callable[[], Awaitable[SessionRateLimitUsage | None]] | None = (
        None
    )
    rate_limit_refresh_task: asyncio.Task[None] | None = None


class CodexAppServerAdapter:
    def __init__(
        self,
        emit_event: ApprovalDecisionHandler,
        on_session_update: SessionUpdateCallback | None = None,
        on_token_usage: TokenUsageCallback | None = None,
        client_factory: ClientFactory | None = None,
        open_question_ids: Callable[[str], set[str]] | None = None,
    ) -> None:
        self._emit_event = emit_event
        self._open_question_ids = open_question_ids
        self._on_session_update = on_session_update
        self._on_token_usage = on_token_usage
        self._client_factory = client_factory or default_client_factory
        self._sessions: dict[str, CodexSessionState] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start_session(
        self,
        session_id: str,
        cwd: str,
        client_factory_override: ClientFactory | None = None,
        model: str | None = None,
        effort: str | None = None,
        custom_args: list[str] | None = None,
        config_overrides: list[str] | None = None,
        launch_env: dict[str, str] | None = None,
    ) -> str:
        effective_factory = _apply_codex_args(
            client_factory_override,
            tuple(custom_args or []),
            tuple(config_overrides or []),
            launch_env or {},
        )
        state = await self._spawn_session(
            session_id,
            cwd,
            client_factory_override=effective_factory,
            model=model,
            effort=effort,
        )
        thread_params: dict[str, Any] = {"cwd": cwd}
        if model:
            thread_params["model"] = model
        if effort:
            # Codex SDK accepts the level under thread `config` per
            # `model_reasoning_effort`; this seeds the thread default.
            thread_params["config"] = {"model_reasoning_effort": effort}
        started = await self._call_client(
            state, state.client.thread_start, thread_params
        )
        state.thread_id = started.thread.id
        state.model = model or getattr(started, "model", None)
        return state.thread_id

    async def restore_session(
        self,
        session_id: str,
        cwd: str,
        thread_id: str,
        client_factory_override: ClientFactory | None = None,
        model: str | None = None,
        effort: str | None = None,
        custom_args: list[str] | None = None,
        config_overrides: list[str] | None = None,
        launch_env: dict[str, str] | None = None,
    ) -> None:
        effective_factory = _apply_codex_args(
            client_factory_override,
            tuple(custom_args or []),
            tuple(config_overrides or []),
            launch_env or {},
        )
        state = await self._spawn_session(
            session_id,
            cwd,
            thread_id=thread_id,
            client_factory_override=effective_factory,
            model=model,
            effort=effort,
        )
        resumed = await self._call_client(state, state.client.thread_resume, thread_id)
        state.model = model or getattr(resumed, "model", None)

    async def fork_session(
        self,
        session_id: str,
        cwd: str,
        thread_id: str,
        client_factory_override: ClientFactory | None = None,
        model: str | None = None,
        effort: str | None = None,
        custom_args: list[str] | None = None,
        config_overrides: list[str] | None = None,
        launch_env: dict[str, str] | None = None,
    ) -> str:
        effective_factory = _apply_codex_args(
            client_factory_override,
            tuple(custom_args or []),
            tuple(config_overrides or []),
            launch_env or {},
        )
        state = await self._spawn_session(
            session_id,
            cwd,
            client_factory_override=effective_factory,
            model=model,
            effort=effort,
        )
        fork_params: dict[str, Any] = {}
        if model:
            fork_params["model"] = model
        if effort:
            fork_params["config"] = {"model_reasoning_effort": effort}
        forked = await self._call_client(
            state, state.client.thread_fork, thread_id, fork_params
        )
        state.thread_id = forked.thread.id
        state.model = model or getattr(forked, "model", None)
        return state.thread_id

    async def register_rate_limit_probe(
        self,
        session_id: str,
        probe: Callable[[], Awaitable[SessionRateLimitUsage | None]],
        *,
        refresh_interval_seconds: float = 60.0,
    ) -> None:
        state = self._require_session(session_id)
        state.rate_limit_probe = probe
        if state.rate_limit_refresh_task is not None:
            state.rate_limit_refresh_task.cancel()
        state.rate_limit_refresh_task = asyncio.create_task(
            self._refresh_rate_limit_usage_loop(
                state, refresh_interval_seconds=refresh_interval_seconds
            )
        )

    async def force_refresh_rate_limit_usage(self, session_id: str) -> None:
        # User-driven path: run the registered probe inline so the caller's
        # response carries the fresh snapshot instead of racing the WS push.
        state = self._sessions.get(session_id)
        if state is None:
            return
        await self._refresh_rate_limit_usage(state)

    async def _spawn_session(
        self,
        session_id: str,
        cwd: str,
        thread_id: str = "",
        client_factory_override: ClientFactory | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> CodexSessionState:
        self._loop = asyncio.get_running_loop()
        holder: dict[str, CodexSessionState] = {}

        def approval_handler(
            method: str, params: dict[str, Any] | None
        ) -> dict[str, Any]:
            state = holder["state"]
            payload = params or {}
            if state.interrupting:
                # Parking here would hold the interrupt's response behind this
                # reader thread.
                return _interrupt_response(method)
            pending = PendingApproval(method=method, params=payload)
            state.pending_approval = pending
            item_id = payload.get("itemId")
            cached_preview = (
                state.file_diff_previews.get(item_id)
                if isinstance(item_id, str)
                else None
            )
            diff_preview = diff_preview_for_approval(method, payload, cached_preview)
            approval_text = format_approval_text(method, payload)
            interaction = InteractionEnvelope(
                kind="approval",
                request_id=(
                    str(item_id) if isinstance(item_id, str) and item_id else method
                ),
                title=approval_text,
            )

            async def emit_request() -> None:
                # An interrupt may have cancelled the approval before this ran;
                # a card recorded after its invalidation note would never clear.
                if pending.event.is_set():
                    return
                await self._emit_event(
                    state.session_id,
                    EventKind.APPROVAL_REQUEST,
                    approval_text,
                    {
                        "method": method,
                        "request": payload,
                        "status": SessionStatus.WAITING_INPUT,
                        INTERACTION_METADATA_KEY: interaction.to_metadata(),
                        **preview_to_metadata(diff_preview),
                    },
                    SessionStatus.WAITING_INPUT,
                )

            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(emit_request(), self._loop)
            pending.event.wait()
            state.pending_approval = None
            return pending.response or {"decision": "decline"}

        factory = client_factory_override or self._client_factory
        client = factory(cwd, approval_handler)
        await asyncio.to_thread(client.start)
        await asyncio.to_thread(client.initialize)
        state = CodexSessionState(
            session_id=session_id,
            cwd=cwd,
            client=client,
            request_lock=asyncio.Lock(),
            thread_id=thread_id,
            model=model,
            effort=effort,
        )
        holder["state"] = state
        self._sessions[session_id] = state
        state.notification_task = asyncio.create_task(self._pump_notifications(state))
        return state

    async def send_input(
        self,
        session_id: str,
        text: str,
        turn_params: dict[str, Any] | None = None,
    ) -> None:
        state = self._require_session(session_id)
        if state.compacting:
            raise CodexCompactingError()
        if state.active_turn_id is None:
            # turn_steer doesn't accept params in the current Codex SDK;
            # policy / reviewer / model overrides only land via turn_start.
            # Override values persist to subsequent turns per SDK semantics,
            # so we re-emit the session's model on every turn_start to keep
            # waypoint's "set once, apply going forward" contract intact even
            # after a restore.
            merged = self._build_turn_params(state, turn_params)
            if merged:
                started = await self._call_client(
                    state,
                    state.client.turn_start,
                    state.thread_id,
                    text,
                    merged,
                )
            else:
                started = await self._call_client(
                    state, state.client.turn_start, state.thread_id, text
                )
            state.active_turn_id = started.turn.id
            state.stream_task = asyncio.create_task(
                self._stream_turn(state, started.turn.id)
            )
            return
        await self._call_client(
            state, state.client.turn_steer, state.thread_id, state.active_turn_id, text
        )

    async def send_input_items(
        self,
        session_id: str,
        items: list[dict[str, Any]],
        turn_params: dict[str, Any] | None = None,
    ) -> None:
        state = self._require_session(session_id)
        if state.compacting:
            raise CodexCompactingError()
        if state.active_turn_id is None:
            merged = self._build_turn_params(state, turn_params)
            if merged:
                started = await self._call_client(
                    state,
                    state.client.turn_start,
                    state.thread_id,
                    items,
                    merged,
                )
            else:
                started = await self._call_client(
                    state, state.client.turn_start, state.thread_id, items
                )
            state.active_turn_id = started.turn.id
            state.stream_task = asyncio.create_task(
                self._stream_turn(state, started.turn.id)
            )
            return
        await self._call_client(
            state, state.client.turn_steer, state.thread_id, state.active_turn_id, items
        )

    async def list_skills(
        self,
        session_id: str,
        *,
        force_reload: bool = False,
    ) -> list[dict[str, Any]]:
        state = self._require_session(session_id)
        params: dict[str, Any] = {"cwds": [state.cwd], "forceReload": force_reload}
        response = await self._call_client(
            state,
            lambda: state.client.request(
                "skills/list",
                params,
                response_model=SkillsListResponse,
            ),
        )
        skills: list[dict[str, Any]] = []
        for entry in response.data:
            for skill in entry.skills:
                payload = skill.model_dump(mode="json", by_alias=True)
                # openai-codex 0.154 adds this optional field. Keep the
                # established Waypoint contract when the server has no plugin.
                if payload.get("pluginId") is None:
                    payload.pop("pluginId", None)
                skills.append(payload)
        return skills

    def _build_turn_params(
        self,
        state: CodexSessionState,
        caller_params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        if state.model:
            merged["model"] = state.model
        if state.effort:
            # turn_start accepts `effort` directly; the SDK forwards it as the
            # per-turn override that persists for subsequent turns.
            merged["effort"] = state.effort
        if caller_params:
            # Caller-supplied entries always win — a per-turn override beats the
            # session's sticky default.
            merged.update(caller_params)
        return merged

    async def interrupt(self, session_id: str) -> None:
        state = self._require_session(session_id)
        if state.compacting and state.active_turn_id is None:
            state.interrupt_pending = True
            return
        if state.active_turn_id is None:
            return
        state.interrupting = True
        try:
            cancelled_approval = self._cancel_pending_approval(state)
            if cancelled_approval:
                await self._emit_event(
                    state.session_id,
                    EventKind.SYSTEM_NOTE,
                    "Pending approval cancelled by interrupt",
                    {"method": "approval.invalidated", "status": SessionStatus.RUNNING},
                    SessionStatus.RUNNING,
                )
            try:
                await self._call_client(
                    state,
                    state.client.turn_interrupt,
                    state.thread_id,
                    state.active_turn_id,
                )
            except Exception:
                # Cancelling a command/file approval already ends the turn, so the
                # interrupt can race it and find no active turn.
                if not cancelled_approval:
                    raise
                log.debug(
                    "codex turn_interrupt after approval cancel failed",
                    exc_info=True,
                    extra={"session_id": session_id},
                )
        finally:
            state.interrupting = False

    def _cancel_pending_approval(self, state: CodexSessionState) -> bool:
        """The SDK runs the approval handler on its only reader thread, so
        while one is open no response is routed: not the interrupt's, and not
        that of a steer holding the request lock."""
        pending = state.pending_approval
        if pending is None:
            return False
        pending.response = _interrupt_response(pending.method)
        state.pending_approval = None
        pending.event.set()
        return True

    def is_busy(self, session_id: str) -> bool:
        """A turn or a manual compaction owns the thread."""
        state = self._require_session(session_id)
        return state.active_turn_id is not None or state.compacting

    async def compact_thread(self, session_id: str) -> None:
        """Start a manual compaction and stream it as a turn.

        Returns once the compaction is queued; its progress, the "Context
        compacted" note, and the settle to idle arrive through the stream.
        """
        state = self._require_session(session_id)
        if state.active_turn_id is not None:
            raise RuntimeError(
                "cannot compact while a codex turn is active; interrupt first"
            )
        if state.compacting:
            raise RuntimeError("codex is already compacting this thread")
        state.compacting = True
        state.stream_task = asyncio.create_task(self._run_compaction(state))

    async def _run_compaction(self, state: CodexSessionState) -> None:
        try:
            turn_id = await asyncio.to_thread(
                start_compaction_turn,
                state.client,
                state.thread_id,
                COMPACTION_TURN_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _end_turn(state)
            log.exception(
                "codex compaction failed to start",
                extra={"session_id": state.session_id, "thread_id": state.thread_id},
            )
            await self._emit_event(
                state.session_id,
                EventKind.SYSTEM_NOTE,
                f"Codex compaction failed: {exc}",
                {"status": SessionStatus.IDLE},
                SessionStatus.IDLE,
            )
            return
        if turn_id is None:
            _end_turn(state)
            await self._emit_event(
                state.session_id,
                EventKind.SYSTEM_NOTE,
                "Compaction started; progress is unavailable",
                {"status": SessionStatus.IDLE},
                SessionStatus.IDLE,
            )
            return
        state.active_turn_id = turn_id
        if state.interrupt_pending:
            state.interrupt_pending = False
            try:
                await self._call_client(
                    state, state.client.turn_interrupt, state.thread_id, turn_id
                )
            except Exception:  # noqa: BLE001
                log.debug(
                    "codex compaction interrupt failed",
                    exc_info=True,
                    extra={"session_id": state.session_id},
                )
        await self._stream_turn(state, turn_id)

    async def set_model(self, session_id: str, model: str | None) -> None:
        """Update the session's sticky model.

        Codex's protocol exposes model as a per-turn override that persists
        once set, so the actual swap lands on the next turn_start. Stored on
        the session state so subsequent turns and restores both pick it up.
        """
        state = self._require_session(session_id)
        state.model = model or None

    def session_model(self, session_id: str) -> str | None:
        state = self._sessions.get(session_id)
        return state.model if state is not None else None

    async def set_effort(self, session_id: str, effort: str | None) -> None:
        """Update the session's sticky reasoning effort.

        Same lifecycle as `set_model`: applied to the next `turn_start` and
        persisted on the session state so restores keep it.
        """
        state = self._require_session(session_id)
        state.effort = effort or None

    def session_effort(self, session_id: str) -> str | None:
        state = self._sessions.get(session_id)
        return state.effort if state is not None else None

    async def list_models(
        self,
        cwd: str = "~",
        client_factory_override: ClientFactory | None = None,
        include_hidden: bool = False,
    ) -> ModelListResponse:
        """Spawn a transient client to enumerate models for this backend.

        Codex's model_list is auth/account-scoped, so we ask the live backend
        instead of mirroring a static table. The transient client is closed
        immediately after — discovery is rare enough that the spawn cost
        (~200-500ms) is acceptable, and reusing a long-lived client risks
        racing with active sessions on the same transport.
        """
        factory = client_factory_override or self._client_factory
        client = factory(cwd, lambda method, params: {"decision": "decline"})
        try:
            await asyncio.to_thread(client.start)
            await asyncio.to_thread(client.initialize)
            return await asyncio.to_thread(client.model_list, include_hidden)
        finally:
            with suppress(Exception):
                await asyncio.to_thread(client.close)

    async def respond_to_approval(
        self, session_id: str, decision: str, text: str | None = None
    ) -> bool:
        state = self._require_session(session_id)
        pending = state.pending_approval
        if pending is None:
            return False
        pending.response = {"decision": self._map_decision(decision)}
        state.pending_approval = None
        pending.event.set()
        return True

    def has_pending_approval(self, session_id: str) -> bool:
        state = self._sessions.get(session_id)
        return bool(state and state.pending_approval is not None)

    async def shutdown(self) -> None:
        for session_id in list(self._sessions.keys()):
            await self.terminate_session(session_id)

    async def terminate_session(self, session_id: str) -> bool:
        state = self._sessions.pop(session_id, None)
        if state is None:
            return False
        if state.rate_limit_refresh_task is not None:
            state.rate_limit_refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await state.rate_limit_refresh_task
        if state.pending_approval is not None:
            state.pending_approval.response = {"decision": "decline"}
            state.pending_approval.event.set()
        # Close the client first. The streaming task is parked in an
        # uncancellable `asyncio.to_thread(next_*_notification)`, so
        # `await stream_task` cannot proceed until the blocking thread returns.
        # Closing the transport drops EOF on the codex stdio pipes, which fails
        # every pending notification read and makes the cancel observable.
        try:
            await asyncio.to_thread(state.client.close)
        except Exception:  # noqa: BLE001
            log.exception("codex client close failed", extra={"session_id": session_id})
        for task in (state.stream_task, state.notification_task):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        return True

    async def _stream_turn(self, state: CodexSessionState, turn_id: str) -> None:
        # One note per consecutive run of retryable errors; any other
        # notification ends the run.
        in_retry_streak = False
        try:
            while True:
                notification = await asyncio.to_thread(
                    state.client.next_turn_notification, turn_id
                )
                method = notification.method
                payload = payload_to_dict(notification.payload)
                if method == "error" and is_retryable_error(payload):
                    if in_retry_streak:
                        log.debug(
                            "codex retry suppressed: %s",
                            error_text(payload),
                            extra={"session_id": state.session_id},
                        )
                        continue
                    in_retry_streak = True
                else:
                    in_retry_streak = False
                if method == "thread/tokenUsage/updated":
                    snapshot = _context_usage_snapshot_from_thread_token_usage(payload)
                    if snapshot is not None:
                        await self._publish_context_usage(state, snapshot)
                        await self._publish_token_usage(state, turn_id, snapshot)
                    continue
                if method == "turn/plan/updated":
                    # Synthesize a todo_list item so the generic metadata path
                    # emits a canonical todo event (rendered in the shared
                    # dock/card). Keying the item by turnId collapses successive
                    # plan updates within a turn into one evolving card.
                    payload["item"] = {
                        "type": "todo_list",
                        "id": payload.get("turnId"),
                        "items": plan_todo_items(payload.get("plan")),
                    }
                await self._emit_notification(state, method, payload, settle=True)
                if method == "turn/completed":
                    _end_turn(state)
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _end_turn(state)
            log.exception(
                "codex stream failed",
                extra={"session_id": state.session_id, "thread_id": state.thread_id},
            )
            await self._emit_event(
                state.session_id,
                EventKind.SYSTEM_NOTE,
                f"Codex app-server stream failed: {exc}",
                {"status": SessionStatus.ERROR},
                SessionStatus.ERROR,
            )
        finally:
            with suppress(Exception):
                state.client.unregister_turn_notifications(turn_id)

    async def _pump_notifications(self, state: CodexSessionState) -> None:
        """Drain notifications that carry no turn id (warnings, config and
        deprecation notices, thread state). They never change session status."""
        while True:
            try:
                notification = await asyncio.to_thread(state.client.next_notification)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                # The client closed; every pending read fails the same way.
                return
            payload = payload_to_dict(notification.payload)
            thread_id = payload.get("threadId")
            if isinstance(thread_id, str) and thread_id != state.thread_id:
                continue
            try:
                await self._emit_notification(
                    state, notification.method, payload, settle=False
                )
            except Exception:  # noqa: BLE001
                log.exception(
                    "codex notification pump failed to emit %s",
                    notification.method,
                    extra={"session_id": state.session_id},
                )

    async def _emit_notification(
        self,
        state: CodexSessionState,
        method: str,
        payload: dict[str, Any],
        *,
        settle: bool,
    ) -> None:
        """Render one notification through the registry and emit it.

        ``settle`` is false for turn-less notifications, which emit with no
        status so they leave the session's status as stored.
        """
        if not is_known_method(method):
            self._log_unknown_method(state, method, payload)
            return
        rendered = render_notification(method, payload)
        if rendered is None or not rendered.text:
            return
        kind = rendered.kind
        status: SessionStatus | None = rendered.status
        if method == "turn/completed":
            status = self._settled_turn_status(state, rendered.status)
        diff_preview = diff_preview_for_notification(method, payload)
        metadata: dict[str, Any] = {
            "method": method,
            "payload": payload,
            **preview_to_metadata(diff_preview),
        }
        item_id = extract_item_id(payload)
        if item_id is not None:
            metadata["item_id"] = item_id
            if diff_preview is not None:
                state.file_diff_previews[item_id] = diff_preview
        item = extract_item(payload) if "item" in payload else None
        if isinstance(item, dict):
            # Store the unwrapped item (not the {"root": {...}} form) so
            # downstream consumers don't each re-implement the unwrap.
            payload["item"] = persisted_item(item)
            item_type = item.get("type")
            if isinstance(item_type, str) and item_type:
                metadata["item_type"] = item_type
            tool_name = extract_tool_name(item_type, item)
            if tool_name:
                metadata["tool_name"] = tool_name
            if method == "item/completed":
                set_completed_outcome(metadata, item_type, item)
            plan_envelope = plan_metadata_for_item(item)
            if plan_envelope is not None:
                metadata["plan"] = plan_envelope
            if apply_async_question(metadata, item):
                state.turn_question_ids.add(metadata["tool_use_id"])
        metadata.update(rendered.metadata)
        item_id = metadata.get("item_id")
        if kind == EventKind.TOOL_RESULT and method in _TOOL_RESULT_DELTA_METHODS:
            if isinstance(item_id, str):
                state.streamed_tool_result_ids.add(item_id)
        if method in REASONING_DELTA_METHODS and isinstance(item_id, str):
            state.streamed_reasoning_ids.add(item_id)
        if method == "item/completed" and item_id is not None:
            if (
                kind == EventKind.TOOL_RESULT
                and item_id in state.streamed_tool_result_ids
                and diff_preview is None
                and "is_error" not in metadata
            ):
                # An outcome-bearing completed is kept so telemetry can
                # resolve the streamed call; the frontend merges it into the
                # delta by item_id.
                return
            if (
                metadata.get("item_kind") == REASONING_ITEM_KIND
                and item_id in state.streamed_reasoning_ids
            ):
                return
        if status is not None and settle:
            metadata["status"] = status
        await self._emit_event(
            state.session_id, kind, rendered.text, metadata, status if settle else None
        )

    def _log_unknown_method(
        self, state: CodexSessionState, method: str, payload: dict[str, Any]
    ) -> None:
        if method in state.unknown_methods_logged:
            return
        state.unknown_methods_logged.add(method)
        log.warning(
            "codex notification %s has no registry entry (keys: %s)",
            method,
            ", ".join(sorted(payload)),
            extra={"session_id": state.session_id},
        )

    def _settled_turn_status(
        self, state: CodexSessionState, status: SessionStatus
    ) -> SessionStatus:
        """A turn that ends with one of its own async questions open waits on
        the human."""
        if (
            status is not SessionStatus.IDLE
            or not state.turn_question_ids
            or self._open_question_ids is None
        ):
            return status
        open_ids = self._open_question_ids(state.session_id)
        if state.turn_question_ids & open_ids:
            return SessionStatus.WAITING_INPUT
        return status

    async def _call_client(
        self, state: CodexSessionState, func: Callable[..., Any], *args: Any
    ) -> Any:
        async with state.request_lock:
            return await asyncio.to_thread(func, *args)

    async def _publish_context_usage(
        self, state: CodexSessionState, snapshot: SessionContextUsage
    ) -> None:
        # Key on the breakdown too, so a same-total/different-split turn refreshes.
        signature = (
            snapshot.used_tokens,
            snapshot.context_window_tokens,
            tuple(sorted(snapshot.breakdown.items())),
        )
        if state.context_usage_signature == signature:
            return
        state.context_usage_signature = signature
        if self._on_session_update is None:
            return
        await self._on_session_update(
            state.session_id,
            {"context_usage": snapshot.model_dump(mode="json")},
            True,
        )

    async def _publish_token_usage(
        self,
        state: CodexSessionState,
        turn_id: str,
        snapshot: SessionContextUsage,
    ) -> None:
        if self._on_token_usage is None:
            return
        record = codex_token_usage_record(
            turn_id, snapshot, model=state.model, effort=state.effort
        )
        if record is None:
            return
        await self._on_token_usage(state.session_id, record, True)

    async def _refresh_rate_limit_usage_loop(
        self, state: CodexSessionState, *, refresh_interval_seconds: float
    ) -> None:
        try:
            while state.session_id in self._sessions:
                await self._refresh_rate_limit_usage(state)
                await asyncio.sleep(refresh_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception(
                "codex rate-limit refresh loop failed",
                extra={"session_id": state.session_id},
            )

    async def _refresh_rate_limit_usage(self, state: CodexSessionState) -> None:
        probe = state.rate_limit_probe
        if probe is None:
            return
        try:
            snapshot = await probe()
        except Exception:  # noqa: BLE001
            log.exception(
                "codex rate-limit probe failed",
                extra={"session_id": state.session_id},
            )
            return
        if snapshot is None:
            return
        await self._publish_rate_limit_usage(state, snapshot)

    async def _publish_rate_limit_usage(
        self, state: CodexSessionState, snapshot: SessionRateLimitUsage
    ) -> None:
        signature = json.dumps(snapshot.model_dump(mode="json"), sort_keys=True)
        if state.rate_limit_usage_signature == signature:
            return
        state.rate_limit_usage_signature = signature
        state.rate_limit_usage_snapshot = snapshot
        if self._on_session_update is None:
            return
        await self._on_session_update(
            state.session_id,
            {"rate_limit_usage": snapshot.model_dump(mode="json")},
            True,
        )

    def _require_session(self, session_id: str) -> CodexSessionState:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise RuntimeError(f"codex session not active: {session_id}") from exc

    def _map_decision(self, decision: str) -> str:
        lowered = decision.strip()
        if lowered in {"approve", "accept", "yes", "y"}:
            return "accept"
        if lowered in {"approve_session", "acceptForSession"}:
            return "acceptForSession"
        if lowered in {"cancel"}:
            return "cancel"
        return "decline"


def _end_turn(state: CodexSessionState) -> None:
    state.active_turn_id = None
    state.stream_task = None
    state.compacting = False
    state.interrupt_pending = False
    state.turn_question_ids.clear()
    state.streamed_tool_result_ids.clear()
    state.streamed_reasoning_ids.clear()
    state.file_diff_previews.clear()


def _context_usage_snapshot_from_thread_token_usage(
    payload: dict[str, Any],
) -> SessionContextUsage | None:
    token_usage = payload.get("tokenUsage") or payload.get("token_usage")
    if not isinstance(token_usage, dict):
        return None

    last = token_usage.get("last")
    if not isinstance(last, dict):
        return None

    used_tokens = _positive_int(last.get("totalTokens"))
    if used_tokens is None:
        return None

    context_window_tokens = _positive_int(token_usage.get("modelContextWindow"))
    breakdown = {
        key: value
        for key, value in {
            "input_tokens": _positive_int(last.get("inputTokens")),
            "cached_input_tokens": _positive_int(last.get("cachedInputTokens")),
            "output_tokens": _positive_int(last.get("outputTokens")),
            "reasoning_output_tokens": _positive_int(last.get("reasoningOutputTokens")),
        }.items()
        if value is not None
    }
    return SessionContextUsage(
        used_tokens=used_tokens,
        context_window_tokens=context_window_tokens,
        updated_at=datetime.now(UTC),
        source="codex",
        breakdown=breakdown,
    )


def codex_token_usage_record(
    record_id: str,
    snapshot: SessionContextUsage,
    *,
    model: str | None = None,
    effort: str | None = None,
) -> TokenUsageRecord | None:
    """Per-turn ledger record from a Codex usage snapshot; ``None`` for an empty
    ``record_id`` (the native turn id).

    Codex categories overlap (cached input ⊆ input), so the grand total is the
    provider's ``totalTokens`` (the snapshot's ``used_tokens``), not their sum.
    ``model``/``effort`` are the resolved values in effect for this turn
    (telemetry's "actual model at turn time"); callers pass ``None`` when they
    can't be resolved, never a guess.
    """
    if not record_id:
        return None
    return TokenUsageRecord(
        record_id=record_id,
        source=snapshot.source,
        observed_at=snapshot.updated_at,
        totals=dict(snapshot.breakdown),
        display_total_tokens=snapshot.used_tokens or None,
        model=model,
        effort=effort,
    )


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer():
        int_value = int(value)
        return int_value if int_value > 0 else None
    return None
