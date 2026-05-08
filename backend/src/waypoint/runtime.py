import asyncio
import json
import logging
import re
import secrets
import shutil
from collections import defaultdict
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from waypoint.backends import BackendRegistry, get_registry
from waypoint.backends.tmux.adapter import TmuxAdapter, TmuxError
from waypoint.backends.tmux.normalize import TerminalNormalizer
from waypoint.git_meta import resolve_git_meta
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.scheduler import Scheduler
from waypoint.schemas import (
    CommandCompletion,
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
)
from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.transports import TransportAdapter

TMUX_TRANSPORT_ID = "tmux"

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
        self.monitor_tasks: dict[str, asyncio.Task[None]] = {}
        self._restore_tasks: set[asyncio.Task[None]] = set()
        self.file_offsets: dict[str, int] = {}
        self.registry = registry or get_registry()
        self._transports: dict[str, TransportAdapter] = {
            plugin.transport_id: plugin.transport_view(self)
            for plugin in self.registry.all()
        }
        for plugin in self.registry.all():
            plugin.setup(self)
        self.scheduler = Scheduler(self)

    def transport_for(self, session: SessionRecord) -> TransportAdapter:
        return self._transports[session.transport]

    async def start(self) -> None:
        for session in self.storage.list_sessions():
            # ERROR sessions get one passive restore attempt at boot — the
            # plugin's restore_session is responsible for tagging them
            # back to ERROR if reattach fails, which the auto-reconnect
            # loop (or the user's /reattach button) can then retry.
            # EXITED stays skipped: that's a terminal user choice.
            if session.status == SessionStatus.EXITED:
                continue
            plugin = self.registry.plugin_for(session)
            task = asyncio.create_task(
                plugin.restore_session(self, session),
                name=f"restore-{session.id}",
            )
            self._restore_tasks.add(task)
            task.add_done_callback(self._restore_tasks.discard)
        await self.scheduler.start()

    async def stop(self) -> None:
        await self.scheduler.stop()
        for task in self.monitor_tasks.values():
            task.cancel()
        for task in self.monitor_tasks.values():
            with suppress(asyncio.CancelledError):
                await task
        self.monitor_tasks.clear()
        restore_tasks = list(self._restore_tasks)
        for task in restore_tasks:
            task.cancel()
        for task in restore_tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._restore_tasks.clear()
        for plugin in self.registry.all():
            await plugin.shutdown(self)
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
            self.registry.get(request.backend).validate_permission_mode(
                request.permission_mode
            )
            or "default"
        )
        # Per-request model wins; otherwise fall back to the per-backend
        # default from the plugin's config block. ``None`` means "let the
        # backend pick" — we omit --model / params.model so the underlying
        # CLI uses its own default instead of waypoint forcing one. Same
        # precedence applies to reasoning effort.
        plugin_config = self.settings.plugin_config(request.backend)
        resolved_model = request.model or plugin_config.default_model_id
        resolved_effort = request.effort or plugin_config.default_effort
        # Pick the plugin that owns the session lifecycle: structured
        # backends launch their own protocol process; if the requested
        # backend reports its adapter isn't ready (e.g. the Claude
        # PreToolUse hook bundle failed to materialise), or if the
        # backend isn't structured at all, fall through to the
        # registry's wrapper plugin (today: tmux) so the user still
        # gets a session.
        plugin = self.registry.get(request.backend)
        if (
            not plugin.is_available_for_managed_launch(self)
            or not plugin.capabilities.is_structured
        ):
            fallback = self.registry.fallback_for_managed_launch()
            if fallback is not None:
                plugin = fallback
        return await plugin.create_session(
            self,
            request,
            session_id=session_id,
            launch_target=launch_target,
            title=title,
            raw_log=raw_log,
            structured_log=structured_log,
            git_meta=git_meta,
            permission_mode=permission_mode,
            resolved_model=resolved_model,
            resolved_effort=resolved_effort,
        )

    async def fork_session(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        plugin = self.registry.plugin_for(session)
        if not plugin.capabilities.supports_fork:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Backend {session.backend} does not support forking",
            )

        new_session_id = self._generate_session_id(session.backend)
        session_dir = self._session_dir(new_session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"

        # Seed the forked session's log files from the parent.
        for src, dst in [
            (Path(session.raw_log_path), raw_log),
            (Path(session.structured_log_path), structured_log),
        ]:
            if src.exists():
                shutil.copy2(src, dst)

        # Add branch/fork suffix to the original title if it doesn't already have one
        base_title = session.title or session.id
        match = re.match(r"^(.+) \(fork #(\d+)\)$", base_title)
        if match:
            new_title = f"{match.group(1)} (fork #{int(match.group(2)) + 1})"
        else:
            new_title = f"{base_title} (fork #1)"

        new_session = await plugin.fork_session(
            self,
            session,
            new_session_id,
            new_title,
            raw_log,
            structured_log,
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
        return new_session

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
            transport=TMUX_TRANSPORT_ID,
            title=title,
            cwd=target.cwd,
            repo_name=git_meta.repo_name,
            branch=git_meta.branch,
            status=SessionStatus.IDLE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            last_event_at=datetime.now(UTC),
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={
                "tmux_session": target.session,
                "tmux_window": target.window,
                "tmux_pane": target.pane,
                "pid": target.pane_pid,
            },
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
        plugin = self.registry.plugin_for(session)
        if transport.is_structured:
            handled = await plugin.maybe_handle_input(self, session, request)
            if handled is not None:
                return handled
        # Flip status and record the user event before send_input so the
        # broadcast snapshot carries status=RUNNING (Claude lags otherwise —
        # nothing comes back between stdin write and first content) and so
        # the user message keeps the lowest sequence number for the turn.
        # OpenCode's POST returns only after the server has already pushed
        # SSE events; recording the user event afterward would land it last
        # in the transcript. Revert on send failure so the UI doesn't show
        # a stuck "running" state for an unsent message.
        previous_status = session.status
        updated = self.storage.update_session(session.id, status=SessionStatus.RUNNING)
        await self._record_user_event(session.id, request.text, submit=request.submit)
        try:
            await transport.send_input(session, request.text)
        except Exception:
            self.storage.update_session(session.id, status=previous_status)
            raise
        return updated

    async def list_command_completions(
        self,
        session_id: str,
        *,
        trigger: str = "/",
        prefix: str = "",
        force_refresh: bool = False,
    ) -> list[CommandCompletion]:
        session = self.get_session(session_id)
        plugin = self.registry.plugin_for(session)
        return await plugin.list_command_completions(
            self,
            session,
            trigger=trigger,
            prefix=prefix,
            force_refresh=force_refresh,
        )

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
        # Route through the plugin (not the transport) so a session whose
        # adapter slot was never warmed in this process — e.g. an opencode
        # session terminated while the user's active backend is codex —
        # cleans up gracefully instead of 503'ing on `_require_adapter`.
        # Plugin hooks already do soft `.get()` lookups and no-op when the
        # adapter is missing.
        plugin = self.registry.plugin_for(session)
        await plugin.terminate_session(self, session)
        await self._record_system_event(
            session.id, "Session terminated", status=SessionStatus.EXITED
        )
        return self.storage.update_session(session.id, status=SessionStatus.EXITED)

    async def reattach(self, session_id: str) -> SessionRecord:
        # Explicit "reconnect this session" without sending a message.
        # Promotes the existing _reattach_session helper to a first-class
        # operation so the frontend can offer a button instead of forcing
        # users to type into a session they want to revive.
        session = self.get_session(session_id)
        return await self._reattach_session(session)

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
        await plugin.terminate_session(self, session)
        # User-initiated retry: bypass any per-target cooldown / circuit
        # breaker so the click takes effect immediately. Plugins without
        # this opt-in hook ignore the call.
        clear_cooldown = getattr(plugin, "clear_health_for_user_retry", None)
        if clear_cooldown is not None:
            clear_cooldown(self, session)
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

    async def delete(self, session_id: str, *, force: bool = False) -> None:
        session = self.get_session(session_id)
        if session.status != SessionStatus.EXITED:
            if force:
                # Last-resort path for sessions whose adapter is wedged
                # (e.g. SSH stuck and the plugin's terminate path can't
                # complete). Best-effort terminate, then drop the row no
                # matter what.
                with suppress(Exception):
                    await self.terminate(session_id)
            else:
                await self.terminate(session_id)
        self.storage.delete_session(session_id)
        self.registry.plugin_for(session).on_session_deleted(self, session)
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
        # Effort can be applied inline (Codex) or via a session restart
        # (Claude respawns the CLI with the new --effort). Plugins that
        # support neither (tmux) raise from `apply_effort`; we keep the
        # explicit gate here so the dispatcher returns a clean 400
        # without exercising the plugin path.
        caps = plugin.capabilities
        if not (
            caps.supports_set_effort_inline or caps.supports_set_effort_with_restart
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"effort selection is not supported for {session.backend}",
            )
        announce = await plugin.apply_effort(self, session, cleaned)
        # Restart-style swaps short-circuit when the value is unchanged
        # (apply_effort returns False); skip the storage write/broadcast
        # in that case so we don't churn for nothing.
        if not announce and not caps.supports_set_effort_inline:
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

    async def set_title(self, session_id: str, title: str) -> SessionRecord:
        session = self.get_session(session_id)
        if session.title == title:
            return session
        updated = self.storage.update_session(session_id, title=title)
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
        plugin = self.registry.plugin_for(session)
        return await plugin.answer_question(self, session, answer, tool_use_id, answers)

    async def approve(
        self, session_id: str, request: SessionApprovalRequest
    ) -> SessionRecord:
        session = self.get_session(session_id)
        transport = self.transport_for(session)
        if transport.is_structured:
            handled = await transport.respond_to_approval(
                session, request.decision, request.text, request.approval_id
            )
            if not handled:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="no pending approval request",
                )
            # Stay WAITING_INPUT if more approvals remain so the pager stays
            # visible; flip to RUNNING only once the queue fully drains.
            next_status = (
                SessionStatus.WAITING_INPUT
                if transport.has_pending_approval(session)
                else SessionStatus.RUNNING
            )
            updated = self.storage.update_session(session.id, status=next_status)
            await self._record_system_event(
                session.id,
                f"Approval response sent: {request.decision}",
                status=next_status,
                metadata=(
                    {"approval_id": request.approval_id}
                    if request.approval_id
                    else None
                ),
            )
            plugin = self.registry.plugin_for(session)
            await plugin.post_approval(self, session)
            return updated
        await transport.respond_to_approval(session, request.decision, request.text)
        return self.get_session(session_id)

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
                    "supported_backends": target.supported_plugins(),
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
        plugin = self.registry.get(backend)
        if launch_target is None:
            executable = (
                self.settings.plugin_config(backend).local_bin
                or plugin.capabilities.cli_binary
            )
            if executable is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"backend {backend} has no CLI binary configured",
                )
            return [executable, *args]
        executable = plugin.remote_executable(launch_target)
        if not executable:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"backend {backend} has no remote binary configured",
            )
        return list(
            launch_target.build_remote_exec_args(
                [executable, *args], cwd or launch_target.default_cwd
            )
        )

    def _infer_backend(self, target: str) -> str:
        lowered = target.lower()
        for plugin in self.registry.all():
            for alias in plugin.capabilities.target_aliases:
                if alias and alias.lower() in lowered:
                    return plugin.id
        return self.settings.default_backend

    def _ensure_monitor(self, session_id: str) -> None:
        if session_id in self.monitor_tasks:
            return
        session = self.get_session(session_id)
        if session.transport != TMUX_TRANSPORT_ID:
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
        state = session.transport_state
        target = state.get("tmux_pane") or state.get("tmux_session") or session.id
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
        new_state = {**state, "pid": target_info.pane_pid}
        updates: dict[str, Any] = {"transport_state": new_state}
        if target_info.pane_dead and session.status != SessionStatus.EXITED:
            log.info(
                "tmux pane reported dead",
                extra={"session_id": session.id, "target": target},
            )
            updates["status"] = SessionStatus.EXITED
        self.storage.update_session(session.id, **updates)
