from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from codex_app_server.client import AppServerClient, AppServerConfig
from codex_app_server.generated.v2_all import ModelListResponse, SkillsListResponse

from waypoint.backends.codex.normalize import (
    diff_preview_for_approval,
    diff_preview_for_notification,
    extract_item,
    extract_item_id,
    format_approval_text,
    map_notification,
    payload_to_dict,
    plan_metadata_for_item,
)
from waypoint.backends.diff_preview import DiffPreviewPayload, preview_to_metadata
from waypoint.schemas import (
    EventKind,
    SessionContextUsage,
    SessionRateLimitUsage,
    SessionStatus,
)

log = logging.getLogger("waypoint.codex")

ApprovalDecisionHandler = Callable[
    [str, EventKind, str, dict[str, Any], SessionStatus],
    Coroutine[Any, Any, None],
]
ApprovalCallback = Callable[[str, dict[str, Any] | None], dict[str, Any]]
ClientFactory = Callable[[str, ApprovalCallback], AppServerClient]
SessionUpdateCallback = Callable[[str, dict[str, Any], bool], Awaitable[Any]]


def _extract_tool_name(item_type: str | None, item: dict[str, Any]) -> str | None:
    """Return a canonical tool name for metadata["tool_name"] given a Codex item."""
    if item_type == "commandExecution":
        return "Bash"
    if item_type == "fileChange":
        return "Edit"
    if item_type == "mcpToolCall":
        server = item.get("server", "")
        tool = item.get("tool", "")
        if server and tool:
            return f"{server}:{tool}"
        return str(tool or server) or None
    if item_type in {"dynamicToolCall", "collabAgentToolCall"}:
        ns = item.get("namespace", "")
        tool = item.get("tool", "")
        if ns and tool:
            return f"{ns}:{tool}"
        return str(tool) if tool else None
    if item_type == "webSearch":
        return "WebSearch"
    return None


def default_client_factory(
    cwd: str, approval_handler: ApprovalCallback
) -> AppServerClient:
    codex_bin = shutil.which("codex")
    if codex_bin is None:
        raise RuntimeError("codex binary not found on PATH")
    return AppServerClient(
        config=AppServerConfig(
            codex_bin=codex_bin,
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
) -> ClientFactory | None:
    """Wrap *base* so it injects *cli_args* / *config_overrides* into local launches.

    Remote factories use ``launch_args_override`` (the entire SSH command),
    so the lists were already baked in by ``build_remote_codex_client_factory``
    before this function is called — return remote factories as-is.

    Local launches normally use ``AppServerConfig`` (which only exposes a
    ``config_overrides`` slot, not raw flags). When *cli_args* is non-empty
    we have to fall back to ``launch_args_override`` and assemble the argv
    ourselves so the raw flags reach codex; otherwise we use the simpler
    ``config_overrides`` slot.
    """
    if not cli_args and not config_overrides:
        return base
    if base is not None:
        # Remote factory — args were baked in at construction; return as-is.
        return base

    def _local(cwd: str, approval_handler: ApprovalCallback) -> AppServerClient:
        codex_bin = shutil.which("codex")
        if codex_bin is None:
            raise RuntimeError("codex binary not found on PATH")
        if cli_args:
            argv: list[str] = [codex_bin]
            argv.extend(cli_args)
            for kv in config_overrides:
                argv.extend(["--config", kv])
            argv.extend(["app-server", "--listen", "stdio://"])
            return AppServerClient(
                config=AppServerConfig(
                    launch_args_override=tuple(argv),
                    cwd=cwd,
                    client_name="waypoint",
                    client_title="Waypoint",
                ),
                approval_handler=approval_handler,
            )
        return AppServerClient(
            config=AppServerConfig(
                codex_bin=codex_bin,
                cwd=cwd,
                client_name="waypoint",
                client_title="Waypoint",
                config_overrides=config_overrides,
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
    client: AppServerClient
    transport_lock: asyncio.Lock
    thread_id: str
    active_turn_id: str | None = None
    stream_task: asyncio.Task[None] | None = None
    pending_approval: PendingApproval | None = None
    terminal_fragments: list[str] = field(default_factory=list)
    streamed_tool_result_ids: set[str] = field(default_factory=set)
    file_diff_previews: dict[str, DiffPreviewPayload] = field(default_factory=dict)
    # Most recent model selection. Codex's protocol exposes model as a per-turn
    # override that persists, so we apply it on every turn_start to keep the
    # waypoint contract — "set once, apply going forward" — even across
    # restarts.
    model: str | None = None
    # Same shape as `model` for reasoning-effort: re-emit on each turn_start so
    # the override survives restarts and turn reuse.
    effort: str | None = None
    context_usage_signature: tuple[int, int | None] | None = None
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
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._emit_event = emit_event
        self._on_session_update = on_session_update
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
    ) -> str:
        effective_factory = _apply_codex_args(
            client_factory_override,
            tuple(custom_args or []),
            tuple(config_overrides or []),
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
    ) -> None:
        effective_factory = _apply_codex_args(
            client_factory_override,
            tuple(custom_args or []),
            tuple(config_overrides or []),
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
    ) -> str:
        effective_factory = _apply_codex_args(
            client_factory_override,
            tuple(custom_args or []),
            tuple(config_overrides or []),
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
            pending = PendingApproval(method=method, params=payload)
            state.pending_approval = pending
            item_id = payload.get("itemId")
            cached_preview = (
                state.file_diff_previews.get(item_id)
                if isinstance(item_id, str)
                else None
            )
            diff_preview = diff_preview_for_approval(method, payload, cached_preview)
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self._emit_event(
                        state.session_id,
                        EventKind.APPROVAL_REQUEST,
                        format_approval_text(method, payload),
                        {
                            "method": method,
                            "request": payload,
                            "status": SessionStatus.WAITING_INPUT,
                            **preview_to_metadata(diff_preview),
                        },
                        SessionStatus.WAITING_INPUT,
                    ),
                    self._loop,
                )
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
            transport_lock=asyncio.Lock(),
            thread_id=thread_id,
            model=model,
            effort=effort,
        )
        holder["state"] = state
        self._sessions[session_id] = state
        return state

    async def send_input(
        self,
        session_id: str,
        text: str,
        turn_params: dict[str, Any] | None = None,
    ) -> None:
        state = self._require_session(session_id)
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
                skills.append(skill.model_dump(mode="json", by_alias=True))
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
        if state.active_turn_id is None:
            return
        await self._call_client(
            state, state.client.turn_interrupt, state.thread_id, state.active_turn_id
        )

    async def compact_thread(self, session_id: str) -> None:
        """Issue thread/compact/start and stream resulting notifications.

        Compaction is rejected by the codex app-server while a turn is in
        flight, so callers must interrupt first. Drains notifications until
        a `thread/compacted` (or `turn/completed`) arrives so progress events
        and the final marker show up in the transcript.
        """
        state = self._require_session(session_id)
        if state.active_turn_id is not None:
            raise RuntimeError(
                "cannot compact while a codex turn is active; interrupt first"
            )
        await self._call_client(state, state.client.thread_compact, state.thread_id)
        state.stream_task = asyncio.create_task(self._stream_compact(state))

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
        pending.event.set()
        return True

    def has_pending_approval(self, session_id: str) -> bool:
        state = self._sessions.get(session_id)
        return bool(state and state.pending_approval is not None)

    def terminal_snapshot(self, session_id: str) -> str:
        state = self._sessions.get(session_id)
        if state is None:
            return ""
        return "".join(state.terminal_fragments)

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
        # uncancellable `asyncio.to_thread(next_notification)` while holding
        # `state.transport_lock`; sending turn_interrupt would deadlock waiting
        # for the same lock, and `await stream_task` cannot proceed until the
        # blocking thread returns. Closing the transport drops EOF on the
        # codex stdio pipes, which unblocks `next_notification` and makes both
        # the lock release and the cancel observable.
        try:
            await asyncio.to_thread(state.client.close)
        except Exception:  # noqa: BLE001
            log.exception("codex client close failed", extra={"session_id": session_id})
        if state.stream_task is not None:
            state.stream_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await state.stream_task
        return True

    async def _stream_turn(self, state: CodexSessionState, turn_id: str) -> None:
        try:
            while True:
                notification = await self._call_client(
                    state, state.client.next_notification
                )
                payload = payload_to_dict(notification.payload)
                if notification.method == "thread/tokenUsage/updated":
                    snapshot = _context_usage_snapshot_from_thread_token_usage(payload)
                    if snapshot is not None:
                        await self._publish_context_usage(state, snapshot)
                    continue
                kind, text, status = map_notification(notification.method, payload)
                if kind is not None and text:
                    if notification.method == "item/commandExecution/outputDelta":
                        state.terminal_fragments.append(text)
                    diff_preview = diff_preview_for_notification(
                        notification.method, payload
                    )
                    metadata: dict[str, Any] = {
                        "method": notification.method,
                        "payload": payload,
                        "status": status,
                        **preview_to_metadata(diff_preview),
                    }
                    item_id = extract_item_id(payload)
                    if item_id is not None:
                        metadata["item_id"] = item_id
                        if diff_preview is not None:
                            state.file_diff_previews[item_id] = diff_preview
                    item = extract_item(payload) if "item" in payload else None
                    if isinstance(item, dict):
                        # Replace the wrapped {"root": {...}} form with the
                        # unwrapped item so downstream consumers (frontend
                        # transcript renderers, telemetry) don't each need to
                        # re-implement the unwrap.
                        payload["item"] = item
                        item_type = item.get("type")
                        if isinstance(item_type, str) and item_type:
                            metadata["item_type"] = item_type
                        tool_name = _extract_tool_name(item_type, item)
                        if tool_name:
                            metadata["tool_name"] = tool_name
                        plan_envelope = plan_metadata_for_item(item)
                        if plan_envelope is not None:
                            metadata["plan"] = plan_envelope
                    if (
                        kind == EventKind.TOOL_RESULT
                        and notification.method
                        in {
                            "item/commandExecution/outputDelta",
                            "item/fileChange/outputDelta",
                        }
                        and item_id
                    ):
                        state.streamed_tool_result_ids.add(item_id)
                    if (
                        kind == EventKind.TOOL_RESULT
                        and notification.method == "item/completed"
                        and item_id in state.streamed_tool_result_ids
                        and diff_preview is None
                    ):
                        continue
                    await self._emit_event(
                        state.session_id,
                        kind,
                        text,
                        metadata,
                        status,
                    )
                if notification.method == "turn/completed":
                    state.active_turn_id = None
                    state.stream_task = None
                    state.streamed_tool_result_ids.clear()
                    state.file_diff_previews.clear()
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            state.active_turn_id = None
            state.stream_task = None
            state.streamed_tool_result_ids.clear()
            state.file_diff_previews.clear()
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

    async def _stream_compact(self, state: CodexSessionState) -> None:
        try:
            while True:
                notification = await self._call_client(
                    state, state.client.next_notification
                )
                payload = payload_to_dict(notification.payload)
                kind, text, status = map_notification(notification.method, payload)
                if kind is not None and text:
                    metadata: dict[str, Any] = {
                        "method": notification.method,
                        "payload": payload,
                        "status": status,
                    }
                    item_id = extract_item_id(payload)
                    if item_id is not None:
                        metadata["item_id"] = item_id
                    await self._emit_event(
                        state.session_id, kind, text, metadata, status
                    )
                if notification.method in {"thread/compacted", "turn/completed"}:
                    state.stream_task = None
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            state.stream_task = None
            log.exception(
                "codex compact stream failed",
                extra={"session_id": state.session_id, "thread_id": state.thread_id},
            )
            await self._emit_event(
                state.session_id,
                EventKind.SYSTEM_NOTE,
                f"Codex compact stream failed: {exc}",
                {"status": SessionStatus.ERROR},
                SessionStatus.ERROR,
            )

    async def _call_client(
        self, state: CodexSessionState, func: Callable[..., Any], *args: Any
    ) -> Any:
        async with state.transport_lock:
            return await asyncio.to_thread(func, *args)

    async def _publish_context_usage(
        self, state: CodexSessionState, snapshot: SessionContextUsage
    ) -> None:
        signature = (snapshot.used_tokens, snapshot.context_window_tokens)
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


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer():
        int_value = int(value)
        return int_value if int_value > 0 else None
    return None
