import asyncio
import json
import logging
import re
import secrets
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codex_app_server.client import AppServerClient
from fastapi import HTTPException, status

from waypoint.claude_cli import ClaudeCliAdapter, ClaudeCliError
from waypoint.claude_runtime import ClaudeHookBundle
from waypoint.codex_app_server import (
    ClientFactory,
    CodexAppServerAdapter,
    default_client_factory,
)
from waypoint.config import Settings
from waypoint.git_meta import resolve_git_meta
from waypoint.normalizer import TerminalNormalizer
from waypoint.scheduler import Scheduler
from waypoint.schemas import (
    Backend,
    CodexThreadImportRequest,
    CodexThreadSummary,
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
from waypoint.server_config import (
    SshLaunchTargetConfig,
    build_remote_claude_launch_factory,
    build_remote_codex_client_factory,
)
from waypoint.storage import Storage
from waypoint.tmux import TmuxAdapter, TmuxError
from waypoint.transports import (
    ClaudeTransport,
    CodexTransport,
    TmuxTransport,
    TransportAdapter,
)

log = logging.getLogger("waypoint.runtime")

SAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]+")


class BroadcastHub:
    def __init__(self) -> None:
        self.global_queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self.session_queues: dict[str, set[asyncio.Queue[dict[str, Any]]]] = (
            defaultdict(set)
        )

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

    def unsubscribe_session(
        self, session_id: str, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        self.session_queues[session_id].discard(queue)
        if not self.session_queues[session_id]:
            self.session_queues.pop(session_id, None)

    async def publish(
        self, message: SessionEnvelope, session_id: str | None = None
    ) -> None:
        payload = message.model_dump(mode="json")
        for queue in list(self.global_queues):
            await queue.put(payload)
        if session_id is not None:
            for queue in list(self.session_queues.get(session_id, set())):
                await queue.put(payload)


class SessionRuntime:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        claude_hook: "ClaudeHookBundle | None" = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.tmux = TmuxAdapter()
        self.normalizer = TerminalNormalizer()
        self.broadcast = BroadcastHub()
        self.ssh_targets = {
            target.id: target for target in self.settings.ssh_targets if target.enabled
        }
        self.codex = CodexAppServerAdapter(self._emit_adapter_event)
        self.claude_hook = claude_hook
        self.claude = self._build_claude_adapter()
        self.monitor_tasks: dict[str, asyncio.Task[None]] = {}
        self.file_offsets: dict[str, int] = {}
        self._transports: dict[SessionTransport, TransportAdapter] = {
            SessionTransport.CODEX_APP_SERVER: CodexTransport(self),
            SessionTransport.CLAUDE_CLI: ClaudeTransport(self),
            SessionTransport.TMUX: TmuxTransport(self),
        }
        self.scheduler = Scheduler(self)

    def transport_for(self, session: SessionRecord) -> TransportAdapter:
        return self._transports[session.transport]

    def _build_claude_adapter(self) -> ClaudeCliAdapter | None:
        if self.claude_hook is None:
            return None
        hook_url = f"http://127.0.0.1:{self.settings.port}"
        return ClaudeCliAdapter(
            self._emit_adapter_event,
            hook_settings_path=self.claude_hook.settings_path,
            hook_secret=self.claude_hook.secret,
            hook_url=hook_url,
        )

    async def start(self) -> None:
        for session in self.storage.list_sessions():
            if session.status in {SessionStatus.EXITED, SessionStatus.ERROR}:
                continue
            if session.transport == SessionTransport.CODEX_APP_SERVER:
                await self._restore_codex_session(session)
                continue
            if session.transport == SessionTransport.CLAUDE_CLI:
                await self._restore_claude_session(session)
                continue
            self._ensure_monitor(session.id)
        await self.scheduler.start()

    async def _restore_claude_session(self, session: SessionRecord) -> None:
        if self.claude is None:
            self.storage.update_session(session.id, status=SessionStatus.ERROR)
            await self._record_system_event(
                session.id,
                "Claude adapter unavailable; cannot restore",
                status=SessionStatus.ERROR,
            )
            return
        if not session.thread_id:
            self.storage.update_session(session.id, status=SessionStatus.EXITED)
            await self._record_system_event(
                session.id,
                "Claude session has no claude_session_id; marking exited",
                status=SessionStatus.EXITED,
            )
            return
        if (
            session.launch_target_id
            and self._find_launch_target(session.launch_target_id) is None
        ):
            self.storage.update_session(session.id, status=SessionStatus.ERROR)
            await self._record_system_event(
                session.id,
                f"Claude session launch target {session.launch_target_id} is no longer configured",
                status=SessionStatus.ERROR,
            )
            return
        try:
            await self.claude.restore_session(
                session.id,
                session.remote_cwd or session.cwd,
                session.thread_id,
                self._claude_launch_factory(session.launch_target_id),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "claude restore failed",
                extra={
                    "session_id": session.id,
                    "claude_session_id": session.thread_id,
                },
            )
            self.storage.update_session(session.id, status=SessionStatus.ERROR)
            await self._record_system_event(
                session.id,
                f"Claude session restore failed: {exc}",
                status=SessionStatus.ERROR,
            )
            return
        self.storage.update_session(session.id, status=SessionStatus.IDLE)
        await self._record_system_event(
            session.id,
            self._claude_restore_message(session.remote_cwd, session.launch_target_id),
            status=SessionStatus.IDLE,
        )

    async def _restore_codex_session(self, session: SessionRecord) -> None:
        if not session.thread_id:
            self.storage.update_session(session.id, status=SessionStatus.EXITED)
            await self._record_system_event(
                session.id,
                "Codex session has no thread id; marking exited",
                status=SessionStatus.EXITED,
            )
            return
        if (
            session.launch_target_id
            and self._find_launch_target(session.launch_target_id) is None
        ):
            self.storage.update_session(session.id, status=SessionStatus.ERROR)
            await self._record_system_event(
                session.id,
                f"Codex session launch target {session.launch_target_id} is no longer configured",
                status=SessionStatus.ERROR,
            )
            return
        try:
            await self.codex.restore_session(
                session.id,
                session.cwd,
                session.thread_id,
                session.remote_cwd,
                self._codex_client_factory(session.launch_target_id),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "codex restore failed",
                extra={
                    "session_id": session.id,
                    "thread_id": session.thread_id,
                    "cwd": session.cwd,
                },
            )
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
            self._codex_restore_message(session.remote_cwd, session.launch_target_id),
            status=SessionStatus.IDLE,
        )

    async def stop(self) -> None:
        await self.scheduler.stop()
        for task in self.monitor_tasks.values():
            task.cancel()
        for task in self.monitor_tasks.values():
            with suppress(asyncio.CancelledError):
                await task
        self.monitor_tasks.clear()
        await self.codex.shutdown()
        if self.claude is not None:
            await self.claude.shutdown()
        self.storage.close()

    def list_sessions(self) -> list[SessionRecord]:
        return self.storage.list_sessions()

    def get_session(self, session_id: str) -> SessionRecord:
        session = self.storage.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="session not found"
            )
        return session

    async def list_importable_codex_threads(
        self, launch_target_id: str | None = None
    ) -> list[CodexThreadSummary]:
        self._resolve_launch_target(launch_target_id, Backend.CODEX)
        imported = {
            (session.launch_target_id, session.thread_id)
            for session in self.storage.list_sessions()
            if session.backend == Backend.CODEX and session.thread_id
        }

        async def operation(client: AppServerClient) -> list[Any]:
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

        threads = await self._run_codex_client_operation(
            launch_target_id, operation=operation
        )
        summaries = [
            self._codex_thread_summary(thread)
            for thread in threads
            if not thread.ephemeral
            and (launch_target_id, thread.id) not in imported
        ]
        return sorted(summaries, key=lambda thread: thread.updated_at, reverse=True)

    async def import_codex_thread(
        self, request: CodexThreadImportRequest
    ) -> SessionRecord:
        launch_target = self._resolve_launch_target(
            request.launch_target_id, Backend.CODEX
        )
        existing = self._find_imported_codex_session(
            request.thread_id, request.launch_target_id
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="codex thread already imported",
            )
        thread = await self._read_codex_thread(
            request.thread_id, request.launch_target_id
        )
        if thread.ephemeral:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ephemeral codex threads cannot be imported",
            )
        session_id = self._generate_session_id(Backend.CODEX)
        session_dir = self._session_dir(session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"
        raw_log.touch(exist_ok=True)
        cwd = thread.cwd
        remote_cwd = thread.cwd if launch_target is not None else None
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=Backend.CODEX,
            source=SessionSource.MANAGED,
            transport=SessionTransport.CODEX_APP_SERVER,
            title=self._codex_thread_title(thread),
            cwd=cwd,
            remote_cwd=remote_cwd,
            launch_target_id=launch_target.id if launch_target else None,
            repo_name=self._codex_thread_repo_name(thread),
            branch=self._codex_thread_branch(thread),
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            thread_id=thread.id,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
        )
        self.storage.create_session(session)
        try:
            await self.codex.restore_session(
                session.id,
                session.cwd,
                thread.id,
                session.remote_cwd,
                self._codex_client_factory(session.launch_target_id),
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
            self.storage.update_session(session.id, status=SessionStatus.ERROR)
            await self._record_system_event(
                session.id,
                f"Codex thread import failed: {exc}",
                status=SessionStatus.ERROR,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to import codex thread: {exc}",
            ) from exc
        self.storage.update_session(session.id, status=SessionStatus.IDLE)
        await self._record_system_event(
            session.id,
            self._codex_import_message(thread.cwd, launch_target),
            status=SessionStatus.IDLE,
            metadata={"imported_thread_id": thread.id},
        )
        return self.get_session(session.id)

    async def create_session(self, request: SessionCreateRequest) -> SessionRecord:
        if request.source_mode != SessionSource.MANAGED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="use attach endpoint for tmux targets",
            )
        session_id = self._generate_session_id(request.backend)
        launch_target = self._resolve_launch_target(
            request.launch_target_id, request.backend
        )
        # Local cwd is fed to subprocess.Popen / tmux new-session, neither of
        # which expand `~`. Resolve it before storing/launching. The remote
        # cwd is left verbatim so the remote shell can do its own expansion.
        if launch_target is not None:
            # Remote sessions only use the remote path; the local cwd is just
            # a label for the UI / git-meta heuristic. Fall back to the remote
            # path when the client didn't supply one.
            local_cwd = (
                request.cwd or request.remote_cwd or launch_target.default_remote_cwd
            )
        else:
            local_cwd = str(Path(request.cwd).expanduser())
        request = request.model_copy(update={"cwd": local_cwd})
        title = (
            request.title
            or f"{request.backend} {Path(request.cwd).name or request.backend}"
        )
        session_dir = self._session_dir(session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"
        git_meta = await resolve_git_meta(request.cwd)
        remote_cwd = self._resolve_remote_cwd(request, launch_target)
        if request.backend == Backend.CODEX:
            raw_log.touch(exist_ok=True)
            session = SessionRecord(
                id=session_id,
                backend=request.backend,
                source=SessionSource.MANAGED,
                transport=SessionTransport.CODEX_APP_SERVER,
                title=title,
                cwd=request.cwd,
                remote_cwd=remote_cwd,
                launch_target_id=launch_target.id if launch_target else None,
                repo_name=git_meta.repo_name,
                branch=git_meta.branch,
                status=SessionStatus.STARTING,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                last_event_at=datetime.now(UTC),
                raw_log_path=str(raw_log),
                structured_log_path=str(structured_log),
            )
            self.storage.create_session(session)
            try:
                thread_id = await self.codex.start_session(
                    session_id,
                    request.cwd,
                    remote_cwd,
                    self._codex_client_factory(session.launch_target_id),
                )
            except Exception:
                self.storage.update_session(session.id, status=SessionStatus.ERROR)
                raise
            self.storage.update_session(
                session.id, thread_id=thread_id, status=SessionStatus.IDLE
            )
            await self._record_system_event(
                session.id,
                self._codex_start_message(remote_cwd, launch_target),
                status=SessionStatus.IDLE,
            )
            return self.get_session(session.id)
        if request.backend == Backend.CLAUDE_CODE and self.claude is not None:
            raw_log.touch(exist_ok=True)
            claude_session_id = self._generate_claude_session_id()
            session = SessionRecord(
                id=session_id,
                backend=request.backend,
                source=SessionSource.MANAGED,
                transport=SessionTransport.CLAUDE_CLI,
                title=title,
                cwd=request.cwd,
                remote_cwd=remote_cwd,
                launch_target_id=launch_target.id if launch_target else None,
                repo_name=git_meta.repo_name,
                branch=git_meta.branch,
                status=SessionStatus.STARTING,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                last_event_at=datetime.now(UTC),
                thread_id=claude_session_id,
                raw_log_path=str(raw_log),
                structured_log_path=str(structured_log),
            )
            self.storage.create_session(session)
            try:
                await self.claude.start_session(
                    session_id,
                    remote_cwd or request.cwd,
                    claude_session_id,
                    self._claude_launch_factory(session.launch_target_id),
                )
            except (ClaudeCliError, FileNotFoundError, OSError) as exc:
                self.storage.update_session(session.id, status=SessionStatus.ERROR)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                ) from exc
            self.storage.update_session(session.id, status=SessionStatus.IDLE)
            await self._record_system_event(
                session.id,
                self._claude_start_message(
                    claude_session_id, remote_cwd, launch_target
                ),
                status=SessionStatus.IDLE,
            )
            return self.get_session(session.id)
        command = self._command_for_backend(
            request.backend, request.args, launch_target, remote_cwd
        )
        try:
            target = await self.tmux.start_managed_session(
                session_id, request.cwd, command
            )
            await self.tmux.pipe_output(target.pane, raw_log)
        except TmuxError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        session = SessionRecord(
            id=session_id,
            backend=request.backend,
            source=SessionSource.MANAGED,
            transport=SessionTransport.TMUX,
            title=title,
            cwd=request.cwd,
            remote_cwd=remote_cwd,
            launch_target_id=launch_target.id if launch_target else None,
            repo_name=git_meta.repo_name,
            branch=git_meta.branch,
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
        await self._record_system_event(
            session.id,
            self._managed_start_message(request.backend, launch_target, remote_cwd),
        )
        self._ensure_monitor(session.id)
        return self.get_session(session.id)

    async def attach_tmux(self, request: SessionAttachRequest) -> SessionRecord:
        try:
            target = await self.tmux.describe_target(request.tmux_target)
        except TmuxError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        backend = request.backend_hint or self._infer_backend(request.tmux_target)
        session_id = self._generate_session_id(backend)
        title = request.title or f"{backend} attached {target.session}"
        session_dir = self._session_dir(session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"
        snapshot = await self.tmux.capture_snapshot(
            target.pane, -self.settings.tail_snapshot_lines
        )
        raw_log.write_text(snapshot, encoding="utf-8")
        await self.tmux.pipe_output(target.pane, raw_log)
        git_meta = await resolve_git_meta(target.cwd)
        session = SessionRecord(
            id=session_id,
            backend=backend,
            source=SessionSource.ATTACHED_TMUX,
            transport=SessionTransport.TMUX,
            title=title,
            cwd=target.cwd,
            repo_name=git_meta.repo_name,
            branch=git_meta.branch,
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
        await self._record_system_event(
            session.id, f"Attached to tmux target {request.tmux_target}"
        )
        self._ensure_monitor(session.id)
        return self.get_session(session.id)

    async def handle_input(
        self, session_id: str, request: SessionInputRequest
    ) -> SessionRecord:
        session = self.get_session(session_id)
        transport = self.transport_for(session)
        if transport.is_structured:
            handled = await self._handle_builtin_command(session, request)
            if handled is not None:
                return handled
        await transport.send_input(session, request.text)
        await self._record_user_event(session.id, request.text, submit=request.submit)
        return self.storage.update_session(session.id, status=SessionStatus.RUNNING)

    async def interrupt(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        await self.transport_for(session).interrupt(session)
        await self._record_system_event(
            session.id, "Sent interrupt", status=SessionStatus.INTERRUPTED
        )
        return self.storage.update_session(session.id, status=SessionStatus.INTERRUPTED)

    async def resume(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        await self.transport_for(session).resume(session)
        await self._record_system_event(
            session.id, "Sent resume", status=SessionStatus.RUNNING
        )
        return self.storage.update_session(session.id, status=SessionStatus.RUNNING)

    async def terminate(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        if session.status == SessionStatus.EXITED:
            return session
        await self.transport_for(session).terminate(session)
        await self._record_system_event(
            session.id, "Session terminated", status=SessionStatus.EXITED
        )
        return self.storage.update_session(session.id, status=SessionStatus.EXITED)

    async def delete(self, session_id: str) -> None:
        session = self.get_session(session_id)
        if session.status != SessionStatus.EXITED:
            await self.terminate(session_id)
        self.storage.delete_session(session_id)
        await self.broadcast.publish(
            SessionEnvelope(
                type="session_list_update",
                payload={
                    "sessions": [
                        item.model_dump(mode="json") for item in self.list_sessions()
                    ]
                },
            )
        )

    async def approve(
        self, session_id: str, request: SessionApprovalRequest
    ) -> SessionRecord:
        session = self.get_session(session_id)
        transport = self.transport_for(session)
        if transport.is_structured:
            handled = await transport.respond_to_approval(
                session, request.decision, request.text
            )
            if not handled:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="no pending approval request",
                )
            await self._record_system_event(
                session.id,
                f"Approval response sent: {request.decision}",
                status=SessionStatus.RUNNING,
            )
            return self.storage.update_session(session.id, status=SessionStatus.RUNNING)
        await transport.respond_to_approval(session, request.decision, request.text)
        return self.get_session(session_id)

    async def _handle_builtin_command(
        self, session: SessionRecord, request: SessionInputRequest
    ) -> SessionRecord | None:
        command = request.text.strip()
        if not command.startswith("/"):
            return None
        name = command.split(None, 1)[0].lower()
        if name == "/status":
            await self._record_user_event(
                session.id,
                request.text,
                submit=request.submit,
                status=session.status,
            )
            await self._record_system_event(
                session.id,
                self._format_builtin_status(session),
                status=session.status,
                metadata={"builtin_command": name},
            )
            return self.get_session(session.id)
        if name == "/permissions":
            await self._record_user_event(
                session.id,
                request.text,
                submit=request.submit,
                status=session.status,
            )
            await self._record_system_event(
                session.id,
                self._format_builtin_permissions(session),
                status=session.status,
                metadata={"builtin_command": name},
            )
            return self.get_session(session.id)
        if name == "/compact":
            # Codex exposes thread/compact/start over the app-server SDK;
            # route through it instead of forwarding the literal text. Claude's
            # CLI handles `/compact` itself in stream-json mode, so let it fall
            # through to send_input below.
            if session.backend == Backend.CODEX:
                try:
                    await self.codex.compact_thread(session.id)
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                    ) from exc
                await self._record_user_event(
                    session.id,
                    request.text,
                    submit=request.submit,
                    status=session.status,
                )
                await self._record_system_event(
                    session.id,
                    "Compacting codex thread…",
                    status=SessionStatus.RUNNING,
                    metadata={"builtin_command": name},
                )
                return self.storage.update_session(
                    session.id, status=SessionStatus.RUNNING
                )
        if name == "/help":
            await self._record_user_event(
                session.id,
                request.text,
                submit=request.submit,
                status=session.status,
            )
            await self._record_system_event(
                session.id,
                "Supported built-in commands: /help, /status, /permissions, /compact",
                status=session.status,
                metadata={"builtin_command": name},
            )
            return self.get_session(session.id)
        return None

    def session_events(
        self, session_id: str, cursor: int | None = None
    ) -> list[EventRecord]:
        self.get_session(session_id)
        return self.storage.list_events(session_id, cursor)

    def terminal_snapshot(self, session_id: str) -> str:
        session = self.get_session(session_id)
        return self.transport_for(session).terminal_snapshot(session)

    def _codex_start_message(
        self, cwd: str | None, launch_target: SshLaunchTargetConfig | None
    ) -> str:
        if launch_target is not None:
            remote_cwd = cwd or launch_target.default_remote_cwd
            return (
                f"Codex app-server session started via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({remote_cwd})"
            )
        return "Codex app-server session started"

    def _codex_restore_message(
        self, cwd: str | None, launch_target_id: str | None = None
    ) -> str:
        launch_target = self._find_launch_target(launch_target_id)
        if launch_target is not None:
            remote_cwd = cwd or launch_target.default_remote_cwd
            return (
                f"Codex session restored via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({remote_cwd})"
            )
        return "Codex session restored from previous backend process"

    def _codex_import_message(
        self, cwd: str, launch_target: SshLaunchTargetConfig | None
    ) -> str:
        if launch_target is not None:
            return (
                f"Imported stored Codex thread via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd})"
            )
        return f"Imported stored Codex thread ({cwd})"

    def _claude_start_message(
        self,
        claude_session_id: str,
        cwd: str | None,
        launch_target: SshLaunchTargetConfig | None,
    ) -> str:
        if launch_target is not None:
            remote_cwd = cwd or launch_target.default_remote_cwd
            return (
                f"Claude session started via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({remote_cwd}) ({claude_session_id})"
            )
        return f"Claude session started ({claude_session_id})"

    def _claude_restore_message(
        self, cwd: str | None, launch_target_id: str | None = None
    ) -> str:
        launch_target = self._find_launch_target(launch_target_id)
        if launch_target is not None:
            remote_cwd = cwd or launch_target.default_remote_cwd
            return (
                f"Claude session restored via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({remote_cwd})"
            )
        return "Claude session restored from previous backend process"

    def _resolve_remote_cwd(
        self,
        request: SessionCreateRequest,
        launch_target: SshLaunchTargetConfig | None,
    ) -> str | None:
        if launch_target is None:
            return None
        return request.remote_cwd or launch_target.default_remote_cwd

    def launch_target_summaries(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for target in self.ssh_targets.values():
            summaries.append(
                {
                    "id": target.id,
                    "name": target.name,
                    "kind": "ssh",
                    "supported_backends": target.supported_backends,
                    "default_backend": target.resolve_default_backend(
                        self.settings.default_backend
                    ),
                    "default_remote_cwd": target.default_remote_cwd,
                }
            )
        return summaries

    def _resolve_launch_target(
        self,
        launch_target_id: str | None,
        backend: Backend,
    ) -> SshLaunchTargetConfig | None:
        if not launch_target_id:
            return None
        launch_target = self.ssh_targets.get(launch_target_id)
        if launch_target is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="unknown launch target"
            )
        if not launch_target.supports(backend):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="launch target does not support backend",
            )
        return launch_target

    def _find_launch_target(
        self, launch_target_id: str | None
    ) -> SshLaunchTargetConfig | None:
        if not launch_target_id:
            return None
        return self.ssh_targets.get(launch_target_id)

    def _codex_client_factory(self, launch_target_id: str | None):
        launch_target = self._find_launch_target(launch_target_id)
        if launch_target is None:
            return None
        return build_remote_codex_client_factory(launch_target)

    def _codex_client_cwds(
        self, launch_target_id: str | None
    ) -> tuple[str, str | None]:
        launch_target = self._find_launch_target(launch_target_id)
        if launch_target is not None:
            return launch_target.default_remote_cwd, launch_target.default_remote_cwd
        return str(Path(self.settings.default_cwd).expanduser()), None

    async def _run_codex_client_operation(
        self,
        launch_target_id: str | None,
        operation: Callable[[AppServerClient], Awaitable[Any]],
        *,
        cwd: str | None = None,
        remote_cwd: str | None = None,
    ) -> Any:
        default_cwd, default_remote_cwd = self._codex_client_cwds(launch_target_id)
        client_factory: ClientFactory = (
            self._codex_client_factory(launch_target_id) or default_client_factory
        )
        client = client_factory(
            cwd or default_cwd,
            remote_cwd if remote_cwd is not None else default_remote_cwd,
            self._deny_codex_approval,
        )
        try:
            await asyncio.to_thread(client.start)
            await asyncio.to_thread(client.initialize)
            return await operation(client)
        finally:
            with suppress(Exception):
                await asyncio.to_thread(client.close)

    async def _read_codex_thread(
        self, thread_id: str, launch_target_id: str | None
    ) -> Any:
        self._resolve_launch_target(launch_target_id, Backend.CODEX)

        async def operation(client: AppServerClient) -> Any:
            response = await asyncio.to_thread(client.thread_read, thread_id, False)
            return response.thread

        try:
            return await self._run_codex_client_operation(
                launch_target_id, operation=operation
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to read codex thread: {exc}",
            ) from exc

    def _find_imported_codex_session(
        self, thread_id: str, launch_target_id: str | None
    ) -> SessionRecord | None:
        for session in self.storage.list_sessions():
            if session.backend != Backend.CODEX:
                continue
            if session.thread_id != thread_id:
                continue
            if session.launch_target_id != launch_target_id:
                continue
            return session
        return None

    def _deny_codex_approval(
        self, _method: str, _params: dict[str, Any] | None
    ) -> dict[str, Any]:
        return {"decision": "decline"}

    def _codex_thread_summary(self, thread: Any) -> CodexThreadSummary:
        return CodexThreadSummary(
            id=thread.id,
            title=self._codex_thread_title(thread),
            cwd=thread.cwd,
            repo_name=self._codex_thread_repo_name(thread),
            branch=self._codex_thread_branch(thread),
            preview=(thread.preview or "").strip() or None,
            created_at=datetime.fromtimestamp(thread.created_at, UTC),
            updated_at=datetime.fromtimestamp(thread.updated_at, UTC),
        )

    def _codex_thread_title(self, thread: Any) -> str:
        if thread.name:
            return thread.name
        preview = (thread.preview or "").strip()
        if preview:
            return preview.splitlines()[0][:80]
        return f"Codex {Path(thread.cwd).name or thread.id}"

    def _codex_thread_branch(self, thread: Any) -> str | None:
        git_info = getattr(thread, "git_info", None)
        return git_info.branch if git_info is not None else None

    def _codex_thread_repo_name(self, thread: Any) -> str | None:
        git_info = getattr(thread, "git_info", None)
        if git_info is not None and git_info.origin_url:
            normalized = git_info.origin_url.rstrip("/").removesuffix(".git")
            name = normalized.rsplit("/", 1)[-1]
            if name:
                return name
        return Path(thread.cwd).name or None

    def _claude_launch_factory(self, launch_target_id: str | None):
        launch_target = self._find_launch_target(launch_target_id)
        if launch_target is None or self.claude_hook is None or self.claude is None:
            return None
        return build_remote_claude_launch_factory(
            launch_target,
            hook_script_path=self.claude_hook.hook_script_path,
            hook_secret=self.claude_hook.secret,
            local_backend_port=self.settings.port,
        )

    def _managed_start_message(
        self,
        backend: Backend,
        launch_target: SshLaunchTargetConfig | None,
        remote_cwd: str | None,
    ) -> str:
        if launch_target is None:
            return f"Managed session started for {backend}"
        return (
            f"Managed session started for {backend} via SSH target {launch_target.name} "
            f"on {launch_target.ssh_destination} ({remote_cwd or launch_target.default_remote_cwd})"
        )

    def _format_builtin_status(self, session: SessionRecord) -> str:
        parts = [
            f"Status: {session.status}",
            f"Backend: {session.backend}",
            f"Transport: {session.transport}",
            f"CWD: {session.cwd}",
        ]
        if session.remote_cwd:
            parts.append(f"Remote cwd: {session.remote_cwd}")
        if session.thread_id:
            parts.append(f"Thread: {session.thread_id}")
        if session.repo_name:
            branch = f" ({session.branch})" if session.branch else ""
            parts.append(f"Repo: {session.repo_name}{branch}")
        return "\n".join(parts)

    def _format_builtin_permissions(self, session: SessionRecord) -> str:
        pending = self.transport_for(session).has_pending_approval(session)
        return "\n".join(
            [
                "Waypoint handles approvals with the in-app approval card.",
                f"Pending approval: {'yes' if pending else 'no'}",
                "Available actions: Approve, Approve for session, Decline, Cancel",
            ]
        )

    async def _record_user_event(
        self,
        session_id: str,
        text: str,
        submit: bool,
        status: SessionStatus = SessionStatus.RUNNING,
    ) -> None:
        event = EventRecord(
            session_id=session_id,
            ts=datetime.now(UTC),
            kind=EventKind.USER_INPUT,
            text=text,
            metadata={"submit": submit, "status": status},
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
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event_metadata = dict(metadata or {})
        if status is not None:
            event_metadata["status"] = status
        event = EventRecord(
            session_id=session_id,
            ts=datetime.now(UTC),
            kind=EventKind.SYSTEM_NOTE,
            text=text,
            metadata=event_metadata,
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
                payload={
                    "sessions": [
                        item.model_dump(mode="json") for item in self.list_sessions()
                    ]
                },
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

    def _generate_claude_session_id(self) -> str:
        import uuid

        return str(uuid.uuid4())

    def _session_dir(self, session_id: str) -> Path:
        path = self.settings.sessions_dir / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _command_for_backend(
        self,
        backend: Backend,
        args: list[str],
        launch_target: SshLaunchTargetConfig | None = None,
        remote_cwd: str | None = None,
    ) -> list[str]:
        if launch_target is None:
            executable = "claude" if backend == Backend.CLAUDE_CODE else "codex"
            return [executable, *args]
        return list(
            launch_target.remote_command_for_backend(
                backend, args, remote_cwd or launch_target.default_remote_cwd
            )
        )

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
        self.monitor_tasks[session_id] = asyncio.create_task(
            self._monitor_session(session_id)
        )

    async def _monitor_session(self, session_id: str) -> None:
        try:
            while True:
                await self._ingest_raw_output(session_id)
                await self._refresh_state(session_id)
                await asyncio.sleep(self.settings.stream_poll_interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "tmux session monitor failed", extra={"session_id": session_id}
            )
            await self._record_system_event(
                session_id, "Session monitor failed", status=SessionStatus.ERROR
            )

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
        normalized = self.normalizer.normalize(
            session_id, chunk, self.storage.next_sequence(session_id)
        )
        for event in normalized.events:
            persisted = self.storage.append_event(event)
            self._append_structured_log(session_id, persisted)
            await self._publish_event(persisted)

    async def _refresh_state(self, session_id: str) -> None:
        session = self.get_session(session_id)
        target = session.tmux_pane or session.tmux_session or session.id
        try:
            target_info = await self.tmux.describe_target(target)
        except TmuxError as exc:
            if session.status != SessionStatus.EXITED:
                log.warning(
                    "tmux target lost; marking session exited",
                    extra={
                        "session_id": session.id,
                        "target": target,
                        "error": str(exc),
                    },
                )
            self.storage.update_session(session.id, status=SessionStatus.EXITED)
            return
        updates: dict[str, Any] = {"pid": target_info.pane_pid}
        if target_info.pane_dead and session.status != SessionStatus.EXITED:
            log.info(
                "tmux pane reported dead",
                extra={"session_id": session.id, "target": target},
            )
            updates["status"] = SessionStatus.EXITED
        self.storage.update_session(session.id, **updates)
