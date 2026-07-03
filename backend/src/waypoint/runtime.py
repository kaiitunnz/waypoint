import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, TextIO

from fastapi import HTTPException, status

from waypoint.assistant_assets import AssistantAssetError, ensure_assistant_assets
from waypoint.attachments import AttachmentStore, ResolvedAttachment
from waypoint.backends import BackendRegistry, get_registry
from waypoint.backends.completions import static_slash_completions
from waypoint.backends.tmux.adapter import TmuxAdapter, TmuxError
from waypoint.backends.tmux.normalize import (
    TMUX_CONTENT_KINDS,
    NormalizedChunk,
    TerminalNormalizer,
)
from waypoint.builtin_completions import waypoint_builtin_completions
from waypoint.git_meta import resolve_git_meta
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.perf import debug_timer
from waypoint.scheduler import Scheduler
from waypoint.schemas import (
    AssistantSummary,
    AttachmentSpec,
    BoardChannel,
    BoardEntry,
    BoardEntryUpdateRequest,
    BoardPostRequest,
    CommandCompletion,
    EventKind,
    EventRecord,
    EventsPageResponse,
    InboxBlockInput,
    InboxItem,
    InboxListResponse,
    InboxPostRequest,
    InboxReplyInput,
    InboxStatus,
    LaunchMode,
    SessionApprovalRequest,
    SessionAttachRequest,
    SessionCommandInvocation,
    SessionCreateRequest,
    SessionEnvelope,
    SessionInputRequest,
    SessionPlanApprovalRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.ssh_master import SshMasterManager, SshMasterStatus
from waypoint.storage import Storage
from waypoint.transports import TransportAdapter

TMUX_TRANSPORT_ID = "tmux"
COMPLETION_REFRESH_INTERVAL_SECONDS = 30.0
# Debounce window for streaming-driven session_state / session_list
# broadcasts. A burst of streamed events collapses into one broadcast per
# window instead of one per token; lifecycle changes still broadcast
# immediately, so this only adds at most this much latency to the live
# list/status updates a fast stream produces.
SESSION_BROADCAST_DEBOUNCE_SECONDS = 0.25
# The per-session structured log (events.jsonl) is a write-only audit/debug
# artifact; the SQLite store is the source of truth for replay. Flushing every
# write turns the streaming path into a syscall per event, so we let the buffer
# absorb a short run and flush once it crosses this many writes (and always on
# close). A crash can lose at most the last few un-flushed lines, all of which
# are still durable in SQLite.
STRUCTURED_LOG_FLUSH_EVERY = 16
# Stable sentinel returned when a launch's working directory does not exist.
# The frontend matches it (like ``ssh-master-required``) to surface an inline
# error on the working-directory field instead of the generic banner.
CWD_NOT_FOUND_DETAIL = "cwd-not-found"


log = logging.getLogger("waypoint.runtime")


def require_existing_local_dir(cwd: str) -> str:
    """Expand ``~`` and return the resolved local cwd, raising 400 if it is not
    an existing directory.

    An unchecked missing cwd is handed to tmux / ``Popen``, which silently fall
    back to ``$HOME``; the agent then runs in the wrong directory and — for the
    tty-tail transport — writes its transcript where the tailer is not watching,
    leaving an empty transcript. Failing fast surfaces the typo instead.
    """
    resolved = str(Path(cwd).expanduser())
    if not Path(resolved).is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=CWD_NOT_FOUND_DETAIL,
        )
    return resolved


@dataclass
class _StructuredLogHandle:
    """Open append handle for a session's structured log plus its write count
    since the last flush, so the streaming path can batch flushes."""

    handle: TextIO
    pending: int = 0


SAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]+")
CompletionCacheKey = tuple[str, str]


def _raw_log_end_offset(path: Path) -> int:
    # Returns a text-mode tell() cookie at EOF, matching the offsets
    # _ingest_raw_output records so a later seek() round-trips cleanly.
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            handle.seek(0, os.SEEK_END)
            return handle.tell()
    except OSError:
        return 0


def _read_and_normalize(
    normalizer: TerminalNormalizer,
    session_id: str,
    path: Path,
    offset: int,
    start_sequence: int,
) -> tuple[int, NormalizedChunk | None]:
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        handle.seek(offset)
        chunk = handle.read()
        new_offset = handle.tell()
    if not chunk.strip():
        return new_offset, None
    return new_offset, normalizer.normalize(session_id, chunk, start_sequence)


class BroadcastHub:
    def __init__(self) -> None:
        self.global_queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self.session_queues: dict[str, set[asyncio.Queue[dict[str, Any]]]] = (
            defaultdict(set)
        )
        # Per-inbox-item streams for ``/ws/inbox/{id}`` (drives ``inbox wait``).
        # A third keyed set alongside the session queues; ``publish`` stays
        # generic — it just fans onto whichever keyed set the caller names.
        self.inbox_queues: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(
            set
        )

    def subscribe_global(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.global_queues.add(queue)
        return queue

    def subscribe_session(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.session_queues[session_id].add(queue)
        return queue

    def subscribe_inbox(self, item_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.inbox_queues[item_id].add(queue)
        return queue

    def unsubscribe_global(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.global_queues.discard(queue)

    def unsubscribe_session(
        self, session_id: str, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        self.session_queues[session_id].discard(queue)
        if not self.session_queues[session_id]:
            self.session_queues.pop(session_id, None)

    def unsubscribe_inbox(
        self, item_id: str, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        self.inbox_queues[item_id].discard(queue)
        if not self.inbox_queues[item_id]:
            self.inbox_queues.pop(item_id, None)

    async def publish(
        self,
        message: SessionEnvelope,
        session_id: str | None = None,
        inbox_id: str | None = None,
    ) -> None:
        payload = message.model_dump(mode="json")
        for queue in list(self.global_queues):
            await queue.put(payload)
        if session_id is not None:
            for queue in list(self.session_queues.get(session_id, set())):
                await queue.put(payload)
        if inbox_id is not None:
            for queue in list(self.inbox_queues.get(inbox_id, set())):
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
        self.attachments = AttachmentStore(settings.attachments_dir)
        self.tmux = TmuxAdapter()
        self.normalizer = TerminalNormalizer()
        self.broadcast = BroadcastHub()
        self.ssh_targets = {
            target.id: target for target in self.settings.ssh_targets if target.enabled
        }
        # Owns the password-authenticated ControlMaster sockets for
        # ``ssh_auth == "password"`` targets. Key-auth targets never touch it.
        self.ssh_master = SshMasterManager()
        self.monitor_tasks: dict[str, asyncio.Task[None]] = {}
        # Per-session helpers that capture the agent's native thread id once
        # the underlying CLI has materialized it on disk (Codex writes its
        # rollout file only after the first user input). Spawned by the tmux
        # pane wrapper and by the Codex adapter; stored here so terminate can
        # cancel them alongside the pane monitor.
        self._thread_id_watchers: dict[str, asyncio.Task[None]] = {}
        # Periodic rate-limit refreshers. Structured backends start their own
        # per-session probe inside the SDK adapter at create time; tmux-wrapped
        # sessions have no adapter and need this fallback to keep the usage pill
        # live.
        self._rate_limit_watchers: dict[str, asyncio.Task[None]] = {}
        # Per-session context-usage sources for non-structured transports.
        # Started when a session with is_structured=False is created or restored;
        # cancelled on session exit alongside the other per-session tasks.
        self._context_usage_sources: dict[str, asyncio.Task[None]] = {}
        self._restore_tasks: set[asyncio.Task[None]] = set()
        self._completion_cache: dict[CompletionCacheKey, list[CommandCompletion]] = {}
        self._completion_cache_updated_at: dict[CompletionCacheKey, float] = {}
        self._completion_refresh_tasks: dict[
            CompletionCacheKey, asyncio.Task[list[CommandCompletion]]
        ] = {}
        self.file_offsets: dict[str, int] = {}
        # Open append handles for per-session structured logs, kept around
        # so the streaming path doesn't reopen (and re-stat via get_session)
        # the file on every event. Closed on terminate/delete and at stop().
        self._structured_log_handles: dict[str, _StructuredLogHandle] = {}
        # Coalesced session_state / session_list broadcasting. The
        # streaming event path marks sessions dirty and wakes the flusher
        # instead of re-serializing every session per event; the flusher
        # debounces and emits one broadcast per window. See
        # ``_session_broadcast_loop``.
        self._dirty_session_states: set[str] = set()
        self._session_list_dirty = False
        self._broadcast_wake = asyncio.Event()
        self._broadcast_flusher: asyncio.Task[None] | None = None
        # Id of the personal-assistant singleton, populated by
        # ``_ensure_assistant_session`` during ``start``. ``None`` when the
        # assistant is disabled or its bootstrap failed.
        self.assistant_session_id: str | None = None
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
        self._broadcast_flusher = asyncio.create_task(
            self._session_broadcast_loop(), name="session-broadcast-flusher"
        )
        for session in self.storage.list_sessions():
            # ERROR sessions get one passive restore attempt at boot — the
            # plugin's restore_session is responsible for tagging them
            # back to ERROR if reattach fails, which the auto-reconnect
            # loop (or the user's /reattach button) can then retry.
            # EXITED stays skipped: that's a terminal user choice.
            if session.status == SessionStatus.EXITED:
                continue
            # A password-auth target has no live ControlMaster at boot, so a
            # restore would block on an unanswerable password prompt. Leave the
            # session in its stored state for the user to reconnect (prompting
            # for the password) and /reattach.
            launch_target = self._find_launch_target(session.launch_target_id)
            if (
                launch_target is not None
                and launch_target.requires_password
                and not self.ssh_master.is_connected_cached(launch_target)
            ):
                continue
            plugin = self.registry.plugin_for(session)
            task = asyncio.create_task(
                self._restore_session_and_warm_completions(plugin, session),
                name=f"restore-{session.id}",
            )
            self._restore_tasks.add(task)
            task.add_done_callback(self._restore_tasks.discard)
        # Bring up the personal-assistant singleton after scheduling
        # restores so a still-alive assistant is reused rather than
        # recreated. A bootstrap failure must never abort startup.
        try:
            await self._ensure_assistant_session()
        except Exception:
            log.exception("failed to ensure the assistant session")
        for plugin in self.registry.all():
            # Optional lifecycle hook; not part of the BackendPlugin protocol.
            hook = getattr(plugin, "start_background_tasks", None)
            if callable(hook):
                asyncio.create_task(
                    hook(self),
                    name=f"plugin-bg-{plugin.id}",
                )
        await self.scheduler.start()

    async def stop(self) -> None:
        await self.scheduler.stop()
        for task in self.monitor_tasks.values():
            task.cancel()
        for task in self.monitor_tasks.values():
            with suppress(asyncio.CancelledError):
                await task
        self.monitor_tasks.clear()
        for task in self._context_usage_sources.values():
            task.cancel()
        for task in self._context_usage_sources.values():
            with suppress(asyncio.CancelledError):
                await task
        self._context_usage_sources.clear()
        restore_tasks = list(self._restore_tasks)
        for task in restore_tasks:
            task.cancel()
        for task in restore_tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._restore_tasks.clear()
        completion_tasks = list(self._completion_refresh_tasks.values())
        self._completion_refresh_tasks.clear()
        for completion_task in completion_tasks:
            completion_task.cancel()
        for completion_task in completion_tasks:
            with suppress(asyncio.CancelledError):
                await completion_task
        if self._broadcast_flusher is not None:
            self._broadcast_flusher.cancel()
            with suppress(asyncio.CancelledError):
                await self._broadcast_flusher
            self._broadcast_flusher = None
        for entry in self._structured_log_handles.values():
            with suppress(Exception):
                entry.handle.close()
        self._structured_log_handles.clear()
        for plugin in self.registry.all():
            await plugin.shutdown(self)
        await self.ssh_master.disconnect_all(list(self.ssh_targets.values()))
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

    def _validate_supported_transport(self, backend: str, transport: str) -> None:
        """Reject a transport the named agent does not declare support for.

        Shared by every launch entry point (create, schedule, import) so a
        pinned transport is validated identically wherever a session begins.
        """
        supported = self.registry.supported_transports(backend)
        if transport not in supported:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{backend} cannot be driven over transport "
                    f"{transport!r}; supported: {', '.join(supported)}"
                ),
            )

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
        await self._require_live_master(launch_target)
        # Local cwd is fed to subprocess.Popen / tmux new-session, neither of
        # which expand `~`. Resolve it before storing/launching. The remote
        # cwd is left verbatim so the remote shell can do its own expansion.
        if launch_target is not None:
            local_cwd = request.cwd or launch_target.default_cwd
        else:
            local_cwd = require_existing_local_dir(request.cwd)
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
                self._effective_permission_mode(request)
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
        plugin = self.registry.get(request.backend)
        if plugin.capabilities.is_fallback_for_managed_launch:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{request.backend} is a managed-launch wrapper and "
                    "cannot be requested as the target backend"
                ),
            )
        # New-session preflight: an agent that can prove the installed CLI
        # won't honor this model/effort combo raises ValueError here. Not
        # consulted for resume / set-model / set-effort — only brand-new
        # sessions. Optional method (see BackendPlugin's docstring), read
        # defensively so agents that don't implement it (or predate it)
        # still launch unimpeded.
        preflight = getattr(plugin, "validate_new_session_selection", None)
        if preflight is not None:
            try:
                await asyncio.to_thread(
                    preflight,
                    self,
                    resolved_model,
                    resolved_effort,
                    request.launch_target_id,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                ) from exc
        launch_mode = request.launch_mode
        # An omitted transport under AUTO launch resolves to the agent's
        # declared default transport, so every spawn path (UI, CLI, spawned
        # subagents, the scheduler) launches over the same default the catalog
        # advertises rather than only the frontend honoring it. An explicit
        # transport or a non-AUTO launch_mode still wins. Remote (SSH) launch
        # targets are excluded: the local tty-tail/tmux transports can't drive a
        # process on another host, so those keep the agent's native structured
        # launch.
        default_transport = (
            getattr(plugin, "default_transport", plugin.transport_id)
            if request.transport is None
            and launch_mode == LaunchMode.AUTO
            and launch_target is None
            else None
        )
        pinned_transport = request.transport or default_transport
        if pinned_transport is not None:
            # A pinned transport selects the driving plugin via the
            # (agent, transport) pair and takes precedence over launch_mode. The
            # transport must be one the agent declares; the resolved plugin owns
            # that transport (the agent's native adapter, the tty-tail driver, or
            # the tmux wrapper).
            self._validate_supported_transport(request.backend, pinned_transport)
            plugin = self.registry.resolve(request.backend, pinned_transport)
            # When the transport came from the default (not an explicit pin) and
            # its driver isn't ready or isn't structured, fall through to the
            # managed-launch wrapper so a spawn the user didn't pin still yields
            # a session — mirroring the legacy launch_mode-derived fallback.
            if default_transport is not None and (
                not plugin.is_available_for_managed_launch(self)
                or not plugin.capabilities.is_structured
            ):
                fallback = self.registry.fallback_for_managed_launch()
                if fallback is not None:
                    plugin = fallback
        elif launch_mode == LaunchMode.TMUX_WRAPPER:
            fallback = self.registry.fallback_for_managed_launch()
            if fallback is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="tmux fallback launch is not available",
                )
            plugin = fallback
        elif launch_mode == LaunchMode.DIRECT:
            if not plugin.capabilities.is_structured:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{request.backend} cannot be launched directly",
                )
            if not plugin.is_available_for_managed_launch(self):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{request.backend} is not available for direct launch",
                )
        # Structured backends launch their own protocol process; if the
        # requested backend reports its adapter isn't ready (e.g. the
        # Claude PreToolUse hook bundle failed to materialise), or if
        # the backend isn't structured at all, fall through to the
        # registry's wrapper plugin (today: tmux) so the user still
        # gets a session.
        elif (
            not plugin.is_available_for_managed_launch(self)
            or not plugin.capabilities.is_structured
        ):
            fallback = self.registry.fallback_for_managed_launch()
            if fallback is not None:
                plugin = fallback
        session = await plugin.create_session(
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
        if request.worktree_path is not None:
            session = self.storage.update_session(
                session.id, worktree_path=request.worktree_path
            )
        # Stamp tags generically after the plugin builds the record, so every
        # backend picks them up without a per-plugin launch-site edit.
        if request.tags:
            session = self.storage.update_session(session.id, tags=request.tags)
        self._warm_command_completions(session)
        self._start_context_usage_source(session)
        return session

    def _effective_permission_mode(self, request: SessionCreateRequest) -> str | None:
        """Resolve the permission mode to validate for a new session.

        An explicitly requested mode always wins (and may widen). Otherwise a
        child inherits its spawner's mode when they share a backend — modes are
        not portable across backends, so a cross-backend spawn falls back to the
        default rather than copying an invalid value.
        """
        if request.permission_mode is not None:
            return request.permission_mode
        if request.spawner_session_id:
            spawner = self.storage.get_session(request.spawner_session_id)
            if spawner is not None and spawner.backend == request.backend:
                return spawner.permission_mode
        return None

    async def _ensure_assistant_session(self) -> None:
        """Create / reuse / replace the personal-assistant singleton.

        Reuses a still-alive assistant whose backend matches the
        configured one. Otherwise demotes any stale assistant rows to
        ``MANAGED`` (preserving their transcripts as ordinary sessions —
        never destructive) and creates a fresh assistant. When the
        assistant is disabled, all assistant rows are released back to
        ``MANAGED`` and no singleton is tracked.
        """
        target_backend = self.settings.assistant_backend()
        existing = [
            session
            for session in self.storage.list_sessions()
            if session.source == SessionSource.ASSISTANT
        ]
        if target_backend is None:
            for session in existing:
                self.storage.update_session(
                    session.id, source=SessionSource.MANAGED, pinned_at=None
                )
            self.assistant_session_id = None
            return
        # Refresh assistant workspace asset links on every boot so repo
        # updates to AGENTS.md / CLAUDE.md / skills are visible to backends
        # without recreating the live thread. If the refresh fails, keep a
        # healthy live assistant tracked rather than making asset validation a
        # new availability gate for an existing thread.
        asset_error: Exception | None = None
        try:
            self._prepare_assistant_workspace()
        except (AssistantAssetError, OSError) as exc:
            asset_error = exc
            log.warning("failed to refresh assistant workspace assets", exc_info=True)
        # Reuse any live assistant that already lives in the managed workspace,
        # regardless of its backend: the live thread is the source of truth, so
        # a backend switched from the UI survives a redeploy. ``assistant_backend()``
        # only seeds the first-ever creation below; later YAML edits to
        # ``assistant.backend`` are ignored while a thread exists (clear context
        # to re-seed). The cwd clause migrates assistants created by older builds
        # (different cwd, bootstrap instructions sent as a visible message) to a fresh,
        # silently-chartered thread.
        workspace = str(self._assistant_workspace_dir())
        live = next(
            (
                session
                for session in existing
                if session.status not in {SessionStatus.EXITED, SessionStatus.ERROR}
                and session.cwd == workspace
            ),
            None,
        )
        if live is not None:
            self.assistant_session_id = live.id
            for session in existing:
                if session.id != live.id:
                    self.storage.update_session(
                        session.id, source=SessionSource.MANAGED, pinned_at=None
                    )
            return
        if asset_error is not None:
            raise asset_error
        for session in existing:
            self.storage.update_session(
                session.id, source=SessionSource.MANAGED, pinned_at=None
            )
        assistant = self.settings.assistant
        assert assistant is not None  # guarded by target_backend is not None
        created = await self._create_assistant_session(
            target_backend,
            model=assistant.model,
            effort=assistant.effort,
            permission_mode=assistant.permission_mode,
            transport=assistant.transport,
        )
        self.assistant_session_id = created.id

    async def _create_assistant_session(
        self,
        backend: str,
        *,
        model: str | None,
        effort: str | None,
        permission_mode: str | None,
        transport: str | None,
    ) -> SessionRecord:
        plugin = self.registry.get(backend)
        validated_mode = (
            plugin.validate_permission_mode(permission_mode)
            if permission_mode
            else None
        )
        workspace = self._prepare_assistant_workspace()
        request = SessionCreateRequest(
            backend=backend,
            cwd=workspace,
            title="Personal Assistant",
            model=model,
            effort=effort,
            permission_mode=validated_mode,
            transport=transport,
        )
        session = await self.create_session(request)
        return self.storage.update_session(
            session.id,
            source=SessionSource.ASSISTANT,
            pinned_at=datetime.now(UTC),
        )

    def _assistant_workspace_dir(self) -> Path:
        return self.settings.data_dir / "assistant"

    def _prepare_assistant_workspace(self) -> str:
        """Ensure the assistant's working dir and repo-tracked assets exist.

        The bootstrap files and skills are linked from the repo by default so
        updates are cheap and visible to backends that reload project context.
        The directory is a scratch cwd; shell access reaches the whole host.
        """
        workspace = self._assistant_workspace_dir()
        ensure_assistant_assets(workspace, config_path=self.settings.config_path)
        return str(workspace)

    def is_assistant_session(self, session: SessionRecord) -> bool:
        return session.source == SessionSource.ASSISTANT

    def assistant_summary(self) -> AssistantSummary | None:
        session_id = self.assistant_session_id
        if session_id is None:
            return None
        session = self.storage.get_session(session_id)
        if session is None:
            return None
        plugin = self.registry.plugin_for(session)
        return AssistantSummary(
            session_id=session.id,
            backend=session.backend,
            transport=session.transport,
            native_thread_id=plugin.native_thread_id(session),
            status=session.status,
            supports_reattach=plugin.capabilities.supports_reattach_after_exit,
        )

    def _require_assistant_summary(self) -> AssistantSummary:
        summary = self.assistant_summary()
        if summary is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="the assistant is disabled",
            )
        return summary

    async def reset_assistant(
        self,
        *,
        backend: str | None = None,
        transport: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        permission_mode: str | None = None,
    ) -> AssistantSummary:
        """Rebuild the assistant on a fresh thread (clear context / switch backend).

        Clearing context keeps the *current* thread's backend and live config
        (model / effort / permission mode / transport), so a context wipe
        doesn't silently revert tuning done from the UI; only an explicit
        ``backend`` switch overrides them, since model/effort/transport are
        backend-specific. waypoint.yaml only seeds the first creation, when no
        live thread exists.

        The previous thread is demoted to an ordinary stopped session so its
        transcript survives in the normal session list — it is never deleted.
        Because boot reuses whatever thread is live, the chosen backend persists
        across redeploys.
        """
        target_backend = self.settings.assistant_backend()
        if target_backend is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="the assistant is disabled",
            )
        old_id = self.assistant_session_id
        old = self.storage.get_session(old_id) if old_id is not None else None
        chosen = backend or (old.backend if old is not None else target_backend)
        if not self.registry.has_backend(chosen):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown backend: {chosen}",
            )
        # Inherit the live thread's config when staying on the same backend;
        # an explicit request value always wins. A backend switch starts from
        # that backend's defaults because model/effort/transport don't transfer.
        if old is not None and chosen == old.backend:
            model = model if model is not None else old.model
            effort = effort if effort is not None else old.effort
            permission_mode = (
                permission_mode if permission_mode is not None else old.permission_mode
            )
            transport = transport if transport is not None else old.transport
        # Spawn the replacement before touching the current thread so a failed
        # launch (e.g. a misconfigured backend) leaves the live, pinned
        # assistant intact rather than orphaning the pointer at a stopped row.
        created = await self._create_assistant_session(
            chosen,
            model=model,
            effort=effort,
            permission_mode=permission_mode,
            transport=transport,
        )
        self.assistant_session_id = created.id
        await self._retire_previous_assistant(old, created.id)
        return self._require_assistant_summary()

    async def import_thread(self, backend: str, body: dict[str, Any]) -> SessionRecord:
        """Import a backend-side thread as a session, honoring a pinned transport.

        Mirrors ``create_session``: the agent id selects the importing plugin
        and is persisted as the session's ``backend``, while a pinned transport
        selects the plugin that drives the resulting session. The agent owns
        thread-metadata lookup, so the tmux wrapper — which cannot enumerate
        threads — is driven through the agent plugin's resume-via-tmux
        delegation rather than called directly. ``transport=None`` preserves the
        ``launch_mode``-derived path exactly.
        """
        if not self.registry.has_backend(backend):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown backend: {backend}",
            )
        agent_plugin = self.registry.get(backend)
        if agent_plugin.capabilities.is_fallback_for_managed_launch:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{backend} is a managed-launch wrapper and "
                    "cannot be requested as the target backend"
                ),
            )
        schema = agent_plugin.import_request_schema
        if not agent_plugin.capabilities.supports_thread_import or schema is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"thread import is not supported for {backend}",
            )
        request = schema.model_validate(body)
        transport = getattr(request, "transport", None)
        if transport is not None:
            self._validate_supported_transport(backend, transport)
            driver = self.registry.resolve(backend, transport)
            # resolve() returns the transport owner; for a wrapper transport
            # (tmux) that owner cannot enumerate threads, so the agent plugin
            # drives the resume-via-tmux path instead.
            if not driver.capabilities.supports_thread_import:
                driver = agent_plugin
        else:
            driver = agent_plugin
        return await driver.import_thread(self, request, agent=backend)

    async def attach_assistant(
        self,
        *,
        backend: str,
        thread_id: str,
        launch_target_id: str | None = None,
    ) -> AssistantSummary:
        """Adopt an existing backend-native thread as the assistant singleton.

        The thread is imported as-is — it resumes its own conversation and
        working directory, so the assistant charter (which lives in the managed
        workspace) does not apply. The previous thread is demoted to an ordinary
        stopped session, never deleted. Because the imported thread lives outside
        the managed workspace, a redeploy will not re-adopt it and falls back to
        a fresh assistant.
        """
        if self.settings.assistant_backend() is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="the assistant is disabled",
            )
        old_id = self.assistant_session_id
        old = self.storage.get_session(old_id) if old_id is not None else None
        # Import before touching the current thread so a failed import (unknown
        # thread, already imported) leaves the live assistant intact. The
        # assistant always adopts a thread over the agent's native transport,
        # so no transport is pinned here.
        imported = await self.import_thread(
            backend, {"thread_id": thread_id, "launch_target_id": launch_target_id}
        )
        adopted = self.storage.update_session(
            imported.id, source=SessionSource.ASSISTANT, pinned_at=datetime.now(UTC)
        )
        self.assistant_session_id = adopted.id
        await self._retire_previous_assistant(old, adopted.id)
        return self._require_assistant_summary()

    async def _retire_previous_assistant(
        self, old: SessionRecord | None, new_id: str
    ) -> None:
        """Demote the prior assistant thread to a normal stopped session.

        Releases the protection guard, then best-effort stops it. The demoted
        row lingers in the normal session list so its transcript is preserved —
        it is never deleted.
        """
        if old is None or old.id == new_id:
            return
        self.storage.update_session(
            old.id, source=SessionSource.MANAGED, pinned_at=None
        )
        with suppress(Exception):
            await self.terminate(old.id)

    async def terminate_assistant(self) -> AssistantSummary:
        """Stop the assistant thread while keeping it the pinned singleton.

        Unlike clearing context, the native thread and transcript are kept so
        ``reattach_assistant`` can resume the same conversation.
        """
        session_id = self.assistant_session_id
        if session_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="the assistant is disabled",
            )
        session = self.get_session(session_id)
        if session.status != SessionStatus.EXITED:
            plugin = self.registry.plugin_for(session)
            await plugin.terminate_session(self, session)
            await self._record_system_event(
                session.id, "Assistant terminated", status=SessionStatus.EXITED
            )
            self.storage.update_session(session.id, status=SessionStatus.EXITED)
        return self._require_assistant_summary()

    async def reattach_assistant(self) -> AssistantSummary:
        session_id = self.assistant_session_id
        if session_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="the assistant is disabled",
            )
        await self.reattach(session_id)
        return self._require_assistant_summary()

    async def fork_side_question(
        self, session_id: str, side_question_id: str
    ) -> SessionRecord:
        session = self.get_session(session_id)
        plugin = self.registry.plugin_for(session)

        new_session_id = self._generate_session_id(session.backend)
        session_dir = self._session_dir(new_session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"

        for src, dst in [
            (Path(session.raw_log_path), raw_log),
            (Path(session.structured_log_path), structured_log),
        ]:
            if src.exists():
                shutil.copy2(src, dst)

        title = f"{session.title or session.id} (btw)"
        new_session = await plugin.fork_side_question(
            self,
            session,
            side_question_id,
            new_session_id=new_session_id,
            title=title,
            raw_log=raw_log,
            structured_log=structured_log,
        )
        await self._broadcast_session_list()
        return new_session

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
        self._warm_command_completions(new_session)
        await self._broadcast_session_list()
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
            request = self._trust_server_completion_invocation(session, request)
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
        attachments = self._resolve_attachments(session.id, request.attachments)
        previous_status = session.status
        updated = self.storage.update_session(session.id, status=SessionStatus.RUNNING)
        await self._record_user_event(
            session.id,
            request.text,
            submit=request.submit,
            attachments=[item.spec for item in attachments],
        )
        # The recorded user event now references these blobs, so exempt them
        # from the orphan sweep; then reap any earlier eager uploads this
        # session never sent.
        if attachments:
            self.attachments.mark_sent(
                session.id, [item.spec.id for item in attachments]
            )
        self.attachments.sweep(session.id, self.settings.attachment_orphan_ttl_seconds)
        try:
            await transport.send_input(session, request.text, attachments or None)
        except Exception:
            self.storage.update_session(session.id, status=previous_status)
            raise
        return updated

    def _resolve_attachments(
        self, session_id: str, attachment_ids: list[str] | None
    ) -> list[ResolvedAttachment]:
        if not attachment_ids:
            return []
        resolved: list[ResolvedAttachment] = []
        for attachment_id in attachment_ids:
            match = self.attachments.resolve(session_id, attachment_id)
            if match is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"attachment not found: {attachment_id}",
                )
            spec, path = match
            resolved.append(ResolvedAttachment(spec=spec, path=path))
        return resolved

    async def list_command_completions(
        self,
        session_id: str,
        *,
        trigger: str = "/",
        prefix: str = "",
        force_refresh: bool = False,
    ) -> list[CommandCompletion]:
        completions, _refreshing = await self.get_command_completions(
            session_id,
            trigger=trigger,
            prefix=prefix,
            force_refresh=force_refresh,
        )
        return completions

    async def get_command_completions(
        self,
        session_id: str,
        *,
        trigger: str = "/",
        prefix: str = "",
        force_refresh: bool = False,
    ) -> tuple[list[CommandCompletion], bool]:
        session = self.get_session(session_id)
        plugin = self.registry.plugin_for(session)
        key = (session.id, trigger)
        builtins = waypoint_builtin_completions(plugin.capabilities, trigger=trigger)
        if force_refresh:
            completions = await self._refresh_command_completion_cache(
                session,
                trigger=trigger,
                force_refresh=True,
            )
            return (
                self._filter_command_completions(
                    self._merge_builtins(builtins, completions),
                    trigger=trigger,
                    prefix=prefix,
                ),
                False,
            )

        cached = self._completion_cache.get(key)
        updated_at = self._completion_cache_updated_at.get(key, 0.0)
        if (
            cached is None
            or monotonic() - updated_at >= COMPLETION_REFRESH_INTERVAL_SECONDS
        ):
            self._ensure_command_completion_refresh(session, trigger=trigger)

        if cached is None:
            cached = self._fallback_command_completions(
                plugin,
                trigger=trigger,
                prefix=prefix,
            )
            return (
                self._filter_command_completions(
                    self._merge_builtins(builtins, cached),
                    trigger=trigger,
                    prefix=prefix,
                ),
                key in self._completion_refresh_tasks,
            )

        return (
            self._filter_command_completions(
                self._merge_builtins(builtins, cached),
                trigger=trigger,
                prefix=prefix,
            ),
            key in self._completion_refresh_tasks,
        )

    @staticmethod
    def _merge_builtins(
        builtins: list[CommandCompletion],
        plugin_completions: list[CommandCompletion],
    ) -> list[CommandCompletion]:
        if not builtins:
            return list(plugin_completions)
        # Waypoint built-ins win on name collision — the user-facing
        # `/new` should always open the Waypoint new-session UI, even
        # for backends (e.g. opencode) that also expose their own
        # backend-scoped `/new`.
        seen = {f"{item.trigger}{item.name}" for item in builtins}
        return [*builtins] + [
            item
            for item in plugin_completions
            if f"{item.trigger}{item.name}" not in seen
        ]

    async def _restore_session_and_warm_completions(
        self, plugin: Any, session: SessionRecord
    ) -> None:
        await plugin.restore_session(self, session)
        refreshed = self.storage.get_session(session.id)
        if refreshed is not None:
            # Boot-restore warming is fire-and-forget for every persisted
            # session, so we skip remote targets to avoid fanning out
            # SSH/plugin-list probes against hosts the user may never
            # open. New SSH sessions still warm eagerly via
            # ``create_session`` / fork; reattached ones fall back to
            # stale-while-revalidate on the first `/` press.
            self._warm_command_completions(refreshed, include_remote=False)
            self._start_context_usage_source(refreshed)

    def _warm_command_completions(
        self, session: SessionRecord, *, include_remote: bool = True
    ) -> None:
        if not include_remote and session.launch_target_id is not None:
            return
        try:
            transport = self.transport_for(session)
        except KeyError:
            return
        if not transport.is_structured:
            return
        for trigger in ("/", "$"):
            self._ensure_command_completion_refresh(session, trigger=trigger)

    def _ensure_command_completion_refresh(
        self, session: SessionRecord, *, trigger: str
    ) -> None:
        key = (session.id, trigger)
        task = self._completion_refresh_tasks.get(key)
        if task is not None and not task.done():
            return
        task = asyncio.create_task(
            self._refresh_command_completion_cache(
                session,
                trigger=trigger,
                force_refresh=True,
            ),
            name=f"completion-refresh-{session.id}-{trigger}",
        )
        self._completion_refresh_tasks[key] = task

        def _finish(completed: asyncio.Task[list[CommandCompletion]]) -> None:
            self._completion_refresh_tasks.pop(key, None)
            if completed.cancelled():
                return
            try:
                completed.result()
            except Exception:
                log.exception(
                    "failed to refresh command completions for session %s trigger %s",
                    session.id,
                    trigger,
                )

        task.add_done_callback(_finish)

    async def _refresh_command_completion_cache(
        self,
        session: SessionRecord,
        *,
        trigger: str,
        force_refresh: bool,
    ) -> list[CommandCompletion]:
        plugin = self.registry.plugin_for(session)
        completions = await plugin.list_command_completions(
            self,
            session,
            trigger=trigger,
            prefix="",
            force_refresh=force_refresh,
        )
        key = (session.id, trigger)
        self._completion_cache[key] = completions
        self._completion_cache_updated_at[key] = monotonic()
        return completions

    def _fallback_command_completions(
        self,
        plugin: Any,
        *,
        trigger: str,
        prefix: str,
    ) -> list[CommandCompletion]:
        if trigger != "/":
            return []
        return static_slash_completions(plugin.id, plugin.capabilities, prefix=prefix)

    def _filter_command_completions(
        self,
        completions: list[CommandCompletion],
        *,
        trigger: str,
        prefix: str,
    ) -> list[CommandCompletion]:
        if not prefix:
            return list(completions)
        normalized_prefix = (
            prefix if prefix.startswith(trigger) else f"{trigger}{prefix}"
        )
        return [
            item
            for item in completions
            if f"{item.trigger}{item.name}".startswith(normalized_prefix)
        ]

    def _trust_server_completion_invocation(
        self, session: SessionRecord, request: SessionInputRequest
    ) -> SessionInputRequest:
        invocation = request.command
        if invocation is None:
            return request
        completion = self._find_cached_completion(session.id, invocation.completion_id)
        if completion is None:
            return request.model_copy(update={"command": None})
        trusted = SessionCommandInvocation(
            completion_id=completion.id,
            name=completion.name,
            arguments=invocation.arguments,
            dispatch=completion.dispatch,
            metadata=dict(completion.metadata),
        )
        return request.model_copy(update={"command": trusted})

    def _find_cached_completion(
        self, session_id: str, completion_id: str
    ) -> CommandCompletion | None:
        for (
            cached_session_id,
            _trigger,
        ), completions in self._completion_cache.items():
            if cached_session_id != session_id:
                continue
            for completion in completions:
                if completion.id == completion_id:
                    return completion
        return None

    def cached_command_completion(
        self, session_id: str, *, trigger: str, name: str
    ) -> CommandCompletion | None:
        completions = self._completion_cache.get((session_id, trigger), [])
        for completion in completions:
            if completion.name == name:
                return completion
        return None

    async def interrupt(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        await self.transport_for(session).interrupt(session)
        await self._record_system_event(
            session.id, "Sent interrupt", status=SessionStatus.INTERRUPTED
        )
        return self.storage.update_session(session.id, status=SessionStatus.INTERRUPTED)

    async def resume(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        # A terminated session has no live pane to wake — sending Enter to the
        # dead target would raise. Relaunch it instead, mirroring handle_input's
        # reattach guard, so "Resume" on an exited session brings it back.
        if session.status in {SessionStatus.EXITED, SessionStatus.ERROR}:
            return await self._reattach_session(session)
        await self.transport_for(session).resume(session)
        await self._record_system_event(
            session.id, "Sent resume", status=SessionStatus.RUNNING
        )
        return self.storage.update_session(session.id, status=SessionStatus.RUNNING)

    async def terminate(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        if session.source == SessionSource.ASSISTANT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="the assistant session cannot be terminated",
            )
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
        await self._cancel_context_usage_source(session_id)
        await self._record_system_event(
            session.id, "Session terminated", status=SessionStatus.EXITED
        )
        self._close_structured_log(session.id)
        return self.storage.update_session(session.id, status=SessionStatus.EXITED)

    async def reattach(self, session_id: str) -> SessionRecord:
        # Explicit "reconnect this session" without sending a message.
        # Promotes the existing _reattach_session helper to a first-class
        # operation so the frontend can offer a button instead of forcing
        # users to type into a session they want to revive.
        session = self.get_session(session_id)
        return await self._reattach_session(session)

    async def refresh_rate_limit_usage(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        plugin = self.registry.plugin_for(session)
        refresher = getattr(plugin, "refresh_rate_limit_usage", None)
        if callable(refresher):
            await refresher(self, session)
        return self.get_session(session_id)

    async def _reattach_session(self, session: SessionRecord) -> SessionRecord:
        # ERROR sessions reach this path while the prior adapter state may
        # still be in `_sessions` — the stream/process watchers emit the
        # error event but do not pop the slot. Tear it down explicitly here
        # so `_spawn` does not overwrite a live state and orphan its
        # subprocess + background tasks. terminate_session is a no-op when
        # the session id is not tracked, so this is safe for clean EXITED
        # paths too.
        plugin = self.registry.plugin_for(session)
        if not plugin.capabilities.supports_reattach_after_exit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="this session cannot be reattached after exit",
            )
        await self._require_live_master(
            self._find_launch_target(session.launch_target_id)
        )
        await plugin.terminate_session(self, session)
        await self._cancel_context_usage_source(session.id)
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
        self._start_context_usage_source(refreshed)
        return refreshed

    async def delete(
        self, session_id: str, *, force: bool = False, prune_branches: bool = False
    ) -> None:
        session = self.get_session(session_id)
        if session.source == SessionSource.ASSISTANT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="the assistant session cannot be deleted",
            )
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
        # terminate() covers the non-EXITED path; cover the already-EXITED
        # case (natural exit never called terminate()) here.
        await self._cancel_context_usage_source(session_id)
        self._close_structured_log(session_id)
        plugin = self.registry.plugin_for(session)
        # Optional async cleanup hook; not part of the BackendPlugin protocol.
        # Run it BEFORE deleting the row so it can read fresh side-question state
        # under lock and honor a concurrent fork-promotion that claimed an aside
        # (and now owns its fork thread) rather than deleting that fork file.
        cleanup = getattr(plugin, "cleanup_side_questions_on_delete", None)
        if cleanup is not None and asyncio.iscoroutinefunction(cleanup):
            await cleanup(self, session)
        self.storage.delete_session(session_id)
        if session.worktree_path is not None:
            self._remove_worktree(session.worktree_path, prune_branches=prune_branches)
        # Reclaim the session's uploaded blobs, which can be large.
        self.attachments.discard(session_id)
        # Drop this session's blackboard posts along with its record.
        pruned = self.storage.prune_board_for_session(session_id)
        # Drop any scheduled messages queued for the now-deleted session; the
        # scheduled_messages FK is declarative only (foreign_keys pragma is off).
        await self.scheduler.purge_session_messages(session_id)
        plugin.on_session_deleted(self, session)
        await self._broadcast_session_list()
        if pruned:
            await self._publish_board_update(None)

    @staticmethod
    def _git_capture(cwd: str, *args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", cwd, *args],
                capture_output=True,
                text=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return (result.stdout or "").strip() or None

    def _remove_worktree(self, worktree_path: str, *, prune_branches: bool) -> None:
        """Remove a session's worktree and, where safe, its branch.

        The branch and the main repo root are captured from the live worktree
        *before* removal so cleanup needs no stored branch name and never
        stats a path git already deleted. ``git branch -d`` (the default)
        refuses unmerged branches, protecting a worker reaped before its work
        merged; ``prune_branches`` upgrades to ``-D`` for crew teardown where
        the branches are meant to be discarded. Only the branch the worktree
        owned is ever touched, and a branch checked out elsewhere is left for
        git to refuse.
        """
        branch = self._git_capture(worktree_path, "rev-parse", "--abbrev-ref", "HEAD")
        common_dir = self._git_capture(
            worktree_path, "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
        repo_root = str(Path(common_dir).parent) if common_dir else None
        # Drive the removal (and branch delete) from the main worktree, not the
        # server's cwd which may sit outside this session's repo.
        remove_cmd = ["git", "worktree", "remove", "--force", worktree_path]
        if repo_root is not None:
            remove_cmd[1:1] = ["-C", repo_root]
        with suppress(Exception):
            subprocess.run(remove_cmd, check=False)
        if branch is None or branch == "HEAD" or repo_root is None:
            return
        flag = "-D" if prune_branches else "-d"
        with suppress(Exception):
            subprocess.run(
                ["git", "-C", repo_root, "branch", flag, branch],
                capture_output=True,
                check=False,
            )

    async def post_board_entry(
        self, channel: str, request: BoardPostRequest
    ) -> BoardEntry:
        author_label: str | None = None
        if request.author_session_id is not None:
            session = self.storage.get_session(request.author_session_id)
            if session is not None:
                author_label = session.title
        entry = self.storage.add_board_entry(
            channel,
            request.text,
            key=request.key,
            author_session_id=request.author_session_id,
            author_label=author_label,
            metadata=request.metadata,
        )
        await self._publish_board_update(channel)
        return entry

    def list_board_entries(
        self, channel: str, *, since: int | None = None, key: str | None = None
    ) -> list[BoardEntry]:
        return self.storage.list_board_entries(channel, since=since, key=key)

    def read_board_channel(
        self, channel: str, *, log_limit: int | None = None, before: int | None = None
    ) -> tuple[list[BoardEntry], int]:
        return self.storage.read_board_channel(
            channel, log_limit=log_limit, before=before
        )

    def list_board_channels(self) -> list[BoardChannel]:
        return self.storage.list_board_channels()

    async def clear_board_channel(
        self, channel: str, keep_last: int | None = None
    ) -> int:
        removed = self.storage.clear_board_channel(channel, keep_last=keep_last)
        await self._publish_board_update(channel)
        return removed

    async def delete_board_channel(self, channel: str) -> int:
        removed = self.storage.delete_board_channel(channel)
        await self._publish_board_update(None)
        return removed

    async def delete_board_entry(self, channel: str, entry_id: int) -> bool:
        deleted = self.storage.delete_board_entry(channel, entry_id)
        if deleted:
            await self._publish_board_update(channel)
        return deleted

    async def update_board_entry(
        self, channel: str, entry_id: int, request: BoardEntryUpdateRequest
    ) -> BoardEntry | None:
        entry = self.storage.update_board_entry(
            channel,
            entry_id,
            request.text,
            request.metadata,
            merge=request.merge,
            unset=request.unset,
        )
        if entry is not None:
            await self._publish_board_update(channel)
        return entry

    async def _publish_board_update(self, channel: str | None) -> None:
        # ``channel=None`` means "the board changed broadly" (e.g. a session
        # delete pruned posts across channels); clients refetch what they show.
        await self.broadcast.publish(
            SessionEnvelope(type="board_update", payload={"channel": channel})
        )

    # ───────────────────────────── Inbox ─────────────────────────────

    async def post_inbox_item(self, request: InboxPostRequest) -> InboxItem:
        from_label: str | None = None
        if request.from_session_id is not None:
            session = self.storage.get_session(request.from_session_id)
            if session is not None:
                from_label = session.title
        blocks: list[InboxBlockInput] = list(request.blocks)
        item = self.storage.create_inbox_item(
            from_session_id=request.from_session_id or "",
            from_label=from_label,
            subject=request.subject,
            blocks=blocks,
        )
        await self._publish_inbox_update(item)
        return item

    def get_inbox_item(self, item_id: str) -> InboxItem | None:
        return self.storage.get_inbox_item(item_id)

    def list_inbox_items(
        self,
        *,
        status: InboxStatus | None = None,
        query: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> InboxListResponse:
        items, has_more, next_cursor = self.storage.list_inbox_items(
            status=status, query=query, limit=limit, cursor=cursor
        )
        return InboxListResponse(items=items, has_more=has_more, cursor=next_cursor)

    async def submit_inbox_block(
        self,
        item_id: str,
        block_id: str,
        *,
        answer: dict[str, Any] | None = None,
        reply: InboxReplyInput | None = None,
    ) -> InboxItem | None:
        item = self.storage.submit_inbox_block(
            item_id, block_id, answer=answer, reply=reply
        )
        if item is not None:
            await self._publish_inbox_update(item)
        return item

    async def mark_inbox_read(self, item_id: str) -> InboxItem | None:
        result = self.storage.mark_inbox_read(item_id)
        if result is None:
            return None
        item, changed = result
        # A repeat read is a no-op; don't re-broadcast to every client.
        if changed:
            await self._publish_inbox_update(item)
        return item

    async def delete_inbox_item(self, item_id: str) -> bool:
        deleted = self.storage.delete_inbox_item(item_id)
        if deleted:
            await self._publish_inbox_deleted(item_id)
        return deleted

    def unresolved_inbox_count(self) -> int:
        return self.storage.unresolved_inbox_count()

    async def _publish_inbox_update(self, item: InboxItem) -> None:
        # Two channels: a global ``inbox_update`` carrying the fresh unresolved
        # count (drives the cross-session badge) plus the full item on the
        # per-item stream so a connected ``inbox wait`` resumes.
        count = self.storage.unresolved_inbox_count()
        await self.broadcast.publish(
            SessionEnvelope(
                type="inbox_update",
                payload={
                    "item_id": item.id,
                    "unresolved_count": count,
                    "deleted": False,
                    "item": item.model_dump(mode="json"),
                },
            ),
            inbox_id=item.id,
        )

    async def _publish_inbox_deleted(self, item_id: str) -> None:
        # Deletion is a terminal outcome for a waiter (``gone``); publish on both
        # the global badge channel and the per-item stream.
        count = self.storage.unresolved_inbox_count()
        await self.broadcast.publish(
            SessionEnvelope(
                type="inbox_update",
                payload={
                    "item_id": item_id,
                    "unresolved_count": count,
                    "deleted": True,
                    "item": None,
                },
            ),
            inbox_id=item_id,
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
        await self._broadcast_session_list()
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
        await self._broadcast_session_list()
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
        await self._broadcast_session_list()
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
        await self._broadcast_session_list()
        return updated

    async def set_tags(
        self, session_id: str, set_tags: dict[str, str], unset: list[str]
    ) -> SessionRecord:
        session = self.get_session(session_id)
        new_tags = {**session.tags, **set_tags}
        for key in unset:
            new_tags.pop(key, None)
        if new_tags == session.tags:
            return session
        updated = self.storage.update_session(session_id, tags=new_tags)
        await self._broadcast_session_list()
        return updated

    async def set_pinned(self, session_id: str, pinned: bool) -> SessionRecord:
        session = self.get_session(session_id)
        pinned_at = datetime.now(UTC) if pinned else None
        if (session.pinned_at is None) == (pinned_at is None):
            return session
        updated = self.storage.update_session(session_id, pinned_at=pinned_at)
        await self._broadcast_session_list()
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

    async def approve_plan(
        self, session_id: str, request: SessionPlanApprovalRequest
    ) -> SessionRecord:
        session = self.get_session(session_id)
        plugin = self.registry.plugin_for(session)
        if not plugin.capabilities.supports_plan_approval:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"plan approval is not supported for {session.backend}",
            )
        return await plugin.approve_plan(
            self, session, request.plan_item_id, request.decision, request.text
        )

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
        # Only the tail request carries the sticky todo snapshot; older pages
        # leave it None so the client never clobbers a valid pre-window todo.
        latest_todo = (
            self.storage.latest_todo_event(session_id)
            if before_sequence is None
            else None
        )
        return EventsPageResponse(
            events=events, has_more=has_more, latest_todo=latest_todo
        )

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
                    "auth": target.ssh_auth,
                    "connected": self.ssh_master.is_connected_cached(target),
                }
            )
        return summaries

    async def connect_launch_target(
        self, target_id: str, password: str
    ) -> SshMasterStatus:
        target = self._find_launch_target(target_id)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="unknown launch target"
            )
        if not target.requires_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="launch target does not use password auth",
            )
        return await self.ssh_master.connect(target, password)

    async def launch_target_status(self, target_id: str) -> SshMasterStatus:
        target = self._find_launch_target(target_id)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="unknown launch target"
            )
        connected = (
            await self.ssh_master.is_connected(target)
            if target.requires_password
            else True
        )
        return SshMasterStatus(
            target_id=target.id, auth=target.ssh_auth, connected=connected
        )

    async def disconnect_launch_target(self, target_id: str) -> None:
        target = self._find_launch_target(target_id)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="unknown launch target"
            )
        if target.requires_password:
            await self.ssh_master.disconnect(target)

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

    def remote_probe_blocked(self, launch_target_id: str | None) -> bool:
        """True when a pre-launch SSH probe (thread/model enumeration) must be
        skipped because the target needs a password and its ControlMaster is not
        up. Otherwise the picker SSHes to an unreachable host on every load —
        codex fails with ``Permission denied`` and opencode blocks on its
        server-start timeout, both surfacing as "load failed" in the UI.
        """
        target = self._find_launch_target(launch_target_id)
        return bool(
            target
            and target.requires_password
            and not self.ssh_master.is_connected_cached(target)
        )

    async def _require_live_master(
        self, launch_target: SshLaunchTargetConfig | None
    ) -> None:
        """Reject a launch on a password-auth target whose ControlMaster is not
        up. The frontend matches the ``ssh-master-required`` detail to prompt
        for the password (via ``/api/launch-targets/{id}/connect``) and retry.
        """
        if launch_target is None or not launch_target.requires_password:
            return
        if not await self.ssh_master.is_connected(launch_target):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="ssh-master-required",
            )

    async def _record_user_event(
        self,
        session_id: str,
        text: str,
        submit: bool,
        status: SessionStatus = SessionStatus.RUNNING,
        extra_metadata: dict[str, Any] | None = None,
        attachments: list[AttachmentSpec] | None = None,
    ) -> None:
        metadata: dict[str, Any] = {"submit": submit, "status": status}
        if attachments:
            metadata["attachments"] = [
                spec.model_dump(mode="json") for spec in attachments
            ]
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

    async def seed_thread_history(
        self,
        session_id: str,
        reader: Callable[[], Awaitable[list[EventRecord]]],
        *,
        enabled: bool,
    ) -> int:
        """Replay imported thread history into a freshly-created session.

        Agent plugins call this from ``import_thread`` after ``create_session``
        and before recording the "Imported…" note, passing a ``reader`` that
        reads the native thread and converts it into ``EventRecord``s. The
        converter is agent-specific (it shares helpers with, but is not, the
        live ``normalize.py`` — historical snapshots must re-add the user turns
        and full assistant text the live path streams on other channels); this
        method owns the transport-agnostic seed and its failure handling.

        When ``enabled`` is false this is a no-op (the transcript stays empty and
        the agent merely resumes its own context). Any failure to read or
        convert history is swallowed: the import still succeeds as a plain
        resume, with a system note explaining history was unavailable. Returns
        the number of events seeded.
        """
        if not enabled:
            return 0
        try:
            events = await reader()
        except Exception:
            log.exception("failed to import thread history for %s", session_id)
            await self._record_system_event(
                session_id,
                "Prior conversation history could not be imported; "
                "the session resumes without it.",
            )
            return 0
        if not events:
            return 0
        persisted = self.storage.seed_events(session_id, events)
        for event in persisted:
            self._append_structured_log(session_id, event)
        return len(persisted)

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
        if status in {SessionStatus.EXITED, SessionStatus.ERROR}:
            await self._cancel_context_usage_source(session_id)

    async def update_session_fields(
        self, session_id: str, *, publish: bool = True, **updates: Any
    ) -> SessionRecord:
        session = self.storage.update_session(session_id, **updates)
        if publish:
            self._publish_session_state(session_id)
        return session

    def session_update_callback(
        self,
    ) -> Callable[[str, dict[str, Any], bool], Awaitable[SessionRecord]]:
        async def _update_session_fields(
            session_id: str, updates: dict[str, Any], publish: bool
        ) -> SessionRecord:
            return await self.update_session_fields(
                session_id, publish=publish, **updates
            )

        return _update_session_fields

    async def _publish_event(self, event: EventRecord) -> None:
        await self.broadcast.publish(
            SessionEnvelope(
                type="event",
                payload={"event": event.model_dump(mode="json")},
            ),
            session_id=event.session_id,
        )
        self._publish_session_state(event.session_id)

    def _publish_session_state(self, session_id: str) -> None:
        # Streaming hot path: mark the session dirty and let the debounced
        # flusher emit the session_state / session_list broadcasts. A fast
        # token stream would otherwise re-serialize every session on every
        # event. Lifecycle changes call ``_broadcast_session_list``
        # directly when they need an immediate update.
        self._dirty_session_states.add(session_id)
        self._session_list_dirty = True
        self._broadcast_wake.set()

    async def _broadcast_session_list(self) -> None:
        with debug_timer(log, "_broadcast_session_list"):
            sessions = [item.model_dump(mode="json") for item in self.list_sessions()]
        await self.broadcast.publish(
            SessionEnvelope(
                type="session_list_update",
                payload={"sessions": sessions},
            )
        )

    async def _broadcast_session_state(self, session_id: str) -> None:
        session = self.storage.get_session(session_id)
        if session is None:
            # Deleted between being marked dirty and this flush; the
            # delete already broadcast a fresh list, so there's nothing to
            # publish for it.
            return
        await self.broadcast.publish(
            SessionEnvelope(
                type="session_state",
                payload={"session": session.model_dump(mode="json")},
            ),
            session_id=session_id,
        )

    async def _session_broadcast_loop(self) -> None:
        while True:
            await self._broadcast_wake.wait()
            await asyncio.sleep(SESSION_BROADCAST_DEBOUNCE_SECONDS)
            # Clear before draining so any event arriving during the
            # broadcast below re-arms the flusher rather than being lost.
            self._broadcast_wake.clear()
            dirty_ids = self._dirty_session_states
            self._dirty_session_states = set()
            list_dirty = self._session_list_dirty
            self._session_list_dirty = False
            for session_id in dirty_ids:
                await self._broadcast_session_state(session_id)
            if list_dirty:
                await self._broadcast_session_list()

    def _append_structured_log(self, session_id: str, event: EventRecord) -> None:
        if not self.settings.write_structured_log:
            return
        with debug_timer(log, "_append_structured_log", session=session_id):
            entry = self._structured_log_handles.get(session_id)
            if entry is None:
                session = self.get_session(session_id)
                handle = Path(session.structured_log_path).open("a", encoding="utf-8")
                entry = _StructuredLogHandle(handle=handle)
                self._structured_log_handles[session_id] = entry
            entry.handle.write(json.dumps(event.model_dump(mode="json")) + "\n")
            entry.pending += 1
            if entry.pending >= STRUCTURED_LOG_FLUSH_EVERY:
                entry.handle.flush()
                entry.pending = 0

    def _close_structured_log(self, session_id: str) -> None:
        if not self.settings.write_structured_log:
            return
        entry = self._structured_log_handles.pop(session_id, None)
        if entry is not None:
            with suppress(Exception):
                entry.handle.close()

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
        *,
        allocate_tty: bool = False,
        session_id: str | None = None,
    ) -> list[str]:
        plugin = self.registry.get(backend)
        extra_env = dict(plugin.extra_env)
        if session_id:
            # So the wrapped agent (and any waypoint CLI it runs) knows its own
            # session and can inherit this session's posture into children.
            extra_env["WAYPOINT_SESSION_ID"] = session_id
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
            if extra_env:
                # ``env KEY=VAL ... cmd`` is portable across macOS/Linux
                # and lets tmux's pane shell exec the CLI with the env
                # var set, matching what we do over SSH.
                env_prefix = [
                    "env",
                    *(f"{k}={v}" for k, v in sorted(extra_env.items())),
                ]
                return [*env_prefix, executable, *args]
            return [executable, *args]
        executable = plugin.remote_executable(launch_target)
        if not executable:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"backend {backend} has no remote binary configured",
            )
        return list(
            launch_target.build_remote_exec_args(
                [executable, *args],
                cwd or launch_target.default_cwd,
                allocate_tty=allocate_tty,
                extra_env=extra_env,
            )
        )

    def _infer_backend(self, target: str) -> str:
        lowered = target.lower()
        for plugin in self.registry.all():
            for alias in plugin.capabilities.target_aliases:
                if alias and alias.lower() in lowered:
                    return plugin.id
        return self.settings.default_backend

    def _start_context_usage_source(self, session: SessionRecord) -> None:
        if session.status in {SessionStatus.EXITED, SessionStatus.ERROR}:
            # Boot-restore passes already-terminal sessions through here; a
            # source started for one would poll until delete with nothing to
            # cancel it (the terminal transition already fired).
            return
        transport = self._transports.get(session.transport)
        if transport is None or transport.is_structured:
            return
        plugin = self.registry.plugin_for(session)
        source = plugin.create_context_usage_source(session, self)
        if source is None:
            return
        old = self._context_usage_sources.pop(session.id, None)
        if old is not None:
            old.cancel()
        self._context_usage_sources[session.id] = asyncio.create_task(
            source.run(), name=f"ctx-usage-{session.id}"
        )

    async def _cancel_context_usage_source(self, session_id: str) -> None:
        task = self._context_usage_sources.pop(session_id, None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def _ensure_monitor(self, session_id: str) -> None:
        if session_id in self.monitor_tasks:
            return
        session = self.get_session(session_id)
        if not self.registry.plugin_for(session).capabilities.live_terminal:
            return
        if session_id not in self.file_offsets:
            # A missing offset means this process has never ingested this raw
            # log — typically a boot-time restore. The prior run already
            # persisted its events, so resume from the current end instead of
            # replaying (and synchronously re-normalizing) the whole backlog,
            # which blocks the event loop and stalls startup.
            self.file_offsets[session_id] = _raw_log_end_offset(
                Path(session.raw_log_path)
            )
        self.monitor_tasks[session_id] = asyncio.create_task(
            self._monitor_session(session_id)
        )

    async def _monitor_session(self, session_id: str) -> None:
        ingest_interval = self.settings.stream_poll_interval
        ticks_per_refresh = max(
            1, round(self.settings.state_poll_interval / ingest_interval)
        )
        try:
            tick = 0
            while True:
                await self._ingest_raw_output(session_id)
                # Ingest the cheap (threaded) output read every tick, but
                # only run the subprocess-spawning liveness refresh every
                # Nth tick. tick 0 refreshes immediately so a pane that's
                # already dead is caught on the first pass.
                if tick % ticks_per_refresh == 0:
                    await self._refresh_state(session_id)
                tick += 1
                await asyncio.sleep(ingest_interval)
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

    def handle_completion_source_init(
        self, session_id: str, payload: dict[str, Any]
    ) -> None:
        slash_commands = payload.get("slash_commands")
        if not isinstance(slash_commands, list):
            return
        commands = [
            command
            for command in slash_commands
            if isinstance(command, str) and command
        ]
        session = self.storage.get_session(session_id)
        if session is None:
            return
        state = dict(session.transport_state)
        if state.get("slash_commands") == commands:
            return
        state["slash_commands"] = commands
        self.storage.update_session(session_id, transport_state=state)
        self._completion_cache.pop((session_id, "/"), None)
        self._completion_cache_updated_at.pop((session_id, "/"), None)
        self._ensure_command_completion_refresh(session, trigger="/")

    async def _ingest_raw_output(self, session_id: str) -> None:
        session = self.get_session(session_id)
        raw_log_path = Path(session.raw_log_path)
        if not raw_log_path.exists():
            return
        offset = self.file_offsets.get(session_id, 0)
        # Read + normalize off the event loop: a large chunk (or slow
        # storage) would otherwise block every other task, including
        # uvicorn's bind during boot restore.
        new_offset, normalized = await asyncio.to_thread(
            _read_and_normalize,
            self.normalizer,
            session_id,
            raw_log_path,
            offset,
            self.storage.next_sequence(session_id),
        )
        self.file_offsets[session_id] = new_offset
        if normalized is None:
            return
        # Heuristic (non-structured) transports — the generic tmux pane — emit
        # raw terminal frames the UI never renders (it shows the live xterm, not
        # a transcript), so persisting them is pure DB bloat. Gate on the
        # transport capability, not a hardcoded id, per the dispatch-by-capability
        # rule. _ingest_raw_output only runs for raw-log transports today, but the
        # capability check keeps a future heuristic transport from regressing.
        is_heuristic = not self.transport_for(session).is_structured
        for event in normalized.events:
            if is_heuristic and event.kind in TMUX_CONTENT_KINDS:
                continue
            persisted = self.storage.append_event(event)
            self._append_structured_log(session_id, persisted)
            await self._publish_event(persisted)
        # Content events (agent_output / raw_terminal_chunk) were not persisted
        # but their two side-effects must still be applied: (1) bump
        # last_event_at, (2) advance the heuristic status. Use update_session
        # when the last event in the chunk was a content event — if a
        # non-content event came last, append_event already handled both.
        if (
            is_heuristic
            and normalized.events
            and normalized.events[-1].kind in TMUX_CONTENT_KINDS
        ):
            self.storage.update_session(
                session_id,
                last_event_at=normalized.events[-1].ts,
                status=normalized.status,
            )
            self._publish_session_state(session_id)

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
                await self._record_system_event(
                    session.id,
                    "Tmux target lost; session ended",
                    status=SessionStatus.EXITED,
                )
                self.storage.update_session(session.id, status=SessionStatus.EXITED)
            return
        new_state = {**state, "pid": target_info.pane_pid}
        if target_info.pane_dead and session.status != SessionStatus.EXITED:
            # Push an explicit event so the frontend can react
            # immediately (offer the Reconnect affordance) instead of
            # waiting for the next list-poll to discover the status
            # flip. ``/exit`` inside the agent flows through this path.
            log.info(
                "tmux pane reported dead",
                extra={"session_id": session.id, "target": target},
            )
            await self._record_system_event(
                session.id,
                "Session exited (tmux pane closed)",
                status=SessionStatus.EXITED,
            )
            self.storage.update_session(
                session.id,
                transport_state=new_state,
                status=SessionStatus.EXITED,
            )
            return
        self.storage.update_session(session.id, transport_state=new_state)
