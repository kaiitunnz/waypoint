"""One ``opencode serve`` process and the Waypoint sessions it drives, over the
2.x server API."""

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import time
import urllib.parse
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Coroutine,
)
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from waypoint.asyncio_tasks import cancel_and_wait
from waypoint.attachments import ResolvedAttachment
from waypoint.backends.opencode import normalize
from waypoint.backends.opencode.catalog import Model, model_id, model_ref, parse_models
from waypoint.backends.opencode.client import (
    LocalOpenCodeClient,
    OpenCodeHttpClient,
    RemoteOpenCodeClient,
    http_status,
)
from waypoint.backends.opencode.forms import Form, parse_form
from waypoint.backends.opencode.probe import (
    IncompatibleServerError,
    ServerInfo,
    probe_server,
)
from waypoint.backends.opencode.remote import build_remote_serve_args
from waypoint.backends.opencode.server_auth import ServerAuth
from waypoint.backends.opencode.sessions import Permission, SessionState, ToolCall
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.questions import QuestionDecline, QuestionLiveness
from waypoint.schemas import (
    EventKind,
    SessionContextUsage,
    SessionStatus,
    TokenUsageRecord,
)

log = logging.getLogger("waypoint.opencode")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 0

# How long a server has to open its event stream, and to migrate a 1.x history.
CONNECT_TIMEOUT_SECONDS = 30.0
MIGRATION_TIMEOUT_SECONDS = 900.0
# A server answers its catalogs empty until its plugins have loaded.
CATALOG_SETTLE_SECONDS = 10.0
# Minimum gap between retries of a failed listing.
CATALOG_RETRY_SECONDS = 60.0
QUESTION_SEED_RETRY_SECONDS = 30.0
# How long a finished subagent has to wake its parent before the session is
# reported idle.
SUBAGENT_SETTLE_SECONDS = 5.0
_LIST_PAGE = 200
_LIST_LIMIT = 500
_MAX_SSE_FAILURES = 10

# Waypoint's decision spellings. ``session`` is not a reply OpenCode takes: its
# own ``always`` saves the rule for the whole project, so an approval for the
# session is a rule on the session itself and a ``once`` reply.
_DECISION_ALIASES: dict[str, str] = {
    "once": "once",
    "approve": "once",
    "accept": "once",
    "yes": "once",
    "always": "session",
    "acceptforsession": "session",
    "reject": "reject",
    "decline": "reject",
    "deny": "reject",
    "cancel": "reject",
    "no": "reject",
}


def _normalize_decision(decision: str) -> str:
    key = decision.strip().lower().replace("-", "").replace("_", "")
    reply = _DECISION_ALIASES.get(key)
    if reply is None:
        raise OpenCodeError(f"unsupported permission decision: {decision}")
    return reply


# A status of ``None`` leaves the session's status as it is.
EmitEvent = Callable[
    [str, EventKind, str, dict[str, Any], SessionStatus | None],
    Any,
]
SessionUpdateCallback = Callable[
    [str, dict[str, Any], bool],
    Awaitable[Any],
]
TokenUsageCallback = Callable[[str, TokenUsageRecord, bool], Awaitable[Any]]
AgentChangedCallback = Callable[[str, str | None, str | None], Any]
ServerDiedCallback = Callable[[list[str]], None]
QuestionsChangedCallback = Callable[[str], Any]


class OpenCodeError(RuntimeError):
    pass


class MigrationGate:
    """Lets one server at a time start until one has seen its history migrated:
    servers migrating one database at once fail."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.migrated = False

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        await self._lock.acquire()
        held = not self.migrated
        if not held:
            self._lock.release()
        try:
            yield
        finally:
            if held:
                self._lock.release()


_LISTENING_LINE = re.compile(r"listening on http://[^:\s]+:(\d+)")
_OUTPUT_CHUNK_BYTES = 64 * 1024


async def _drain_output(reader: asyncio.StreamReader) -> None:
    """Read a server's output to its end: an unread pipe blocks the server."""
    while chunk := await reader.read(_OUTPUT_CHUNK_BYTES):
        log.debug("opencode server: %s", chunk.decode(errors="replace").rstrip())


class OpenCodeAdapter:
    def __init__(
        self,
        emit_event: EmitEvent,
        on_session_update: SessionUpdateCallback | None = None,
        on_token_usage: TokenUsageCallback | None = None,
        binary: str | None = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        launch_target: SshLaunchTargetConfig | None = None,
        on_agent_changed: AgentChangedCallback | None = None,
        on_server_died: ServerDiedCallback | None = None,
        workdir: str | None = None,
        extra_args: tuple[str, ...] = (),
        launch_env: dict[str, str] | None = None,
        on_questions_changed: QuestionsChangedCallback | None = None,
        migration_gate: MigrationGate | None = None,
    ) -> None:
        self._emit_event = emit_event
        self._on_questions_changed = on_questions_changed
        self._on_session_update = on_session_update
        self._on_token_usage = on_token_usage
        self._binary = binary
        self._host = host
        self._port = port
        self._launch_target = launch_target
        self._on_agent_changed = on_agent_changed
        self._on_server_died_callback = on_server_died
        self._workdir = workdir
        self._extra_args = extra_args
        self._launch_env = dict(launch_env or {})
        self._migration_gate = migration_gate or MigrationGate()

        self._sessions: dict[str, SessionState] = {}
        # OpenCode session id (a root or a subagent) -> Waypoint session id.
        self._routes: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._ask_seed_tasks: dict[str, asyncio.Task[None]] = {}
        # Serializes writes to session rulesets, which replace the whole list.
        self._ruleset_lock = asyncio.Lock()
        self._models: list[Model] = []
        self._models_failed_at: float | None = None

        self._server_process: asyncio.subprocess.Process | None = None
        self._output_tasks: list[asyncio.Task[None]] = []
        self._sse_task: asyncio.Task[None] | None = None
        self._connected = asyncio.Event()
        self._client: OpenCodeHttpClient | None = None
        # The absolute directory the server runs in.
        self._directory: str | None = None
        self.server_info: ServerInfo | None = None
        self._started = False
        self._start_lock = asyncio.Lock()

    @property
    def workdir(self) -> str | None:
        return self._workdir

    # ── Server lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        if self._started:
            return
        async with self._start_lock:
            if self._started:
                return
            try:
                async with self._migration_gate.hold():
                    if self._launch_target is not None:
                        await self._start_remote()
                    else:
                        await self._start_local()
                    await self._await_migration()
                    self._migration_gate.migrated = True
                await self._connect_events()
            except BaseException:
                # Includes cancellation: a timed-out restore must not leave a
                # server running.
                await self._stop_events()
                await self._discard_server()
                raise
            self._started = True
            log.info("opencode server started successfully")

    async def _start_local(self) -> None:
        binary = self._binary or shutil.which("opencode")
        if binary is None:
            raise OpenCodeError("opencode binary not found on PATH")
        cwd = str(Path(self._workdir).expanduser()) if self._workdir else None
        self._directory = os.path.abspath(cwd) if cwd else os.getcwd()

        log.info("starting local opencode server on %s:%d", self._host, self._port)
        auth = ServerAuth.generate()
        # Spawn directly with the requested port (0 = let the kernel pick) and
        # parse the bound port from the server's "listening on" log line. The
        # previous bind/close/spawn dance left a TOCTOU window where another
        # process could grab the port between probe and spawn; here EADDRINUSE
        # surfaces as the child exiting with stderr we re-raise.
        self._server_process = await asyncio.create_subprocess_exec(
            binary,
            "serve",
            f"--hostname={self._host}",
            f"--port={self._port}",
            *self._extra_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            cwd=cwd,
            env={**os.environ, **self._launch_env, **auth.server_env()},
        )

        bound_port = await self._read_local_listening_port()
        if bound_port is None:
            stderr = b""
            proc = self._server_process
            if proc is not None and proc.stderr is not None:
                with suppress(Exception):
                    stderr = await asyncio.wait_for(proc.stderr.read(), timeout=1.0)
            self._terminate_server_process()
            await self._await_server_process_exit(timeout=2.0)
            raise OpenCodeError(
                f"opencode server failed to bind {self._host}:{self._port}: "
                f"{stderr.decode(errors='replace').strip() or 'no listening line emitted'}"
            )

        self._port = bound_port
        self._drain_server_output()
        self._client = LocalOpenCodeClient(f"http://{self._host}:{self._port}", auth)
        try:
            await self._wait_for_server()
        except Exception as exc:
            log.error("failed to start local opencode server: %s", exc)
            raise OpenCodeError(f"failed to start opencode server: {exc}") from exc

    async def _read_local_listening_port(self, timeout: float = 30.0) -> int | None:
        proc = self._server_process
        if proc is None or proc.stdout is None:
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.returncode is not None:
                return None
            try:
                line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=0.5)
            except TimeoutError:
                continue
            if not line_bytes:
                return None
            match = _LISTENING_LINE.search(line_bytes.decode("utf-8", errors="replace"))
            if match:
                return int(match.group(1))
        return None

    async def _start_remote(self) -> None:
        assert self._launch_target is not None
        binary = (
            self._launch_target.remote_bin_for("opencode", "opencode") or "opencode"
        )
        args = build_remote_serve_args(
            self._launch_target,
            binary,
            self._workdir,
            self._extra_args,
            self._launch_env,
        )
        log.info(
            "starting remote opencode server on %s", self._launch_target.ssh_destination
        )

        self._server_process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # New session so the local SSH client gets its own process group
            # and we can `killpg` it as a backstop if `terminate()` isn't
            # enough to bring the SSH child down (the remote bash trap kills
            # the OpenCode server on its own once the SSH channel closes).
            start_new_session=True,
        )

        assert self._server_process.stdin is not None
        assert self._server_process.stdout is not None

        # The serve script reads the password from its first stdin line.
        auth = ServerAuth.generate()
        self._server_process.stdin.write(f"{auth.password}\n".encode())
        await self._server_process.stdin.drain()

        # Wait for the port sentinel, which follows the directory one.
        self._directory = None
        remote_port = None
        start_time = time.monotonic()
        while time.monotonic() - start_time < 30:
            if self._server_process.returncode is not None:
                stderr = (
                    await self._server_process.stderr.read()
                    if self._server_process.stderr
                    else b""
                )
                raise OpenCodeError(
                    f"remote opencode script exited unexpectedly: {stderr.decode(errors='replace')}"
                )

            try:
                line_bytes = await asyncio.wait_for(
                    self._server_process.stdout.readline(), timeout=0.5
                )
                if not line_bytes:
                    continue
                line = line_bytes.decode("utf-8").strip()
                if line.startswith("__WP_DIR__="):
                    self._directory = line.partition("=")[2]
                elif line.startswith("__WP_PORT__="):
                    remote_port = int(line.split("=")[1])
                    break
            except TimeoutError:
                continue

        if remote_port is None:
            raise OpenCodeError("timeout waiting for remote opencode server port")

        log.info("remote opencode bound to port %d", remote_port)
        self._drain_server_output()
        self._client = RemoteOpenCodeClient(
            self._launch_target, remote_port, auth, self._workdir
        )
        try:
            await self._wait_for_server()
        except Exception as exc:
            log.error("failed to start remote opencode server: %s", exc)
            raise OpenCodeError(
                f"failed to start remote opencode server: {exc}"
            ) from exc

    def _drain_server_output(self) -> None:
        proc = self._server_process
        assert proc is not None
        self._output_tasks = [
            asyncio.create_task(_drain_output(reader))
            for reader in (proc.stdout, proc.stderr)
            if reader is not None
        ]

    async def _stop_draining_output(self) -> None:
        tasks, self._output_tasks = self._output_tasks, []
        await cancel_and_wait(*tasks)

    async def _discard_server(self, timeout: float = 2.0) -> None:
        self._terminate_server_process()
        with suppress(Exception):
            await self._await_server_process_exit(timeout=timeout)
        self._server_process = None
        await self._stop_draining_output()
        await self._close_client()

    async def _close_client(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            try:
                await client.close()
            except Exception:
                log.exception("error closing the opencode http client")

    def _terminate_server_process(self) -> None:
        """Kill the OpenCode subprocess and any of its descendants."""
        proc = self._server_process
        if proc is None or proc.returncode is not None:
            return

        if self._launch_target is not None:
            # Close stdin so the remote bash script's `read` returns and the
            # script's EXIT/HUP trap kills opencode. Also terminate the local
            # SSH child directly so it dies even if it never finished
            # connecting (e.g. firewall drop) and the stdin signal never
            # reaches the remote.
            if proc.stdin is not None:
                with suppress(Exception):
                    proc.stdin.close()
            with suppress(ProcessLookupError):
                proc.terminate()
            return

        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError:
            with suppress(ProcessLookupError):
                proc.terminate()

    async def _await_server_process_exit(self, timeout: float = 5.0) -> None:
        """Wait for the server process to exit, escalating to SIGKILL if needed."""
        proc = self._server_process
        if proc is None or proc.returncode is not None:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except TimeoutError:
            log.warning(
                "opencode server process %d did not exit within %.1fs; killing",
                proc.pid,
                timeout,
            )
            # The server, or the SSH child, leads its own process group.
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=2.0)

    async def _wait_for_server(self) -> None:
        client = self._require_client()
        deadline = time.monotonic() + 30
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OpenCodeError("timeout waiting for opencode server")
            try:
                async with asyncio.timeout(remaining):
                    self.server_info = await probe_server(client)
                return
            except IncompatibleServerError as exc:
                raise OpenCodeError(f"incompatible OpenCode server: {exc}") from exc
            except Exception:
                pass
            await asyncio.sleep(0.5)

    async def _await_migration(self) -> None:
        """Wait until the server has migrated a 1.x history: until then, the
        sessions in it read as missing."""
        deadline = time.monotonic() + MIGRATION_TIMEOUT_SECONDS
        logged_at = 0.0
        while True:
            try:
                data = await self._require_client().get(
                    "/api/experimental/migration/v1"
                )
            except Exception as exc:
                if http_status(exc) == 404:
                    return
                raise OpenCodeError(f"failed to read migration status: {exc}") from exc
            state = data.get("status") if isinstance(data, dict) else None
            if state == "error":
                raise OpenCodeError(
                    f"OpenCode history migration failed: {data.get('error')}"
                )
            if state not in ("required", "running"):
                return
            now = time.monotonic()
            if now > deadline:
                raise OpenCodeError("timeout waiting for the history migration")
            if now - logged_at > 10:
                log.info("waiting for opencode to migrate its history: %s", data)
                logged_at = now
            await asyncio.sleep(0.5)

    async def _connect_events(self) -> None:
        """Start the event loop and wait for the stream to open: events are not
        replayed, and session status follows them."""
        self._connected = asyncio.Event()
        self._sse_task = asyncio.create_task(self._listen_events())
        connected = asyncio.create_task(self._connected.wait())
        try:
            await asyncio.wait(
                {connected, self._sse_task},
                timeout=CONNECT_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            connected.cancel()
        if not self._connected.is_set() or self._sse_task.done():
            raise OpenCodeError("failed to open the opencode event stream")

    async def _stop_events(self) -> None:
        if self._sse_task is not None:
            task, self._sse_task = self._sse_task, None
            with suppress(Exception):
                await cancel_and_wait(task)

    def _require_client(self) -> OpenCodeHttpClient:
        if self._client is None:
            raise OpenCodeError("opencode http client not initialized")
        return self._client

    async def _on_server_died(self) -> None:
        if not self._started:
            # ``start`` sees the event task end, and fails.
            return
        active = [state for state in self._sessions.values() if not state.closing]
        # Snapshot ids before we clear `_sessions` so the plugin's
        # auto-reconnect loop knows which sessions to restore once the
        # remote comes back.
        active_session_ids = [state.session_id for state in active]

        for state in active:
            try:
                for ask_id in [*state.permissions, *state.forms]:
                    await self._drop_ask(state, ask_id, SessionStatus.ERROR)
                await self._emit(
                    state,
                    normalize.note(
                        "OpenCode server disconnected — connection lost",
                        "server.disconnected",
                    ),
                    SessionStatus.ERROR,
                )
            except Exception:
                log.exception(
                    "failed to emit error event for session %s", state.session_id
                )

        self._sessions.clear()
        self._routes.clear()

        await self._close_client()
        self._terminate_server_process()
        self._server_process = None
        await self._stop_draining_output()
        self._started = False
        log.info("opencode adapter reset after server death; ready for restart")

        if self._on_server_died_callback is not None:
            try:
                self._on_server_died_callback(active_session_ids)
            except Exception:
                log.exception("on_server_died callback failed")

    async def shutdown(self) -> None:
        for task in [*self._ask_seed_tasks.values(), *self._tasks]:
            with suppress(Exception):
                await cancel_and_wait(task)
        self._ask_seed_tasks.clear()
        self._tasks.clear()
        await self._stop_events()
        await self._discard_server(timeout=5.0)
        self._sessions.clear()
        self._routes.clear()
        self._started = False

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ── Event stream ────────────────────────────────────────────────────

    async def _listen_events(self) -> None:
        # The stream does not support resume, so a mid-session disconnect is
        # unrecoverable: events generated while the connection was down would be
        # silently dropped. The retry budget is therefore only spent before the
        # first event (server still warming up). Once one has arrived, any error
        # or clean EOF is a server death, which surfaces the disconnect instead
        # of pretending the transcript is still live.
        consecutive_failures = 0
        delivered_any = False
        try:
            while True:
                try:
                    client = self._require_client()
                    buffer: list[str] = []
                    async for raw_line in client.stream_events("/api/event"):
                        line = raw_line.rstrip("\r\n")
                        if not line:
                            payload = _decode_sse_payload(buffer)
                            buffer.clear()
                            if payload is not None:
                                delivered_any = True
                                await self._handle_event(payload)
                            continue
                        if line.startswith("data:"):
                            buffer.append(line[5:].lstrip())

                    # Handle last bit of decoder state on clean EOF.
                    payload = _decode_sse_payload(buffer)
                    if payload is not None:
                        delivered_any = True
                        await self._handle_event(payload)

                    if delivered_any:
                        log.warning(
                            "opencode sse stream closed mid-session — treating as server death"
                        )
                        await self._on_server_died()
                        return

                    consecutive_failures += 1
                    if consecutive_failures >= _MAX_SSE_FAILURES:
                        log.error(
                            "opencode sse closed %d times before delivering any event — "
                            "treating server as dead",
                            consecutive_failures,
                        )
                        await self._on_server_died()
                        return
                    await asyncio.sleep(1)

                except asyncio.CancelledError:
                    raise
                except Exception:
                    proc_dead = (
                        self._server_process is not None
                        and self._server_process.returncode is not None
                    )
                    if delivered_any or proc_dead:
                        log.exception(
                            "opencode sse failed%s — treating server as dead",
                            " (process exited)" if proc_dead else " mid-session",
                        )
                        await self._on_server_died()
                        return
                    consecutive_failures += 1
                    if consecutive_failures >= _MAX_SSE_FAILURES:
                        log.error(
                            "opencode sse failed %d times before any event — "
                            "treating server as dead",
                            consecutive_failures,
                        )
                        await self._on_server_died()
                        return
                    log.exception(
                        "opencode sse connection failed, retrying in 1s (%d/%d)",
                        consecutive_failures,
                        _MAX_SSE_FAILURES,
                    )
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def _handle_event(self, event: dict[str, Any]) -> None:
        """Dispatch one event. A failure to handle it does not end the stream."""
        try:
            await self._dispatch_event(event)
        except Exception:
            log.exception("failed to handle opencode event %s", event.get("type"))

    async def _dispatch_event(self, event: dict[str, Any]) -> None:
        kind, data = event.get("type"), event.get("data")
        if kind == "server.connected":
            self._connected.set()
            return
        if not isinstance(kind, str) or not isinstance(data, dict):
            return
        if kind == "session.created":
            self._adopt_child(data.get("parentID"), data.get("sessionID"))
            return
        form = data.get("form")
        origin = data.get("sessionID") or (
            form.get("sessionID") if isinstance(form, dict) else None
        )
        if not isinstance(origin, str):
            return
        state = self._sessions.get(self._routes.get(origin, ""))
        # ``closing`` drops a late event that would re-populate state being
        # torn down.
        if state is None or state.closing:
            return
        root = origin == state.root_id

        match kind:
            case "session.execution.started":
                state.execution_started(origin)
                if root:
                    await self._emit(
                        state, normalize.status_note(SessionStatus.RUNNING, kind)
                    )
            case (
                "session.execution.succeeded"
                | "session.execution.failed"
                | "session.execution.interrupted"
            ):
                await self._on_execution_ended(state, origin, kind, data)
            case "session.text.delta" | "session.reasoning.delta" if root:
                await self._on_text(state, data, kind, data.get("delta"))
            case "session.text.ended" | "session.reasoning.ended" if root:
                # The whole text, for a part no delta carried.
                message_id, ordinal, reasoning = _part(data, kind)
                part = normalize.part_id(message_id, ordinal, reasoning=reasoning)
                if part not in state.streamed:
                    await self._on_text(state, data, kind, data.get("text"))
            case "session.step.started":
                if model := model_id(data.get("model")):
                    state.message_models[str(data.get("assistantMessageID"))] = model
            case "session.step.ended" | "session.step.failed":
                await self._on_step_ended(state, origin, data)
            case "session.tool.input.started":
                state.tools[str(data.get("id"))] = ToolCall(str(data.get("name")), {})
            case "session.tool.called":
                await self._on_tool_called(state, data, root=root)
            case "session.tool.progress":
                if isinstance(metadata := data.get("metadata"), dict):
                    self._adopt_child(origin, metadata.get("sessionID"))
            case "session.tool.success" | "session.tool.failed":
                await self._on_tool_ended(state, data, kind, root=root)
            case "permission.asked":
                await self._on_permission_asked(state, data)
            case "permission.replied":
                await self._on_permission_replied(state, data)
            case "form.created":
                await self._on_form_created(state, data)
            case "form.replied" | "form.cancelled":
                form_id = str(data.get("id"))
                state.forget_ask(form_id)
                await self._emit(
                    state,
                    normalize.question_closed(form_id, answered=kind == "form.replied"),
                )
            case "session.retry.scheduled" if root:
                error = data.get("error")
                message = error.get("message") if isinstance(error, dict) else None
                await self._emit(
                    state,
                    normalize.note(
                        f"Retrying (attempt {data.get('attempt')}): {message or ''}",
                        kind,
                    ),
                )
            case "session.compaction.ended" if root:
                await self._emit(state, normalize.note("Session compacted", kind))
            case "session.compaction.failed" if root:
                await self._emit(
                    state,
                    normalize.note(f"Error: compaction failed: {_reason(data)}", kind),
                )
            case "session.agent.selected" if root:
                self._on_agent_selected(state, data.get("agent"))
            case "session.model.selected" if root:
                self._adopt_model(state, data.get("model"))

    async def _emit(
        self,
        state: SessionState,
        emission: normalize.Emission,
        status: SessionStatus | None = None,
    ) -> None:
        """Emit with ``status``, by default the one the session's state gives.
        Nothing is emitted for a session being closed."""
        # Handling that awaited the server may resume after the close, and its
        # event would revive the session.
        if state.closing:
            return
        kind, text, metadata = emission
        status = status or state.status()
        await self._emit_event(
            state.session_id, kind, text, {**metadata, "status": status}, status
        )

    def _adopt_child(self, parent: Any, child: Any) -> None:
        if not isinstance(parent, str) or not isinstance(child, str):
            return
        state = self._sessions.get(self._routes.get(parent, ""))
        if state is None or state.owns(child):
            return
        state.children.add(child)
        self._routes[child] = state.session_id
        if state.subagent_permissions is not None:
            self._spawn(self._write_subagent_rules(state, [child]))

    async def _on_execution_ended(
        self, state: SessionState, origin: str, kind: str, data: dict[str, Any]
    ) -> None:
        reason = data.get("reason")
        if reason == "superseded":
            # Another execution takes over.
            return
        state.execution_ended(origin, by_human=reason == "user")
        if origin != state.root_id:
            mark = state.mark()
            await self._reconcile(state)
            if not state.running:
                self._spawn(self._settle_after_subagent(state, mark))
            return

        if state.changed_files:
            # Before the ending note: an idle status releases queued messages,
            # and the diff is of the newest turn.
            state.changed_files = False
            await self._emit_changes(state)
        await self._reconcile(state)
        if kind == "session.execution.failed":
            await self._emit(state, normalize.note(f"Error: {_reason(data)}", kind))
        elif kind == "session.execution.interrupted":
            await self._emit(state, normalize.note("Turn interrupted", kind))
        else:
            await self._emit_resting(state, kind)

    async def _emit_resting(self, state: SessionState, method: str) -> None:
        """Report a session at rest. Nothing is said of one still running or
        waiting."""
        if (status := state.status()) in (
            SessionStatus.IDLE,
            SessionStatus.INTERRUPTED,
        ):
            await self._emit(state, normalize.status_note(status, method))

    async def _settle_after_subagent(self, state: SessionState, mark: int) -> None:
        """A finished subagent hands its result to the parent, which runs again.
        When it does not, report that the session is at rest."""
        await asyncio.sleep(SUBAGENT_SETTLE_SECONDS)
        if (
            self._sessions.get(state.session_id) is state
            # A parent that ran since has reported its own ending.
            and not state.touched_since(state.root_id, mark)
        ):
            await self._emit_resting(state, "session.subagents.ended")

    async def _emit_changes(self, state: SessionState) -> None:
        try:
            data = await self._require_client().get(
                f"/api/session/{state.root_id}/diff", params={"context": "3"}
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to read opencode turn diff: %s", exc)
            return
        emission = normalize.turn_changes(data.get("data"))
        if emission and not state.closing:
            # The note says nothing about the session's status.
            kind, text, metadata = emission
            await self._emit_event(state.session_id, kind, text, metadata, None)

    async def _on_text(
        self, state: SessionState, data: dict[str, Any], kind: str, text: Any
    ) -> None:
        if not isinstance(text, str) or not text:
            return
        message_id, ordinal, reasoning = _part(data, kind)
        state.streamed.add(normalize.part_id(message_id, ordinal, reasoning=reasoning))
        await self._emit(
            state, normalize.text_delta(message_id, ordinal, text, reasoning=reasoning)
        )

    async def _on_tool_called(
        self, state: SessionState, data: dict[str, Any], *, root: bool
    ) -> None:
        call_id = str(data.get("id"))
        call = state.tools.get(call_id)
        if call is None:
            return
        state.tools[call_id] = ToolCall(call.name, data.get("input"))
        if root and call.name != normalize.QUESTION_TOOL:
            await self._emit(
                state, normalize.tool_call(call_id, call.name, data.get("input"))
            )

    async def _on_tool_ended(
        self, state: SessionState, data: dict[str, Any], kind: str, *, root: bool
    ) -> None:
        call_id = str(data.get("id"))
        call = state.tools.pop(call_id, None)
        proposed = state.proposed_files.pop(call_id, None)
        if call is None or not root or call.name == normalize.QUESTION_TOOL:
            return
        metadata = data.get("metadata")
        applied = metadata.get("files") if isinstance(metadata, dict) else None
        failed = kind == "session.tool.failed"
        await self._emit(
            state,
            normalize.tool_result(
                call_id,
                call.name,
                call.input,
                content=data.get("content"),
                error=(data.get("error") or "Unknown error") if failed else None,
                # A write reports no diff; the one its approval showed stands.
                files=applied or proposed,
            ),
        )

    async def _on_step_ended(
        self, state: SessionState, origin: str, data: dict[str, Any]
    ) -> None:
        if data.get("files"):
            state.changed_files = True
        tokens = data.get("tokens")
        message_id = data.get("assistantMessageID")
        if not isinstance(tokens, dict) or not isinstance(message_id, str):
            return
        model = state.message_models.pop(message_id, None)
        root = origin == state.root_id
        if self._on_token_usage is not None:
            record = _token_usage_record(
                f"{origin}:{message_id}",
                tokens,
                model=model,
                effort=state.effort if root else None,
            )
            await self._on_token_usage(state.session_id, record, False)
        # A subagent has a context of its own.
        if not root or model is None:
            return
        entry = self._cached_model(model)
        if entry is not None:
            await self._publish_context_usage(
                state, tokens, entry.context_window, publish=False
            )
        else:
            self._spawn(self._look_up_context_usage(state, tokens, model))

    async def _look_up_context_usage(
        self, state: SessionState, tokens: dict[str, Any], model: str
    ) -> None:
        # The listing would restart a server that has since died.
        if not self._started:
            return
        entry = await self._model(model)
        if (
            entry is None
            or self._sessions.get(state.session_id) is not state
            or state.closing
        ):
            return
        await self._publish_context_usage(
            state, tokens, entry.context_window, publish=True
        )

    async def _publish_context_usage(
        self,
        state: SessionState,
        tokens: dict[str, Any],
        context_window: int | None,
        *,
        publish: bool,
    ) -> None:
        if context_window is None or self._on_session_update is None:
            return
        snapshot = _context_usage_snapshot(tokens, context_window)
        if snapshot is None:
            return
        # Compared with the breakdown, so a same-total/different-split turn
        # refreshes.
        previous = state.context_usage
        if previous is not None and (
            previous.used_tokens,
            previous.context_window_tokens,
            previous.breakdown,
        ) == (
            snapshot.used_tokens,
            snapshot.context_window_tokens,
            snapshot.breakdown,
        ):
            return
        state.context_usage = snapshot
        await self._on_session_update(
            state.session_id,
            {"context_usage": snapshot.model_dump(mode="json")},
            publish,
        )

    def _on_agent_selected(self, state: SessionState, agent: Any) -> None:
        if not isinstance(agent, str) or agent == state.agent:
            return
        old, state.agent = state.agent, agent
        if self._on_agent_changed:
            self._on_agent_changed(state.session_id, old, agent)

    def _adopt_model(self, state: SessionState, ref: Any) -> None:
        if model := model_id(ref):
            variant = ref.get("variant")
            state.model = model
            # ``default`` names no variant.
            state.effort = variant if variant != "default" else None

    # ── Asks ────────────────────────────────────────────────────────────

    async def _on_permission_asked(
        self, state: SessionState, data: dict[str, Any]
    ) -> None:
        permission = Permission.parse(data)
        if permission is None or permission.id in state.permissions:
            return
        state.add_permission(permission)
        await self._emit_approval(state, permission)

    async def _emit_approval(self, state: SessionState, permission: Permission) -> None:
        call = state.tools.get(permission.call_id or "")
        await self._emit(
            state,
            normalize.approval_request(
                permission,
                name=call.name if call else None,
                raw_input=call.input if call else None,
                from_subagent=permission.session_id != state.root_id,
            ),
        )

    async def _on_permission_replied(
        self, state: SessionState, data: dict[str, Any]
    ) -> None:
        request_id = str(data.get("requestID"))
        if request_id not in state.replying:
            await self._drop_ask(state, request_id)
            return
        state.replying.discard(request_id)
        state.forget_ask(request_id)
        await self._emit(
            state,
            normalize.note(f"Permission {data.get('reply')}", "permission.replied"),
        )

    async def _on_form_created(self, state: SessionState, data: dict[str, Any]) -> None:
        payload = data.get("form")
        form = parse_form(payload) if isinstance(payload, dict) else None
        if form is None or form.id in state.forms:
            return
        if (emission := normalize.question(form)) is None:
            return
        state.add_form(form)
        await self._emit(state, emission)

    async def _drop_ask(
        self,
        state: SessionState,
        ask_id: str,
        status: SessionStatus | None = None,
    ) -> None:
        """Forget an ask that ended other than by Waypoint's reply, and close
        its card."""
        if ask_id in state.permissions:
            state.forget_ask(ask_id)
            await self._emit(state, normalize.approval_invalidated(ask_id), status)
        elif ask_id in state.forms:
            state.forget_ask(ask_id)
            await self._emit(
                state, normalize.question_closed(ask_id, answered=False), status
            )

    async def _list_asks(
        self, opencode_session_ids: Collection[str]
    ) -> tuple[list[Any], list[Any]] | None:
        """The pending permissions and forms of those sessions, or ``None`` when
        the server could not be asked."""
        client = self._require_client()
        permissions: list[Any] = []
        forms: list[Any] = []
        for session_id in sorted(opencode_session_ids):
            try:
                path = f"/api/session/{session_id}"
                permissions += (await client.get(f"{path}/permission")).get(
                    "data"
                ) or []
                forms += (await client.get(f"{path}/form")).get("data") or []
            except Exception as exc:  # noqa: BLE001
                # A session that is gone has nothing pending.
                if http_status(exc) != 404:
                    log.warning("failed to list opencode asks: %s", exc)
                    return None
        return permissions, forms

    async def _reconcile(self, state: SessionState) -> None:
        """Drop the asks the server has ended without saying so."""
        pending = [*state.permissions, *state.forms]
        asking = {ask.session_id for ask in state.permissions.values()}
        asking |= {form.session_id for form in state.forms.values()}
        if not pending or (listed := await self._list_asks(asking)) is None:
            return
        ids = {item.get("id") for group in listed for item in group}
        for ask_id in pending:
            if ask_id not in ids and ask_id not in state.replying:
                await self._drop_ask(state, ask_id)

    async def respond_to_permission(
        self,
        session_id: str,
        decision: str,
        text: str | None = None,
        approval_id: str | None = None,
    ) -> bool:
        state = self._sessions.get(session_id)
        if state is None or not state.permissions:
            return False
        reply = _normalize_decision(decision)
        permission = (
            state.permissions.get(approval_id)
            if approval_id
            else next(iter(state.permissions.values()))
        )
        if permission is None:
            return False
        if reply == "session":
            await self._grant(state, permission)
            reply = "once"

        body: dict[str, Any] = {"decision": reply}
        if text is not None:
            body["message"] = text
        state.replying.add(permission.id)
        try:
            await self._require_client().post(
                f"/api/session/{permission.session_id}/permission/{permission.id}/reply",
                json_data=body,
            )
        except Exception as exc:
            state.replying.discard(permission.id)
            if http_status(exc) == 404:
                await self._drop_ask(state, permission.id)
                return False
            raise OpenCodeError(f"failed to reply to the permission: {exc}") from exc
        # It stays in ``replying`` until its event, which then reads as
        # Waypoint's own.
        state.forget_ask(permission.id)
        return True

    async def _grant(self, state: SessionState, permission: Permission) -> None:
        """Allow what the request offers to save, for this session only."""
        rules = [
            {"action": permission.action, "resource": pattern, "effect": "allow"}
            for pattern in permission.save
        ]
        if not rules:
            return
        client = self._require_client()
        async with self._ruleset_lock:
            # The root too, for the subagents it starts, unless they get rules
            # of their own: then the root's are its agent's (plan mode), which
            # a subagent's approval must not widen. Read under the lock, which
            # a mode change holds while it sets them.
            targets = [permission.session_id]
            if state.subagent_permissions is None:
                targets.append(state.root_id)
            for target in dict.fromkeys(targets):
                try:
                    info = await self._session_info(target)
                    ruleset = list(info.get("permissions") or [])
                    if missing := [rule for rule in rules if rule not in ruleset]:
                        await client.patch(
                            f"/api/session/{target}",
                            json_data={"permissions": ruleset + missing},
                        )
                except Exception as exc:
                    raise OpenCodeError(
                        f"failed to allow this for the session: {exc}"
                    ) from exc
            # Subagents started later get these rules rather than the root's.
            if (subagent := state.subagent_permissions) is not None:
                state.subagent_permissions = subagent + [
                    rule for rule in rules if rule not in subagent
                ]

    async def answer_question(
        self,
        session_id: str,
        request_id: str,
        answer: str,
        answers: list[dict[str, Any]] | None = None,
    ) -> bool:
        state = self._sessions.get(session_id)
        if state is None or (form := state.forms.get(request_id)) is None:
            return False
        try:
            await self._require_client().post(
                f"/api/session/{form.session_id}/form/{form.id}/reply",
                json_data={"answer": form.reply(answer, answers)},
            )
        except Exception as exc:
            log.warning("opencode question reply failed: %s", exc)
            if http_status(exc) in (404, 409):
                await self._drop_ask(state, form.id)
            return False
        state.forget_ask(form.id)
        return True

    async def reject_question(
        self, session_id: str, request_id: str
    ) -> QuestionDecline:
        """Decline one question request, which ends the asking turn."""
        state = self._sessions.get(session_id)
        if state is None:
            return QuestionDecline.FAILED
        form = state.forms.get(request_id)
        origin = form.session_id if form else state.root_id
        try:
            await self._require_client().delete(
                f"/api/session/{origin}/form/{request_id}"
            )
        except Exception as exc:
            log.warning("opencode question reject failed: %s", exc)
            if http_status(exc) not in (404, 409):
                return QuestionDecline.FAILED
            state.forget_ask(request_id)
            return QuestionDecline.MISSING
        state.forget_ask(request_id)
        return QuestionDecline.TURN_ENDS

    def current_question_id(self, session_id: str) -> str | None:
        state = self._sessions.get(session_id)
        return next(iter(state.forms), None) if state else None

    def has_pending_approval(self, session_id: str) -> bool:
        state = self._sessions.get(session_id)
        return bool(state and state.permissions)

    def pending_approval_ids(self, session_id: str) -> tuple[str, ...]:
        state = self._sessions.get(session_id)
        return tuple(state.permissions) if state else ()

    def selected_model(self, session_id: str) -> tuple[str | None, str | None]:
        """The session's model and effort as the server holds them."""
        state = self._sessions.get(session_id)
        return (state.model, state.effort) if state else (None, None)

    def session_status(self, session_id: str) -> SessionStatus | None:
        state = self._sessions.get(session_id)
        return state.status() if state else None

    async def _seed_asks(
        self, state: SessionState
    ) -> tuple[list[Permission], list[Form]]:
        """Adopt the server's pending asks of the session's tree, returning the
        ones the stream had not delivered."""
        state.asks_seed_attempted_at = time.monotonic()
        mark = state.mark()
        permissions: list[Permission] = []
        forms: list[Form] = []
        # Only a running session has anything pending, and the root is asked
        # whether or not it is listed as running.
        listed = await self._list_asks({state.root_id, *state.running})
        if listed is None:
            return permissions, forms
        for item in listed[0]:
            permission = Permission.parse(item)
            if (
                permission is not None
                and state.owns(permission.session_id)
                and not state.touched_since(permission.id, mark)
                and permission.id not in state.permissions
            ):
                state.add_permission(permission)
                permissions.append(permission)
        for item in listed[1]:
            form = parse_form(item) if isinstance(item, dict) else None
            if (
                form is not None
                and state.owns(form.session_id)
                and not state.touched_since(form.id, mark)
                and form.id not in state.forms
                and form.questions()
            ):
                state.add_form(form)
                forms.append(form)
        state.asks_seeded = True
        return permissions, forms

    def question_liveness(
        self, session_id: str, request_ids: list[str]
    ) -> dict[str, QuestionLiveness]:
        state = self._sessions.get(session_id)
        if state is None:
            return dict.fromkeys(request_ids, QuestionLiveness.UNAVAILABLE)
        if not state.asks_seeded:
            # Questions the stream delivered since restore are known live; the
            # rest wait for a successful listing.
            self._schedule_ask_seed(state)
        untracked = (
            QuestionLiveness.CLOSED
            if state.asks_seeded
            else QuestionLiveness.UNAVAILABLE
        )
        return {
            request_id: (
                QuestionLiveness.ACTIONABLE if request_id in state.forms else untracked
            )
            for request_id in request_ids
        }

    def _schedule_ask_seed(self, state: SessionState) -> None:
        if state.session_id in self._ask_seed_tasks:
            return
        elapsed = time.monotonic() - state.asks_seed_attempted_at
        if elapsed < QUESTION_SEED_RETRY_SECONDS:
            return

        task = asyncio.create_task(self._reseed(state))
        self._ask_seed_tasks[state.session_id] = task
        task.add_done_callback(
            lambda _: self._ask_seed_tasks.pop(state.session_id, None)
        )

    async def _show_seeded(
        self,
        state: SessionState,
        permissions: list[Permission],
        forms: list[Form],
    ) -> bool:
        """Once the server's asks are listed, show those the transcript lacks and
        close the approval cards it no longer has. Returns whether anything was
        emitted."""
        if not state.asks_seeded:
            # A listing that failed says nothing about which cards are stale.
            return False
        shown, state.open_approvals = state.open_approvals, set()
        asked, state.open_questions = state.open_questions, set()
        emitted = False
        for permission in permissions:
            if permission.id not in shown:
                await self._emit_approval(state, permission)
                emitted = True
        for form in forms:
            if form.id not in asked and (card := normalize.question(form)):
                await self._emit(state, card)
                emitted = True
        for approval_id in sorted(shown):
            if approval_id not in state.permissions:
                await self._emit(state, normalize.approval_invalidated(approval_id))
                emitted = True
        return emitted

    async def _reseed(self, state: SessionState) -> None:
        """List the asks a restore could not."""
        if state.asks_seeded:
            return
        permissions, forms = await self._seed_asks(state)
        if not state.asks_seeded:
            return
        emitted = await self._show_seeded(state, permissions, forms)
        # The restore recorded a status without these.
        if not emitted and state.status() == SessionStatus.WAITING_INPUT:
            await self._emit(
                state,
                normalize.status_note(
                    SessionStatus.WAITING_INPUT, "session.asks.listed"
                ),
            )
        if self._on_questions_changed is not None:
            self._on_questions_changed(state.session_id)

    async def _repair_seed(self, state: SessionState) -> None:
        """Retry until the asks are listed: until then, the cards a restore left
        open cannot be answered."""
        while not state.asks_seeded:
            await asyncio.sleep(QUESTION_SEED_RETRY_SECONDS)
            if state.closing or self._sessions.get(state.session_id) is not state:
                return
            # Through the one seed task: two at once would repeat cards.
            self._schedule_ask_seed(state)
            if task := self._ask_seed_tasks.get(state.session_id):
                await asyncio.wait({task})

    # ── Session lifecycle ───────────────────────────────────────────────

    def _location(self) -> dict[str, str]:
        return {"location[directory]": self._directory} if self._directory else {}

    def _register_session(self, state: SessionState) -> None:
        self._sessions[state.session_id] = state
        self._routes[state.root_id] = state.session_id

    def _forget_session(self, state: SessionState) -> None:
        self._sessions.pop(state.session_id, None)
        for route, owner in list(self._routes.items()):
            if owner == state.session_id:
                del self._routes[route]

    async def _session_info(self, opencode_session_id: str) -> dict[str, Any]:
        data = await self._require_client().get(f"/api/session/{opencode_session_id}")
        info = data.get("data")
        if not isinstance(info, dict):
            raise OpenCodeError(f"opencode session {opencode_session_id} not found")
        return info

    async def start_session(
        self,
        session_id: str,
        *,
        title: str = "New Session",
        model: str | None = None,
        agent: str | None = None,
        effort: str | None = None,
        permissions: list[dict[str, str]] | None = None,
        subagent_permissions: list[dict[str, str]] | None = None,
    ) -> str:
        await self.start()
        body: dict[str, Any] = {
            "location": {"directory": self._directory},
            "title": title,
        }
        if agent:
            body["agent"] = agent
        if permissions:
            body["permissions"] = permissions
        if model and (ref := await self._model_selection(model, effort)):
            body["model"] = ref
        try:
            data = await self._require_client().post("/api/session", json_data=body)
            opencode_session_id = str(data["data"]["id"])
        except Exception as exc:
            log.error("failed to create session: %s", exc)
            raise OpenCodeError(f"failed to create session: {exc}") from exc

        state = SessionState(
            session_id=session_id,
            root_id=opencode_session_id,
            agent=agent,
            subagent_permissions=_differing(subagent_permissions, permissions),
        )
        self._adopt_model(state, body.get("model"))
        self._register_session(state)
        if effort and not model:
            # A variant travels only with a model.
            await self._select_model(state, await self.default_model_id(), effort)
        await self._emit(
            state,
            normalize.note(
                f"OpenCode session started ({opencode_session_id})", "session.created"
            ),
        )
        return opencode_session_id

    async def fork_session(
        self,
        session_id: str,
        source_id: str,
        permissions: list[dict[str, str]],
        subagent_permissions: list[dict[str, str]] | None = None,
    ) -> str:
        await self.start()
        client = self._require_client()
        try:
            data = await client.post(f"/api/session/{source_id}/fork", json_data={})
            info = data["data"]
            opencode_session_id = str(info["id"])
            async with self._ruleset_lock:
                await self._write_ruleset(opencode_session_id, permissions)
        except Exception as exc:
            log.error("failed to fork session: %s", exc)
            raise OpenCodeError(f"failed to fork session: {exc}") from exc

        state = SessionState(
            session_id=session_id,
            root_id=opencode_session_id,
            agent=info.get("agent"),
            subagent_permissions=_differing(subagent_permissions, permissions),
        )
        self._adopt_model(state, info.get("model"))
        self._register_session(state)
        return opencode_session_id

    async def restore_session(
        self,
        session_id: str,
        opencode_session_id: str,
        *,
        permissions: list[dict[str, str]],
        subagent_permissions: list[dict[str, str]] | None = None,
        model: str | None = None,
        agent: str | None = None,
        effort: str | None = None,
        open_approvals: Collection[str] = (),
        open_questions: Collection[str] = (),
    ) -> None:
        """Drive an existing OpenCode session. ``open_approvals`` and
        ``open_questions`` are the ids of the cards the transcript still shows.
        The caller records the restore note."""
        await self.start()
        try:
            info = await self._session_info(opencode_session_id)
        except Exception as exc:
            raise OpenCodeError(
                f"opencode session {opencode_session_id} not found on server: {exc}"
            ) from exc

        state = SessionState(
            session_id=session_id,
            root_id=opencode_session_id,
            agent=info.get("agent"),
            asks_seeded=False,
            open_approvals=set(open_approvals),
            open_questions=set(open_questions),
        )
        self._adopt_model(state, info.get("model"))
        self._register_session(state)
        try:
            await self._adopt_tree(state)
            adopted_permissions, adopted_forms = await self._seed_asks(state)
            # A migrated 1.x session has lost its rules.
            await self._write_rulesets(state, permissions, subagent_permissions)
            await self._restore_selection(state, model, agent, effort)
            await self._show_seeded(state, adopted_permissions, adopted_forms)
        except BaseException as exc:
            # Includes cancellation: a restore that timed out must not leave a
            # session behind for events to revive.
            self._forget_session(state)
            if isinstance(exc, Exception):
                raise OpenCodeError(f"failed to restore session: {exc}") from exc
            raise
        if not state.asks_seeded:
            self._spawn(self._repair_seed(state))

    async def _adopt_tree(self, state: SessionState) -> None:
        """Find the session's subagents, and which of its tree is running."""
        parents = [state.root_id]
        while parents:
            for child in await self._list("/api/session", {"parentID": parents.pop()}):
                child_id = child.get("id")
                if isinstance(child_id, str) and not state.owns(child_id):
                    state.children.add(child_id)
                    self._routes[child_id] = state.session_id
                    parents.append(child_id)
        mark = state.mark()
        active = await self._require_client().get("/api/session/active")
        state.seed_running(set(active.get("data") or {}), mark)

    async def _restore_selection(
        self,
        state: SessionState,
        model: str | None,
        agent: str | None,
        effort: str | None,
    ) -> None:
        """Apply what Waypoint recorded where the session differs; what it did
        not record is left to the session."""
        try:
            if (model and model != state.model) or (effort and effort != state.effort):
                await self._select_model(
                    state,
                    model or state.model or await self.default_model_id(),
                    effort or state.effort,
                )
            if agent and agent != state.agent:
                await self._select_agent(state, agent)
        except Exception as exc:  # noqa: BLE001
            # The session is usable; a model it cannot take must not lose it.
            await self._emit(
                state,
                normalize.note(
                    f"Could not restore the session's model or agent: {exc}",
                    "session.selection.failed",
                ),
            )

    async def terminate_session(self, session_id: str) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        # Flip `closing` before the mutations below so an in-flight event
        # short-circuits and cannot re-populate state being torn down.
        state.closing = True
        self._forget_session(state)
        kind, text, metadata = normalize.note(
            "OpenCode session terminated", "session.terminated"
        )
        await self._emit_event(
            session_id,
            kind,
            text,
            {**metadata, "status": SessionStatus.EXITED},
            SessionStatus.EXITED,
        )
        return True

    # ── Turns ───────────────────────────────────────────────────────────

    def _require_session(self, session_id: str) -> SessionState:
        state = self._sessions.get(session_id)
        if state is None:
            raise OpenCodeError(f"session not found: {session_id}")
        return state

    async def send_input(
        self,
        session_id: str,
        text: str,
        attachments: list[ResolvedAttachment] | None = None,
        skills: list[str] | None = None,
    ) -> None:
        state = self._require_session(session_id)
        body: dict[str, Any] = {"text": text}
        if files := _files(attachments):
            body["files"] = files
        if skills:
            body["skills"] = [{"id": skill} for skill in skills]
        try:
            # The reply only admits the prompt; results arrive as events.
            await self._require_client().post(
                f"/api/session/{state.root_id}/prompt",
                json_data=body,
                long_running=True,
            )
        except Exception as exc:
            raise OpenCodeError(f"failed to send message: {exc}") from exc
        state.input_sent()

    async def execute_command(
        self,
        session_id: str,
        command: str,
        arguments: str,
        attachments: list[ResolvedAttachment] | None = None,
    ) -> None:
        state = self._require_session(session_id)
        body: dict[str, Any] = {"name": command, "text": arguments}
        if files := _files(attachments):
            body["files"] = files
        try:
            await self._require_client().post(
                f"/api/session/{state.root_id}/command",
                json_data=body,
                long_running=True,
            )
        except Exception as exc:
            raise OpenCodeError(f"failed to execute command: {exc}") from exc
        state.input_sent()

    async def compact_session(self, session_id: str) -> None:
        state = self._require_session(session_id)
        try:
            await self._require_client().post(
                f"/api/session/{state.root_id}/compact", json_data={}
            )
        except Exception as exc:
            raise OpenCodeError(f"failed to compact session: {exc}") from exc
        state.input_sent()

    async def interrupt(self, session_id: str) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            return
        client = self._require_client()
        # Subagents run on their own: interrupting the root leaves them running.
        subagents = state.running - {state.root_id}
        state.interrupt_requested()
        for target in [*subagents, state.root_id]:
            try:
                await client.post(f"/api/session/{target}/interrupt")
            except Exception as exc:  # noqa: BLE001
                log.warning("opencode interrupt failed: %s", exc)
        await self._reconcile(state)

    # ── Model, agent, permissions ───────────────────────────────────────

    async def set_model(
        self, session_id: str, model: str | None, effort: str | None = None
    ) -> bool:
        """Select ``model``, keeping the recorded ``effort`` (else the
        session's) where the model has it."""
        state = self._sessions.get(session_id)
        if state is None:
            return False
        await self._select_model(
            state, model or await self.default_model_id(), effort or state.effort
        )
        return True

    async def set_effort(self, session_id: str, effort: str | None) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        model = state.model or await self.default_model_id()
        if effort and model:
            entry = await self._model(model)
            if entry is None or effort not in entry.variants:
                raise OpenCodeError(f"{model} has no reasoning effort {effort!r}")
        await self._select_model(state, model, effort)
        return True

    async def _model_selection(
        self, model: str, effort: str | None
    ) -> dict[str, str] | None:
        """The reference selecting ``model``, with ``effort`` when the model has
        that variant: OpenCode fails the turn, not the selection, for one it
        lacks."""
        entry = await self._model(model) if effort else None
        variant = effort if entry and effort in entry.variants else None
        return model_ref(model, variant)

    async def _select_model(
        self, state: SessionState, model: str | None, effort: str | None
    ) -> None:
        ref = await self._model_selection(model, effort) if model else None
        if ref is None:
            raise OpenCodeError(f"unknown model: {model}")
        try:
            await self._require_client().post(
                f"/api/session/{state.root_id}/model", json_data={"model": ref}
            )
        except Exception as exc:
            raise OpenCodeError(f"failed to select the model: {exc}") from exc
        self._adopt_model(state, ref)

    async def set_agent(self, session_id: str, agent: str) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        await self._select_agent(state, agent)
        return True

    async def _select_agent(self, state: SessionState, agent: str) -> None:
        # Recorded first, so the event reads as Waypoint's own switch.
        previous, state.agent = state.agent, agent
        try:
            await self._require_client().post(
                f"/api/session/{state.root_id}/agent", json_data={"agent": agent}
            )
        except Exception as exc:
            state.agent = previous
            raise OpenCodeError(f"failed to select the agent: {exc}") from exc

    def get_agent(self, session_id: str) -> str | None:
        state = self._sessions.get(session_id)
        return state.agent if state else None

    def set_pre_plan_mode(self, session_id: str, mode: str | None) -> None:
        if state := self._sessions.get(session_id):
            state.pre_plan_mode = mode

    def get_pre_plan_mode(self, session_id: str) -> str | None:
        state = self._sessions.get(session_id)
        return state.pre_plan_mode if state is not None else None

    async def _write_ruleset(
        self, opencode_session_id: str, permissions: list[dict[str, str]]
    ) -> None:
        await self._require_client().patch(
            f"/api/session/{opencode_session_id}",
            json_data={"permissions": permissions},
        )

    async def _write_rulesets(
        self,
        state: SessionState,
        permissions: list[dict[str, str]],
        subagent_permissions: list[dict[str, str]] | None = None,
    ) -> None:
        """Replace the rules of the session and of its subagents, which hold
        their own copy and can be resumed. Only the session's own write can
        fail the change."""
        if subagent_permissions is None:
            subagent_permissions = permissions
        async with self._ruleset_lock:
            await self._write_ruleset(state.root_id, permissions)
            # Before the subagents: one started meanwhile is written too.
            state.subagent_permissions = _differing(subagent_permissions, permissions)
        await self._write_subagent_rules(
            state, sorted(state.children), subagent_permissions
        )

    async def _write_subagent_rules(
        self,
        state: SessionState,
        children: list[str],
        permissions: list[dict[str, str]] | None = None,
    ) -> None:
        """Write ``permissions``, by default the session's subagent rules as
        they stand once the lock is held, so a grant in flight is not lost."""
        for child in children:
            try:
                async with self._ruleset_lock:
                    rules = (
                        state.subagent_permissions
                        if permissions is None
                        else permissions
                    )
                    if rules is None:
                        continue
                    await self._write_ruleset(child, rules)
            except Exception as exc:  # noqa: BLE001
                # A subagent that is gone takes no more tools.
                if http_status(exc) == 404:
                    continue
                log.warning("failed to set subagent %s permissions: %s", child, exc)
                await self._emit(
                    state,
                    normalize.note(
                        f"Could not apply the permission mode to subagent {child}: "
                        f"{exc}",
                        "session.subagent.permissions.failed",
                    ),
                )

    async def set_session_permission(
        self,
        session_id: str,
        permissions: list[dict[str, str]],
        subagent_permissions: list[dict[str, str]] | None = None,
    ) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        try:
            await self._write_rulesets(state, permissions, subagent_permissions)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to set opencode permissions: %s", exc)
            return False
        return True

    # ── Catalogs and threads ────────────────────────────────────────────

    async def _settled_list(self, path: str) -> list[Any]:
        """A catalog listing, once the server has loaded it."""
        client = self._require_client()
        deadline = time.monotonic() + CATALOG_SETTLE_SECONDS
        while True:
            data = (await client.get(path, params=self._location())).get("data")
            if data or time.monotonic() >= deadline:
                return data or []
            await asyncio.sleep(0.5)

    async def list_models(self, *, include_deprecated: bool = False) -> list[Model]:
        await self.start()
        models = parse_models(
            await self._settled_list("/api/model"),
            include_deprecated=include_deprecated,
        )
        if models:
            self._models = models
        return models

    async def provider_names(self) -> dict[str, str]:
        await self.start()
        data = await self._require_client().get(
            "/api/provider", params=self._location()
        )
        return {
            str(item["id"]): str(item.get("name") or item["id"])
            for item in data.get("data") or []
            if isinstance(item, dict) and item.get("id")
        }

    async def default_model_id(self) -> str | None:
        await self.start()
        models = self._models or await self.list_models()
        data = await self._require_client().get(
            "/api/model/default", params=self._location()
        )
        entry = data.get("data")
        if isinstance(entry, dict):
            ref = {"providerID": entry.get("providerID"), "id": entry.get("id")}
            if default := model_id(ref):
                return default
        return models[0].id if models else None

    def _cached_model(self, model: str) -> Model | None:
        return next((item for item in self._models if item.id == model), None)

    async def _model(self, model: str) -> Model | None:
        entry = self._cached_model(model)
        if entry is not None:
            return entry
        failed_at = self._models_failed_at
        if failed_at and time.monotonic() - failed_at < CATALOG_RETRY_SECONDS:
            return None
        try:
            await self.list_models()
        except Exception as exc:  # noqa: BLE001
            log.debug("failed to list opencode models: %s", exc)
        entry = self._cached_model(model)
        self._models_failed_at = None if entry else time.monotonic()
        return entry

    async def list_commands(self) -> list[dict[str, Any]]:
        """The server's slash commands and skills, each with its ``source``."""
        await self.start()
        try:
            commands = await self._settled_list("/api/command")
            skills = await self._require_client().get(
                "/api/skill", params=self._location()
            )
        except Exception as exc:
            raise OpenCodeError(f"failed to list commands: {exc}") from exc
        # A skill is invoked by its id.
        return [
            {
                "name": item.get(key),
                "description": item.get("description"),
                "source": source,
            }
            for source, key, items in (
                ("command", "name", commands),
                ("skill", "id", skills.get("data") or []),
            )
            for item in items
            if isinstance(item, dict)
        ]

    async def _list(
        self,
        path: str,
        params: dict[str, str] | None = None,
        limit: int | None = _LIST_LIMIT,
    ) -> list[dict[str, Any]]:
        """A paginated listing, up to ``limit`` entries."""
        client = self._require_client()
        query = {"limit": str(_LIST_PAGE), **(params or {})}
        items: list[dict[str, Any]] = []
        while True:
            page = await client.get(path, params=query)
            data = page.get("data") or []
            items.extend(data)
            cursor = (page.get("cursor") or {}).get("next")
            if not data or not cursor or (limit and len(items) >= limit):
                return items[:limit]
            query = {"limit": str(_LIST_PAGE), "cursor": cursor}

    async def list_sessions(self) -> list[dict[str, Any]]:
        """The server's sessions, newest first, without subagents."""
        await self.start()
        try:
            sessions = await self._list("/api/session", {"order": "desc"})
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to list opencode sessions: %s", exc)
            return []
        return [item for item in sessions if not item.get("parentID")]

    async def get_session(self, opencode_session_id: str) -> dict[str, Any] | None:
        await self.start()
        try:
            return await self._session_info(_path_id(opencode_session_id))
        except Exception:  # noqa: BLE001
            return None

    async def get_session_messages(
        self, opencode_session_id: str
    ) -> list[dict[str, Any]]:
        """The full message history of a session, oldest first.

        Unlike ``get_session``/``list_sessions``, failures are raised:
        ``runtime.seed_thread_history``'s reader contract expects a raise to
        signal "history unavailable" so it can degrade to a plain resume with a
        system note instead of seeding an empty transcript.
        """
        await self.start()
        try:
            return await self._list(
                f"/api/session/{_path_id(opencode_session_id)}/message",
                {"order": "asc"},
                limit=None,
            )
        except Exception as exc:
            raise OpenCodeError(f"failed to fetch session messages: {exc}") from exc

    async def delete_session(self, opencode_session_id: str) -> bool:
        await self.start()
        try:
            await self._require_client().delete(
                f"/api/session/{_path_id(opencode_session_id)}"
            )
        except Exception:  # noqa: BLE001
            return False
        return True


def _differing(
    subagent: list[dict[str, str]] | None, root: list[dict[str, str]] | None
) -> list[dict[str, str]] | None:
    """Subagent rules worth writing: ``None`` when a subagent's copy of the
    root's would do."""
    return None if subagent is None or subagent == (root or []) else subagent


def _decode_sse_payload(lines: list[str]) -> dict[str, Any] | None:
    if not lines:
        return None
    try:
        payload = json.loads("\n".join(lines))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _part(data: dict[str, Any], kind: str) -> tuple[str, int, bool]:
    """The message, ordinal and kind of the text part an event is about."""
    return (
        str(data.get("assistantMessageID")),
        data.get("ordinal") or 0,
        kind.startswith("session.reasoning"),
    )


def _path_id(opencode_session_id: str) -> str:
    """An id from outside the server, as one path segment."""
    return urllib.parse.quote(opencode_session_id, safe="")


def _files(attachments: list[ResolvedAttachment] | None) -> list[dict[str, str]]:
    # Inline data URLs rather than host paths: the server may run on a remote
    # host that can't see the local attachments directory.
    return [
        {"uri": attachment.to_data_url(), "name": attachment.spec.filename}
        for attachment in attachments or []
    ]


def _reason(data: dict[str, Any]) -> str:
    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("type") or "unknown error")
    return str(error or "unknown error")


def _token_counts(tokens: dict[str, Any]) -> dict[str, int]:
    """Waypoint's token totals. OpenCode's ``output`` excludes reasoning, which
    Waypoint's includes."""
    cache = tokens.get("cache")
    if not isinstance(cache, dict):
        cache = {}
    counts = {
        key: value
        for key, value in {
            "input_tokens": _non_negative_int(tokens.get("input")),
            "output_tokens": _non_negative_int(tokens.get("output")),
            "reasoning_tokens": _non_negative_int(tokens.get("reasoning")),
            "cache_read_tokens": _non_negative_int(cache.get("read")),
            "cache_write_tokens": _non_negative_int(cache.get("write")),
        }.items()
        if value is not None
    }
    if "output_tokens" in counts:
        counts["output_tokens"] += counts.get("reasoning_tokens", 0)
    return counts


def _context_usage_snapshot(
    tokens: dict[str, Any], context_window_tokens: int
) -> SessionContextUsage | None:
    counts = _token_counts(tokens)
    used = sum(
        counts.get(key, 0)
        for key in ("input_tokens", "cache_read_tokens", "cache_write_tokens")
    )
    if used <= 0:
        return None
    return SessionContextUsage(
        used_tokens=used,
        context_window_tokens=context_window_tokens,
        updated_at=datetime.now(UTC),
        source="opencode",
        breakdown=counts,
    )


def _token_usage_record(
    record_id: str,
    tokens: dict[str, Any],
    *,
    model: str | None = None,
    effort: str | None = None,
) -> TokenUsageRecord:
    """A step's ledger record. ``model`` and ``effort`` are those in effect
    for the step, when known."""
    return TokenUsageRecord(
        record_id=record_id,
        source="opencode",
        observed_at=datetime.now(UTC),
        totals=_token_counts(tokens),
        display_total_tokens=None,
        model=model,
        effort=effort,
    )


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float) and value.is_integer():
        return max(0, int(value))
    return None
