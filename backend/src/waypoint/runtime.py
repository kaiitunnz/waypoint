import asyncio
from collections import defaultdict
from contextlib import suppress
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import secrets
from typing import Any

from fastapi import HTTPException, status

from waypoint.config import Settings
from waypoint.codex_app_server import CodexAppServerAdapter
from waypoint.normalizer import TerminalNormalizer
from waypoint.schemas import (
    Backend,
    EventKind,
    EventRecord,
    SessionApprovalRequest,
    SessionAttachRequest,
    SessionCreateRequest,
    SessionEnvelope,
    SessionInputRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
    SessionTransport,
)
from waypoint.storage import Storage
from waypoint.tmux import TmuxAdapter, TmuxError

SAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]+")


class BroadcastHub:
    def __init__(self) -> None:
        self.global_queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self.session_queues: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def subscribe_global(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.global_queues.add(queue)
        return queue

    def subscribe_session(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.session_queues[session_id].add(queue)
        return queue

    def unsubscribe_global(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.global_queues.discard(queue)

    def unsubscribe_session(self, session_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.session_queues[session_id].discard(queue)
        if not self.session_queues[session_id]:
            self.session_queues.pop(session_id, None)

    async def publish(self, message: SessionEnvelope, session_id: str | None = None) -> None:
        payload = message.model_dump(mode="json")
        for queue in list(self.global_queues):
            await queue.put(payload)
        if session_id is not None:
            for queue in list(self.session_queues.get(session_id, set())):
                await queue.put(payload)


class SessionRuntime:
    def __init__(self, settings: Settings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage
        self.tmux = TmuxAdapter()
        self.normalizer = TerminalNormalizer()
        self.broadcast = BroadcastHub()
        self.codex = CodexAppServerAdapter(self._emit_adapter_event)
        self.monitor_tasks: dict[str, asyncio.Task[None]] = {}
        self.file_offsets: dict[str, int] = {}

    async def start(self) -> None:
        for session in self.storage.list_sessions():
            if session.status in {SessionStatus.EXITED, SessionStatus.ERROR}:
                continue
            if session.transport == SessionTransport.CODEX_APP_SERVER:
                await self._restore_codex_session(session)
                continue
            self._ensure_monitor(session.id)

    async def _restore_codex_session(self, session: SessionRecord) -> None:
        if not session.thread_id:
            self.storage.update_session(session.id, status=SessionStatus.EXITED)
            await self._record_system_event(
                session.id,
                "Codex session has no thread id; marking exited",
                status=SessionStatus.EXITED,
            )
            return
        try:
            await self.codex.restore_session(session.id, session.cwd, session.thread_id)
        except Exception as exc:  # noqa: BLE001
            self.storage.update_session(session.id, status=SessionStatus.ERROR)
            await self._record_system_event(
                session.id,
                f"Codex session restore failed: {exc}",
                status=SessionStatus.ERROR,
            )
            return
        self.storage.update_session(session.id, status=SessionStatus.IDLE)
        await self._record_system_event(
            session.id,
            "Codex session restored from previous backend process",
            status=SessionStatus.IDLE,
        )

    async def stop(self) -> None:
        for task in self.monitor_tasks.values():
            task.cancel()
        for task in self.monitor_tasks.values():
            with suppress(asyncio.CancelledError):
                await task
        self.monitor_tasks.clear()
        await self.codex.shutdown()
        self.storage.close()

    def list_sessions(self) -> list[SessionRecord]:
        return self.storage.list_sessions()

    def get_session(self, session_id: str) -> SessionRecord:
        session = self.storage.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
        return session

    async def create_session(self, request: SessionCreateRequest) -> SessionRecord:
        if request.source_mode != SessionSource.MANAGED:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="use attach endpoint for tmux targets")
        session_id = self._generate_session_id(request.backend)
        title = request.title or f"{request.backend} {Path(request.cwd).name or request.backend}"
        session_dir = self._session_dir(session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"
        if request.backend == Backend.CODEX:
            raw_log.touch(exist_ok=True)
            session = SessionRecord(
                id=session_id,
                backend=request.backend,
                source=SessionSource.MANAGED,
                transport=SessionTransport.CODEX_APP_SERVER,
                title=title,
                cwd=request.cwd,
                repo_name=Path(request.cwd).name or None,
                branch=None,
                status=SessionStatus.STARTING,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                last_event_at=datetime.now(UTC),
                raw_log_path=str(raw_log),
                structured_log_path=str(structured_log),
            )
            self.storage.create_session(session)
            try:
                thread_id = await self.codex.start_session(session_id, request.cwd)
            except Exception:
                self.storage.update_session(session.id, status=SessionStatus.ERROR)
                raise
            self.storage.update_session(session.id, thread_id=thread_id, status=SessionStatus.IDLE)
            await self._record_system_event(session.id, "Codex app-server session started", status=SessionStatus.IDLE)
            return self.get_session(session.id)
        command = self._command_for_backend(request.backend, request.args)
        try:
            target = await self.tmux.start_managed_session(session_id, request.cwd, command)
            await self.tmux.pipe_output(target.pane, raw_log)
        except TmuxError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        session = SessionRecord(
            id=session_id,
            backend=request.backend,
            source=SessionSource.MANAGED,
            transport=SessionTransport.TMUX,
            title=title,
            cwd=request.cwd,
            repo_name=Path(request.cwd).name or None,
            branch=None,
            status=SessionStatus.STARTING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            last_event_at=datetime.now(UTC),
            tmux_session=target.session,
            tmux_window=target.window,
            tmux_pane=target.pane,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            pid=target.pane_pid,
        )
        self.storage.create_session(session)
        await self._record_system_event(session.id, f"Managed session started for {request.backend}")
        self._ensure_monitor(session.id)
        return self.get_session(session.id)

    async def attach_tmux(self, request: SessionAttachRequest) -> SessionRecord:
        try:
            target = await self.tmux.describe_target(request.tmux_target)
        except TmuxError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        backend = request.backend_hint or self._infer_backend(request.tmux_target)
        session_id = self._generate_session_id(backend)
        title = request.title or f"{backend} attached {target.session}"
        session_dir = self._session_dir(session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"
        snapshot = await self.tmux.capture_snapshot(target.pane, -self.settings.tail_snapshot_lines)
        raw_log.write_text(snapshot, encoding="utf-8")
        await self.tmux.pipe_output(target.pane, raw_log)
        session = SessionRecord(
            id=session_id,
            backend=backend,
            source=SessionSource.ATTACHED_TMUX,
            transport=SessionTransport.TMUX,
            title=title,
            cwd=target.cwd,
            repo_name=Path(target.cwd).name or None,
            branch=None,
            status=SessionStatus.IDLE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            last_event_at=datetime.now(UTC),
            tmux_session=target.session,
            tmux_window=target.window,
            tmux_pane=target.pane,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            pid=target.pane_pid,
        )
        self.storage.create_session(session)
        self.file_offsets[session.id] = 0
        await self._ingest_raw_output(session.id)
        await self._record_system_event(session.id, f"Attached to tmux target {request.tmux_target}")
        self._ensure_monitor(session.id)
        return self.get_session(session.id)

    async def handle_input(self, session_id: str, request: SessionInputRequest) -> SessionRecord:
        session = self.get_session(session_id)
        if session.transport == SessionTransport.CODEX_APP_SERVER:
            try:
                await self.codex.send_input(session.id, request.text)
            except Exception as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
            await self._record_user_event(session.id, request.text, submit=request.submit)
            return self.storage.update_session(session.id, status=SessionStatus.RUNNING)
        target = session.tmux_pane or session.tmux_session or session.id
        try:
            await self.tmux.send_input(target, request.text, request.submit)
        except TmuxError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        await self._record_user_event(session.id, request.text, submit=request.submit)
        return self.storage.update_session(session.id, status=SessionStatus.RUNNING)

    async def interrupt(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        if session.transport == SessionTransport.CODEX_APP_SERVER:
            await self.codex.interrupt(session.id)
            await self._record_system_event(session.id, "Sent interrupt", status=SessionStatus.INTERRUPTED)
            return self.storage.update_session(session.id, status=SessionStatus.INTERRUPTED)
        target = session.tmux_pane or session.tmux_session or session.id
        await self.tmux.interrupt(target)
        await self._record_system_event(session.id, "Sent interrupt", status=SessionStatus.INTERRUPTED)
        return self.storage.update_session(session.id, status=SessionStatus.INTERRUPTED)

    async def resume(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        if session.transport == SessionTransport.CODEX_APP_SERVER:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="resume is not supported for Codex app-server sessions")
        target = session.tmux_pane or session.tmux_session or session.id
        await self.tmux.resume(target)
        await self._record_system_event(session.id, "Sent resume", status=SessionStatus.RUNNING)
        return self.storage.update_session(session.id, status=SessionStatus.RUNNING)

    async def approve(self, session_id: str, request: SessionApprovalRequest) -> SessionRecord:
        session = self.get_session(session_id)
        if session.transport == SessionTransport.CODEX_APP_SERVER:
            handled = await self.codex.respond_to_approval(session.id, request.decision)
            if not handled:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no pending approval request")
            await self._record_system_event(session.id, f"Approval response sent: {request.decision}", status=SessionStatus.RUNNING)
            return self.storage.update_session(session.id, status=SessionStatus.RUNNING)
        decision = request.decision.strip().lower()
        mapped = "y" if decision in {"approve", "yes", "y"} else "n"
        text = request.text or mapped
        await self.handle_input(session_id, SessionInputRequest(text=text, submit=True))
        await self._record_system_event(session_id, f"Approval response sent: {mapped}")
        return self.get_session(session_id)

    def session_events(self, session_id: str, cursor: int | None = None) -> list[EventRecord]:
        self.get_session(session_id)
        return self.storage.list_events(session_id, cursor)

    def terminal_snapshot(self, session_id: str) -> str:
        session = self.get_session(session_id)
        if session.transport == SessionTransport.CODEX_APP_SERVER:
            return self.codex.terminal_snapshot(session.id)
        raw_log_path = Path(session.raw_log_path)
        if not raw_log_path.exists():
            return ""
        snapshot = raw_log_path.read_text(encoding="utf-8", errors="ignore")
        return self.normalizer.clean(snapshot)

    async def _record_user_event(self, session_id: str, text: str, submit: bool) -> None:
        event = EventRecord(
            session_id=session_id,
            ts=datetime.now(UTC),
            kind=EventKind.USER_INPUT,
            text=text,
            metadata={"submit": submit, "status": SessionStatus.RUNNING},
            sequence=self.storage.next_sequence(session_id),
        )
        persisted = self.storage.append_event(event)
        self._append_structured_log(session_id, persisted)
        await self._publish_event(persisted)

    async def _record_system_event(
        self,
        session_id: str,
        text: str,
        status: SessionStatus | None = None,
    ) -> None:
        metadata: dict[str, Any] = {}
        if status is not None:
            metadata["status"] = status
        event = EventRecord(
            session_id=session_id,
            ts=datetime.now(UTC),
            kind=EventKind.SYSTEM_NOTE,
            text=text,
            metadata=metadata,
            sequence=self.storage.next_sequence(session_id),
        )
        persisted = self.storage.append_event(event)
        self._append_structured_log(session_id, persisted)
        await self._publish_event(persisted)

    async def _publish_event(self, event: EventRecord) -> None:
        await self.broadcast.publish(
            SessionEnvelope(
                type="event",
                payload={"event": event.model_dump(mode="json")},
            ),
            session_id=event.session_id,
        )
        session = self.get_session(event.session_id)
        await self.broadcast.publish(
            SessionEnvelope(
                type="session_state",
                payload={"session": session.model_dump(mode="json")},
            ),
            session_id=event.session_id,
        )
        await self.broadcast.publish(
            SessionEnvelope(
                type="session_list_update",
                payload={"sessions": [item.model_dump(mode="json") for item in self.list_sessions()]},
            )
        )

    def _append_structured_log(self, session_id: str, event: EventRecord) -> None:
        session = self.get_session(session_id)
        path = Path(session.structured_log_path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.model_dump(mode="json")) + "\n")

    def _generate_session_id(self, backend: Backend) -> str:
        token = secrets.token_hex(4)
        prefix = SAFE_NAME.sub("-", backend.value)
        return f"{prefix}-{token}"

    def _session_dir(self, session_id: str) -> Path:
        path = self.settings.sessions_dir / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _command_for_backend(self, backend: Backend, args: list[str]) -> list[str]:
        executable = "claude" if backend == Backend.CLAUDE_CODE else "codex"
        return [executable, *args]

    def _infer_backend(self, target: str) -> Backend:
        lowered = target.lower()
        if "claude" in lowered:
            return Backend.CLAUDE_CODE
        return Backend.CODEX

    def _ensure_monitor(self, session_id: str) -> None:
        if session_id in self.monitor_tasks:
            return
        session = self.get_session(session_id)
        if session.transport != SessionTransport.TMUX:
            return
        self.monitor_tasks[session_id] = asyncio.create_task(self._monitor_session(session_id))

    async def _monitor_session(self, session_id: str) -> None:
        try:
            while True:
                await self._ingest_raw_output(session_id)
                await self._refresh_state(session_id)
                await asyncio.sleep(self.settings.stream_poll_interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._record_system_event(session_id, "Session monitor failed", status=SessionStatus.ERROR)

    async def _emit_adapter_event(
        self,
        session_id: str,
        kind: EventKind,
        text: str,
        metadata: dict[str, Any],
        status: SessionStatus,
    ) -> None:
        event = EventRecord(
            session_id=session_id,
            ts=datetime.now(UTC),
            kind=kind,
            text=text,
            metadata={**metadata, "status": status},
            sequence=self.storage.next_sequence(session_id),
        )
        persisted = self.storage.append_event(event)
        self._append_structured_log(session_id, persisted)
        await self._publish_event(persisted)

    async def _ingest_raw_output(self, session_id: str) -> None:
        session = self.get_session(session_id)
        raw_log_path = Path(session.raw_log_path)
        if not raw_log_path.exists():
            return
        offset = self.file_offsets.get(session_id, 0)
        with raw_log_path.open("r", encoding="utf-8", errors="ignore") as handle:
            handle.seek(offset)
            chunk = handle.read()
            self.file_offsets[session_id] = handle.tell()
        if not chunk.strip():
            return
        normalized = self.normalizer.normalize(session_id, chunk, self.storage.next_sequence(session_id))
        for event in normalized.events:
            persisted = self.storage.append_event(event)
            self._append_structured_log(session_id, persisted)
            await self._publish_event(persisted)

    async def _refresh_state(self, session_id: str) -> None:
        session = self.get_session(session_id)
        target = session.tmux_pane or session.tmux_session or session.id
        try:
            target_info = await self.tmux.describe_target(target)
        except TmuxError:
            self.storage.update_session(session.id, status=SessionStatus.EXITED)
            return
        updates: dict[str, Any] = {"pid": target_info.pane_pid}
        if target_info.pane_dead:
            updates["status"] = SessionStatus.EXITED
        self.storage.update_session(session.id, **updates)
