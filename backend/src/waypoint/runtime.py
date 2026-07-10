import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, TextIO, cast

from fastapi import HTTPException, status

from waypoint.assistant_assets import AssistantAssetError, ensure_assistant_assets
from waypoint.attachments import AttachmentStore, ResolvedAttachment
from waypoint.backends import BackendRegistry, get_registry
from waypoint.backends.account_profiles import (
    account_profile_static_checks,
    probe_account,
    redacted_profile_metadata,
    resolve_account_profiles,
)
from waypoint.backends.base import (
    AgentLaunchContract,
    ConfigDirNotReadyError,
    ConfigDirValidating,
    DefaultConfigDirProviding,
    FreshThreadRestarting,
    config_dir_for,
)
from waypoint.backends.capabilities import BackendCapabilities
from waypoint.backends.completions import static_slash_completions
from waypoint.backends.plugin_config import AccountProfileConfig
from waypoint.backends.tmux.adapter import TmuxAdapter, TmuxError
from waypoint.backends.tmux.normalize import (
    TMUX_CONTENT_KINDS,
    NormalizedChunk,
    TerminalNormalizer,
)
from waypoint.backends.transcript_fs import (
    LocalTranscriptFilesystem,
    TranscriptFilesystem,
)
from waypoint.backends.transcript_fs_remote import RemoteTranscriptFilesystem
from waypoint.backends.transcripts import (
    ThreadAvailability,
    TranscriptUnavailableError,
    ensure_symlink_shared,
    ensure_thread_available,
    setup_transcripts_symlink,
    unpersisted_thread_error,
)
from waypoint.builtin_completions import waypoint_builtin_completions
from waypoint.git_meta import resolve_git_meta
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.perf import debug_timer
from waypoint.presets import PresetManager
from waypoint.scheduler import Scheduler
from waypoint.schemas import (
    AccountProbeResult,
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
    InboxAttachmentBlockInput,
    InboxAttachmentRef,
    InboxBlockInput,
    InboxItem,
    InboxListResponse,
    InboxPostRequest,
    InboxReplyInput,
    InboxStatus,
    LaunchMode,
    LaunchSettingsResponse,
    LaunchSettingsUpdateRequest,
    ProfileCheck,
    ProfileDoctorReport,
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
    TokenUsageInit,
    TokenUsageRecord,
    TransportSettingsOption,
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
        # Serializes disruptive per-session lifecycle ops (a launch-settings
        # switch is a terminate→restore sequence that must not interleave with
        # another switch/reattach/terminate on the same session).
        self._session_locks: dict[str, asyncio.Lock] = {}
        # Resolved remote ``$HOME`` (or ``~user``) per (target id, tilde prefix),
        # so ``_apply_account_profile_env`` can expand a ``~``-relative remote
        # config_dir without an SSH round-trip on its own (synchronous) path.
        self._remote_home_cache: dict[tuple[str, str], str] = {}
        self._completion_cache: dict[CompletionCacheKey, list[CommandCompletion]] = {}
        self._completion_cache_updated_at: dict[CompletionCacheKey, float] = {}
        self._completion_refresh_tasks: dict[
            CompletionCacheKey, asyncio.Task[list[CommandCompletion]]
        ] = {}
        # Fire-and-forget verified-account probes (launch, thread-import,
        # reattach, boot-restore). Tracked so a task isn't GC'd mid-flight;
        # discarded on done.
        self._account_probe_tasks: set[asyncio.Task[None]] = set()
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
        self.presets = PresetManager(storage)
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
        account_probe_tasks = list(self._account_probe_tasks)
        self._account_probe_tasks.clear()
        for probe_task in account_probe_tasks:
            probe_task.cancel()
        for probe_task in account_probe_tasks:
            with suppress(asyncio.CancelledError):
                await probe_task
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

    def _default_launch_env(
        self,
        backend: str,
        launch_target: SshLaunchTargetConfig | None = None,
    ) -> dict[str, str]:
        env = dict(self.settings.plugin_config(backend).env)
        if launch_target is not None:
            env.update(launch_target.plugin_config(backend).env)
        return env

    def _effective_launch_env_for_request(
        self,
        request: Any,
        launch_target: SshLaunchTargetConfig | None,
    ) -> dict[str, str]:
        if "launch_env" in request.model_fields_set:
            return dict(request.launch_env)
        return self._default_launch_env(request.backend, launch_target)

    def _require_account_profile(
        self,
        backend: str,
        account_profile_id: str,
        launch_target: SshLaunchTargetConfig | None,
    ) -> AccountProfileConfig:
        """Resolve a selected profile or raise 400 if the id is unknown."""
        profiles = resolve_account_profiles(self.settings, backend, launch_target)
        profile = profiles.get(account_profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"unknown account profile {account_profile_id!r} "
                    f"for backend {backend}"
                ),
            )
        return profile

    def _apply_account_profile_env(
        self,
        backend: str,
        launch_env: dict[str, str],
        account_profile_id: str | None,
        launch_target: SshLaunchTargetConfig | None,
    ) -> tuple[dict[str, str], str | None]:
        """Overlay the selected account profile's config-dir env key.

        Profile-owned: when a profile is selected its ``config_dir`` wins for the
        backend's ``config_dir_env_var``, stripping any raw value the request
        supplied for that key (profile-wins, never a 400 on disagreement).
        Returns ``(launch_env, label)`` — the label for stamping — and is a no-op
        returning ``(launch_env, None)`` when no profile is selected. Unknown
        ids, or a backend without a config-dir env var, are rejected with 400.
        """
        if account_profile_id is None:
            return launch_env, None
        profile = self._require_account_profile(
            backend, account_profile_id, launch_target
        )
        config_dir_key = self.registry.get(backend).capabilities.config_dir_env_var
        if config_dir_key is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"backend {backend} does not support account profiles",
            )
        config_dir = profile.config_dir
        # Expand ``~`` for local launches (the config dir is a path on this
        # host) via the stdlib. Env values are injected shell-quoted so a
        # remote shell won't expand ``~`` itself — a remote ``~``-relative
        # config_dir is expanded here from the cached remote home instead
        # (warmed ahead of this call by an async call site; this method stays
        # synchronous and only reads the cache).
        if launch_target is None:
            config_dir = os.path.expanduser(config_dir)
        elif config_dir.startswith("~"):
            resolved = self._expand_remote_config_dir(launch_target, config_dir)
            if resolved is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "could not resolve remote home for expansion; use an "
                        "absolute config_dir or ensure the target is reachable"
                    ),
                )
            config_dir = resolved
        env = dict(launch_env)
        env[config_dir_key] = config_dir
        return env, profile.label

    @staticmethod
    def _split_tilde_config_dir(config_dir: str) -> tuple[str, str]:
        """Split a ``~``-relative path into its tilde prefix and remainder.

        ``"~/.codex-work"`` -> ``("~", "/.codex-work")``; ``"~alice/x"`` ->
        ``("~alice", "/x")``; bare ``"~"`` -> ``("~", "")``. Mirrors how
        ``os.path.expanduser`` scopes the expansion to the first path segment.
        """
        head, sep, tail = config_dir.partition("/")
        return head, (sep + tail if sep else "")

    def _expand_remote_config_dir(
        self, launch_target: SshLaunchTargetConfig, config_dir: str
    ) -> str | None:
        """Expand a ``~``-relative remote ``config_dir`` from the warm cache.

        Returns ``None`` on a cache miss (target unreachable, or
        :meth:`_ensure_remote_home_cached` never called for this tilde prefix)
        rather than raising, so both the raising overlay
        (``_apply_account_profile_env``) and the non-raising doctor check can
        share the same resolution. A non-``~`` ``config_dir`` is returned
        unchanged.
        """
        if not config_dir.startswith("~"):
            return config_dir
        tilde_prefix, remainder = self._split_tilde_config_dir(config_dir)
        resolved_home = self._remote_home_cache.get((launch_target.id, tilde_prefix))
        if not resolved_home:
            return None
        return resolved_home.rstrip("/") + remainder

    async def _ensure_remote_home_cached(
        self, launch_target: SshLaunchTargetConfig, tilde_prefix: str = "~"
    ) -> None:
        """Resolve and cache ``launch_target``'s remote home for ``tilde_prefix``.

        Auth-agnostic: uses ``launch_target.ssh_capture`` (a plain SSH round
        trip that transparently reuses the ControlMaster socket for
        password-auth targets) rather than ``_require_live_master`` /
        ``connect_launch_target``, both of which are password-only and would
        never warm a key-auth target (the ``ssh_auth`` default). Bare ``~``
        resolves via ``$HOME``; a ``~user`` prefix resolves via shell tilde
        expansion for that user. No-op on cache hit; leaves the cache cold
        (for the synchronous 400 in ``_apply_account_profile_env``) when the
        target is unreachable or reports no home.
        """
        cache_key = (launch_target.id, tilde_prefix)
        if cache_key in self._remote_home_cache:
            return
        remote_cmd = "echo $HOME" if tilde_prefix == "~" else f"echo {tilde_prefix}"
        resolved = (await launch_target.ssh_capture(remote_cmd)).strip()
        if resolved:
            self._remote_home_cache[cache_key] = resolved

    async def _warm_remote_home_for_profile(
        self,
        launch_target: SshLaunchTargetConfig | None,
        backend: str,
        profile_id: str | None,
    ) -> None:
        """Warm the remote-home cache for a profile's tilde prefix before the
        synchronous ``_apply_account_profile_env`` read on the same code path.

        No-op locally. Every path that applies a profile env for a remote
        target (switch, create, import, probe) must call this first, or a
        ``~``-relative remote ``config_dir`` hits a cold cache and 400s. Bare
        ``~`` covers a non-``~`` / unknown / unresolvable profile, since the
        cache read is a no-op for an absolute ``config_dir``.
        """
        if launch_target is None:
            return
        tilde_prefix = "~"
        if profile_id is not None:
            candidate = resolve_account_profiles(
                self.settings, backend, launch_target
            ).get(profile_id)
            if candidate is not None and candidate.config_dir.startswith("~"):
                tilde_prefix, _ = self._split_tilde_config_dir(candidate.config_dir)
        await self._ensure_remote_home_cached(launch_target, tilde_prefix)

    def _ensure_profile_config_dir_ready(
        self,
        backend: str,
        transport_caps: BackendCapabilities,
        launch_env: Mapping[str, str],
        account_profile_id: str | None,
        launch_target: SshLaunchTargetConfig | None,
    ) -> None:
        """Reject a local profile whose config dir would strand this launch on an
        interactive first-run prompt (e.g. claude onboarding), before the process
        is spawned or a running session is destructively switched.

        Two independent facts gate the check, kept on the axes that own them: the
        *agent* (``registry.get(backend)`` — profiles are agent-owned) knows how
        to judge readiness (:class:`ConfigDirValidating`); the resolved
        *transport* knows whether an un-ready dir actually hangs — only an
        interactive TUI in a terminal pane (``has_terminal_pane``) does, so a
        headless transport (``claude --print``) is exempt and never rejected.
        Remote dirs can't be stat'd here, so the check is local-only.
        """
        if account_profile_id is None or launch_target is not None:
            return
        if not transport_caps.has_terminal_pane:
            return
        agent = self.registry.get(backend)
        # The config-dir env var is an agent-axis fact — read it off the agent,
        # not the transport (the generic tmux wrapper is agent-agnostic and
        # leaves it unset). ``has_terminal_pane`` above stays on the transport.
        config_dir = config_dir_for(agent.capabilities, launch_env)
        if config_dir is None or not isinstance(agent, ConfigDirValidating):
            return
        try:
            agent.ensure_config_dir_ready(config_dir)
        except ConfigDirNotReadyError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"account profile {account_profile_id!r} is not set up: {exc}",
            ) from exc

    async def _ensure_new_session_profile_transcript_store(
        self,
        backend: str,
        launch_env: Mapping[str, str],
        account_profile_id: str | None,
        launch_target: SshLaunchTargetConfig | None,
    ) -> None:
        """Prepare a ``symlink_shared`` store before a profile-scoped launch.

        A new thread has no prior transcript to transfer, but its first agent
        process will create the native store. Establish the configured symlink
        first so that process writes directly into the shared tree. The guarded
        helper refuses a populated real directory; migrating existing user data
        remains the explicit ``accounts setup-transcripts`` operation.
        """
        if account_profile_id is None:
            return
        profile = self._require_account_profile(
            backend, account_profile_id, launch_target
        )
        if profile.transcript_policy != "symlink_shared":
            return
        caps = self.registry.get(backend).capabilities
        if caps.native_thread_store is None:
            return
        config_dir = config_dir_for(caps, launch_env)
        if config_dir is None:
            return
        fs: TranscriptFilesystem = (
            LocalTranscriptFilesystem()
            if launch_target is None
            else RemoteTranscriptFilesystem(launch_target)
        )
        try:
            await asyncio.to_thread(
                ensure_symlink_shared,
                Path(fs.expanduser(config_dir)) / caps.native_thread_store,
                Path(fs.expanduser(cast(str, profile.shared_transcript_dir))),
                fs=fs,
            )
        except TranscriptUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"cannot prepare account profile transcripts: {exc}",
            ) from exc

    def _agent_process_env(
        self,
        backend: str,
        launch_env: dict[str, str],
        *,
        session_id: str | None = None,
    ) -> dict[str, str]:
        plugin = self.registry.get(backend)
        env = {**launch_env, **plugin.extra_env}
        if session_id:
            # Runtime-owned keys win even if a user-supplied launch env tried
            # to shadow them.
            env["WAYPOINT_SESSION_ID"] = session_id
        return env

    def account_lookup_env(
        self, backend: str, launch_env: dict[str, str]
    ) -> dict[str, str]:
        """Env for account-scoped *lookups* (rate-limit/thread/model probes).

        The account a probe authenticates as is selected by the config-dir env
        var (``CLAUDE_CONFIG_DIR``/``CODEX_HOME``), which a profile bakes into
        ``launch_env``. Mirror the env the session process actually sees —
        ``os.environ`` overlaid with the session's ``launch_env`` and the
        backend's ``extra_env`` — so a lookup resolves the same account the
        session runs as. Unlike ``_agent_process_env`` this never adds
        runtime-only keys (e.g. ``WAYPOINT_SESSION_ID``); it's a read helper,
        not a launch helper. Dispatches through the registry — no per-backend
        branching.
        """
        plugin = self.registry.get(backend)
        return {**os.environ, **launch_env, **plugin.extra_env}

    async def discovery_env(
        self,
        backend: str,
        launch_target: SshLaunchTargetConfig | None,
        account_profile_id: str | None,
    ) -> dict[str, str]:
        """Env for session-less discovery (models, threads, import, delete).

        Mirrors the launch overlay for the read side: the backend's default env
        with the selected profile's ``config_dir`` overlaid on the config-dir
        env var, returned as an ``account_lookup_env`` so a probe resolves the
        same account a session would launch under. ``account_profile_id=None``
        yields the process-default store (back-compat). Warms the remote-home
        cache first so a ``~``-relative remote ``config_dir`` doesn't hit a cold
        cache and 400 — remote profile scoping otherwise inherits the
        remote-switching constraints (task #33). Unknown profile, or a backend
        without a config-dir env var, are rejected with 400 via
        ``_apply_account_profile_env``.
        """
        if account_profile_id is not None and launch_target is not None:
            await self._warm_remote_home_for_profile(
                launch_target, backend, account_profile_id
            )
        base = self._default_launch_env(backend, launch_target)
        env, _ = self._apply_account_profile_env(
            backend, base, account_profile_id, launch_target
        )
        return self.account_lookup_env(backend, env)

    def _profile_launch_env(
        self,
        backend: str,
        profile_id: str,
        launch_target: SshLaunchTargetConfig | None,
    ) -> dict[str, str]:
        """Build the launch env a profile resolves to, outside any session.

        Mirrors the switch path: the backend's default env with the profile's
        ``config_dir`` overlaid on the config-dir env var. Raises 400 for an
        unknown profile or a backend without a config-dir env var.
        """
        env = self._default_launch_env(backend, launch_target)
        env, _ = self._apply_account_profile_env(
            backend, env, profile_id, launch_target
        )
        return env

    async def probe_account_profile(
        self,
        backend: str,
        profile_id: str,
        *,
        launch_target_id: str | None = None,
        cwd: str = ".",
    ) -> AccountProbeResult:
        """Probe the account an account profile authenticates as.

        Resolves the profile's launch env exactly as a switch would and runs the
        account probe against it — the canonical way to read a profile's verified
        ``account_key``/``label`` (e.g. to fill in ``expected_account_key``).
        Raises 400 when the account can't be verified.
        """
        launch_target = self._resolve_launch_target(launch_target_id, backend)
        if launch_target is not None:
            await self._require_live_master(launch_target)
        await self._warm_remote_home_for_profile(launch_target, backend, profile_id)
        env = self._profile_launch_env(backend, profile_id, launch_target)
        result = await probe_account(
            self, backend, env, launch_target=launch_target, cwd=cwd
        )
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"could not verify the account for profile {profile_id!r} "
                    f"on backend {backend}"
                ),
            )
        return result

    def _stamp_verified_account(
        self, session_id: str, probe: AccountProbeResult, probed_at: datetime
    ) -> None:
        """Persist the verified-account triple from a probe result.

        Shared by every population point (switch, launch, thread-import,
        reattach, boot-restore) so the write is always the same three fields.
        """
        self.storage.update_session(
            session_id,
            verified_account_key=probe.account_key,
            verified_account_label=probe.account_label,
            verified_account_probed_at=probed_at,
        )

    def _schedule_verified_account_probe(
        self,
        session_id: str,
        backend: str,
        env: dict[str, str],
        *,
        launch_target: SshLaunchTargetConfig | None,
        cwd: str,
    ) -> None:
        """Fire-and-forget probe+stamp of ``verified_account_*``.

        ``probe_account`` is a live, uncached HTTP call with up to a 30s
        timeout, so this always runs off the launch/reattach/restore response
        path. Tracked in ``_account_probe_tasks`` so the task isn't GC'd
        mid-flight; discarded on done.
        """
        task = asyncio.create_task(
            self._probe_and_stamp_verified_account(
                session_id, backend, env, launch_target=launch_target, cwd=cwd
            ),
            name=f"verified-account-probe-{session_id}",
        )
        self._account_probe_tasks.add(task)
        task.add_done_callback(self._account_probe_tasks.discard)

    async def _probe_and_stamp_verified_account(
        self,
        session_id: str,
        backend: str,
        env: dict[str, str],
        *,
        launch_target: SshLaunchTargetConfig | None,
        cwd: str,
    ) -> None:
        # Wraps the whole probe+stamp: a raised probe (timeout) or a
        # post-probe storage write (session deleted mid-probe) must never
        # fail the launch/reattach/restore this runs alongside. A ``None``
        # probe result leaves the prior value untouched rather than
        # clobbering good provenance with a transient failure.
        try:
            probe = await probe_account(
                self, backend, env, launch_target=launch_target, cwd=cwd
            )
            if probe is None:
                return
            self._stamp_verified_account(session_id, probe, datetime.now(UTC))
        except Exception:
            log.exception(
                "failed to probe/stamp verified account for session %s", session_id
            )

    async def _drain_account_probe_tasks(self) -> None:
        """Test seam: await any in-flight verified-account probe tasks."""
        tasks = list(self._account_probe_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def account_doctor(
        self,
        *,
        backend: str,
        launch_target_id: str | None = None,
        show_paths: bool = False,
        show_key: bool = False,
    ) -> list[ProfileDoctorReport]:
        """Run the per-profile ``accounts doctor`` checklist for one backend.

        Static checks (config dir, readiness, transcript setup, support) come
        from the shared server-free checklist; a live ``account_matches_expected``
        check is added when the profile declares an ``expected_account_key`` and
        the target is probeable. A profile with any failing check reports
        ``ok=False`` so the CLI can exit non-zero. Account keys stay out of the
        check details unless ``show_key`` is set (phase-1 redaction rules).
        """
        launch_target = self._resolve_launch_target(launch_target_id, backend)
        local = launch_target is None
        probe_blocked = self.remote_probe_blocked(launch_target_id)
        profiles = resolve_account_profiles(self.settings, backend, launch_target)
        reports: list[ProfileDoctorReport] = []
        for profile_id, profile in profiles.items():
            checks = account_profile_static_checks(
                self.settings,
                backend,
                profile_id,
                profile,
                local=local,
                show_paths=show_paths,
            )
            if launch_target is not None:
                checks.append(
                    await self._remote_config_dir_check(
                        launch_target, profile, probe_blocked, show_paths=show_paths
                    )
                )
            checks.append(
                await self._account_matches_check(
                    backend,
                    profile_id,
                    profile,
                    launch_target,
                    probe_blocked,
                    show_key=show_key,
                )
            )
            reports.append(
                ProfileDoctorReport(
                    backend=backend,
                    profile=profile_id,
                    label=profile.label,
                    ok=all(c.ok for c in checks),
                    checks=checks,
                )
            )
        return reports

    async def _remote_config_dir_check(
        self,
        launch_target: SshLaunchTargetConfig,
        profile: AccountProfileConfig,
        probe_blocked: bool,
        *,
        show_paths: bool,
    ) -> ProfileCheck:
        """Best-effort remote existence check for a profile's config dir.

        ``account_profile_static_checks`` skips its filesystem checks entirely
        for a remote target (it has no launch target to reach over SSH); this
        fills the do-not-regress gap by confirming the config dir actually
        resolves and exists on the target, catching a typo'd/never-created
        remote config dir before it surfaces as a launch-time 400 or (worse) a
        silent onboarding hang. The interactive-onboarding readiness verdict
        (``ConfigDirReadinessReporting``) reads local files and has no remote
        implementation yet — a documented follow-up, not covered here.
        """
        name = "remote_config_dir_exists"
        if probe_blocked:
            return ProfileCheck(
                name=name,
                ok=True,
                detail="skipped: launch target needs an SSH master to probe",
            )
        config_dir = profile.config_dir
        if config_dir.startswith("~"):
            tilde_prefix, _ = self._split_tilde_config_dir(config_dir)
            await self._ensure_remote_home_cached(launch_target, tilde_prefix)
        resolved = self._expand_remote_config_dir(launch_target, config_dir)
        if resolved is None:
            return ProfileCheck(
                name=name,
                ok=False,
                detail=(
                    "could not resolve remote home for '~' expansion; "
                    "target may be unreachable"
                ),
            )
        exists = RemoteTranscriptFilesystem(launch_target).exists(resolved)
        shown = resolved if show_paths else "<hidden; pass --show-paths>"
        return ProfileCheck(
            name=name,
            ok=exists,
            detail=f"remote config dir {shown} "
            + ("exists" if exists else "is missing"),
        )

    async def _account_matches_check(
        self,
        backend: str,
        profile_id: str,
        profile: AccountProfileConfig,
        launch_target: SshLaunchTargetConfig | None,
        probe_blocked: bool,
        *,
        show_key: bool,
    ) -> ProfileCheck:
        name = "account_matches_expected"
        if not profile.expected_account_key:
            return ProfileCheck(
                name=name, ok=True, detail="n/a: no expected_account_key set"
            )
        if probe_blocked:
            return ProfileCheck(
                name=name,
                ok=True,
                detail="skipped: launch target needs an SSH master to probe",
            )
        try:
            env = self._profile_launch_env(backend, profile_id, launch_target)
            probe = await probe_account(self, backend, env, launch_target=launch_target)
        except HTTPException as exc:
            return ProfileCheck(name=name, ok=False, detail=str(exc.detail))
        if probe is None:
            return ProfileCheck(name=name, ok=False, detail="could not verify account")
        if probe.account_key != profile.expected_account_key:
            detail = (
                f"authenticates as {probe.account_key!r}, expected "
                f"{profile.expected_account_key!r}"
                if show_key
                else "authenticates as a different account than expected "
                "(pass --show-key for the keys)"
            )
            return ProfileCheck(name=name, ok=False, detail=detail)
        detail = (
            f"matches {profile.expected_account_key!r}"
            if show_key
            else "matches the expected account"
        )
        return ProfileCheck(name=name, ok=True, detail=detail)

    def setup_account_transcripts(
        self,
        backend: str,
        profile_id: str,
        *,
        launch_target_id: str | None = None,
        shared_dir: str | None = None,
        policy: str | None = None,
    ) -> list[str]:
        """Perform the guarded transcript symlink setup for a profile (local only).

        Migrates a populated native store into the shared transcript dir and
        replaces it with a symlink, per the profile's ``symlink_shared`` policy.
        Never runs implicitly during a switch. Raises 400 for a remote target,
        an unsupported policy, a missing shared dir, or a migration conflict.
        """
        launch_target = self._resolve_launch_target(launch_target_id, backend)
        if launch_target is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="remote setup-transcripts is not supported yet",
            )
        profile = self._require_account_profile(backend, profile_id, None)
        effective_policy = policy or profile.transcript_policy
        if effective_policy != "symlink_shared":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"setup-transcripts only applies to the 'symlink_shared' "
                    f"policy, not {effective_policy!r}"
                ),
            )
        effective_shared = shared_dir or profile.shared_transcript_dir
        if not effective_shared:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "symlink_shared requires a shared dir; set "
                    "shared_transcript_dir on the profile or pass --shared-dir"
                ),
            )
        native_store = self.registry.get(backend).capabilities.native_thread_store
        if native_store is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"backend {backend} has no native transcript store",
            )
        store_dir = Path(profile.config_dir).expanduser() / native_store
        try:
            return setup_transcripts_symlink(
                store_dir, Path(effective_shared).expanduser()
            )
        except TranscriptUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    async def create_session(
        self,
        request: SessionCreateRequest,
        *,
        preset_id: str | None = None,
        preset_name: str | None = None,
    ) -> SessionRecord:
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
        await self._warm_remote_home_for_profile(
            launch_target, request.backend, request.account_profile_id
        )
        # Local cwd is fed to subprocess.Popen / tmux new-session, neither of
        # which expand `~`. Resolve it before storing/launching. The remote
        # cwd is left verbatim so the remote shell can do its own expansion.
        if launch_target is not None:
            local_cwd = request.cwd or launch_target.default_cwd
        else:
            local_cwd = require_existing_local_dir(request.cwd)
        effective_env = self._effective_launch_env_for_request(request, launch_target)
        effective_env, account_profile_label = self._apply_account_profile_env(
            request.backend,
            effective_env,
            request.account_profile_id,
            launch_target,
        )
        request = request.model_copy(
            update={
                "cwd": local_cwd,
                "launch_env": effective_env,
            }
        )
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
        # ``plugin`` is now the resolved (agent, transport) driver — its caps
        # carry the transport axis (e.g. ``has_terminal_pane``) the guard needs.
        self._ensure_profile_config_dir_ready(
            request.backend,
            plugin.capabilities,
            effective_env,
            request.account_profile_id,
            launch_target,
        )
        await self._ensure_new_session_profile_transcript_store(
            request.backend,
            effective_env,
            request.account_profile_id,
            launch_target,
        )
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
        # Preset provenance is opaque display metadata — stamped the same generic
        # way as tags so the runtime never inspects the preset spec.
        if preset_id is not None or preset_name is not None:
            session = self.storage.update_session(
                session.id, preset_id=preset_id, preset_name=preset_name
            )
        # Account-profile selection is opaque display/audit metadata; the
        # profile's config-dir is already baked into launch_env above. Stamped
        # generically like preset provenance.
        if request.account_profile_id is not None:
            session = self.storage.update_session(
                session.id,
                account_profile_id=request.account_profile_id,
                account_profile_label=account_profile_label,
            )
            # Fire-and-forget verified-account probe+stamp — a no-profile
            # launch leaves verified_account_* None.
            self._schedule_verified_account_probe(
                session.id,
                request.backend,
                effective_env,
                launch_target=launch_target,
                cwd=session.cwd,
            )
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
            account_profile_id=assistant.account_profile_id,
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
        account_profile_id: str | None = None,
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
            account_profile_id=account_profile_id,
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
            account_profile_id=session.account_profile_id,
            account_profile_label=session.account_profile_label,
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
        account_profile_id: str | None = None,
        account_profile_supplied: bool = False,
        model: str | None = None,
        effort: str | None = None,
        permission_mode: str | None = None,
    ) -> AssistantSummary:
        """Rebuild the assistant on a fresh thread (clear context / switch backend).

        Clearing context keeps the *current* thread's backend and live config
        (model / effort / permission mode / transport / account profile), so a context wipe
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
        if account_profile_supplied:
            selected_profile_id = account_profile_id
        elif old is not None and chosen == old.backend:
            selected_profile_id = old.account_profile_id
        else:
            selected_profile_id = None
        # Spawn the replacement before touching the current thread so a failed
        # launch (e.g. a misconfigured backend) leaves the live, pinned
        # assistant intact rather than orphaning the pointer at a stopped row.
        created = await self._create_assistant_session(
            chosen,
            model=model,
            effort=effort,
            permission_mode=permission_mode,
            transport=transport,
            account_profile_id=selected_profile_id,
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
        launch_target_id = getattr(request, "launch_target_id", None)
        launch_target = self._resolve_launch_target(launch_target_id, backend)
        launch_env = (
            dict(cast(Any, request).launch_env)
            if "launch_env" in request.model_fields_set
            else self._default_launch_env(backend, launch_target)
        )
        account_profile_id = getattr(request, "account_profile_id", None)
        await self._warm_remote_home_for_profile(
            launch_target, backend, account_profile_id
        )
        launch_env, account_profile_label = self._apply_account_profile_env(
            backend, launch_env, account_profile_id, launch_target
        )
        request = request.model_copy(update={"launch_env": launch_env})
        transport = getattr(request, "transport", None)
        if transport is not None:
            self._validate_supported_transport(backend, transport)
            resolved = self.registry.resolve(backend, transport)
            # The resolved transport owner carries the transport axis (e.g.
            # has_terminal_pane) for the readiness guard; below it may be swapped
            # for the agent plugin as the import *mechanism* (a wrapper transport
            # can't enumerate threads), but the session still runs on ``resolved``.
            transport_caps = resolved.capabilities
            driver = (
                resolved
                if resolved.capabilities.supports_thread_import
                else agent_plugin
            )
        else:
            driver = agent_plugin
            # No pinned transport: import resumes over the agent's structured
            # (headless) path unless it falls through to the tmux wrapper — the
            # caller forced it, or AUTO can't use the structured adapter. Mirror
            # the plugin's own resume-wrapper predicate so the guard sees the
            # transport the session will actually run on.
            launch_mode = getattr(request, "launch_mode", None)
            uses_wrapper = launch_mode == LaunchMode.TMUX_WRAPPER or (
                launch_mode == LaunchMode.AUTO
                and not agent_plugin.is_available_for_managed_launch(self)
            )
            if uses_wrapper:
                fallback = self.registry.fallback_for_managed_launch()
                transport_caps = (fallback or agent_plugin).capabilities
            else:
                transport_caps = agent_plugin.capabilities
        self._ensure_profile_config_dir_ready(
            backend, transport_caps, launch_env, account_profile_id, launch_target
        )
        session = await driver.import_thread(self, request, agent=backend)
        # Persist the profile used for listing/import so a later
        # resume/delete/history-read uses the same state root.
        if account_profile_id is not None:
            session = self.storage.update_session(
                session.id,
                account_profile_id=account_profile_id,
                account_profile_label=account_profile_label,
            )
            # Fire-and-forget verified-account probe+stamp, parity with
            # ``create_session``.
            self._schedule_verified_account_probe(
                session.id,
                backend,
                launch_env,
                launch_target=launch_target,
                cwd=session.cwd,
            )
        return session

    async def attach_assistant(
        self,
        *,
        backend: str,
        thread_id: str,
        launch_target_id: str | None = None,
        account_profile_id: str | None = None,
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
            backend,
            {
                "thread_id": thread_id,
                "launch_target_id": launch_target_id,
                "account_profile_id": account_profile_id,
            },
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
            # Boot-restore re-probe is local-only, mirroring the completion
            # warming above — remote hosts aren't fanned out to at boot.
            # Gated on a profile being set, same as launch/thread-import: an
            # unconditional probe would mass-probe the provider's rate-limit
            # endpoint for every no-profile session on every restart.
            if (
                refreshed.account_profile_id is not None
                and refreshed.launch_target_id is None
            ):
                self._schedule_verified_account_probe(
                    refreshed.id,
                    refreshed.backend,
                    refreshed.launch_env,
                    launch_target=None,
                    cwd=refreshed.cwd,
                )

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
        # Serialized with the launch-settings switch and reattach so a terminate
        # can't interleave their terminate→restore window on the same session.
        async with self._session_lock(session_id):
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
        # paths too. Serialized with terminate and the launch-settings switch.
        async with self._session_lock(session.id):
            # Re-read under the lock: a concurrent switch/terminate may have
            # changed the record since the caller captured it.
            session = self.get_session(session.id)
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
            # Re-probe after the terminal-status check so a probe failure
            # never converts a good reattach into a 400. Gated on a profile
            # being set, same as launch/thread-import/boot-restore — a
            # no-profile session has nothing to re-verify.
            if refreshed.account_profile_id is not None:
                self._schedule_verified_account_probe(
                    refreshed.id,
                    refreshed.backend,
                    refreshed.launch_env,
                    launch_target=self._find_launch_target(refreshed.launch_target_id),
                    cwd=refreshed.cwd,
                )
            self._start_context_usage_source(refreshed)
            return refreshed

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    def _transport_switch_options(
        self, session: SessionRecord, caps: BackendCapabilities
    ) -> list[TransportSettingsOption]:
        """Project the interfaces a session may safely switch to.

        Switching is offered only for a managed session whose agent has a
        resumable native store (``native_thread_store`` — Claude/Codex, not
        OpenCode or a bare attached pane) and declares more than one usable
        transport. Each option carries the restart-scoped launch capabilities of
        the resulting ``(agent, transport)`` pair so the modal can gate advanced
        fields while an Interface change is staged. The current transport is
        listed first so the controlled select is valid before any change.
        """
        if session.source == SessionSource.ATTACHED_TMUX:
            return []
        # An agent with no resumable native store cannot prove a switch keeps the
        # conversation, so it advertises none (agent-agnostic OpenCode exclusion).
        if not caps.native_thread_store:
            return []
        safe_targets: list[str] = []
        for transport_id in self.registry.supported_transports(session.backend):
            if transport_id == session.transport:
                continue
            try:
                pair_caps = self.registry.capabilities_for_pair(
                    session.backend, transport_id
                )
            except KeyError:
                continue
            if not pair_caps.supports_reattach_after_exit:
                continue
            safe_targets.append(transport_id)
        if not safe_targets:
            return []
        options: list[TransportSettingsOption] = []
        for transport_id in [session.transport, *safe_targets]:
            try:
                pair_caps = self.registry.capabilities_for_pair(
                    session.backend, transport_id
                )
            except KeyError:
                continue
            options.append(
                TransportSettingsOption(
                    id=transport_id,
                    supports_launch_settings_with_restart=(
                        pair_caps.supports_launch_settings_with_restart
                        and pair_caps.supports_reattach_after_exit
                    ),
                    supports_account_profile_with_restart=(
                        pair_caps.supports_account_profile_with_restart
                    ),
                    supports_custom_args=pair_caps.supports_custom_cli_args,
                    supports_config_overrides=pair_caps.supports_config_overrides,
                )
            )
        return options

    def get_launch_settings(self, session_id: str) -> LaunchSettingsResponse:
        session = self.get_session(session_id)
        caps = self.registry.capabilities_for(session)
        launch_target = self._find_launch_target(session.launch_target_id)
        profiles = redacted_profile_metadata(
            self.settings, session.backend, launch_target
        )
        # A bare attached tmux pane advertises the restart capability at the
        # transport level, but Waypoint does not own the process, so it cannot
        # honestly restart-and-resume it. Mirror the runtime gate here.
        is_attached_tmux = session.source == SessionSource.ATTACHED_TMUX
        supports_launch_settings_with_restart = (
            caps.supports_launch_settings_with_restart
            and caps.supports_reattach_after_exit
            and not is_attached_tmux
        )
        # An attached pane's derived account-profile capability is True (its
        # agent has a config-dir env var), but Waypoint doesn't own the process,
        # so force it false — else the modal renders a profile picker it can't
        # honestly apply (the terminal overflow now opens this modal).
        supports_account_profile_with_restart = (
            caps.supports_account_profile_with_restart and not is_attached_tmux
        )
        transport_options = self._transport_switch_options(session, caps)
        config_dir_env_var = caps.config_dir_env_var
        protected_launch_env_keys = ["WAYPOINT_SESSION_ID"]
        if config_dir_env_var and session.account_profile_id:
            # The selected profile owns its config-dir key; it must not be
            # editable as a raw env var while that profile is active.
            protected_launch_env_keys.append(config_dir_env_var)
        return LaunchSettingsResponse(
            backend=session.backend,
            transport=session.transport,
            launch_target_id=session.launch_target_id,
            account_profile_id=session.account_profile_id,
            account_profile_label=session.account_profile_label,
            account_profiles=[cast(Any, meta) for meta in profiles],
            args=list(session.args),
            config_overrides=list(session.config_overrides),
            # Redacted: only the env keys, never their (possibly secret) values.
            launch_env_keys=sorted(session.launch_env.keys()),
            protected_launch_env_keys=protected_launch_env_keys,
            config_dir_env_var=config_dir_env_var,
            supports_custom_args=caps.supports_custom_cli_args,
            supports_config_overrides=caps.supports_config_overrides,
            supports_account_profile_with_restart=(
                supports_account_profile_with_restart
            ),
            supports_launch_settings_with_restart=(
                supports_launch_settings_with_restart
            ),
            transport_options=transport_options,
            supports_transport_switch_with_restart=len(transport_options) > 1,
            requires_restart=True,
        )

    async def update_launch_settings(
        self, session_id: str, request: LaunchSettingsUpdateRequest
    ) -> SessionRecord:
        """Apply restart-scoped launch-settings edits via terminate → restore.

        Serialized per session. When the account profile changes it flushes any
        running turn, ensures the target profile can see the native transcript,
        and probes the target account before terminating; after restore it
        re-probes and rolls back to the prior settings if the account didn't
        actually change. See the RFC (docs/… issue #230) for the state machine.
        """
        lock = self._session_lock(session_id)
        if lock.locked():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="another lifecycle operation is in progress for this session",
            )
        async with lock:
            return await self._update_launch_settings_locked(session_id, request)

    def _has_durable_conversation(self, session_id: str) -> bool:
        """Whether a fresh native thread could discard visible conversation context."""
        return any(
            event.kind in {EventKind.USER_INPUT, EventKind.AGENT_OUTPUT}
            for event in self.storage.list_events(session_id)
        )

    async def _update_launch_settings_locked(
        self, session_id: str, request: LaunchSettingsUpdateRequest
    ) -> SessionRecord:
        session = self.get_session(session_id)
        plugin = self.registry.plugin_for(session)
        agent_plugin = self.registry.get(session.backend)
        caps = self.registry.capabilities_for(session)
        target_transport = (
            request.transport
            if "transport" in request.model_fields_set and request.transport is not None
            else session.transport
        )
        transport_changing = target_transport != session.transport
        # Gate restart-scoped capability checks on the pair the session will run
        # as after the switch; operate/terminate the current process through the
        # current pair. For a non-switch these are the same composed caps.
        gate_caps = (
            self.registry.capabilities_for_pair(session.backend, target_transport)
            if transport_changing
            else caps
        )
        fresh_thread_restarter: FreshThreadRestarting | None = None
        turn_flushed = False
        # A pending approval/question can't be carried to another interface; note
        # its cancellation so the transition is observable in the transcript.
        had_pending_input = session.status == SessionStatus.WAITING_INPUT
        if session.status == SessionStatus.STARTING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="cannot change launch settings while the session is STARTING",
            )
        if session.source == SessionSource.ATTACHED_TMUX:
            # Waypoint does not own the process behind a bare attached pane, so
            # it cannot restart-and-resume it; the transport still advertises
            # the restart capability, so guard here rather than trusting caps.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="cannot change launch settings for an attached tmux session",
            )
        if transport_changing:
            # Only interfaces the server projects as safe targets are accepted;
            # recompute under the lock rather than trusting the client. The set
            # excludes attached panes, agents without a resumable native store
            # (OpenCode), and single-usable-transport agents.
            valid_targets = {
                option.id for option in self._transport_switch_options(session, caps)
            }
            if target_transport not in valid_targets:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"cannot switch {session.backend} to interface "
                        f"{target_transport!r}: not an offered switch target"
                    ),
                )
        if not (
            gate_caps.supports_launch_settings_with_restart
            and gate_caps.supports_reattach_after_exit
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{session.backend} does not support restart-applied launch settings",
            )
        if not request.restart:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="restart must be true to change a running session's launch settings",
            )

        launch_target = self._find_launch_target(session.launch_target_id)
        fields = request.model_fields_set
        selected_profile_id = (
            request.account_profile_id
            if "account_profile_id" in fields
            else session.account_profile_id
        )
        profile_changing = (
            "account_profile_id" in fields
            and request.account_profile_id != session.account_profile_id
            and request.account_profile_id is not None
        )
        if profile_changing and not gate_caps.supports_account_profile_with_restart:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{session.backend} does not support account-profile switching",
            )

        native_thread_id: str | None = None
        if transport_changing:
            # v1 requires a durably-persisted native thread: the switch is a pure
            # resume onto the target interface. Non-destructive, so run before any
            # teardown — an unpersisted thread is rejected while the session is
            # untouched (FR5: never silently start fresh over recorded events).
            native_thread_id = agent_plugin.native_thread_id(session)
            thread_config_dir = config_dir_for(caps, session.launch_env)
            # A native-thread-store agent (gated by the switch-options projection)
            # implements the launch contract; guard for mypy and defensiveness.
            persisted = (
                bool(native_thread_id)
                and isinstance(agent_plugin, AgentLaunchContract)
                and await agent_plugin.conversation_exists(
                    cast(str, native_thread_id),
                    session.cwd,
                    launch_target,
                    config_dir=thread_config_dir,
                )
            )
            if not persisted:
                if self._has_durable_conversation(session.id):
                    detail = (
                        "cannot switch interface: the native thread has no "
                        "persisted transcript but this session has conversation "
                        "events, so switching would lose context"
                    )
                else:
                    detail = (
                        "cannot switch interface: send a message first so the "
                        "conversation is persisted before changing interface"
                    )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=detail
                )

        if launch_target is not None:
            # Clean 409 ``ssh-master-required`` for a dead password master
            # before any destructive step (the frontend prompts + retries).
            await self._require_live_master(launch_target)
            await self._warm_remote_home_for_profile(
                launch_target, session.backend, selected_profile_id
            )

        if request.args is not None and not caps.supports_custom_cli_args:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{session.backend} does not support custom CLI args",
            )
        if request.config_overrides is not None and not caps.supports_config_overrides:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{session.backend} does not support config overrides",
            )
        new_args = (
            list(request.args) if request.args is not None else list(session.args)
        )
        new_config_overrides = (
            list(request.config_overrides)
            if request.config_overrides is not None
            else list(session.config_overrides)
        )
        new_env = dict(session.launch_env)
        for key in request.env_unset:
            if key == "WAYPOINT_SESSION_ID":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="cannot unset WAYPOINT_SESSION_ID",
                )
            new_env.pop(key, None)
        new_env.update(request.env_set)
        # Profile owns its config-dir env key (strips any raw value); re-applied
        # even when unchanged so an env edit can't shadow it.
        new_env, resolved_label = self._apply_account_profile_env(
            session.backend, new_env, selected_profile_id, launch_target
        )
        new_profile_label = resolved_label if selected_profile_id is not None else None
        # Reject before the destructive terminate/restore if the target profile's
        # config dir would strand this session on an interactive first-run prompt
        # (``caps`` is the session's composed transport capability).
        self._ensure_profile_config_dir_ready(
            session.backend, caps, new_env, selected_profile_id, launch_target
        )

        old_launch_env = dict(session.launch_env)
        config_dir_key = caps.config_dir_env_var
        verified_account_fields: dict[str, Any] = {}
        if profile_changing:
            profile = self._require_account_profile(
                session.backend, cast(str, request.account_profile_id), launch_target
            )
            # Verify the account *before* any destructive step. A probe
            # authenticates from launch_env (not the live process), so the
            # target account is fully knowable now — reject here, while the
            # session is still untouched, rather than after terminating. (A
            # post-restore re-probe would read the same launch_env and so is
            # tautological; the account is fixed by the env we verify here.)
            target_probe = await probe_account(
                self,
                session.backend,
                new_env,
                launch_target=launch_target,
                cwd=session.cwd,
            )
            if target_probe is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="could not verify the target account before switching",
                )
            # Stamp verified_account_* from the probe already run above — no
            # second probe. Unlike the other population points, which probe
            # off the response path, this is the only synchronous stamp.
            verified_account_fields = {
                "verified_account_key": target_probe.account_key,
                "verified_account_label": target_probe.account_label,
                "verified_account_probed_at": datetime.now(UTC),
            }
            if profile.expected_account_key:
                if target_probe.account_key != profile.expected_account_key:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"target account {target_probe.account_key!r} does not "
                            f"match the profile's expected account "
                            f"{profile.expected_account_key!r}"
                        ),
                    )
            else:
                # No expected key: refuse a switch that wouldn't actually change
                # the account (e.g. macOS keeps credentials in the Keychain, so
                # moving the config dir changes settings but not the account) —
                # persisting it as a switch would be a false success.
                current_probe = await probe_account(
                    self,
                    session.backend,
                    old_launch_env,
                    launch_target=launch_target,
                    cwd=session.cwd,
                )
                if (
                    current_probe is not None
                    and current_probe.account_key == target_probe.account_key
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            "the target profile resolves to the same account as the "
                            "current one, so switching its config dir would not change "
                            "the account (set expected_account_key if this is intended)"
                        ),
                    )
            # Account verified — now flush a running turn so the native
            # transcript is complete before the transcript step / termination.
            if session.status in {SessionStatus.RUNNING, SessionStatus.WAITING_INPUT}:
                await self.transport_for(session).interrupt(session)
                session = self.storage.update_session(
                    session.id, status=SessionStatus.INTERRUPTED
                )
                await self.transport_for(session).flush_before_restart(session)
                turn_flushed = True
            if config_dir_key:
                # ``config_dir_for`` reads only launch_env — no ``os.environ``
                # fallback for remote (the backend host's env is meaningless on
                # the remote target); local keeps the existing host-env fallback
                # for a session that never had an explicit override.
                current_config_dir = config_dir_for(caps, old_launch_env)
                if launch_target is None:
                    current_config_dir = current_config_dir or os.environ.get(
                        config_dir_key
                    )
                    if current_config_dir is None and isinstance(
                        plugin, DefaultConfigDirProviding
                    ):
                        current_config_dir = plugin.default_config_dir()
                target_config_dir = config_dir_for(caps, new_env)
                if target_config_dir:
                    fs: TranscriptFilesystem = (
                        LocalTranscriptFilesystem()
                        if launch_target is None
                        else RemoteTranscriptFilesystem(launch_target)
                    )
                    try:
                        # Off-thread: the remote fs does blocking SSH I/O
                        # (~8-15 round-trips), which would freeze the event
                        # loop for every other session; the local fs is fast
                        # but wrapping uniformly keeps one code path.
                        transcript_availability = await asyncio.to_thread(
                            ensure_thread_available,
                            plugin,
                            session,
                            current_config_dir=current_config_dir,
                            target_config_dir=target_config_dir,
                            policy=profile.transcript_policy,
                            shared_transcript_dir=profile.shared_transcript_dir,
                            native_thread_store=caps.native_thread_store,
                            fs=fs,
                        )
                    except TranscriptUnavailableError as exc:
                        await self._broadcast_session_list()
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"cannot switch account profile: {exc}",
                        ) from exc
                    if transcript_availability == ThreadAvailability.UNPERSISTED:
                        if self._has_durable_conversation(session.id):
                            await self._broadcast_session_list()
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=(
                                    "cannot switch account profile: the native thread "
                                    "has no persisted transcript but this session has "
                                    "conversation events, so starting fresh would lose context"
                                ),
                            )
                        if not isinstance(plugin, FreshThreadRestarting):
                            await self._broadcast_session_list()
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=(
                                    "cannot switch account profile: "
                                    f"{unpersisted_thread_error(profile.transcript_policy)}"
                                ),
                            )
                        fresh_thread_restarter = plugin

        # Clearing the profile (set -> None) is not ``profile_changing``, but a
        # de-profiled session must not keep stale provenance, so null the
        # triple explicitly here; ``profile_changing`` already set the triple
        # above from the probe just run, and any other update (model/args-only)
        # leaves it empty so the persisted values are untouched.
        if not profile_changing and (
            "account_profile_id" in fields
            and request.account_profile_id is None
            and session.account_profile_id is not None
        ):
            verified_account_fields = {
                "verified_account_key": None,
                "verified_account_label": None,
                "verified_account_probed_at": None,
            }

        # Flush a running turn before teardown for any restart-resume that hasn't
        # already (the profile branch flushes after account-verify). A transport
        # switch of a RUNNING/WAITING session interrupts here so the native
        # transcript includes the settled final turn at handoff.
        if not turn_flushed and session.status in {
            SessionStatus.RUNNING,
            SessionStatus.WAITING_INPUT,
        }:
            await self.transport_for(session).interrupt(session)
            session = self.storage.update_session(
                session.id, status=SessionStatus.INTERRUPTED
            )
            await self.transport_for(session).flush_before_restart(session)
            turn_flushed = True

        # For a transport switch, reset transport_state to the neutral,
        # agent-owned handoff payload (the native thread id) and discard the old
        # driver's pane/adapter keys; the target transport rebuilds its own state
        # in restore_session from this id plus the persisted launch fields.
        transport_state_fields: dict[str, Any] = {}
        if transport_changing:
            transport_state_fields["transport"] = target_transport
            transport_state_fields["transport_state"] = (
                {"thread_id": native_thread_id} if native_thread_id else {}
            )

        # Terminate → persist new settings → restore. terminate/restore cycle the
        # rate-limit watcher; restore rebuilds env from the persisted record, so
        # the account is fixed by the (already-verified) launch_env. Mark the
        # record EXITED before restore: the process is gone, and a pane-wrapping
        # transport (claude_tty) only relaunches on an EXITED reattach — without
        # this it would take the boot-time branch, keep the dead pane, and reject
        # the next input with "can't find pane". Native transports (claude_cli,
        # codex) relaunch unconditionally, so this is a no-op for them. The old
        # (current) plugin tears down; the target plugin restores.
        await plugin.terminate_session(self, session)
        await self._cancel_context_usage_source(session.id)
        self.storage.update_session(
            session.id,
            status=SessionStatus.EXITED,
            args=new_args,
            config_overrides=new_config_overrides,
            launch_env=new_env,
            account_profile_id=selected_profile_id,
            account_profile_label=new_profile_label,
            **transport_state_fields,
            **verified_account_fields,
        )
        restored_session = self.get_session(session.id)
        target_plugin = self.registry.resolve(session.backend, target_transport)
        if fresh_thread_restarter is not None:
            await fresh_thread_restarter.restart_unpersisted_session(
                self, restored_session
            )
        else:
            await target_plugin.restore_session(self, restored_session)
        refreshed = self.get_session(session.id)
        if refreshed.status in {SessionStatus.EXITED, SessionStatus.ERROR}:
            # Restore failed: keep the new settings on the record (per the RFC —
            # a rollback here could flip back to an already-rate-limited account)
            # and surface the terminal state so the user can reattach.
            await self._broadcast_session_list()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"restore failed after applying launch settings "
                    f"({refreshed.status}); settings kept, reattach to retry"
                ),
            )
        self._start_context_usage_source(refreshed)
        if transport_changing:
            note = (
                f"Session interface changed from {plugin.label} to "
                f"{target_plugin.label}"
            )
        elif selected_profile_id is not None and profile_changing:
            note = f"Session restarted with account profile {new_profile_label}"
        else:
            note = "Session restarted with new launch settings"
        await self._record_system_event(session.id, note)
        if transport_changing and had_pending_input:
            await self._record_system_event(
                session.id,
                "Pending approval was cancelled by the interface change; "
                "re-run the request on the new interface if needed",
            )
        await self._broadcast_session_list()
        return self.get_session(session.id)

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
        # Drop the per-session lock along with the record so the registry
        # doesn't grow unbounded over a long-lived server's session churn.
        self._session_locks.pop(session_id, None)
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

    def _enrich_inbox_ref(self, ref: InboxAttachmentRef) -> InboxAttachmentRef:
        # Denormalize the display name/kind from the resolved spec so the UI
        # renders it inline (no per-session lookup). Backend-authoritative:
        # overwrite whatever the client sent, and clear both fields when the
        # attachment can't be resolved so a bogus ref can't carry a fake label.
        match = self.attachments.resolve(ref.session_id, ref.attachment_id)
        spec = match[0] if match is not None else None
        return ref.model_copy(
            update={
                "filename": spec.filename if spec else None,
                "kind": spec.kind if spec else None,
            }
        )

    async def post_inbox_item(self, request: InboxPostRequest) -> InboxItem:
        from_label: str | None = None
        if request.from_session_id is not None:
            session = self.storage.get_session(request.from_session_id)
            if session is not None:
                from_label = session.title
        blocks: list[InboxBlockInput] = [
            (
                block.model_copy(update={"ref": self._enrich_inbox_ref(block.ref)})
                if isinstance(block, InboxAttachmentBlockInput)
                else block
            )
            for block in request.blocks
        ]
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
        if reply is not None:
            reply = reply.model_copy(
                update={
                    "attachments": [
                        self._enrich_inbox_ref(ref) for ref in reply.attachments
                    ]
                }
            )
        result = self.storage.submit_inbox_block(
            item_id, block_id, answer=answer, reply=reply
        )
        if result is None:
            return None
        item, changed = result
        # A no-op submit (neither answer nor reply) leaves the item untouched;
        # don't re-broadcast to every client.
        if changed:
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

    async def delete_inbox_items(self, item_ids: list[str]) -> list[str]:
        deleted = self.storage.delete_inbox_items(item_ids)
        for item_id in deleted:
            await self._publish_inbox_deleted(item_id)
        return deleted

    async def delete_resolved_inbox_items(self) -> list[str]:
        deleted = self.storage.delete_resolved_inbox_items()
        for item_id in deleted:
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
        account_profile_id: str | None = None,
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
            account_profile_id=account_profile_id,
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
                    "default_launch_env_by_backend": {
                        backend: self._default_launch_env(backend, target)
                        for backend in target.supported_plugins()
                    },
                    # Only agent backends that host profiles produce a non-empty
                    # list, so transports/opencode are omitted (agent-ids only).
                    "account_profiles_by_backend": {
                        backend: profiles
                        for backend in target.supported_plugins()
                        if (
                            profiles := redacted_profile_metadata(
                                self.settings, backend, target
                            )
                        )
                    },
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
        # Every import routes through here, so stamp the adoption marker that
        # makes token-usage coverage report "tracked since" (the adopted thread
        # had prior turns Waypoint never metered). Merge to keep other keys.
        session = self.storage.get_session(session_id)
        if session is not None and not session.transport_state.get("adopted_thread"):
            merged = {**session.transport_state, "adopted_thread": True}
            self.storage.update_session(session_id, transport_state=merged)
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

    def _token_usage_init(
        self, session: SessionRecord, observed_at: datetime
    ) -> "TokenUsageInit":
        """Coverage seed for a session's first token-usage record.

        Claims the whole session only when metered from turn one; anything that
        adopted prior native history (imports, attached tmux, or sessions
        predating the ledger) can honestly claim only "tracked since".
        """
        is_from_birth = (
            session.source in {SessionSource.MANAGED, SessionSource.ASSISTANT}
            and not session.transport_state.get("adopted_thread")
            and not session.transport_state.get("pretracked_tokens")
        )
        if is_from_birth:
            return TokenUsageInit(
                coverage="entire_waypoint_session",
                observed_from=session.created_at,
            )
        return TokenUsageInit(coverage="tracked_since", observed_from=observed_at)

    async def publish_token_usage_record(
        self,
        session_id: str,
        record: "TokenUsageRecord",
        *,
        publish: bool = True,
    ) -> None:
        """Fold one per-turn record into the durable session token aggregate.

        Additive only — never touches ``context_usage``, so a swallowed failure
        can't drop the snapshot or interrupt the turn.
        """
        if not record.record_id:
            # An identity-less event can't key a ledger row (a "" key would
            # collapse distinct turns), so skip aggregation.
            log.debug(
                "token usage record rejected: missing identity",
                extra={"session_id": session_id, "source": record.source},
            )
            return
        try:
            session = self.storage.get_session(session_id)
            if session is None:
                return
            init = self._token_usage_init(session, record.observed_at)
            self.storage.record_token_usage(session_id, record, init=init)
        except Exception:
            log.debug(
                "failed to record token usage",
                extra={"session_id": session_id},
                exc_info=True,
            )
            return
        if publish:
            self._publish_session_state(session_id)

    def token_usage_callback(
        self,
    ) -> Callable[[str, "TokenUsageRecord", bool], Awaitable[None]]:
        async def _publish_token_usage(
            session_id: str, record: "TokenUsageRecord", publish: bool
        ) -> None:
            await self.publish_token_usage_record(session_id, record, publish=publish)

        return _publish_token_usage

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
        launch_env: dict[str, str] | None = None,
    ) -> list[str]:
        plugin = self.registry.get(backend)
        extra_env = self._agent_process_env(
            backend, dict(launch_env or {}), session_id=session_id
        )
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
