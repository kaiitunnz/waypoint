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
import json
import os
import re
import shlex
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Never

from fastapi import HTTPException, status
from pydantic import BaseModel

from waypoint.backends.capabilities import BackendCapabilities, ModelSource
from waypoint.backends.completions import static_slash_completions
from waypoint.backends.plugin_config import PluginConfig, PluginLaunchTargetConfig
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.git_meta import GitMeta
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import (
    CommandCompletion,
    SessionCreateRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


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
    label = "Tmux"
    import_request_schema: type[BaseModel] | None = None
    config_schema: type[PluginConfig] = TmuxPluginConfig
    launch_target_schema: type[PluginLaunchTargetConfig] = PluginLaunchTargetConfig
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
        model_source=ModelSource.NONE,
        badges={"glyph": "T", "color": "#94a3b8"},
        is_fallback_for_managed_launch=True,
    )

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        from waypoint.backends.tmux.transport import TmuxTransport

        return TmuxTransport(runtime)

    def setup(self, runtime: "SessionRuntime") -> None:
        return None

    async def shutdown(self, runtime: "SessionRuntime") -> None:
        return None

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
            with suppress(asyncio.CancelledError, Exception):
                await monitor
        capture = runtime._tmux_thread_id_watchers.pop(session.id, None)
        if capture is not None:
            capture.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await capture

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
            runtime._ensure_monitor(session.id)
            if session.backend == "codex" and "thread_id" not in state:
                lt = runtime._find_launch_target(session.launch_target_id)
                self._spawn_codex_thread_id_watcher(
                    runtime, session.id, session.cwd, session.created_at, lt
                )
            return

        # EXITED reconnect path. Wipe whatever might still be running
        # under the old tmux session name so the new ``new-session``
        # doesn't collide; ignore errors since the typical case is that
        # nothing is there.
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
        if thread_id and await self._conversation_exists(
            session.backend, thread_id, session.cwd, launch_target
        ):
            effective_thread_id = thread_id
        launch_args = self._resume_args(
            session.backend, effective_thread_id, list(stored_args)
        )
        try:
            command = runtime._command_for_backend(
                session.backend, launch_args, launch_target, session.cwd
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
        # Carry-forward rule depends on how the backend acquires its
        # thread id:
        #   - claude_code: the uuid is generated up-front and pinned in
        #     ``stored_args`` via ``--session-id``. Keep ``thread_id``
        #     even when the conversation file doesn't exist yet — the
        #     next reconnect will re-check existence and choose between
        #     ``--resume`` and the verbatim ``--session-id`` launch.
        #     Dropping it would short-circuit the existence check and
        #     leave us passing ``--session-id`` against an already-
        #     materialised conversation.
        #   - codex: the uuid is captured asynchronously by the watcher
        #     from the rollout filename. A phantom id (file deleted
        #     out of band) would suppress the watcher-spawn guard below
        #     and the session could never re-acquire a real one.
        if effective_thread_id:
            new_state["thread_id"] = effective_thread_id
        elif session.backend == "claude_code" and thread_id:
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
        # Re-attach the monitor against the new pane.
        runtime._ensure_monitor(session.id)
        if session.backend == "codex" and not effective_thread_id:
            self._spawn_codex_thread_id_watcher(
                runtime, session.id, session.cwd, now, launch_target
            )

    async def _conversation_exists(
        self,
        backend: str,
        thread_id: str,
        cwd: str,
        launch_target: SshLaunchTargetConfig | None,
    ) -> bool:
        """Return True if the inner CLI has actually persisted ``thread_id``.

        Both Claude and Codex defer conversation-file creation until
        first input. A captured uuid is useless until that's happened
        — passing ``--resume`` (or ``codex resume``) for a never-written
        thread makes the CLI exit immediately with "no conversation
        found". This check lets restore fall back to a verbatim launch
        in that case, keeping the same uuid so a later reconnect can
        resume once the user has actually conversed.

        For SSH-launched sessions the file lives on the remote host;
        the check runs over SSH (cheap ``test -f`` / glob). OpenCode
        sessions never use the tmux fallback (no ``cli_binary``), so
        the backend dispatch falls through to ``False`` and the
        verbatim-launch fallback covers them.
        """
        if backend == "claude_code":
            # ~/.claude/projects/<dashed-cwd>/<uuid>.jsonl
            dashed = cwd.replace("/", "-")
            rel = f".claude/projects/{dashed}/{thread_id}.jsonl"
            if launch_target is None:
                return (Path.home() / rel).is_file()
            # ``$HOME`` must be left *outside* the quoted arg so the
            # remote shell expands it. shlex-quoting the whole path
            # would single-quote the dollar and look for a literal
            # ``$HOME`` directory.
            return await self._ssh_test(
                launch_target, f"test -f $HOME/{shlex.quote(rel)}"
            )
        if backend == "codex":
            # $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*-<uuid>.jsonl
            needle = f"-{thread_id}.jsonl"
            if launch_target is None:
                home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
                sessions_dir = home / "sessions"
                if not sessions_dir.is_dir():
                    return False
                for entry in sessions_dir.glob("*/*/*/rollout-*.jsonl"):
                    if entry.name.endswith(needle):
                        return True
                return False
            stdout = await self._ssh_capture(
                launch_target,
                f'ls "${{CODEX_HOME:-$HOME/.codex}}/sessions/"*/*/*/rollout-*{needle} '
                "2>/dev/null | head -n 1",
            )
            return bool(stdout.strip())
        return False

    @staticmethod
    async def _ssh_test(launch_target: SshLaunchTargetConfig, remote_cmd: str) -> bool:
        """``ssh <host> <cmd>`` — True when the remote shell exits 0.

        The caller supplies the full remote command; quoting and shell
        expansion are the caller's call.
        """
        proc = await asyncio.create_subprocess_exec(
            launch_target.ssh_bin,
            *launch_target.ssh_args,
            launch_target.ssh_destination,
            remote_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0

    @staticmethod
    async def _ssh_capture(
        launch_target: SshLaunchTargetConfig, remote_cmd: str
    ) -> str:
        """``ssh <host> <cmd>`` — returns stdout (empty on non-zero exit)."""
        proc = await asyncio.create_subprocess_exec(
            launch_target.ssh_bin,
            *launch_target.ssh_args,
            launch_target.ssh_destination,
            remote_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return ""
        return stdout.decode("utf-8", errors="ignore")

    def _resume_args(
        self, backend: str, thread_id: str | None, stored_args: list[str]
    ) -> list[str]:
        """Translate the original launch args into the inner CLI's resume form."""
        if backend == "claude_code" and thread_id:
            # The original args may already start with ``--session-id <uuid>``
            # (inserted by ``create_session``). Strip it and substitute
            # ``--resume <uuid>``; Claude treats the two flags as
            # mutually exclusive.
            scrubbed: list[str] = []
            skip = 0
            for arg in stored_args:
                if skip:
                    skip -= 1
                    continue
                if arg == "--session-id":
                    skip = 1
                    continue
                scrubbed.append(arg)
            return ["--resume", thread_id, *scrubbed]
        if backend == "codex" and thread_id:
            # ``codex resume <uuid>`` is a subcommand, not a flag. The
            # rest of the original args go after it.
            return ["resume", thread_id, *stored_args]
        return list(stored_args)

    def _spawn_codex_thread_id_watcher(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        cwd: str,
        since: datetime,
        launch_target: SshLaunchTargetConfig | None,
    ) -> None:
        """Spawn a one-shot task that captures Codex's session UUID.

        Codex writes its rollout file to
        ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<UUID>.jsonl``
        only after the first persist (typically the first user input),
        and the UUID is embedded in the filename. The watcher polls
        for that file (locally or over SSH for remote launch targets)
        and stores ``transport_state.thread_id`` when found so a later
        reconnect can ``codex resume <uuid>``.

        The interactive ``codex`` CLI exposes no session-id flag, so
        capture has to happen post-launch.
        """
        if session_id in runtime._tmux_thread_id_watchers:
            return
        task = asyncio.create_task(
            self._capture_codex_thread_id(
                runtime, session_id, cwd, since, launch_target
            )
        )
        runtime._tmux_thread_id_watchers[session_id] = task

    # Filename UUID matches the trailing ``<uuid>`` in
    # ``rollout-YYYY-MM-DDThh-mm-ss-<UUID>.jsonl``.
    _ROLLOUT_UUID_RE = re.compile(
        r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-"
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
    )

    async def _capture_codex_thread_id(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        cwd: str,
        since: datetime,
        launch_target: SshLaunchTargetConfig | None,
    ) -> None:
        # Give up after this many seconds of polling; if the user never
        # interacted, there's no thread to resume and a fresh launch on
        # reconnect is the correct behavior anyway.
        DEADLINE = 30 * 60  # 30 minutes
        # Polling interval: local fs is cheap, but the remote variant
        # opens an SSH connection per tick, so back off there.
        POLL_INTERVAL = 2.0 if launch_target is None else 10.0
        elapsed = 0.0
        try:
            while elapsed < DEADLINE:
                # Probe before sleeping so we don't waste POLL_INTERVAL
                # in the case where the user has already typed and the
                # rollout file exists when this watcher starts.
                if launch_target is None:
                    uuid_found = self._find_codex_thread_id_local(cwd, since)
                else:
                    uuid_found = await self._find_codex_thread_id_remote(
                        cwd, since, launch_target
                    )
                if uuid_found is not None:
                    session = runtime.storage.get_session(session_id)
                    if session is None:
                        return
                    state = dict(session.transport_state or {})
                    if state.get("thread_id"):
                        return
                    state["thread_id"] = uuid_found
                    runtime.storage.update_session(session_id, transport_state=state)
                    return
                await asyncio.sleep(POLL_INTERVAL)
                elapsed += POLL_INTERVAL
        except asyncio.CancelledError:
            raise
        except Exception:
            # The watcher is best-effort. Don't crash the session over
            # a missing rollout file or a JSON decode hiccup.
            return
        finally:
            runtime._tmux_thread_id_watchers.pop(session_id, None)

    def _find_codex_thread_id_local(self, cwd: str, since: datetime) -> str | None:
        codex_home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
        sessions_dir = codex_home / "sessions"
        if not sessions_dir.is_dir():
            return None
        since_ts = since.timestamp()
        best: tuple[float, str] | None = None
        for entry in sessions_dir.glob("*/*/*/rollout-*.jsonl"):
            try:
                stat = entry.stat()
            except OSError:
                continue
            if stat.st_mtime < since_ts - 5:
                continue
            match = self._ROLLOUT_UUID_RE.search(entry.name)
            if not match:
                continue
            if not self._codex_rollout_matches_cwd(entry, cwd):
                continue
            if best is None or stat.st_mtime > best[0]:
                best = (stat.st_mtime, match.group(1))
        return best[1] if best else None

    async def _find_codex_thread_id_remote(
        self,
        cwd: str,
        since: datetime,
        launch_target: SshLaunchTargetConfig,
    ) -> str | None:
        # Single SSH round-trip: list candidate rollout files and emit
        # the filename + first JSONL line per file, tab-separated. The
        # remote shell does the globbing; we filter in Python so the
        # cwd-matching logic stays identical to the local path.
        remote_cmd = (
            'for f in "${CODEX_HOME:-$HOME/.codex}/sessions/"*/*/*/rollout-*.jsonl; '
            "do "
            '[ -f "$f" ] || continue; '
            'printf "%s\\t" "$f"; '
            'head -n1 "$f" 2>/dev/null; '
            'printf "\\n"; '
            "done 2>/dev/null"
        )
        stdout = await self._ssh_capture(launch_target, remote_cmd)
        if not stdout:
            return None

        since_ts = since.timestamp()
        best: tuple[float, str] | None = None
        for line in stdout.splitlines():
            path, sep, header = line.partition("\t")
            if not sep or not header.strip():
                continue
            # Filename embeds the timestamp the rollout was opened at;
            # use that to filter out files older than the watcher start
            # (no need for a stat round-trip).
            match = self._ROLLOUT_UUID_RE.search(path)
            if not match:
                continue
            ts = self._parse_rollout_timestamp(path)
            if ts is not None and ts < since_ts - 5:
                continue
            try:
                payload = json.loads(header.strip())
            except (json.JSONDecodeError, ValueError):
                continue
            if not self._payload_cwd_matches(payload, cwd):
                continue
            score = ts if ts is not None else since_ts
            if best is None or score > best[0]:
                best = (score, match.group(1))
        return best[1] if best else None

    @staticmethod
    def _parse_rollout_timestamp(path: str) -> float | None:
        """Extract the YYYY-MM-DDThh-mm-ss prefix from a rollout filename."""
        match = re.search(
            r"rollout-(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})-", path
        )
        if not match:
            return None
        try:
            return datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(4)),
                int(match.group(5)),
                int(match.group(6)),
            ).timestamp()
        except ValueError:
            return None

    @staticmethod
    def _payload_cwd_matches(payload: Any, cwd: str) -> bool:
        for candidate in (
            payload.get("payload") if isinstance(payload, dict) else None,
            payload if isinstance(payload, dict) else None,
        ):
            if not isinstance(candidate, dict):
                continue
            recorded = candidate.get("cwd")
            if isinstance(recorded, str) and recorded == cwd:
                return True
        return False

    @classmethod
    def _codex_rollout_matches_cwd(cls, path: Path, cwd: str) -> bool:
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                first_line = fh.readline()
        except OSError:
            return False

        try:
            payload = json.loads(first_line)
        except (json.JSONDecodeError, ValueError):
            return False
        # SessionMeta is either nested under "payload" or at the top
        # level depending on Codex's rollout schema version. The
        # ``_payload_cwd_matches`` helper checks both.
        return cls._payload_cwd_matches(payload, cwd)

    async def list_threads(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
    ) -> list[Any]:
        return []

    async def import_thread(
        self, runtime: "SessionRuntime", request: Any
    ) -> SessionRecord:
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
        # Tmux fallback launches the actual backend binary inside a tmux
        # pane and tails the pane log. The plugin doesn't pick the
        # binary itself — it asks the registry for the cli_binary the
        # requested backend advertises. A backend without a cli_binary
        # (e.g. an HTTP-only OpenCode) can opt out of tmux fallback by
        # leaving the capability unset.
        #
        # Inner-backend resume support is dispatched here. Claude's CLI
        # accepts ``--session-id <uuid>`` on launch, so we pre-generate
        # the UUID and store it for the eventual reconnect path. Codex
        # doesn't expose a session-id flag — its UUID is captured later
        # by a watcher (see _watch_codex_thread_id) when the rollout
        # file appears on disk.
        launch_args = list(request.args)
        thread_id: str | None = None
        if request.backend == "claude_code":
            thread_id = str(uuid.uuid4())
            launch_args = ["--session-id", thread_id, *launch_args]
        command = runtime._command_for_backend(
            request.backend, launch_args, launch_target, request.cwd
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
        )
        runtime.storage.create_session(session)
        await runtime._record_system_event(
            session.id,
            self.format_start_message(request.backend, launch_target, request.cwd),
        )
        runtime._ensure_monitor(session.id)
        if request.backend == "codex":
            self._spawn_codex_thread_id_watcher(
                runtime, session.id, session.cwd, now, launch_target
            )
        return runtime.get_session(session.id)


def build_plugin() -> TmuxPlugin:
    return TmuxPlugin()


__all__ = ["TmuxPlugin", "TmuxPluginConfig", "build_plugin"]
