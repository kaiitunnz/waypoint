from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import asdict, dataclass, field, is_dataclass
import json
import logging
from pathlib import Path
import shutil
import threading
from typing import Any, Awaitable, Callable

from codex_app_server.client import AppServerClient, AppServerConfig
from codex_app_server.generated.v2_all import AskForApprovalValue
from codex_app_server.models import Notification, UnknownNotification

from waypoint.schemas import EventKind, SessionStatus

log = logging.getLogger("waypoint.codex")

ApprovalDecisionHandler = Callable[[str, EventKind, str, dict[str, Any], SessionStatus], Awaitable[None]]
ApprovalCallback = Callable[[str, dict[str, Any] | None], dict[str, Any]]
ClientFactory = Callable[[str, str | None, ApprovalCallback], AppServerClient]


def _default_client_factory(cwd: str, remote_cwd: str | None, approval_handler: ApprovalCallback) -> AppServerClient:
    codex_bin = shutil.which("codex")
    if codex_bin is None:
        raise RuntimeError("codex binary not found on PATH")
    return AppServerClient(
        config=AppServerConfig(
            codex_bin=codex_bin,
            cwd=remote_cwd or cwd,
            client_name="waypoint",
            client_title="Waypoint",
        ),
        approval_handler=approval_handler,
    )


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


class CodexAppServerAdapter:
    def __init__(
        self,
        emit_event: ApprovalDecisionHandler,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._emit_event = emit_event
        self._client_factory = client_factory or _default_client_factory
        self._sessions: dict[str, CodexSessionState] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start_session(
        self,
        session_id: str,
        cwd: str,
        remote_cwd: str | None = None,
        client_factory_override: ClientFactory | None = None,
    ) -> str:
        state = await self._spawn_session(
            session_id,
            cwd,
            remote_cwd=remote_cwd,
            client_factory_override=client_factory_override,
        )
        started = await self._call_client(state, state.client.thread_start, {"cwd": remote_cwd or cwd})
        state.thread_id = started.thread.id
        return state.thread_id

    async def restore_session(
        self,
        session_id: str,
        cwd: str,
        thread_id: str,
        remote_cwd: str | None = None,
        client_factory_override: ClientFactory | None = None,
    ) -> None:
        state = await self._spawn_session(
            session_id,
            cwd,
            thread_id=thread_id,
            remote_cwd=remote_cwd,
            client_factory_override=client_factory_override,
        )
        await self._call_client(state, state.client.thread_resume, thread_id)

    async def _spawn_session(
        self,
        session_id: str,
        cwd: str,
        thread_id: str = "",
        remote_cwd: str | None = None,
        client_factory_override: ClientFactory | None = None,
    ) -> CodexSessionState:
        self._loop = asyncio.get_running_loop()
        holder: dict[str, CodexSessionState] = {}

        def approval_handler(method: str, params: dict[str, Any] | None) -> dict[str, Any]:
            state = holder["state"]
            payload = params or {}
            pending = PendingApproval(method=method, params=payload)
            state.pending_approval = pending
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self._emit_event(
                        state.session_id,
                        EventKind.APPROVAL_REQUEST,
                        self._format_approval_text(method, payload),
                        {
                            "method": method,
                            "request": payload,
                            "status": SessionStatus.WAITING_INPUT,
                        },
                        SessionStatus.WAITING_INPUT,
                    ),
                    self._loop,
                )
            pending.event.wait()
            state.pending_approval = None
            return pending.response or {"decision": "decline"}

        factory = client_factory_override or self._client_factory
        client = factory(cwd, remote_cwd, approval_handler)
        await asyncio.to_thread(client.start)
        await asyncio.to_thread(client.initialize)
        state = CodexSessionState(
            session_id=session_id,
            cwd=cwd,
            client=client,
            transport_lock=asyncio.Lock(),
            thread_id=thread_id,
        )
        holder["state"] = state
        self._sessions[session_id] = state
        return state

    async def send_input(self, session_id: str, text: str) -> None:
        state = self._require_session(session_id)
        if state.active_turn_id is None:
            started = await self._call_client(state, state.client.turn_start, state.thread_id, text)
            state.active_turn_id = started.turn.id
            state.stream_task = asyncio.create_task(self._stream_turn(state, started.turn.id))
            return
        await self._call_client(state, state.client.turn_steer, state.thread_id, state.active_turn_id, text)

    async def interrupt(self, session_id: str) -> None:
        state = self._require_session(session_id)
        if state.active_turn_id is None:
            return
        await self._call_client(state, state.client.turn_interrupt, state.thread_id, state.active_turn_id)

    async def respond_to_approval(self, session_id: str, decision: str) -> bool:
        state = self._require_session(session_id)
        pending = state.pending_approval
        if pending is None:
            return False
        pending.response = {"decision": self._map_decision(decision)}
        pending.event.set()
        return True

    def terminal_snapshot(self, session_id: str) -> str:
        state = self._require_session(session_id)
        return "".join(state.terminal_fragments)

    async def shutdown(self) -> None:
        for session_id in list(self._sessions.keys()):
            await self.terminate_session(session_id)

    async def terminate_session(self, session_id: str) -> bool:
        state = self._sessions.pop(session_id, None)
        if state is None:
            return False
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
                notification = await self._call_client(state, state.client.next_notification)
                payload = self._payload_to_dict(notification.payload)
                kind, text, status = self._map_notification(notification.method, payload)
                if kind is not None and text:
                    if notification.method == "item/commandExecution/outputDelta":
                        state.terminal_fragments.append(text)
                    metadata: dict[str, Any] = {
                        "method": notification.method,
                        "payload": payload,
                        "status": status,
                    }
                    item_id = self._extract_item_id(payload)
                    if item_id is not None:
                        metadata["item_id"] = item_id
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
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            state.active_turn_id = None
            state.stream_task = None
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

    async def _call_client(self, state: CodexSessionState, func: Callable[..., Any], *args: Any) -> Any:
        async with state.transport_lock:
            return await asyncio.to_thread(func, *args)

    def _require_session(self, session_id: str) -> CodexSessionState:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise RuntimeError(f"codex session not active: {session_id}") from exc

    def _map_notification(
        self,
        method: str,
        payload: dict[str, Any],
    ) -> tuple[EventKind | None, str, SessionStatus]:
        if method == "item/agentMessage/delta":
            return EventKind.AGENT_OUTPUT, str(payload.get("delta", "")), SessionStatus.RUNNING
        if method == "item/commandExecution/outputDelta":
            return EventKind.TOOL_RESULT, str(payload.get("delta", "")), SessionStatus.RUNNING
        if method == "item/fileChange/outputDelta":
            return EventKind.TOOL_RESULT, str(payload.get("delta", "")), SessionStatus.RUNNING
        if method == "turn/started":
            turn = payload.get("turn", {})
            return EventKind.SYSTEM_NOTE, f"Turn started: {turn.get('id', '')}".strip(), SessionStatus.RUNNING
        if method == "turn/completed":
            turn = payload.get("turn", {})
            status = self._map_turn_status(turn.get("status"))
            return EventKind.SYSTEM_NOTE, f"Turn {turn.get('status', 'completed')}", status
        if method == "item/started":
            item = self._extract_item(payload)
            return self._format_item_started(item)
        if method == "item/completed":
            item = self._extract_item(payload)
            return self._format_item_completed(item)
        if method == "turn/plan/updated":
            plan = payload.get("plan", [])
            text = "\n".join(f"- {entry.get('step', '')} [{entry.get('status', '')}]" for entry in plan)
            return EventKind.SYSTEM_NOTE, text, SessionStatus.RUNNING
        if method == "error":
            error = payload.get("error", {})
            return EventKind.SYSTEM_NOTE, str(error.get("message", "Codex error")), SessionStatus.ERROR
        return None, "", SessionStatus.RUNNING

    def _format_item_started(self, item: dict[str, Any]) -> tuple[EventKind, str, SessionStatus]:
        item_type = item.get("type")
        if item_type == "commandExecution":
            return EventKind.TOOL_CALL, f"$ {item.get('command', '')}", SessionStatus.RUNNING
        if item_type == "fileChange":
            paths = ", ".join(change.get("path", "") for change in item.get("changes", []))
            return EventKind.TOOL_CALL, f"Preparing file changes: {paths}", SessionStatus.RUNNING
        if item_type == "mcpToolCall":
            return EventKind.TOOL_CALL, f"MCP {item.get('server', '')}:{item.get('tool', '')}", SessionStatus.RUNNING
        if item_type == "plan":
            return EventKind.SYSTEM_NOTE, item.get("text", ""), SessionStatus.RUNNING
        if item_type == "agentMessage":
            return EventKind.AGENT_OUTPUT, item.get("text", ""), SessionStatus.RUNNING
        return EventKind.SYSTEM_NOTE, f"Started {item_type or 'item'}", SessionStatus.RUNNING

    def _format_item_completed(self, item: dict[str, Any]) -> tuple[EventKind | None, str, SessionStatus]:
        item_type = item.get("type")
        if item_type == "agentMessage":
            return None, "", SessionStatus.RUNNING
        if item_type == "commandExecution":
            output = item.get("aggregatedOutput") or ""
            suffix = f"\n{output}" if output else ""
            status = SessionStatus.IDLE if item.get("status") == "completed" else SessionStatus.RUNNING
            return EventKind.TOOL_RESULT, f"$ {item.get('command', '')}{suffix}", status
        if item_type == "fileChange":
            paths = ", ".join(change.get("path", "") for change in item.get("changes", []))
            status = SessionStatus.IDLE if item.get("status") == "completed" else SessionStatus.RUNNING
            return EventKind.TOOL_RESULT, f"File changes completed: {paths}", status
        return EventKind.SYSTEM_NOTE, f"Completed {item_type or 'item'}", SessionStatus.RUNNING

    def _extract_item_id(self, payload: dict[str, Any]) -> str | None:
        candidate = payload.get("itemId")
        if isinstance(candidate, str) and candidate:
            return candidate
        item = self._extract_item(payload) if "item" in payload else None
        if isinstance(item, dict):
            inner = item.get("id")
            if isinstance(inner, str) and inner:
                return inner
        return None

    def _extract_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        item = payload.get("item", {})
        if isinstance(item, dict) and len(item) == 1 and "root" in item:
            root = item["root"]
            if isinstance(root, dict):
                return root
        return item if isinstance(item, dict) else {}

    def _payload_to_dict(self, payload: Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            dumped = payload.model_dump(mode="json", by_alias=True)
            return dumped if isinstance(dumped, dict) else {"value": dumped}
        if is_dataclass(payload):
            dumped = asdict(payload)
            return dumped if isinstance(dumped, dict) else {"value": dumped}
        if isinstance(payload, UnknownNotification):
            return payload.params
        if isinstance(payload, dict):
            return payload
        return {"value": str(payload)}

    def _map_turn_status(self, value: Any) -> SessionStatus:
        if value == "completed":
            return SessionStatus.IDLE
        if value == "interrupted":
            return SessionStatus.INTERRUPTED
        if value == "failed":
            return SessionStatus.ERROR
        return SessionStatus.RUNNING

    def _format_approval_text(self, method: str, params: dict[str, Any]) -> str:
        if method == "item/commandExecution/requestApproval":
            return f"Approve command: {params.get('command', '')}"
        if method == "item/fileChange/requestApproval":
            return "Approve file changes"
        return f"Approve request: {method}"

    def _map_decision(self, decision: str) -> str:
        lowered = decision.strip()
        if lowered in {"approve", "accept", "yes", "y"}:
            return "accept"
        if lowered in {"approve_session", "acceptForSession"}:
            return "acceptForSession"
        if lowered in {"cancel"}:
            return "cancel"
        return "decline"
