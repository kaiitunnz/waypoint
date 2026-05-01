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

from waypoint.backends import BackendRegistry, get_registry
from waypoint.backends.claude_code.adapter import ClaudeCliAdapter, ClaudeCliError
from waypoint.backends.claude_code.runtime_hook import ClaudeHookBundle
from waypoint.backends.claude_code.threads import (
    ClaudeThreadInfo,
    find_local_claude_thread,
    list_local_claude_threads,
)
from waypoint.backends.claude_code.threads_remote import RemoteClaudeThreadEnumerator
from waypoint.backends.codex.adapter import (
    ClientFactory,
    CodexAppServerAdapter,
    default_client_factory,
)
from waypoint.backends.tmux.normalize import TerminalNormalizer
from waypoint.config import Settings
from waypoint.git_meta import resolve_git_meta
from waypoint.scheduler import Scheduler, validate_permission_mode_for_backend
from waypoint.schemas import (
    Backend,
    ClaudeThreadImportRequest,
    ClaudeThreadSummary,
    CodexThreadImportRequest,
    CodexThreadSummary,
    EventKind,
    EventRecord,
    EventsPageResponse,
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
from waypoint.backends.tmux.adapter import TmuxAdapter, TmuxError
from waypoint.storage import Storage
from waypoint.transports import TransportAdapter

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
        registry: BackendRegistry | None = None,
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
        self.claude_thread_enumerator: RemoteClaudeThreadEnumerator | None = (
            RemoteClaudeThreadEnumerator(claude_hook.thread_enumerator_path)
            if claude_hook is not None
            else None
        )
        self.monitor_tasks: dict[str, asyncio.Task[None]] = {}
        self.file_offsets: dict[str, int] = {}
        self.registry = registry or get_registry()
        self._transports: dict[str, TransportAdapter] = {
            plugin.transport_id: plugin.transport_view(self)
            for plugin in self.registry.all()
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
            plugin = self.registry.plugin_for(session)
            await plugin.restore_session(self, session)
        await self.scheduler.start()

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
            if not thread.ephemeral and (launch_target_id, thread.id) not in imported
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
        cwd = self._codex_thread_cwd(thread)
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=Backend.CODEX,
            source=SessionSource.MANAGED,
            transport=SessionTransport.CODEX_APP_SERVER,
            title=self._codex_thread_title(thread),
            cwd=cwd,
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
            permission_mode="default",
        )
        self.storage.create_session(session)
        try:
            await self.codex.restore_session(
                session.id,
                session.cwd,
                thread.id,
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
        plugin = self.registry.get(Backend.CODEX)
        await self._record_system_event(
            session.id,
            plugin.format_import_message(cwd, launch_target),  # type: ignore[attr-defined]
            status=SessionStatus.IDLE,
            metadata={"imported_thread_id": thread.id},
        )
        return self.get_session(session.id)

    async def list_importable_claude_threads(
        self, launch_target_id: str | None = None
    ) -> list[ClaudeThreadSummary]:
        if self.claude is None:
            return []
        imported = {
            (session.launch_target_id, session.thread_id)
            for session in self.storage.list_sessions()
            if session.backend == Backend.CLAUDE_CODE and session.thread_id
        }
        if launch_target_id is None:
            infos = await asyncio.to_thread(list_local_claude_threads)
        else:
            target = self._resolve_launch_target(launch_target_id, Backend.CLAUDE_CODE)
            if target is None or self.claude_thread_enumerator is None:
                return []
            infos = await self.claude_thread_enumerator.list(target)
        return [
            self._claude_thread_summary(info)
            for info in infos
            if (launch_target_id, info.id) not in imported
        ]

    async def import_claude_thread(
        self, request: ClaudeThreadImportRequest
    ) -> SessionRecord:
        if self.claude is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="claude adapter is not initialized",
            )
        launch_target = self._resolve_launch_target(
            request.launch_target_id, Backend.CLAUDE_CODE
        )
        existing = self._find_imported_claude_session(
            request.thread_id, request.launch_target_id
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude thread already imported",
            )
        if launch_target is None:
            info = await asyncio.to_thread(find_local_claude_thread, request.thread_id)
        else:
            if self.claude_thread_enumerator is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="claude thread enumerator is not initialized",
                )
            info = await self.claude_thread_enumerator.find(
                launch_target, request.thread_id
            )
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
                        f"claude thread cwd {info.cwd} no longer exists; "
                        "cannot resume"
                    ),
                )
            cwd = str(cwd_path)
        else:
            # Remote cwd lives on the SSH host; we can't stat it from here.
            # If the directory is gone, claude itself surfaces the error
            # through the existing exception path below.
            cwd = info.cwd
        session_id = self._generate_session_id(Backend.CLAUDE_CODE)
        session_dir = self._session_dir(session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"
        raw_log.touch(exist_ok=True)
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=Backend.CLAUDE_CODE,
            source=SessionSource.MANAGED,
            transport=SessionTransport.CLAUDE_CLI,
            title=info.title,
            cwd=cwd,
            launch_target_id=launch_target.id if launch_target else None,
            repo_name=info.repo_name,
            branch=info.branch,
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            thread_id=info.id,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            permission_mode="default",
        )
        self.storage.create_session(session)
        try:
            await self.claude.restore_session(
                session.id,
                cwd,
                info.id,
                self._claude_launch_factory(session.launch_target_id),
                permission_mode=session.permission_mode,
                model=session.model,
                effort=session.effort,
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
            self.storage.update_session(session.id, status=SessionStatus.ERROR)
            await self._record_system_event(
                session.id,
                f"Claude thread import failed: {exc}",
                status=SessionStatus.ERROR,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to import claude thread: {exc}",
            ) from exc
        if launch_target is not None and self.claude_thread_enumerator is not None:
            self.claude_thread_enumerator.invalidate(launch_target.id)
        self.storage.update_session(session.id, status=SessionStatus.IDLE)
        plugin = self.registry.get(Backend.CLAUDE_CODE)
        await self._record_system_event(
            session.id,
            plugin.format_import_message(cwd, launch_target),  # type: ignore[attr-defined]
            status=SessionStatus.IDLE,
            metadata={"imported_thread_id": info.id},
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
            local_cwd = request.cwd or launch_target.default_cwd
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
        permission_mode = (
            validate_permission_mode_for_backend(
                request.backend, request.permission_mode
            )
            or "default"
        )
        # Per-request model wins; otherwise fall back to the per-backend default
        # from settings. Missing key (or empty) means "let the backend pick" —
        # we omit --model / params.model so the underlying CLI uses its own
        # default instead of waypoint forcing one.
        resolved_model = request.model or self.settings.default_models.get(
            request.backend
        )
        # Same precedence for reasoning effort. Missing key means "let the
        # backend pick" (Codex falls back to the model's default; Claude
        # omits the --effort flag).
        resolved_effort = request.effort or self.settings.default_efforts.get(
            request.backend
        )
        if request.backend == Backend.CODEX:
            raw_log.touch(exist_ok=True)
            session = SessionRecord(
                id=session_id,
                backend=request.backend,
                source=SessionSource.MANAGED,
                transport=SessionTransport.CODEX_APP_SERVER,
                title=title,
                cwd=request.cwd,
                launch_target_id=launch_target.id if launch_target else None,
                repo_name=git_meta.repo_name,
                branch=git_meta.branch,
                status=SessionStatus.STARTING,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                last_event_at=datetime.now(UTC),
                raw_log_path=str(raw_log),
                structured_log_path=str(structured_log),
                permission_mode=permission_mode,
                model=resolved_model,
                effort=resolved_effort,
            )
            self.storage.create_session(session)
            try:
                thread_id = await self.codex.start_session(
                    session_id,
                    request.cwd,
                    self._codex_client_factory(session.launch_target_id),
                    model=resolved_model,
                    effort=resolved_effort,
                )
            except Exception:
                self.storage.update_session(session.id, status=SessionStatus.ERROR)
                raise
            self.storage.update_session(
                session.id, thread_id=thread_id, status=SessionStatus.IDLE
            )
            plugin = self.registry.get(Backend.CODEX)
            await self._record_system_event(
                session.id,
                plugin.format_start_message(request.cwd, launch_target),  # type: ignore[attr-defined]
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
                permission_mode=permission_mode,
                model=resolved_model,
                effort=resolved_effort,
            )
            self.storage.create_session(session)
            try:
                await self.claude.start_session(
                    session_id,
                    request.cwd,
                    claude_session_id,
                    self._claude_launch_factory(session.launch_target_id),
                    permission_mode=session.permission_mode,
                    model=session.model,
                    effort=session.effort,
                )
            except (ClaudeCliError, FileNotFoundError, OSError) as exc:
                self.storage.update_session(session.id, status=SessionStatus.ERROR)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                ) from exc
            self.storage.update_session(session.id, status=SessionStatus.IDLE)
            plugin = self.registry.get(Backend.CLAUDE_CODE)
            await self._record_system_event(
                session.id,
                plugin.format_start_message(  # type: ignore[attr-defined]
                    claude_session_id, request.cwd, launch_target
                ),
                status=SessionStatus.IDLE,
            )
            return self.get_session(session.id)
        command = self._command_for_backend(
            request.backend, request.args, launch_target, request.cwd
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
        tmux_plugin = self.registry.get("tmux")
        await self._record_system_event(
            session.id,
            tmux_plugin.format_start_message(  # type: ignore[attr-defined]
                request.backend, launch_target, request.cwd
            ),
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
        if session.status in {SessionStatus.EXITED, SessionStatus.ERROR}:
            session = await self._reattach_session(session)
        transport = self.transport_for(session)
        if transport.is_structured:
            handled = await self._route_codex_compact(session, request)
            if handled is not None:
                return handled
        await transport.send_input(session, request.text)
        # Flip status to RUNNING before recording the event. _record_user_event
        # broadcasts a session_state snapshot derived from storage; if the
        # update lands after the broadcast, the snapshot still says "idle" and
        # the frontend's busy-state derivation lags until the agent's first
        # output triggers another broadcast (visibly delayed for Claude, which
        # emits nothing between stdin write and first content).
        updated = self.storage.update_session(session.id, status=SessionStatus.RUNNING)
        await self._record_user_event(session.id, request.text, submit=request.submit)
        return updated

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

    async def _reattach_session(self, session: SessionRecord) -> SessionRecord:
        # ERROR sessions reach this path while the prior adapter state may
        # still be in `_sessions` — the stream/process watchers emit the
        # error event but do not pop the slot. Tear it down explicitly here
        # so `_spawn` does not overwrite a live state and orphan its
        # subprocess + background tasks. terminate_session is a no-op when
        # the session id is not tracked, so this is safe for clean EXITED
        # paths too.
        plugin = self.registry.plugin_for(session)
        if not plugin.capabilities.is_structured:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="this session cannot be reattached after exit",
            )
        if session.transport == SessionTransport.CLAUDE_CLI:
            if self.claude is not None:
                await self.claude.terminate_session(session.id)
        elif session.transport == SessionTransport.CODEX_APP_SERVER:
            await self.codex.terminate_session(session.id)
        await plugin.restore_session(self, session)
        # _restore_*_session swallows failures (it tags the session ERROR or
        # EXITED and emits a system_note instead of raising). Re-read storage
        # so the caller sees the post-restore status, and translate any
        # terminal state into a 400 so the frontend surfaces a clear error
        # rather than silently relaunching into a dead session.
        refreshed = self.get_session(session.id)
        if refreshed.status in {SessionStatus.EXITED, SessionStatus.ERROR}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to reattach session ({refreshed.status})",
            )
        return refreshed

    async def delete(self, session_id: str) -> None:
        session = self.get_session(session_id)
        if session.status != SessionStatus.EXITED:
            await self.terminate(session_id)
        self.storage.delete_session(session_id)
        if (
            session.backend == Backend.CLAUDE_CODE
            and session.launch_target_id
            and self.claude_thread_enumerator is not None
        ):
            self.claude_thread_enumerator.invalidate(session.launch_target_id)
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

    async def set_permission_mode(self, session_id: str, mode: str) -> SessionRecord:
        session = self.get_session(session_id)
        plugin = self.registry.plugin_for(session)
        if not plugin.capabilities.supports_set_permission_mode_inline:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"permission mode is not supported for {session.backend}",
            )
        validated = plugin.validate_permission_mode(mode)
        if validated is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="permission mode is required",
            )
        await plugin.apply_permission_mode(self, session, validated)
        updated = self.storage.update_session(session_id, permission_mode=validated)
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
        return updated

    async def set_model(self, session_id: str, model: str | None) -> SessionRecord:
        session = self.get_session(session_id)
        cleaned = model.strip() if isinstance(model, str) and model.strip() else None
        plugin = self.registry.plugin_for(session)
        if not plugin.capabilities.supports_set_model_inline:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"model selection is not supported for {session.backend}",
            )
        await plugin.apply_model(self, session, cleaned)
        updated = self.storage.update_session(session_id, model=cleaned)
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
        return updated

    async def set_effort(self, session_id: str, effort: str | None) -> SessionRecord:
        session = self.get_session(session_id)
        cleaned = effort.strip() if isinstance(effort, str) and effort.strip() else None
        plugin = self.registry.plugin_for(session)
        # Some backends (Claude) treat set_effort as a session restart and
        # advertise `supports_set_effort_inline=False` because the knob
        # isn't truly inline. We still let the call through here — the
        # plugin decides whether the swap actually does anything (e.g.
        # short-circuit when the value is unchanged) and reports back.
        if (
            not plugin.capabilities.supports_set_effort_inline
            and not hasattr(plugin, "apply_effort")
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"effort selection is not supported for {session.backend}",
            )
        announce = await plugin.apply_effort(self, session, cleaned)
        if not announce and not plugin.capabilities.supports_set_effort_inline:
            return session
        if announce:
            await self._record_system_event(
                session_id,
                plugin.effort_swap_message(cleaned),
                status=SessionStatus.IDLE,
            )
        updated = self.storage.update_session(session_id, effort=cleaned)
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
        return updated

    async def list_backend_models(
        self,
        backend: str,
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        if not self.registry.has_backend(backend):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown backend: {backend}",
            )
        plugin = self.registry.get(backend)
        return await plugin.list_models(
            self,
            launch_target_id=launch_target_id,
            include_hidden=include_hidden,
        )

    async def set_pinned(self, session_id: str, pinned: bool) -> SessionRecord:
        session = self.get_session(session_id)
        pinned_at = datetime.now(UTC) if pinned else None
        if (session.pinned_at is None) == (pinned_at is None):
            return session
        updated = self.storage.update_session(session_id, pinned_at=pinned_at)
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
        return updated

    async def answer_question(
        self,
        session_id: str,
        answer: str,
        tool_use_id: str | None = None,
        answers: list[dict[str, Any]] | None = None,
    ) -> SessionRecord:
        session = self.get_session(session_id)
        if session.transport != SessionTransport.CLAUDE_CLI or self.claude is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="answer-question is only supported for Claude sessions",
            )
        try:
            handled = await self.claude.respond_to_ask_question(
                session_id, answer, tool_use_id
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
        # Stash the structured per-question answers + notes so the frontend
        # can render this user_input as a styled "answers" card instead of
        # the raw `"<question>"="<answer>" user notes: …` payload Claude
        # was tuned around.
        extra: dict[str, Any] = {"kind": "ask_user_question_answer"}
        if answers:
            extra["answers"] = answers
        if tool_use_id:
            extra["tool_use_id"] = tool_use_id
        # Same ordering as handle_input: flip status to RUNNING before
        # _record_user_event broadcasts the session_state snapshot, otherwise
        # the spinner stays off until Claude's next emitted chunk lands.
        updated = self.storage.update_session(session.id, status=SessionStatus.RUNNING)
        await self._record_user_event(
            session.id, answer, submit=True, extra_metadata=extra
        )
        return updated

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
            # Same ordering as handle_input: flip status to RUNNING before
            # _record_system_event broadcasts the session_state snapshot, so
            # the spinner doesn't lag until Claude's next emitted chunk.
            updated = self.storage.update_session(
                session.id, status=SessionStatus.RUNNING
            )
            await self._record_system_event(
                session.id,
                f"Approval response sent: {request.decision}",
                status=SessionStatus.RUNNING,
            )
            # Side-effect of an ExitPlanMode approval: the Claude adapter
            # has already flipped the binary's permission mode to default
            # via set_permission_mode. Sync storage + broadcast so the UI
            # pill reflects the change instead of staying stuck on "plan".
            await self._sync_claude_permission_mode(session)
            return updated
        await transport.respond_to_approval(session, request.decision, request.text)
        return self.get_session(session_id)

    async def _sync_claude_permission_mode(self, session: SessionRecord) -> None:
        if session.transport != SessionTransport.CLAUDE_CLI or self.claude is None:
            return
        current = self.claude.session_permission_mode(session.id)
        if current is None:
            return
        previous = session.permission_mode or "default"
        if current == previous:
            return
        self.storage.update_session(session.id, permission_mode=current)
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

    async def _route_codex_compact(
        self, session: SessionRecord, request: SessionInputRequest
    ) -> SessionRecord | None:
        # Codex's app-server doesn't parse user text as control commands —
        # `/compact` only takes effect via the thread/compact/start RPC. Every
        # other slash command (including `/help`, `/status`, `/permissions`,
        # and Codex- or Claude-specific extras) is forwarded as user input so
        # the underlying CLI / SDK can surface its own response.
        if session.backend != Backend.CODEX:
            return None
        command = request.text.strip()
        if command.split(None, 1)[0].lower() != "/compact":
            return None
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
            metadata={"builtin_command": "/compact"},
        )
        return self.storage.update_session(session.id, status=SessionStatus.RUNNING)

    def session_events(
        self, session_id: str, cursor: int | None = None
    ) -> list[EventRecord]:
        self.get_session(session_id)
        return self.storage.list_events(session_id, cursor)

    def session_events_page(
        self,
        session_id: str,
        *,
        message_limit: int,
        before_sequence: int | None = None,
    ) -> EventsPageResponse:
        """Return a paginated window of events spanning ``message_limit``
        logical chat messages, plus a `has_more` flag.

        Pagination units are *visible* chat entries, not raw events:
        Codex streams a single agent reply into hundreds of deltas, so
        capping by raw count would mean one click of "Load older" yields
        no new bubble (the bubble's leading text shifts but nothing new
        appears). The storage paginator groups events by
        ``_logical_message_key`` (item_id for agent_output and tool
        pairs, per-event otherwise) so N messages reliably surface N
        entries regardless of backend chattiness.

        - ``before_sequence is None`` → tail mode: latest N messages.
        - ``before_sequence is not None`` → up to N messages older than
          that sequence (used by the chat view's "Load older").
        """
        self.get_session(session_id)
        events = self.storage.list_events_by_message_count(
            session_id,
            message_limit=message_limit,
            before_sequence=before_sequence,
        )
        oldest_in_window = events[0].sequence if events else before_sequence
        has_more = False
        if oldest_in_window is not None:
            has_more = self.storage.has_events_before_sequence(
                session_id, oldest_in_window
            )
        return EventsPageResponse(events=events, has_more=has_more)

    def terminal_snapshot(self, session_id: str) -> str:
        session = self.get_session(session_id)
        return self.transport_for(session).terminal_snapshot(session)

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
                    "default_cwd": target.default_cwd,
                }
            )
        return summaries

    def _resolve_launch_target(
        self,
        launch_target_id: str | None,
        backend: str,
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

    def _codex_client_cwd(self, launch_target_id: str | None) -> str:
        launch_target = self._find_launch_target(launch_target_id)
        if launch_target is not None:
            return launch_target.default_cwd
        return str(Path(self.settings.default_cwd).expanduser())

    async def _run_codex_client_operation(
        self,
        launch_target_id: str | None,
        operation: Callable[[AppServerClient], Awaitable[Any]],
        *,
        cwd: str | None = None,
    ) -> Any:
        default_cwd = self._codex_client_cwd(launch_target_id)
        client_factory: ClientFactory = (
            self._codex_client_factory(launch_target_id) or default_client_factory
        )
        client = client_factory(
            cwd or default_cwd,
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
        cwd = self._codex_thread_cwd(thread)
        return CodexThreadSummary(
            id=thread.id,
            title=self._codex_thread_title(thread),
            cwd=cwd,
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
        return f"Codex {Path(self._codex_thread_cwd(thread)).name or thread.id}"

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
        return Path(self._codex_thread_cwd(thread)).name or None

    def _codex_thread_cwd(self, thread: Any) -> str:
        cwd = getattr(thread, "cwd", "")
        return getattr(cwd, "root", cwd)

    def _claude_thread_summary(self, info: ClaudeThreadInfo) -> ClaudeThreadSummary:
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

    def _find_imported_claude_session(
        self, thread_id: str, launch_target_id: str | None
    ) -> SessionRecord | None:
        for session in self.storage.list_sessions():
            if session.backend != Backend.CLAUDE_CODE:
                continue
            if session.thread_id != thread_id:
                continue
            if session.launch_target_id != launch_target_id:
                continue
            return session
        return None

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

    async def _record_user_event(
        self,
        session_id: str,
        text: str,
        submit: bool,
        status: SessionStatus = SessionStatus.RUNNING,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        metadata: dict[str, Any] = {"submit": submit, "status": status}
        if extra_metadata:
            metadata.update(extra_metadata)
        event = EventRecord(
            session_id=session_id,
            ts=datetime.now(UTC),
            kind=EventKind.USER_INPUT,
            text=text,
            metadata=metadata,
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

    def _generate_session_id(self, backend: str) -> str:
        token = secrets.token_hex(4)
        prefix = SAFE_NAME.sub("-", backend)
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
        backend: str,
        args: list[str],
        launch_target: SshLaunchTargetConfig | None = None,
        cwd: str | None = None,
    ) -> list[str]:
        if launch_target is None:
            plugin = self.registry.get(backend)
            executable = plugin.capabilities.cli_binary
            if executable is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"backend {backend} has no CLI binary configured",
                )
            return [executable, *args]
        return list(
            launch_target.remote_command_for_backend(
                backend, args, cwd or launch_target.default_cwd
            )
        )

    def _infer_backend(self, target: str) -> str:
        lowered = target.lower()
        for plugin in self.registry.all():
            for alias in plugin.capabilities.target_aliases:
                if alias and alias.lower() in lowered:
                    return plugin.id
        return Backend.CODEX.value

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
