"""Tmux fallback plugin.

Tmux is the legacy attached-session path: terminal output is scraped
from a pane log instead of a structured stream-json/notification
channel. The plugin's capability descriptor advertises
``is_structured=False`` so the frontend renders the heuristic transcript
view, ``supports_resume=True`` so users can re-attach a detached pane,
and disables every inline control knob (model/effort/permission mode)
because no protocol is available to set them mid-session.
"""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Never

from fastapi import HTTPException, status
from pydantic import BaseModel

from waypoint.backends.base import AgentLaunchContract
from waypoint.backends.capabilities import BackendCapabilities, ModelSource
from waypoint.backends.completions import static_slash_completions
from waypoint.backends.plugin_config import PluginConfig, PluginLaunchTargetConfig
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.git_meta import GitMeta, resolve_git_meta
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import (
    CommandCompletion,
    LaunchMode,
    SessionCreateRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.backends.context_usage_source import ContextUsageSource
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.tmux")


def _unsupported(action: str) -> Never:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"{action} is not supported for tmux sessions",
    )


class TmuxPluginConfig(PluginConfig):
    """Tmux fallback plugin configuration block.

    Tmux exposes no model/effort knobs; the inherited
    :class:`PluginConfig` defaults are unused but the field is required
    so all plugins satisfy the same contract.
    """


class TmuxPlugin:
    id = "tmux"
    transport_id = "tmux"
    # A generic transport, not an agent: it wraps other agents' CLIs and is
    # never itself an agent in an (agent, transport) pair.
    supported_transports = ("tmux",)
    default_transport = "tmux"
    label = "Tmux"
    import_request_schema: type[BaseModel] | None = None
    config_schema: type[PluginConfig] = TmuxPluginConfig
    launch_target_schema: type[PluginLaunchTargetConfig] = PluginLaunchTargetConfig
    extra_env: dict[str, str] = {}
    capabilities = BackendCapabilities(
        is_structured=False,
        supports_resume=True,
        supports_reattach_after_exit=True,
        supports_set_model_inline=False,
        supports_set_effort_inline=False,
        supports_set_permission_mode_inline=False,
        supports_thread_discovery=False,
        supports_thread_import=False,
        supports_slash_compact=False,
        supports_attachments=True,
        model_source=ModelSource.NONE,
        badges={"glyph": "T", "color": "#94a3b8"},
        live_terminal=True,
        has_terminal_pane=True,
        terminal_interactive=True,
        terminal_key_injection=True,
        terminal_resizable=True,
        is_fallback_for_managed_launch=True,
    )

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        from waypoint.backends.tmux.transport import TmuxTransport

        return TmuxTransport(runtime)

    def setup(self, runtime: "SessionRuntime") -> None:
        return None

    async def shutdown(self, runtime: "SessionRuntime") -> None:
        return None

    def create_context_usage_source(
        self, session: SessionRecord, runtime: "SessionRuntime"
    ) -> "ContextUsageSource | None":
        # Tmux is the transport wrapper; context usage is an agent-axis
        # concern, so delegate to the wrapped agent plugin (which reads its
        # own on-disk artifact). Mirrors how the wrapper delegates other
        # agent-specific calls (built-ins, rate-limit probe, resume).
        if session.backend == self.id or not runtime.registry.has_backend(
            session.backend
        ):
            return None
        return runtime.registry.get(session.backend).create_context_usage_source(
            session, runtime
        )

    def register_routes(self, app: Any, context: Any) -> None:
        return None

    def is_available_for_managed_launch(self, runtime: "SessionRuntime") -> bool:
        return True

    def remote_executable(self, launch_target: SshLaunchTargetConfig) -> str:
        # Tmux is the *wrapper*, not the wrapped binary — the runtime
        # only calls remote_executable on the inner backend, never on
        # the tmux plugin itself.
        return ""

    async def terminate_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        # Tear down the actual tmux session: stop the pipe-pane writer,
        # kill the tmux session (which kills Codex/Claude inside it),
        # and cancel the pane monitor task. Wrapped in suppressions so a
        # partial teardown (e.g. tmux gone but monitor still alive)
        # still cleans up the rest.
        state = session.transport_state
        target = state.get("tmux_pane") or state.get("tmux_session") or session.id
        with suppress(TmuxError):
            await runtime.tmux.stop_pipe(target)
        tmux_session = state.get("tmux_session")
        if session.source == SessionSource.MANAGED and tmux_session:
            with suppress(TmuxError):
                await runtime.tmux.kill_session(tmux_session)
        monitor = runtime.monitor_tasks.pop(session.id, None)
        if monitor is not None:
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass
            except Exception:
                log.debug("monitor task raised during terminate", exc_info=True)
        capture = runtime._thread_id_watchers.pop(session.id, None)
        if capture is not None:
            capture.cancel()
            try:
                await capture
            except asyncio.CancelledError:
                pass
            except Exception:
                log.debug("thread-id watcher raised during terminate", exc_info=True)
        rl_watcher = runtime._rate_limit_watchers.pop(session.id, None)
        if rl_watcher is not None:
            rl_watcher.cancel()
            try:
                await rl_watcher
            except asyncio.CancelledError:
                pass
            except Exception:
                log.debug("rate-limit watcher raised during terminate", exc_info=True)

    def native_thread_id(self, session: SessionRecord) -> str | None:
        thread_id = session.transport_state.get("thread_id")
        return thread_id if isinstance(thread_id, str) else None

    def on_session_deleted(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        return None

    def validate_permission_mode(self, mode: str | None) -> str | None:
        return None  # tmux has no concept of permission modes

    async def apply_permission_mode(
        self, runtime: "SessionRuntime", session: SessionRecord, mode: str
    ) -> None:
        _unsupported("permission mode")

    async def apply_model(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        model: str | None,
    ) -> None:
        _unsupported("model selection")

    async def apply_effort(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        effort: str | None,
    ) -> bool:
        _unsupported("effort selection")

    def effort_swap_message(self, effort: str | None) -> str:
        return ""

    async def list_models(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        return {
            "backend": self.id,
            "models": [],
            "default_model_id": None,
            "default_model_label": None,
            "default_effort": None,
            "supports_free_text": False,
        }

    async def list_command_completions(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        *,
        trigger: str = "/",
        prefix: str = "",
        force_refresh: bool = False,
    ) -> list[CommandCompletion]:
        # Tmux is a transport, not a backend in the user-visible sense:
        # the wrapped CLI (Claude Code, Codex, OpenCode, …) is what
        # actually interprets slash commands. Delegate so the quick
        # compose surfaces the wrapped backend's built-ins and any
        # workspace skills it advertises. Pure-tmux sessions (rare —
        # ``backend == "tmux"``) fall back to the local static list.
        if session.backend != self.id and runtime.registry.has_backend(session.backend):
            wrapped = runtime.registry.get(session.backend)
            return await wrapped.list_command_completions(
                runtime,
                session,
                trigger=trigger,
                prefix=prefix,
                force_refresh=force_refresh,
            )
        if trigger != "/":
            return []
        return static_slash_completions(self.id, self.capabilities, prefix=prefix)

    async def maybe_handle_input(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        request: Any,
    ) -> SessionRecord | None:
        return None

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
        _unsupported("side-questions")

    async def dismiss_side_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        side_question_id: str,
    ) -> None:
        _unsupported("side-questions")

    async def answer_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        answer: str,
        tool_use_id: str | None,
        answers: list[dict[str, Any]] | None,
    ) -> SessionRecord:
        _unsupported("answer-question")

    async def approve_plan(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        plan_item_id: str,
        decision: str,
        text: str | None,
    ) -> SessionRecord:
        _unsupported("plan approval")

    async def post_approval(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        return None

    async def refresh_rate_limit_usage(
        self, runtime: "SessionRuntime", session: SessionRecord, *, force: bool = True
    ) -> None:
        """Populate ``rate_limit_usage`` on tmux-wrapped sessions.

        The wrapped CLI runs unmonitored — no per-session SDK adapter is
        watching it — so the structured backends' probe machinery never
        fires. Delegate to the inner plugin's account-level probe (same
        upstream API call the structured adapter makes) and persist +
        broadcast the snapshot via ``update_session_fields``.

        ``force`` defaults to True because the user-driven refresh contract
        reaches here directly; the background watcher passes ``force=False``
        so its periodic ticks coalesce through the shared probe cache.
        """
        inner = runtime.registry.get(session.backend)
        probe = getattr(inner, "probe_account_rate_limit", None)
        if probe is None:
            return
        launch_target = (
            runtime._find_launch_target(session.launch_target_id)
            if session.launch_target_id
            else None
        )
        # The account-level probe is uniform across agents; ``cwd`` is
        # always supplied (codex's ``/status`` PTY fallback needs it, the
        # others ignore it) so no per-backend call shape is needed here.
        try:
            snapshot = await probe(runtime, launch_target, cwd=session.cwd, force=force)
        except Exception:  # noqa: BLE001
            log.exception(
                "tmux rate-limit probe failed",
                extra={"session_id": session.id, "inner": session.backend},
            )
            return
        if snapshot is None:
            return
        await runtime.update_session_fields(
            session.id,
            publish=True,
            rate_limit_usage=snapshot.model_dump(mode="json"),
        )

    async def restore_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        # Two flows reach here:
        #
        # 1. Boot-time replay (``SessionRuntime.start``): the tmux
        #    process *should* still be alive — just re-attach the pane
        #    monitor and resume the rollout-file watcher if applicable.
        # 2. User-initiated reconnect of an EXITED session (via
        #    ``/api/sessions/{id}/reattach``): spawn a fresh tmux
        #    session, truncate the logs, and rewire transport_state to
        #    point at the new pane. If we captured a thread_id during
        #    the prior run, replay with the inner CLI's resume command
        #    so the conversation continues.
        state = session.transport_state
        if session.status != SessionStatus.EXITED:
            inner = self._agent_launch(runtime, session.backend)
            runtime._ensure_monitor(session.id)
            # Agents whose id only appears post-launch (no pregenerated id)
            # need the rollout/thread-id watcher resumed if we never captured
            # one; agents that pregenerate their id (claude) have nothing to
            # discover.
            if inner.pregenerate_thread_id() is None and "thread_id" not in state:
                lt = runtime._find_launch_target(session.launch_target_id)
                self._spawn_thread_id_watcher(
                    runtime,
                    session.backend,
                    session.id,
                    session.cwd,
                    session.created_at,
                    lt,
                )
            self._spawn_rate_limit_watcher(runtime, session)
            return

        # ATTACHED-TMUX reconnect: the user terminated a session we
        # never launched, so the underlying pane is still alive (see
        # the ``MANAGED``-only check in ``terminate_session``). The
        # correct reconnect is to re-pipe to that same pane and
        # reseat the monitor — *not* to kill the user's tmux session
        # and spawn a fresh inner CLI.
        if session.source == SessionSource.ATTACHED_TMUX:
            await self._reattach_attached_tmux(runtime, session)
            return

        # MANAGED reconnect path. Wipe whatever might still be running
        # under the old tmux session name so the new ``new-session``
        # doesn't collide; ignore errors since the typical case is that
        # nothing is there.
        inner = self._agent_launch(runtime, session.backend)
        old_tmux_session = state.get("tmux_session")
        if old_tmux_session:
            with suppress(TmuxError):
                await runtime.tmux.kill_session(old_tmux_session)

        thread_id = state.get("thread_id")
        stored_args = state.get("launch_args")
        if not isinstance(stored_args, list):
            stored_args = []
        # A captured thread_id is only useful if the inner CLI has
        # actually persisted the conversation to disk. ``claude
        # --session-id <uuid>`` doesn't create the session file until
        # the user sends a message — a terminate-before-first-message
        # leaves the uuid stranded, and ``--resume`` would die with
        # "No conversation found with session ID: …". Same shape for
        # ``codex resume <uuid>``: no rollout file means no thread to
        # resume. Fall back to verbatim launch args in that case.
        launch_target = runtime._find_launch_target(session.launch_target_id)
        effective_thread_id: str | None = None
        if thread_id and await inner.conversation_exists(
            thread_id, session.cwd, launch_target
        ):
            effective_thread_id = thread_id
        launch_args = (
            inner.resume_args(effective_thread_id, list(stored_args))
            if effective_thread_id
            else list(stored_args)
        )
        try:
            command = runtime._command_for_backend(
                session.backend,
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
        structured_log = Path(session.structured_log_path)
        # Truncate both logs for a clean slate — the renderer reseeds
        # from a fresh capture-pane on every connect, and the structured
        # event stream restarts at sequence 0 for the new conversation.
        with suppress(OSError):
            raw_log.parent.mkdir(parents=True, exist_ok=True)
            raw_log.write_bytes(b"")
        with suppress(OSError):
            structured_log.parent.mkdir(parents=True, exist_ok=True)
            structured_log.write_bytes(b"")
        runtime.file_offsets.pop(session.id, None)

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
            "launch_args": launch_args,
        }
        # Keep a pregenerating agent's id (claude's ``--session-id`` uuid)
        # even when the conversation file doesn't exist yet — the next
        # reconnect re-checks. Agents that discover their id post-launch
        # (codex's rollout watcher) must not carry a phantom forward, or
        # it would suppress the watcher-spawn guard below.
        if effective_thread_id:
            new_state["thread_id"] = effective_thread_id
        elif inner.pregenerate_thread_id() is not None and thread_id:
            new_state["thread_id"] = thread_id
        runtime.storage.update_session(
            session.id, transport_state=new_state, status=SessionStatus.STARTING
        )
        now = datetime.now(UTC)
        message = (
            f"Session reconnected (resumed thread {effective_thread_id})"
            if effective_thread_id
            else "Session reconnected (new thread)"
        )
        await runtime._record_system_event(
            session.id, message, status=SessionStatus.STARTING
        )
        runtime._ensure_monitor(session.id)
        if inner.pregenerate_thread_id() is None and not effective_thread_id:
            self._spawn_thread_id_watcher(
                runtime, session.backend, session.id, session.cwd, now, launch_target
            )
        self._spawn_rate_limit_watcher(runtime, session)

    async def _reattach_attached_tmux(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        """Re-pipe an externally-owned tmux pane after a Waypoint-side
        terminate. We never owned the pane's lifecycle, so reconnect is
        the inverse of ``terminate_session``: confirm the pane is still
        alive, resume ``pipe-pane`` to the existing raw_log, and
        reseat the monitor. The inner CLI keeps running with no
        interruption.
        """
        state = session.transport_state
        pane = state.get("tmux_pane") or state.get("tmux_session") or session.id
        try:
            target = await runtime.tmux.describe_target(pane)
        except TmuxError as exc:
            await runtime._record_system_event(
                session.id,
                f"Cannot reattach: tmux target lost ({exc})",
                status=SessionStatus.EXITED,
            )
            return
        if target.pane_dead:
            await runtime._record_system_event(
                session.id,
                "Cannot reattach: tmux pane is dead",
                status=SessionStatus.EXITED,
            )
            return
        raw_log = Path(session.raw_log_path)
        with suppress(OSError):
            raw_log.parent.mkdir(parents=True, exist_ok=True)
        try:
            await runtime.tmux.pipe_output(target.pane, raw_log)
        except TmuxError as exc:
            await runtime._record_system_event(
                session.id,
                f"Cannot reattach: pipe-output failed ({exc})",
                status=SessionStatus.EXITED,
            )
            return
        new_state: dict[str, Any] = {
            "tmux_session": target.session,
            "tmux_window": target.window,
            "tmux_pane": target.pane,
            "pid": target.pane_pid,
        }
        # Carry forward any captured thread_id so the codex watcher
        # below isn't asked to re-poll for a uuid we already had.
        prior_thread_id = state.get("thread_id")
        if prior_thread_id:
            new_state["thread_id"] = prior_thread_id
        runtime.storage.update_session(
            session.id,
            transport_state=new_state,
            status=SessionStatus.IDLE,
        )
        await runtime._record_system_event(
            session.id,
            f"Session reconnected (attached to tmux target {target.session})",
            status=SessionStatus.IDLE,
        )
        runtime._ensure_monitor(session.id)
        # Respawn the post-launch thread-id watcher if we never captured a
        # uuid — the prior watcher was cancelled by terminate_session.
        # Agents that pregenerate their id (claude) have nothing to discover.
        inner = self._agent_launch(runtime, session.backend)
        if inner.pregenerate_thread_id() is None and not prior_thread_id:
            launch_target = runtime._find_launch_target(session.launch_target_id)
            self._spawn_thread_id_watcher(
                runtime,
                session.backend,
                session.id,
                session.cwd,
                datetime.now(UTC),
                launch_target,
            )
        self._spawn_rate_limit_watcher(runtime, session)

    @staticmethod
    def _agent_launch(runtime: "SessionRuntime", backend: str) -> AgentLaunchContract:
        """The wrapped agent's launch contract for ``backend``.

        Every backend a pane can wrap is an agent plugin mixing in
        :class:`DefaultLaunchContract`, so this enforces the invariant: the tmux
        wrapper only ever resolves agent ids here, never its own transport id. A
        plugin that doesn't satisfy the contract is a misconfiguration, surfaced
        as a 400 rather than a stripped ``assert`` or an opaque 500.
        """
        inner = runtime.registry.get(backend)
        if not isinstance(inner, AgentLaunchContract):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{backend} cannot be wrapped in a tmux pane: "
                "it does not implement the agent launch contract",
            )
        return inner

    def _spawn_thread_id_watcher(
        self,
        runtime: "SessionRuntime",
        backend: str,
        session_id: str,
        cwd: str,
        since: datetime,
        launch_target: SshLaunchTargetConfig | None,
    ) -> None:
        """Spawn the agent's post-launch thread-id discovery, if it has one.

        Agents whose native id only appears after the first persist (codex
        writes a ``rollout-<ts>-<uuid>.jsonl``) discover it here; the agent
        owns the polling and stores ``transport_state.thread_id`` so a later
        reconnect can resume. A no-op for agents that pregenerate their id.
        """
        if session_id in runtime._thread_id_watchers:
            return
        inner = self._agent_launch(runtime, backend)
        task = asyncio.create_task(
            inner.capture_thread_id(runtime, session_id, cwd, since, launch_target)
        )
        runtime._thread_id_watchers[session_id] = task

    def _spawn_rate_limit_watcher(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        """Run periodic rate-limit refreshes for a tmux-wrapped session.

        Structured backends register a per-session probe inside their SDK
        adapter, so their pill stays current without any explicit kick.
        Tmux-wrapped sessions have no adapter, so this watcher fills the
        gap — same cadence (300 s) as the SDK probes, and an immediate
        first refresh so the pill populates within the first paint
        instead of staying empty until the user clicks.
        """
        if session.id in runtime._rate_limit_watchers:
            return
        inner = runtime.registry.get(session.backend)
        if getattr(inner, "probe_account_rate_limit", None) is None:
            return
        task = asyncio.create_task(self._rate_limit_refresh_loop(runtime, session.id))
        runtime._rate_limit_watchers[session.id] = task

    async def _rate_limit_refresh_loop(
        self, runtime: "SessionRuntime", session_id: str
    ) -> None:
        # Match the 300 s interval the structured adapters use; the
        # upstream rate-limit endpoints are account-scoped, so probing
        # any faster doesn't surface fresher data.
        REFRESH_INTERVAL = 300.0
        try:
            while True:
                session = runtime.storage.get_session(session_id)
                if session is None:
                    return
                # Mirrors the structured adapter's
                # ``while state.session_id in self._sessions`` — those
                # loops naturally terminate when the adapter drops the
                # session on exit; here we have no adapter map to watch,
                # so the storage status is the equivalent signal.
                if session.status in {SessionStatus.EXITED, SessionStatus.ERROR}:
                    return
                try:
                    await self.refresh_rate_limit_usage(runtime, session, force=False)
                except Exception:  # noqa: BLE001
                    log.exception(
                        "tmux rate-limit refresh loop probe failed",
                        extra={"session_id": session_id},
                    )
                await asyncio.sleep(REFRESH_INTERVAL)
        finally:
            runtime._rate_limit_watchers.pop(session_id, None)

    async def list_threads(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
    ) -> list[Any]:
        return []

    async def delete_thread(
        self,
        runtime: "SessionRuntime",
        thread_id: str,
        launch_target_id: str | None = None,
    ) -> bool:
        # No deletable transcript store; supports_thread_delete is False so the
        # API never routes here.
        return False

    async def import_thread(
        self, runtime: "SessionRuntime", request: Any, *, agent: str | None = None
    ) -> SessionRecord:
        # The tmux wrapper cannot enumerate agent threads; the runtime drives
        # tmux imports through the agent plugin's import_thread_via_resume.
        raise NotImplementedError

    async def fork_session(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        new_session_id: str,
        title: str,
        raw_log: Path,
        structured_log: Path,
    ) -> SessionRecord:
        raise NotImplementedError

    def format_start_message(self, backend: str, launch_target: Any, cwd: str) -> str:
        if launch_target is not None:
            return (
                f"{backend} session started via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd or launch_target.default_cwd})"
            )
        return f"{backend} session started"

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
        inner = self._agent_launch(runtime, request.backend)
        inner_flags = inner.launch_flags(
            model=resolved_model,
            effort=resolved_effort,
            permission_mode=permission_mode,
        )
        # Match the SessionRecord to what the wrapped CLI actually
        # received: persist a control value only when the agent's
        # launch_flags actually pin it at startup. Codex, for instance,
        # has no --effort flag and maps only two of its permission presets,
        # so storing the un-applied values would make the pill and
        # launch-panel re-open lie about runtime behavior.
        persisted_effort = (
            resolved_effort
            if inner.launch_flags(
                model=resolved_model,
                effort=None,
                permission_mode=permission_mode,
            )
            != inner_flags
            else None
        )
        persisted_permission_mode = (
            permission_mode
            if inner.launch_flags(
                model=resolved_model,
                effort=resolved_effort,
                permission_mode=None,
            )
            != inner_flags
            else None
        )
        launch_args = [*inner_flags, *request.args]
        # Agents that accept a pregenerated id (claude's ``--session-id``)
        # get one pinned now so the reconnect path has a thread id ready;
        # agents that reveal their id post-launch return None here and rely
        # on the thread-id watcher instead.
        thread_id = inner.pregenerate_thread_id()
        if thread_id is not None:
            launch_args = ["--session-id", thread_id, *launch_args]
        command = runtime._command_for_backend(
            request.backend,
            launch_args,
            launch_target,
            request.cwd,
            allocate_tty=True,
            session_id=session_id,
        )
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
        transport_state: dict[str, Any] = {
            "tmux_session": target.session,
            "tmux_window": target.window,
            "tmux_pane": target.pane,
            "pid": target.pane_pid,
            # Stored so reconnect can replay verbatim launch args even
            # when no thread_id was captured (or when the backend has no
            # resume contract at all).
            "launch_args": launch_args,
        }
        if thread_id is not None:
            transport_state["thread_id"] = thread_id
        session = SessionRecord(
            id=session_id,
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
            transport_state=transport_state,
            spawner_session_id=request.spawner_session_id,
            permission_mode=persisted_permission_mode,
            model=resolved_model,
            effort=persisted_effort,
        )
        runtime.storage.create_session(session)
        await runtime._record_system_event(
            session.id,
            self.format_start_message(request.backend, launch_target, request.cwd),
        )
        runtime._ensure_monitor(session.id)
        if thread_id is None:
            self._spawn_thread_id_watcher(
                runtime, request.backend, session.id, session.cwd, now, launch_target
            )
        self._spawn_rate_limit_watcher(runtime, session)
        return runtime.get_session(session.id)

    async def import_thread_via_resume(
        self,
        runtime: "SessionRuntime",
        *,
        backend: str,
        thread_id: str,
        cwd: str,
        launch_target_id: str | None,
        title: str,
    ) -> SessionRecord:
        """Create a tmux-wrapped session that resumes an existing thread.

        The structured-plugin ``import_thread`` calls this when the
        user picks ``launch_mode=tmux_wrapper`` (or when ``auto``
        decides the structured backend isn't available for managed
        launch). The agent owns the resume contract for its CLI
        (``--resume <uuid>`` for claude, ``resume <uuid>`` sub-command
        for codex) so the structured plugins don't have to know about it.
        """
        inner = self._agent_launch(runtime, backend)
        launch_target = runtime._find_launch_target(launch_target_id)
        if not await inner.conversation_exists(thread_id, cwd, launch_target):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"no {backend} conversation file found for thread "
                    f"{thread_id} — cannot resume via tmux"
                ),
            )
        launch_args = inner.resume_args(thread_id, [])
        session_id = runtime._generate_session_id(backend)
        try:
            command = runtime._command_for_backend(
                backend,
                launch_args,
                launch_target,
                cwd,
                allocate_tty=True,
                session_id=session_id,
            )
        except HTTPException:
            raise
        session_dir = runtime._session_dir(session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"
        raw_log.parent.mkdir(parents=True, exist_ok=True)
        raw_log.touch(exist_ok=True)
        try:
            target = await runtime.tmux.start_managed_session(session_id, cwd, command)
            await runtime.tmux.pipe_output(target.pane, raw_log)
        except TmuxError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        # ``resolve_git_meta`` is local-only; remote launch targets get
        # an empty GitMeta and the frontend renders without repo/branch
        # chips. The metadata is purely cosmetic for the session list.
        git_meta = (
            GitMeta(repo_name=None, branch=None)
            if launch_target is not None
            else await resolve_git_meta(cwd)
        )
        now = datetime.now(UTC)
        transport_state: dict[str, Any] = {
            "tmux_session": target.session,
            "tmux_window": target.window,
            "tmux_pane": target.pane,
            "pid": target.pane_pid,
            "launch_args": launch_args,
            "thread_id": thread_id,
        }
        session = SessionRecord(
            id=session_id,
            backend=backend,
            source=SessionSource.MANAGED,
            transport=self.transport_id,
            title=title,
            cwd=cwd,
            launch_target_id=launch_target.id if launch_target else None,
            launch_mode=LaunchMode.TMUX_WRAPPER,
            repo_name=git_meta.repo_name,
            branch=git_meta.branch,
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state=transport_state,
        )
        runtime.storage.create_session(session)
        await runtime._record_system_event(
            session.id,
            self.format_start_message(backend, launch_target, cwd),
        )
        runtime._ensure_monitor(session.id)
        self._spawn_rate_limit_watcher(runtime, session)
        return runtime.get_session(session.id)


def build_plugin() -> TmuxPlugin:
    return TmuxPlugin()


__all__ = ["TmuxPlugin", "TmuxPluginConfig", "build_plugin"]
