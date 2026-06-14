"""Claude TUI backend plugin (claude_tty).

Drives the interactive Claude Code TUI (``claude``) instead of ``claude -p``
(stream-json mode).  The TUI path is exempt from the API rate limit applied to
``claude -p``, making it the preferred backend for autonomous Waypoint sessions.

Architecture summary:
- Input: reuses the ``tmux`` transport (send-keys injection, pipe-pane logging).
- Output: a transcript tailer reads ``~/.claude/projects/<cwd>/<uuid>.jsonl`` by
  byte offset and normalizes records into the canonical event stream.
- ``is_structured=True`` so the frontend renders structured events, not the
  heuristic raw-terminal view.

Inherits from ``TmuxPlugin`` to reuse shared infrastructure:
``_conversation_exists``, ``_spawn_rate_limit_watcher``, ``_rate_limit_refresh_loop``,
``refresh_rate_limit_usage``, ``_ssh_capture``, ``native_thread_id``,
``on_session_deleted``, ``_resume_args``.
"""

import asyncio
import logging
import shutil
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status
from pydantic import Field

from waypoint.backends.capabilities import BackendCapabilities, ModelSource
from waypoint.backends.claude_code.models import (
    DEFAULT_CLAUDE_MODELS,
    claude_default_model_id,
)
from waypoint.backends.claude_code.permission_modes import (
    CLAUDE_PERMISSION_MODE_SPECS,
    CLAUDE_PERMISSION_MODES,
)
from waypoint.backends.claude_code.rate_limits import (
    probe_claude_usage,
    probe_claude_usage_remote,
)
from waypoint.backends.claude_tty.tailer import TranscriptTailer
from waypoint.backends.plugin_config import PluginConfig
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.backends.tmux.plugin import TmuxPlugin
from waypoint.git_meta import GitMeta
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import (
    BackendModelOption,
    SessionCreateRequest,
    SessionRateLimitUsage,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.claude_tty")


class ClaudeTtyPluginConfig(PluginConfig):
    """Configuration for the claude_tty plugin.

    Shares the same static model catalogue as claude_code; no runtime
    model/list RPC is available for Claude.
    """

    models: list[BackendModelOption] = Field(
        default_factory=lambda: list(DEFAULT_CLAUDE_MODELS)
    )
    default_model_id: str | None = Field(default_factory=claude_default_model_id)
    default_effort: str | None = None


class ClaudeTtyPlugin(TmuxPlugin):
    id = "claude_tty"
    transport_id = "claude_tty"
    label = "Claude TUI"
    config_schema: type[PluginConfig] = ClaudeTtyPluginConfig
    extra_env = {"CLAUDE_CODE_NO_FLICKER": "1"}
    capabilities = BackendCapabilities(
        is_structured=True,
        supports_resume=True,
        supports_reattach_after_exit=True,
        supports_fork=True,
        supports_attachments=True,
        supports_set_model_inline=False,
        supports_set_effort_inline=False,
        supports_set_permission_mode_inline=False,
        model_source=ModelSource.STATIC,
        permission_modes=CLAUDE_PERMISSION_MODE_SPECS,
        badges={"glyph": "C", "color": "#a78bfa"},
        cli_binary="claude",
        target_aliases=("claude_tty",),
    )

    def __init__(self) -> None:
        self._tailer_tasks: dict[str, asyncio.Task[None]] = {}

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        from waypoint.backends.claude_tty.transport import ClaudeTtyTransport

        return ClaudeTtyTransport(runtime)

    def is_available_for_managed_launch(self, runtime: "SessionRuntime") -> bool:
        return shutil.which("claude") is not None

    def remote_executable(self, launch_target: SshLaunchTargetConfig) -> str:
        return "claude"

    def validate_permission_mode(self, mode: str | None) -> str | None:
        if mode is None:
            return None
        if mode not in CLAUDE_PERMISSION_MODES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown permission mode for claude_tty: {mode!r}",
            )
        return mode

    def _config(self, runtime: "SessionRuntime") -> ClaudeTtyPluginConfig:
        config = runtime.settings.plugin_config(self.id)
        if not isinstance(config, ClaudeTtyPluginConfig):
            return ClaudeTtyPluginConfig()
        return config

    async def list_models(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        config = self._config(runtime)
        default_model = config.default_model_id
        options = [opt.model_dump(mode="json") for opt in config.models]
        default_model_id: str | None = None
        if default_model is None:
            for opt in config.models:
                if opt.is_default:
                    default_model_id = opt.id
                    break
        else:
            default_model_id = default_model
        default_model_label: str | None = None
        if default_model_id:
            for opt in config.models:
                if opt.id == default_model_id:
                    default_model_label = opt.label
                    break
        return {
            "backend": self.id,
            "models": options,
            "default_model_id": default_model_id,
            "default_model_label": default_model_label,
            "default_effort": config.default_effort,
            "supports_free_text": True,
        }

    async def probe_account_rate_limit(
        self,
        runtime: "SessionRuntime",
        launch_target: SshLaunchTargetConfig | None,
    ) -> SessionRateLimitUsage | None:
        if launch_target is None:
            return await probe_claude_usage()
        return await probe_claude_usage_remote(launch_target)

    # ── Session lifecycle ────────────────────────────────────────────────────

    def _start_tailer(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        thread_id: str,
        cwd: str,
        *,
        start_at_end: bool = False,
    ) -> None:
        if session_id in self._tailer_tasks:
            return
        tailer = TranscriptTailer(
            session_id=session_id,
            session_uuid=thread_id,
            cwd=cwd,
            runtime=runtime,
            start_at_end=start_at_end,
        )
        self._tailer_tasks[session_id] = asyncio.create_task(tailer.run())

    async def terminate_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        await super().terminate_session(runtime, session)
        tailer_task = self._tailer_tasks.pop(session.id, None)
        if tailer_task is not None:
            tailer_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await tailer_task

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
        thread_id = str(uuid.uuid4())
        launch_args = _build_launch_args(
            thread_id=thread_id,
            permission_mode=permission_mode,
            model=resolved_model,
            effort=resolved_effort,
            extra_args=request.args,
        )
        try:
            command = runtime._command_for_backend(
                self.id,
                launch_args,
                launch_target,
                request.cwd,
                allocate_tty=True,
                session_id=session_id,
            )
        except HTTPException:
            raise
        try:
            target = await runtime.tmux.start_managed_session(
                session_id, request.cwd, command
            )
            await runtime.tmux.pipe_output(target.pane, raw_log)
        except TmuxError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=self.id,
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
            transport_state={
                "tmux_session": target.session,
                "tmux_window": target.window,
                "tmux_pane": target.pane,
                "pid": target.pane_pid,
                "thread_id": thread_id,
                "launch_args": launch_args,
            },
            spawner_session_id=request.spawner_session_id,
            permission_mode=permission_mode,
            model=resolved_model,
            effort=resolved_effort,
            args=request.args,
        )
        runtime.storage.create_session(session)
        await runtime._record_system_event(
            session.id,
            f"Claude TUI session started (thread {thread_id})",
            status=SessionStatus.IDLE,
        )
        self._start_tailer(runtime, session.id, thread_id, request.cwd)
        self._spawn_rate_limit_watcher(runtime, session)
        return runtime.get_session(session.id)

    async def restore_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        state = session.transport_state
        thread_id: str | None = state.get("thread_id")

        if session.status != SessionStatus.EXITED:
            # Boot-time restore: pane should still be running.  Start the
            # tailer at the current end of the transcript so we don't replay
            # records that are already in the event DB.
            if thread_id:
                self._start_tailer(
                    runtime,
                    session.id,
                    thread_id,
                    session.cwd,
                    start_at_end=True,
                )
            self._spawn_rate_limit_watcher(runtime, session)
            return

        # EXITED reconnect: relaunch a new tmux session.
        old_tmux_session = state.get("tmux_session")
        if old_tmux_session:
            with suppress(TmuxError):
                await runtime.tmux.kill_session(old_tmux_session)

        launch_target = runtime._find_launch_target(session.launch_target_id)
        stored_args = state.get("launch_args")
        base_args = _scrub_session_args(
            stored_args if isinstance(stored_args, list) else []
        )

        effective_thread_id: str | None = None
        if thread_id and await self._conversation_exists(
            "claude_code", thread_id, session.cwd, launch_target
        ):
            effective_thread_id = thread_id

        if effective_thread_id:
            new_thread_id = effective_thread_id
            launch_args = ["--resume", effective_thread_id, *base_args]
        else:
            new_thread_id = str(uuid.uuid4())
            launch_args = ["--session-id", new_thread_id, *base_args]

        try:
            command = runtime._command_for_backend(
                self.id,
                launch_args,
                launch_target,
                session.cwd,
                allocate_tty=True,
                session_id=session.id,
            )
        except HTTPException as exc:
            await runtime._record_system_event(
                session.id,
                f"Failed to rebuild launch command for reconnect: {exc.detail}",
                status=SessionStatus.EXITED,
            )
            return

        raw_log = Path(session.raw_log_path)
        with suppress(OSError):
            raw_log.parent.mkdir(parents=True, exist_ok=True)
            raw_log.write_bytes(b"")

        try:
            target = await runtime.tmux.start_managed_session(
                session.id, session.cwd, command
            )
            await runtime.tmux.pipe_output(target.pane, raw_log)
        except TmuxError as exc:
            await runtime._record_system_event(
                session.id,
                f"Failed to relaunch tmux session: {exc}",
                status=SessionStatus.EXITED,
            )
            return

        new_state: dict[str, Any] = {
            "tmux_session": target.session,
            "tmux_window": target.window,
            "tmux_pane": target.pane,
            "pid": target.pane_pid,
            "thread_id": new_thread_id,
            "launch_args": launch_args,
        }
        runtime.storage.update_session(
            session.id, transport_state=new_state, status=SessionStatus.STARTING
        )
        message = (
            f"Session reconnected (resumed thread {new_thread_id})"
            if effective_thread_id
            else "Session reconnected (new thread)"
        )
        await runtime._record_system_event(
            session.id, message, status=SessionStatus.IDLE
        )
        self._start_tailer(runtime, session.id, new_thread_id, session.cwd)
        self._spawn_rate_limit_watcher(runtime, session)

    async def fork_session(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        new_session_id: str,
        title: str,
        raw_log: Path,
        structured_log: Path,
    ) -> SessionRecord:
        src_thread_id: str | None = session.transport_state.get("thread_id")
        if not src_thread_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude_tty session has no thread id to fork from",
            )
        launch_target = (
            runtime._find_launch_target(session.launch_target_id)
            if session.launch_target_id
            else None
        )
        stored_args = session.transport_state.get("launch_args")
        base_args = _scrub_session_args(
            stored_args if isinstance(stored_args, list) else []
        )
        new_thread_id = str(uuid.uuid4())
        launch_args = [
            "--resume",
            src_thread_id,
            "--fork-session",
            "--session-id",
            new_thread_id,
            *base_args,
        ]
        try:
            command = runtime._command_for_backend(
                self.id,
                launch_args,
                launch_target,
                session.cwd,
                allocate_tty=True,
                session_id=new_session_id,
            )
        except HTTPException:
            raise
        raw_log.parent.mkdir(parents=True, exist_ok=True)
        raw_log.touch(exist_ok=True)
        try:
            target = await runtime.tmux.start_managed_session(
                new_session_id, session.cwd, command
            )
            await runtime.tmux.pipe_output(target.pane, raw_log)
        except TmuxError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        now = datetime.now(UTC)
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
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={
                "tmux_session": target.session,
                "tmux_window": target.window,
                "tmux_pane": target.pane,
                "pid": target.pane_pid,
                "thread_id": new_thread_id,
                "launch_args": launch_args,
            },
            permission_mode=session.permission_mode,
            model=session.model,
            effort=session.effort,
            args=session.args,
        )
        runtime.storage.create_session(new_session)
        await runtime._record_system_event(
            new_session_id,
            f"Claude TUI forked from {session.title or session.id} (thread {new_thread_id})",
            status=SessionStatus.IDLE,
        )
        self._start_tailer(runtime, new_session_id, new_thread_id, session.cwd)
        self._spawn_rate_limit_watcher(runtime, new_session)
        return runtime.get_session(new_session_id)


def _build_launch_args(
    *,
    thread_id: str,
    permission_mode: str | None,
    model: str | None,
    effort: str | None,
    extra_args: list[str],
) -> list[str]:
    args = ["--session-id", thread_id]
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    if permission_mode:
        args += ["--permission-mode", permission_mode]
    args += extra_args
    return args


def _scrub_session_args(args: list[str]) -> list[str]:
    """Strip ``--session-id`` and ``--resume`` (plus their value) from args."""
    result: list[str] = []
    skip = 0
    for arg in args:
        if skip:
            skip -= 1
            continue
        if arg in ("--session-id", "--resume", "--fork-session"):
            # --fork-session has no value argument; --session-id and --resume do
            if arg != "--fork-session":
                skip = 1
            continue
        result.append(arg)
    return result
