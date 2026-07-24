"""Claude TUI / Emulated transport plugin (claude_tty).

Drives the interactive Claude Code TUI (``claude``) instead of ``claude -p``
(stream-json mode).  The TUI path is exempt from the API rate limit applied to
``claude -p``, making it the preferred transport for autonomous Claude Code sessions.

This is the ``claude_code`` agent driven over a tty-tail transport rather
than the structured ``claude -p`` adapter. It is a session shaped as an
(agent, transport) pair: the plugin **composes** rather than reimplements
both halves —

- Agent half: a ``ClaudeCodePlugin`` instance (``self._claude``) supplies the
  claude knowledge — permission-mode catalogue, effort-swap note, the
  account rate-limit probe, and the conversation-file lookup. It is never
  ``setup()``, so no structured SDK adapter is built.
- Transport half: a ``TmuxPlugin`` instance (``self._tmux``) supplies the
  shared pane-wrapper infrastructure — session teardown plus the rate-limit
  refresh watcher.

Output is read by a transcript tailer over ``~/.claude/projects/<cwd>/
<uuid>.jsonl`` and normalized into the canonical event stream, so
``is_structured=True`` and the frontend renders structured events.
"""

import asyncio
import logging
import shutil
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from fastapi import HTTPException, status
from pydantic import BaseModel, Field, model_validator

from waypoint.backends.base import (
    TerminalAppearance,
    config_dir_for,
)
from waypoint.backends.capabilities import BackendCapabilities, ModelSource
from waypoint.backends.claude_code import side_question as _sq
from waypoint.backends.claude_code.adapter import seed_context_usage_from_transcript
from waypoint.backends.claude_code.commands import list_claude_command_completions
from waypoint.backends.claude_code.history import (
    read_local_claude_history,
    read_local_claude_token_usage_history,
)
from waypoint.backends.claude_code.models import (
    CLAUDE_EFFORT_LEVELS,
    DEFAULT_CLAUDE_MODELS,
    claude_default_model_id,
    resolve_import_model_id,
)
from waypoint.backends.claude_code.plugin import (
    ClaudeCodePlugin,
    log_extra_model_overrides,
    offered_claude_models,
    raise_for_unsupported_selection,
)
from waypoint.backends.claude_code.schemas import (
    ClaudeThreadImportRequest,
    ClaudeThreadSummary,
)
from waypoint.backends.claude_code.threads import (
    ClaudeThreadInfo,
    find_local_claude_thread,
    list_local_claude_threads,
    local_claude_thread_artifacts,
)
from waypoint.backends.claude_tty import pane_dialog
from waypoint.backends.claude_tty._state import PendingTtyApproval, PendingTtyQuestion
from waypoint.backends.claude_tty.byte_source import (
    LocalTranscriptByteSource,
    RemoteClaudeTranscriptByteSource,
    TranscriptByteSource,
)
from waypoint.backends.claude_tty.tailer import TranscriptTailer
from waypoint.backends.completions import static_slash_completions
from waypoint.backends.plugin_config import PluginConfig, PluginLaunchTargetConfig
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.backends.tmux.plugin import TmuxPlugin
from waypoint.git_meta import GitMeta
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import (
    BackendModelOption,
    CommandCompletion,
    CompletionDispatch,
    EventKind,
    EventRecord,
    SessionCreateRequest,
    SessionRateLimitUsage,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.backends.context_usage_source import ContextUsageSource
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.claude_tty")

# Sentinel distinguishing "argument not passed" from "explicitly set to None"
# in the control-swap helper, so a caller can clear a flag (effort=None) while
# the unspecified flags keep the session's current value.
_UNSET: Any = object()

# CLI flags Waypoint owns; users may not smuggle them in through custom args.
_RESERVED_CLI_FLAGS = frozenset(
    {
        "--session-id",
        "--resume",
        "--fork-session",
        "--model",
        "--effort",
        "--permission-mode",
    }
)


class ClaudeTtyPluginConfig(PluginConfig):
    """Configuration for the claude_tty plugin.

    Shares the same static model catalogue as claude_code; no runtime
    model/list RPC is available for Claude.
    """

    models: list[BackendModelOption] = Field(
        default_factory=lambda: list(DEFAULT_CLAUDE_MODELS)
    )
    # Appended to the version-gated catalogue; honored identically to claude_code.
    extra_models: list[BackendModelOption] = Field(default_factory=list)
    default_model_id: str | None = Field(default_factory=claude_default_model_id)
    default_effort: str | None = None

    @model_validator(mode="after")
    def _warn_extra_model_overrides(self) -> Self:
        log_extra_model_overrides(self.extra_models)
        return self


class ClaudeTtyPlugin:
    id = "claude_tty"
    transport_id = "claude_tty"
    # The Claude agent composed over a single tty-tail transport; it pairs
    # with nothing else.
    supported_transports = ("claude_tty",)
    default_transport = "claude_tty"
    label = "Claude TUI"
    import_request_schema: type[BaseModel] | None = ClaudeThreadImportRequest
    config_schema: type[PluginConfig] = ClaudeTtyPluginConfig
    launch_target_schema: type[PluginLaunchTargetConfig] = PluginLaunchTargetConfig
    extra_env = {"CLAUDE_CODE_NO_FLICKER": "1"}
    # Catalogues are the claude agent's, sourced from its capabilities so the
    # two backends can't drift; the flags below are this transport's own.
    capabilities = BackendCapabilities(
        is_structured=True,
        supports_resume=True,
        supports_reattach_after_exit=True,
        supports_fork=True,
        supports_attachments=True,
        # All control swaps restart the pane with `--resume <thread>` and a
        # rebuilt flag set; none of them mutate a live process inline. The
        # ``*_inline`` flags here gate whether the change is *allowed* at all
        # (the runtime's only knob), not whether it skips a restart, so they
        # read True; ``settings_change_interrupts_turn`` records the real cost.
        supports_set_model_inline=True,
        supports_set_effort_inline=False,
        supports_set_effort_with_restart=True,
        supports_set_permission_mode_inline=True,
        settings_change_interrupts_turn=True,
        supports_launch_settings_with_restart=True,
        supports_custom_cli_args=True,
        supports_thread_discovery=True,
        supports_thread_import=True,
        supports_thread_import_model=True,
        effort_levels=ClaudeCodePlugin.capabilities.effort_levels,
        model_source=ModelSource.STATIC,
        permission_modes=ClaudeCodePlugin.capabilities.permission_modes,
        slash_commands=ClaudeCodePlugin.capabilities.slash_commands,
        # Agent-axis fields sourced from the wrapped claude_code agent so the
        # two backends can't drift on the config-dir/thread-store contract.
        config_dir_env_var=ClaudeCodePlugin.capabilities.config_dir_env_var,
        native_thread_store=ClaudeCodePlugin.capabilities.native_thread_store,
        badges={"glyph": "C", "color": "#a78bfa"},
        has_terminal_pane=True,
        terminal_interactive=False,
        # The pane stays read-only for typing (structured chat is the input
        # surface), but accepts injected key-bar chips and scroll-wheel events
        # so the user can drive the TUI through unexpected dialogs and scroll
        # its history.
        terminal_key_injection=True,
        terminal_resizable=False,
        cli_binary="claude",
        target_aliases=("claude_tty",),
    )

    def __init__(self) -> None:
        # Compose the two halves of the session. The claude_code instance is
        # the agent (catalogues, probe, conversation lookup) and is never
        # ``setup()`` — claude_tty drives the CLI through tmux, not the SDK
        # adapter. The tmux instance is the shared pane-wrapper transport.
        self._claude = ClaudeCodePlugin()
        self._tmux = TmuxPlugin()
        self._tailer_tasks: dict[str, asyncio.Task[None]] = {}
        self._pending_approvals: dict[str, PendingTtyApproval] = {}
        self._pending_questions: dict[str, PendingTtyQuestion] = {}

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        from waypoint.backends.claude_tty.transport import ClaudeTtyTransport

        return ClaudeTtyTransport(runtime, self)

    # ── Composed transport infrastructure (tmux pane wrapper) ────────────────

    def setup(self, runtime: "SessionRuntime") -> None:
        return None

    async def start_background_tasks(self, runtime: "SessionRuntime") -> None:
        # claude_code's recovery sweep only covers its own backend id, so legacy
        # backend=claude_tty rows (this alias) would be skipped. Recover ours via
        # the shared Claude one-shot machinery, scoped to this backend id.
        await _sq.recover_pending_side_questions(
            runtime, self._claude, backend_ids={self.id}
        )

    async def shutdown(self, runtime: "SessionRuntime") -> None:
        return None

    def create_context_usage_source(
        self, session: SessionRecord, runtime: "SessionRuntime"
    ) -> "ContextUsageSource | None":
        return None

    def register_routes(self, app: Any, context: Any) -> None:
        return None

    def native_thread_id(self, session: SessionRecord) -> str | None:
        return self._tmux.native_thread_id(session)

    def native_thread_artifacts(
        self, session: SessionRecord, config_dir: str | None = None
    ) -> list[Path]:
        # The native transcript is Claude's (projects JSONL); the agent owns
        # the config-dir/thread-store path logic, so delegate to it.
        return self._claude.native_thread_artifacts(session, config_dir)

    def native_thread_artifact_glob(self, session: SessionRecord) -> str | None:
        return self._claude.native_thread_artifact_glob(session)

    def pane_ready_for_input(self, pane_text: str) -> bool:
        return pane_dialog.composer_ready(pane_text)

    def pane_shows_blocking_dialog(self, pane_text: str) -> bool:
        return pane_dialog.shows_blocking_dialog(pane_text)

    def confirm_pane_submit(self, pane_text: str, sent_text: str) -> bool:
        # The Claude TUI can swallow the submit Enter while loading an image
        # pasted by path; the composer clearing is how the tmux transport
        # confirms the message was actually sent (vs typed-but-unsent). The TUI
        # collapses the paste to a chip, so emptiness — not the sent text — is
        # the signal.
        return pane_dialog.composer_is_empty(pane_text)

    async def terminal_appearance(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> TerminalAppearance:
        # Delegate to the composed Claude agent so this tty-tail transport and
        # claude_code cannot drift on theme resolution.
        return await self._claude.terminal_appearance(runtime, session)

    def on_session_deleted(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        return None

    async def maybe_handle_input(
        self, runtime: "SessionRuntime", session: SessionRecord, request: Any
    ) -> SessionRecord | None:
        text = request.text.strip()
        if text == "/btw" or text.startswith("/btw "):
            question = text[len("/btw") :].strip()
            if question:
                await _sq.start_side_question(runtime, self._claude, session, question)
            return runtime.get_session(session.id)
        return None

    async def approve_plan(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        plan_item_id: str,
        decision: str,
        text: str | None,
    ) -> SessionRecord:
        return await self._tmux.approve_plan(
            runtime, session, plan_item_id, decision, text
        )

    async def post_approval(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        return None

    def _spawn_rate_limit_watcher(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        self._tmux._spawn_rate_limit_watcher(runtime, session)

    async def refresh_rate_limit_usage(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        await self._tmux.refresh_rate_limit_usage(runtime, session, force=True)

    # ── Composed agent knowledge (claude_code) ───────────────────────────────

    def is_available_for_managed_launch(self, runtime: "SessionRuntime") -> bool:
        return shutil.which("claude") is not None

    def remote_executable(self, launch_target: SshLaunchTargetConfig) -> str:
        return "claude"

    async def probe_account_rate_limit(
        self,
        runtime: "SessionRuntime",
        launch_target: SshLaunchTargetConfig | None,
        *,
        cwd: str | None = None,
        launch_env: dict[str, str] | None = None,
        force: bool = False,
    ) -> SessionRateLimitUsage | None:
        return await self._claude.probe_account_rate_limit(
            runtime, launch_target, cwd=cwd, launch_env=launch_env, force=force
        )

    def validate_permission_mode(self, mode: str | None) -> str | None:
        if mode is None:
            return None
        if mode not in self._claude.permission_mode_ids:
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
        account_profile_id: str | None = None,
    ) -> dict[str, Any]:
        config = self._config(runtime)
        default_model = config.default_model_id
        launch_target = (
            runtime._find_launch_target(launch_target_id) if launch_target_id else None
        )
        models, _version = await asyncio.to_thread(
            offered_claude_models,
            config,
            self._claude.capabilities.cli_binary or "claude",
            launch_target,
        )
        options = [opt.model_dump(mode="json") for opt in models]
        default_model_id: str | None = None
        if default_model is None:
            for opt in models:
                if opt.is_default:
                    default_model_id = opt.id
                    break
        else:
            default_model_id = default_model
        default_model_label: str | None = None
        if default_model_id:
            for opt in models:
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
            "effort_levels": list(CLAUDE_EFFORT_LEVELS),
        }

    def validate_new_session_selection(
        self,
        runtime: "SessionRuntime",
        model: str | None,
        effort: str | None,
        launch_target_id: str | None,
    ) -> None:
        # Same agent as claude_code, so the same version-gated preflight
        # applies over the TUI transport.
        if effort is None:
            return None
        launch_target = (
            runtime._find_launch_target(launch_target_id) if launch_target_id else None
        )
        models, version = offered_claude_models(
            self._config(runtime),
            self._claude.capabilities.cli_binary or "claude",
            launch_target,
        )
        raise_for_unsupported_selection(models, version, model, effort)

    async def list_command_completions(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        *,
        trigger: str = "/",
        prefix: str = "",
        force_refresh: bool = False,
    ) -> list[CommandCompletion]:
        if trigger != "/":
            return []
        completions = static_slash_completions(
            self.id, self.capabilities, prefix=prefix
        )
        # /btw is a Waypoint-owned command; always add it
        norm_prefix = (
            prefix if prefix.startswith("/") else f"/{prefix}" if prefix else "/"
        )
        if "/btw".startswith(norm_prefix) or norm_prefix == "/":
            completions.append(
                CommandCompletion(
                    id="claude_tty:waypoint:btw",
                    trigger="/",
                    replacement="/btw ",
                    name="btw",
                    description="Ask a side-question without interrupting the session",
                    kind="command",
                    source="waypoint",
                    dispatch=CompletionDispatch.PLAIN_TEXT,
                    metadata={"builtin_command": "/btw"},
                )
            )
        # Custom commands and skills come from the same on-disk discovery
        # claude_code uses; both wrap the ``claude`` binary in the same cwd.
        launch_target = (
            runtime._find_launch_target(session.launch_target_id)
            if session.launch_target_id
            else None
        )
        claude_bin = (
            self.remote_executable(launch_target)
            if launch_target is not None
            else self.capabilities.cli_binary
        )
        if not claude_bin:
            return completions
        try:
            dynamic = await list_claude_command_completions(
                cwd=session.cwd,
                claude_bin=claude_bin,
                prefix=prefix,
                launch_target=launch_target,
                config_dir=self._config_dir(session),
            )
        except Exception:
            return completions
        seen = {f"{item.trigger}{item.name}" for item in completions}
        for item in dynamic:
            key = f"{item.trigger}{item.name}"
            if key not in seen:
                completions.append(item)
                seen.add(key)
        return completions

    def _config_dir_from_env(self, launch_env: dict[str, str]) -> str | None:
        """The CLAUDE_CONFIG_DIR override carried in a launch env, if any."""
        return config_dir_for(self._claude.capabilities, launch_env)

    def _config_dir(self, session: SessionRecord) -> str | None:
        """The session's CLAUDE_CONFIG_DIR override, if any.

        A session launched under (or switched to) an account profile carries
        its config dir in ``launch_env``; the resume-existence check and the
        transcript tailer must look there, not the default ~/.claude, or they
        read the wrong path — resuming into a fresh conversation, or tailing a
        file the CLI never writes so the session hangs in ``running``.
        """
        return self._config_dir_from_env(session.launch_env)

    async def _conversation_exists(
        self,
        thread_id: str,
        cwd: str,
        launch_target: SshLaunchTargetConfig | None,
        config_dir: str | None = None,
    ) -> bool:
        """Whether Claude has persisted ``thread_id`` to disk.

        claude_tty stores transcripts in the same ``projects`` tree
        claude_code uses, so the existence check defers to the composed
        claude_code agent's launch contract. ``config_dir`` scopes it to the
        session's (possibly profile-switched) CLAUDE_CONFIG_DIR.
        """
        return await self._claude.conversation_exists(
            thread_id, cwd, launch_target, config_dir
        )

    # ── Session lifecycle ────────────────────────────────────────────────────

    def _start_tailer(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        thread_id: str,
        cwd: str,
        *,
        start_at_end: bool = False,
        config_dir: str | None = None,
        launch_target: SshLaunchTargetConfig | None = None,
    ) -> None:
        if session_id in self._tailer_tasks:
            return
        # A local session tails the shared projects tree directly; a session on
        # an SSH launch target reads its transcript over the remote filesystem
        # seam. The selection lives here in the transport plugin so the runtime
        # stays unaware of Claude-specific transcript storage.
        source: TranscriptByteSource
        if launch_target is None:
            source = LocalTranscriptByteSource(cwd, thread_id, config_dir)
        else:
            source = RemoteClaudeTranscriptByteSource(
                runtime, session_id, self, launch_target, config_dir
            )
        tailer = TranscriptTailer(
            session_id=session_id,
            source=source,
            runtime=runtime,
            plugin=self,
            start_at_end=start_at_end,
            config_dir=config_dir,
        )
        self._tailer_tasks[session_id] = asyncio.create_task(tailer.run())

    async def terminate_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        await self._tmux.terminate_session(runtime, session)
        tailer_task = self._tailer_tasks.pop(session.id, None)
        if tailer_task is not None:
            tailer_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await tailer_task
        self._pending_approvals.pop(session.id, None)
        self._pending_questions.pop(session.id, None)

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
        _validate_custom_args(request.args)
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
                launch_env=request.launch_env,
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
        with suppress(TmuxError):
            await runtime.tmux.resize_window(target.session, 120, 50)

        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            # The driving plugin is resolved from the (agent, transport) pair,
            # so this row records the requested agent (claude_code or the
            # legacy claude_tty) while transport pins the tty-tail driver.
            backend=request.backend,
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
            launch_env=request.launch_env,
        )
        runtime.storage.create_session(session)
        await runtime._record_system_event(
            session.id,
            f"Claude TUI session started (thread {thread_id})",
            status=SessionStatus.IDLE,
        )
        self._start_tailer(
            runtime,
            session.id,
            thread_id,
            request.cwd,
            config_dir=self._config_dir_from_env(request.launch_env),
            launch_target=launch_target,
        )
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
                    config_dir=self._config_dir(session),
                    launch_target=runtime._find_launch_target(session.launch_target_id),
                )
            else:
                log.warning(
                    "restored session has no thread_id; "
                    "transcript tailer not started",
                    extra={"session_id": session.id},
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
        # After a transport switch the state is reset to the neutral native-thread
        # handoff (no launch_args); fall back to the persisted custom args so they
        # survive the switch. Model/effort/permission are inherited via --resume
        # (scrubbed here as on every reconnect).
        base_args = _scrub_session_args(
            stored_args if isinstance(stored_args, list) else list(session.args)
        )

        effective_thread_id: str | None = None
        if thread_id and await self._conversation_exists(
            thread_id, session.cwd, launch_target, self._config_dir(session)
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
                launch_env=session.launch_env,
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
        with suppress(TmuxError):
            await runtime.tmux.resize_window(target.session, 120, 50)

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
        # Resuming an existing thread reopens its already-populated transcript,
        # whose records are already in the event DB; tail from the end so they
        # are not replayed. A new thread starts an empty file, so read from 0.
        self._start_tailer(
            runtime,
            session.id,
            new_thread_id,
            session.cwd,
            start_at_end=effective_thread_id is not None,
            config_dir=self._config_dir(session),
            launch_target=launch_target,
        )
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
                launch_env=session.launch_env,
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
        with suppress(TmuxError):
            await runtime.tmux.resize_window(target.session, 120, 50)

        now = datetime.now(UTC)
        new_session = SessionRecord(
            id=new_session_id,
            # Inherit the source session's agent so a forked claude_code+claude_tty
            # session stays backend=claude_code, transport=claude_tty.
            backend=session.backend,
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
            launch_env=session.launch_env,
            account_profile_id=session.account_profile_id,
            account_profile_label=session.account_profile_label,
        )
        runtime.storage.create_session(new_session)
        await runtime._record_system_event(
            new_session_id,
            f"Claude TUI forked from {session.title or session.id} (thread {new_thread_id})",
            status=SessionStatus.IDLE,
        )
        self._start_tailer(
            runtime,
            new_session_id,
            new_thread_id,
            session.cwd,
            config_dir=self._config_dir(new_session),
            launch_target=launch_target,
        )
        self._spawn_rate_limit_watcher(runtime, new_session)
        return runtime.get_session(new_session_id)

    # ── Control swaps (restart-with-resume) ──────────────────────────────────

    async def apply_model(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        model: str | None,
    ) -> None:
        await self._restart_with_args(runtime, session, model=model)

    async def apply_permission_mode(
        self, runtime: "SessionRuntime", session: SessionRecord, mode: str
    ) -> None:
        await self._restart_with_args(runtime, session, permission_mode=mode)

    async def apply_effort(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        effort: str | None,
    ) -> bool:
        return await self._restart_with_args(runtime, session, effort=effort)

    def effort_swap_message(self, effort: str | None) -> str:
        return self._claude.effort_swap_message(effort)

    async def _restart_with_args(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        *,
        model: Any = _UNSET,
        effort: Any = _UNSET,
        permission_mode: Any = _UNSET,
    ) -> bool:
        """Relaunch the pane with ``--resume <thread>`` and a rebuilt flag set.

        Claude's TUI has no in-process knob for model/effort/permission mode,
        so every swap kills the pane and respawns ``claude --resume <thread>``
        with the merged flags. Only the kwargs the caller passes change; the
        others keep the session's current value. Returns ``True`` when a
        restart happened, ``False`` when the requested value was already
        active (so ``set_effort`` knows not to announce a no-op swap).
        """
        field = (
            "model"
            if model is not _UNSET
            else "effort" if effort is not _UNSET else "permission mode"
        )
        # The swap kills the pane and respawns ``--resume``, which interrupts
        # any in-flight turn — acceptable (the frontend warns first) and the
        # only way to apply the change. A STARTING pane has nothing to resume
        # yet, so reject that one state.
        if session.status is SessionStatus.STARTING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"cannot change {field} while the session is starting",
            )
        interrupting = session.status in {
            SessionStatus.RUNNING,
            SessionStatus.WAITING_INPUT,
        }

        new_model = session.model if model is _UNSET else model
        new_effort = session.effort if effort is _UNSET else effort
        new_permission_mode = (
            session.permission_mode if permission_mode is _UNSET else permission_mode
        )
        current = (
            session.model or None,
            session.effort or None,
            session.permission_mode or None,
        )
        merged = (new_model or None, new_effort or None, new_permission_mode or None)
        if merged == current:
            return False

        state = session.transport_state
        thread_id = state.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude_tty session has no thread id to resume",
            )

        old_tmux_session = state.get("tmux_session")
        if old_tmux_session:
            with suppress(TmuxError):
                await runtime.tmux.kill_session(old_tmux_session)
        self._pending_approvals.pop(session.id, None)
        self._pending_questions.pop(session.id, None)
        tailer_task = self._tailer_tasks.pop(session.id, None)
        if tailer_task is not None:
            tailer_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await tailer_task

        stored_args = state.get("launch_args")
        base_args = _scrub_session_args(
            stored_args if isinstance(stored_args, list) else []
        )
        flag_pairs: list[str] = []
        if merged[0]:
            flag_pairs += ["--model", merged[0]]
        if merged[1]:
            flag_pairs += ["--effort", merged[1]]
        if merged[2]:
            flag_pairs += ["--permission-mode", merged[2]]

        launch_target = runtime._find_launch_target(session.launch_target_id)
        # The thread file is only written on first input, so a settings change
        # made before the session's first turn has nothing to resume — relaunching
        # with `--resume` makes the CLI exit with "no conversation found" and kills
        # the pane. Reuse the same thread id via `--session-id` in that case so the
        # relaunch starts the (still-empty) conversation cleanly with the new flags.
        resumed = await self._conversation_exists(
            thread_id, session.cwd, launch_target, self._config_dir(session)
        )
        identity = ["--resume", thread_id] if resumed else ["--session-id", thread_id]
        launch_args = [*identity, *flag_pairs, *base_args]
        command = runtime._command_for_backend(
            self.id,
            launch_args,
            launch_target,
            session.cwd,
            allocate_tty=True,
            session_id=session.id,
            launch_env=session.launch_env,
        )
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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        with suppress(TmuxError):
            await runtime.tmux.resize_window(target.session, 120, 50)

        new_state: dict[str, Any] = {
            "tmux_session": target.session,
            "tmux_window": target.window,
            "tmux_pane": target.pane,
            "pid": target.pane_pid,
            "thread_id": thread_id,
            "launch_args": launch_args,
        }
        # Remember the mode held before entering plan so an ExitPlanMode approval
        # can restore it (mirroring Chat). Set it on the transition into plan,
        # carry it across a model/effort restart that stays in plan, and drop it
        # on any restart that leaves plan. new_state is rebuilt fresh each restart,
        # so this is the only place the key survives across one.
        if new_permission_mode == "plan":
            pre_plan_mode = (
                session.permission_mode
                if session.permission_mode != "plan"
                else state.get("pre_plan_mode")
            )
            if isinstance(pre_plan_mode, str) and pre_plan_mode:
                new_state["pre_plan_mode"] = pre_plan_mode
        runtime.storage.update_session(
            session.id, transport_state=new_state, status=SessionStatus.STARTING
        )
        note = (
            f"Interrupted the running turn and restarted Claude TUI session to "
            f"apply {field} change (thread {thread_id})"
            if interrupting
            else f"Restarted Claude TUI session to apply {field} change (thread {thread_id})"
        )
        await runtime._record_system_event(
            session.id,
            note,
            status=SessionStatus.IDLE,
        )
        # A resumed thread reopens its already-populated transcript (records are
        # in the event DB) so tail from the end; a fresh --session-id thread
        # starts an empty file, so read from byte 0.
        self._start_tailer(
            runtime,
            session.id,
            thread_id,
            session.cwd,
            start_at_end=resumed,
            config_dir=self._config_dir(session),
            launch_target=launch_target,
        )
        return True

    # ── AskUserQuestion ──────────────────────────────────────────────────────

    async def cleanup_side_questions_on_delete(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        # No stale-snapshot guard: the helper re-reads fresh state under the
        # per-session lock (and returns cheaply when there is nothing to clean),
        # so a /btw persisted after this snapshot is still cleaned up.
        await _sq.delete_session_side_questions(runtime, self._claude, session)

    async def fork_side_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        side_question_id: str,
        *,
        new_session_id: str,
        title: str,
        raw_log: Path,
        structured_log: Path,
    ) -> SessionRecord:
        async def _bring_up(new_session: SessionRecord, fork_thread_id: str) -> None:
            await self._launch_resumed_pane(
                runtime, session, new_session, fork_thread_id
            )

        return await _sq.fork_aside(
            runtime,
            session,
            side_question_id,
            new_session_id=new_session_id,
            transport_id=self.transport_id,
            title=title,
            raw_log=raw_log,
            structured_log=structured_log,
            bring_up=_bring_up,
        )

    async def _launch_resumed_pane(
        self,
        runtime: "SessionRuntime",
        parent: SessionRecord,
        new_session: SessionRecord,
        thread_id: str,
    ) -> None:
        """Bring up a managed tmux pane resuming ``thread_id`` for a forked aside.

        The aside already forked a self-contained thread off the parent, so we
        resume it directly (no second ``--fork-session``); the new session owns
        it. Launch flags are inherited from ``parent`` so model/permission carry
        over, mirroring :meth:`fork_session` minus the re-fork.
        """
        launch_target = (
            runtime._find_launch_target(parent.launch_target_id)
            if parent.launch_target_id
            else None
        )
        stored_args = parent.transport_state.get("launch_args")
        base_args = _scrub_session_args(
            stored_args if isinstance(stored_args, list) else []
        )
        launch_args = ["--resume", thread_id, *base_args]
        command = runtime._command_for_backend(
            self.id,
            launch_args,
            launch_target,
            new_session.cwd,
            allocate_tty=True,
            session_id=new_session.id,
            launch_env=parent.launch_env,
        )
        raw_log = Path(new_session.raw_log_path)
        raw_log.parent.mkdir(parents=True, exist_ok=True)
        raw_log.touch(exist_ok=True)
        target = await runtime.tmux.start_managed_session(
            new_session.id, new_session.cwd, command
        )
        # Once the pane (a live `claude --resume`) exists, any later failure must
        # kill it before re-raising — otherwise fork_aside's rollback restores the
        # source aside while an unmanaged process still holds the same fork thread.
        try:
            await runtime.tmux.pipe_output(target.pane, raw_log)
            with suppress(TmuxError):
                await runtime.tmux.resize_window(target.session, 120, 50)
            runtime.storage.update_session(
                new_session.id,
                transport_state={
                    "tmux_session": target.session,
                    "tmux_window": target.window,
                    "tmux_pane": target.pane,
                    "pid": target.pane_pid,
                    "thread_id": thread_id,
                    "launch_args": launch_args,
                },
                status=SessionStatus.STARTING,
            )
            await runtime._record_system_event(
                new_session.id,
                f"Promoted side question to a session (resumed thread {thread_id})",
                status=SessionStatus.IDLE,
            )
            # The transcript (parent + aside Q&A) is already seeded in the event
            # DB by fork_aside, so tail from the end and only pick up turns added
            # from here.
            self._start_tailer(
                runtime,
                new_session.id,
                thread_id,
                new_session.cwd,
                start_at_end=True,
                config_dir=self._config_dir(new_session),
                launch_target=launch_target,
            )
            self._spawn_rate_limit_watcher(runtime, new_session)
        except Exception:
            with suppress(TmuxError):
                await runtime.tmux.kill_session(target.session)
            raise

    async def dismiss_side_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        side_question_id: str,
    ) -> None:
        await _sq.dismiss_aside(runtime, self._claude, session, side_question_id)

    async def answer_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        answer: str,
        tool_use_id: str | None,
        answers: list[dict[str, Any]] | None,
    ) -> SessionRecord:
        """Deliver an answer to a surfaced AskUserQuestion as a new user turn.

        The popup was already Esc-dismissed when the tailer surfaced it, so the
        pane sits at the ready prompt; the answer is sent as an ordinary message
        the way claude_code carries it on a denied tool. A synthetic tool_result
        flips the surfaced card to answered so it stops accepting input, and a
        styled answers card records the choices.
        """
        pending = self._pending_questions.get(session.id)
        if pending is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="no pending question for this session",
            )
        if (
            tool_use_id is not None
            and pending.tool_use_id
            and pending.tool_use_id != tool_use_id
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="question answer does not match the pending question",
            )
        self._pending_questions.pop(session.id, None)
        resolved_tool_use_id = pending.tool_use_id or tool_use_id

        transport = runtime.transport_for(session)
        await transport.send_input(
            session,
            f"User has answered your questions: {answer}. "
            "You can now continue with the user's answers in mind.",
        )

        if resolved_tool_use_id:
            await runtime._emit_adapter_event(
                session.id,
                EventKind.TOOL_RESULT,
                "User answered the question.",
                {
                    "method": "user.tool_result",
                    "item_id": resolved_tool_use_id,
                    "tool_use_id": resolved_tool_use_id,
                    "is_error": False,
                },
                SessionStatus.RUNNING,
            )

        extra: dict[str, Any] = {"kind": "ask_user_question_answer"}
        if answers:
            extra["answers"] = answers
        if resolved_tool_use_id:
            extra["tool_use_id"] = resolved_tool_use_id
        # Flip status to RUNNING before recording the answer so the broadcast
        # snapshot shows the spinner immediately, matching handle_input.
        updated = runtime.storage.update_session(
            session.id, status=SessionStatus.RUNNING
        )
        await runtime._record_user_event(
            session.id, answer, submit=True, extra_metadata=extra
        )
        return updated

    # ── Thread discovery + import ────────────────────────────────────────────

    def _thread_summary(self, info: ClaudeThreadInfo) -> ClaudeThreadSummary:
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

    def _imported_thread_ids(
        self, runtime: "SessionRuntime", backend: str | None = None
    ) -> set[str]:
        # Keyed on thread_id alone, which is sufficient only because claude_tty
        # imports the local store exclusively. If remote enumeration lands, switch
        # to a (launch_target_id, thread_id) key so a local import cannot mask the
        # same thread on a remote target (cf. claude_code's dedup). Filters on the
        # persisted agent id (``backend``) so an import under ``claude_code`` over
        # this transport dedups against the same agent's other transports.
        agent_id = backend or self.id
        imported: set[str] = set()
        for session in runtime.storage.list_sessions():
            if session.backend != agent_id:
                continue
            thread_id = session.transport_state.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                imported.add(thread_id)
        return imported

    async def list_threads(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        account_profile_id: str | None = None,
    ) -> list[ClaudeThreadSummary]:
        # Remote enumeration is a follow-up; only the local store is read here.
        if launch_target_id is not None:
            return []
        launch_target = runtime._resolve_launch_target(launch_target_id, self.id)
        env = await runtime.discovery_env(self.id, launch_target, account_profile_id)
        config_dir = config_dir_for(self.capabilities, env)
        infos = await asyncio.to_thread(list_local_claude_threads, config_dir)
        imported = self._imported_thread_ids(runtime)
        return [self._thread_summary(info) for info in infos if info.id not in imported]

    async def delete_thread(
        self,
        runtime: "SessionRuntime",
        thread_id: str,
        launch_target_id: str | None = None,
        account_profile_id: str | None = None,
    ) -> bool:
        # Deletion is offered by the claude_code plugin; this tty-tail driver
        # leaves supports_thread_delete False so the API never routes here.
        return False

    async def import_thread(
        self,
        runtime: "SessionRuntime",
        request: ClaudeThreadImportRequest,
        *,
        agent: str | None = None,
    ) -> SessionRecord:
        # When the runtime resolves an agent's ``claude_tty`` transport to this
        # driver, the session is persisted under that agent (e.g.
        # ``claude_code``) rather than this plugin's own id.
        backend = agent or self.id
        if request.launch_target_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="remote import not yet supported for claude_tty",
            )
        config_dir = self._config_dir_from_env(request.launch_env)
        info = await asyncio.to_thread(
            find_local_claude_thread, request.thread_id, config_dir
        )
        if info is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="claude thread not found",
            )
        cwd_path = Path(info.cwd).expanduser()
        if not cwd_path.exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"claude thread cwd {info.cwd} no longer exists; cannot resume",
            )
        if request.thread_id in self._imported_thread_ids(runtime, backend):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude thread already imported",
            )
        cwd = str(cwd_path)
        # Durable effective model — request choice, else the configured default.
        # Pin it on the resume command so the relaunched CLI is deterministic,
        # and persist it as the authoritative context-window denominator.
        effective_model = resolve_import_model_id(
            request.model, self._config(runtime).default_model_id
        )
        launch_args = ["--resume", request.thread_id]
        if effective_model:
            launch_args += ["--model", effective_model]
        session_id = runtime._generate_session_id(self.id)
        session_dir = runtime._session_dir(session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"
        raw_log.parent.mkdir(parents=True, exist_ok=True)
        raw_log.touch(exist_ok=True)
        try:
            command = runtime._command_for_backend(
                self.id,
                launch_args,
                None,
                cwd,
                allocate_tty=True,
                session_id=session_id,
                launch_env=request.launch_env,
            )
        except HTTPException:
            raise
        try:
            target = await runtime.tmux.start_managed_session(session_id, cwd, command)
            await runtime.tmux.pipe_output(target.pane, raw_log)
        except TmuxError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        with suppress(TmuxError):
            await runtime.tmux.resize_window(target.session, 120, 50)

        # Seed the context pill from the last transcript turn so the correct
        # window shows immediately; the tailer starts at EOF and would otherwise
        # leave it empty until the first new assistant message.
        seeded_context_usage = None
        artifacts = local_claude_thread_artifacts(request.thread_id, config_dir)
        if artifacts:
            seeded_context_usage = await asyncio.to_thread(
                seed_context_usage_from_transcript, artifacts[0], effective_model
            )

        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=backend,
            source=SessionSource.MANAGED,
            transport=self.transport_id,
            title=info.title,
            cwd=cwd,
            launch_target_id=None,
            repo_name=info.repo_name,
            branch=info.branch,
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
                "thread_id": request.thread_id,
                "launch_args": launch_args,
            },
            model=effective_model,
            context_usage=seeded_context_usage,
            launch_env=request.launch_env,
        )
        runtime.storage.create_session(session)

        async def _read_thread_history() -> list[EventRecord]:
            return await read_local_claude_history(session.id, request.thread_id)

        await runtime.seed_thread_history(
            session.id,
            reader=_read_thread_history,
            enabled=request.import_history,
        )
        if request.import_history:
            # Same on-disk transcript the event seed above just replayed;
            # the resumed tailer starts at EOF (below), so this is the only
            # source of per-turn model/effort for the imported turns' ledger.
            for token_record in await read_local_claude_token_usage_history(
                request.thread_id
            ):
                await runtime.publish_token_usage_record(
                    session.id, token_record, publish=False
                )
        await runtime._record_system_event(
            session.id,
            f"Imported stored Claude thread ({cwd})",
            status=SessionStatus.IDLE,
            metadata={"imported_thread_id": request.thread_id},
        )
        # The resumed thread reopens an already-populated transcript; tail from
        # the end so records already on disk are not replayed into the DB (the
        # seed above has already replayed them, from the on-disk transcript
        # directly rather than the live tail).
        self._start_tailer(
            runtime,
            session.id,
            request.thread_id,
            cwd,
            start_at_end=True,
            config_dir=self._config_dir(session),
        )
        self._spawn_rate_limit_watcher(runtime, session)
        return runtime.get_session(session.id)


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
    """Strip Waypoint-managed flags (and their values) from stored args.

    Removes the thread-identity flags (``--session-id``/``--resume``/
    ``--fork-session``) and the control flags (``--model``/``--effort``/
    ``--permission-mode``) so a restart can rebuild them from the merged
    session state. Custom user args pass through untouched.
    """
    valued = {
        "--session-id",
        "--resume",
        "--model",
        "--effort",
        "--permission-mode",
    }
    result: list[str] = []
    skip = 0
    for arg in args:
        if skip:
            skip -= 1
            continue
        if arg == "--fork-session":
            # Valueless toggle; drop it on its own.
            continue
        if arg in valued:
            skip = 1
            continue
        result.append(arg)
    return result


def _validate_custom_args(args: list[str]) -> None:
    """Reject Waypoint-managed flags supplied as custom CLI args.

    Thread identity and the model/effort/permission knobs are rebuilt by the
    plugin on every launch and restart; letting a user pin them through
    ``args`` would desync the stored session state from the live pane.
    """
    for arg in args:
        flag = arg.split("=", 1)[0]
        if flag in _RESERVED_CLI_FLAGS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{flag} is managed by Waypoint and cannot be passed "
                    "as a custom CLI arg"
                ),
            )
