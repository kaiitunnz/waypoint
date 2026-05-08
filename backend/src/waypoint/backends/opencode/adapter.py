import asyncio
import json
import logging
import os
import re
import shutil
import signal
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from waypoint.backends.opencode.client import (
    LocalOpenCodeClient,
    OpenCodeHttpClient,
    RemoteOpenCodeClient,
)
from waypoint.backends.opencode.normalize import map_event
from waypoint.backends.opencode.remote import build_remote_serve_args
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import EventKind, SessionStatus

log = logging.getLogger("waypoint.opencode")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 0
DEFAULT_TIMEOUT_SECONDS = 60.0

PERMISSION_REPLIES = {"once", "always", "reject"}
# OpenCode's reply schema is strictly {once|always|reject}. Waypoint and the
# frontend speak a richer alias vocabulary (e.g. capability descriptors
# advertise camelCase decisions). Normalizing through this single map keeps
# the translation explicit instead of relying on case-folding rescues.
_DECISION_ALIASES: dict[str, str] = {
    "once": "once",
    "always": "always",
    "reject": "reject",
    "approve": "once",
    "accept": "once",
    "acceptforsession": "always",
    "yes": "once",
    "decline": "reject",
    "deny": "reject",
    "cancel": "reject",
    "no": "reject",
}
_MAX_SSE_FAILURES = 10


def _normalize_decision(decision: str) -> str:
    key = decision.strip().lower().replace("-", "").replace("_", "")
    reply = _DECISION_ALIASES.get(key)
    if reply is None:
        raise OpenCodeError(f"unsupported permission decision: {decision}")
    return reply


EmitEvent = Callable[
    [str, EventKind, str, dict[str, Any], SessionStatus],
    Any,
]


class OpenCodeError(RuntimeError):
    pass


_LISTENING_LINE = re.compile(r"listening on http://[^:\s]+:(\d+)")


@dataclass
class OpenCodeSessionState:
    session_id: str
    cwd: str
    opencode_session_id: str
    pending_permission_ids: list[str] = field(default_factory=list)
    pending_question_ids: list[str] = field(default_factory=list)
    # partID -> part type (text|reasoning|tool|step-start|step-finish).
    # Populated on message.part.updated *-start, consulted to tag
    # message.part.delta events whose payload only carries field="text".
    part_types: dict[str, str] = field(default_factory=dict)
    model: str | None = None
    agent: str | None = None
    effort: str | None = None
    pre_plan_mode: str | None = None
    closing: bool = False


AgentChangedCallback = Callable[[str, str | None, str | None], Any]
ServerDiedCallback = Callable[[list[str]], Any]


class OpenCodeAdapter:
    def __init__(
        self,
        emit_event: EmitEvent,
        binary: str | None = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        launch_target: SshLaunchTargetConfig | None = None,
        on_agent_changed: AgentChangedCallback | None = None,
        on_server_died: ServerDiedCallback | None = None,
        workdir: str | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        self._emit_event = emit_event
        self._binary = binary
        self._host = host
        self._port = port
        self._launch_target = launch_target
        self._on_agent_changed = on_agent_changed
        self._on_server_died_callback = on_server_died
        self._workdir = workdir
        self._extra_args = extra_args
        self._sessions: dict[str, OpenCodeSessionState] = {}
        self._remote_sessions: dict[str, str] = {}
        self._part_sessions: dict[str, str] = {}

        self._server_process: asyncio.subprocess.Process | None = None
        self._sse_task: asyncio.Task[None] | None = None
        self._client: OpenCodeHttpClient | None = None
        self._started = False
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._started:
            return
        async with self._start_lock:
            if self._started:
                return
            if self._launch_target is not None:
                await self._start_remote()
            else:
                await self._start_local()
            self._started = True
            self._sse_task = asyncio.create_task(self._listen_events())
            log.info("opencode server started successfully")

    async def _start_local(self) -> None:
        binary = self._binary or shutil.which("opencode")
        if binary is None:
            raise OpenCodeError("opencode binary not found on PATH")
        cwd = str(Path(self._workdir).expanduser()) if self._workdir else None

        log.info("starting local opencode server on %s:%d", self._host, self._port)
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
        self._client = LocalOpenCodeClient(f"http://{self._host}:{self._port}")
        try:
            await self._wait_for_server()
        except Exception as exc:
            log.error("failed to start local opencode server: %s", exc)
            await self._client.close()
            self._client = None
            self._terminate_server_process()
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
            self._launch_target, binary, self._workdir, self._extra_args
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

        # Wait for the port sentinel
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
                if line.startswith("__WP_PORT__="):
                    remote_port = int(line.split("=")[1])
                    break
            except TimeoutError:
                continue

        if remote_port is None:
            self._terminate_server_process()
            raise OpenCodeError("timeout waiting for remote opencode server port")

        log.info("remote opencode bound to port %d", remote_port)
        self._client = RemoteOpenCodeClient(
            self._launch_target, remote_port, self._workdir
        )
        try:
            await self._wait_for_server()
        except Exception as exc:
            log.error("failed to start remote opencode server: %s", exc)
            await self._client.close()
            self._client = None
            self._terminate_server_process()
            raise OpenCodeError(
                f"failed to start remote opencode server: {exc}"
            ) from exc

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
            if self._launch_target is not None:
                # SSH child got its own process group via start_new_session.
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGKILL)
            else:
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGKILL)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=2.0)

    async def _wait_for_server(self) -> None:
        client = self._require_client()
        deadline = time.monotonic() + 30
        while True:
            if time.monotonic() >= deadline:
                raise OpenCodeError("timeout waiting for opencode server")
            try:
                await client.get("/config")
                return
            except Exception:
                pass
            await asyncio.sleep(0.5)

    def _require_client(self) -> OpenCodeHttpClient:
        if self._client is None:
            raise OpenCodeError("opencode http client not initialized")
        return self._client

    async def _on_server_died(self) -> None:
        active_sessions = [
            state for state in self._sessions.values() if not state.closing
        ]
        # Snapshot ids before we clear `_sessions` so the plugin's
        # auto-reconnect loop knows which sessions to restore once the
        # remote comes back.
        active_session_ids = [state.session_id for state in active_sessions]

        for state in active_sessions:
            try:
                await self._emit_event(
                    state.session_id,
                    EventKind.SYSTEM_NOTE,
                    "OpenCode server disconnected — connection lost",
                    {"status": SessionStatus.ERROR},
                    SessionStatus.ERROR,
                )
            except Exception:
                log.exception(
                    "failed to emit error event for session %s", state.session_id
                )

        self._sessions.clear()
        self._remote_sessions.clear()
        self._part_sessions.clear()

        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                log.exception("error closing opencode http client on server death")
            self._client = None

        self._terminate_server_process()
        self._server_process = None
        self._started = False
        log.info("opencode adapter reset after server death; ready for restart")

        if self._on_server_died_callback is not None:
            try:
                result = self._on_server_died_callback(active_session_ids)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("on_server_died callback failed")

    async def _listen_events(self) -> None:
        # OpenCode's /event stream does not support resume: events are emitted
        # with `id: undefined`, so a reconnect cannot pick up where it left
        # off. That means any mid-session disconnect is unrecoverable — events
        # generated while the connection was down would be silently dropped.
        # The retry budget below is therefore only spent on *pre-first-event*
        # failures (server still warming up). Once we've delivered at least
        # one event, any error or clean EOF promotes straight to
        # `_on_server_died`, which surfaces the disconnect to the user
        # instead of pretending the transcript is still live.
        consecutive_failures = 0
        delivered_any = False
        try:
            while True:
                try:
                    client = self._require_client()
                    buffer: list[str] = []
                    async for raw_line in client.stream_events("/event"):
                        line = raw_line.rstrip("\r\n")
                        if not line:
                            payload = self._decode_sse_payload(buffer)
                            buffer.clear()
                            if payload is not None:
                                await self._dispatch_event(payload)
                                delivered_any = True
                            continue
                        if line.startswith(":"):
                            continue
                        if line.startswith("data:"):
                            buffer.append(line[5:].lstrip())

                    # Handle last bit of decoder state on clean EOF.
                    payload = self._decode_sse_payload(buffer)
                    if payload is not None:
                        await self._dispatch_event(payload)
                        delivered_any = True

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

    def _decode_sse_payload(self, lines: list[str]) -> dict[str, Any] | None:
        if not lines:
            return None
        try:
            payload = json.loads("\n".join(lines))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    async def _dispatch_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        properties = event.get("properties", {})
        if not isinstance(properties, dict):
            return
        state = self._resolve_state_for_event(event_type, properties)
        if state is None or state.closing:
            # Short-circuit on `closing` so a late SSE event can't re-add
            # entries to `_part_sessions` after `terminate_session` cleared
            # them but before its emit_event await returned.
            return
        session_id = state.session_id
        if "sessionID" not in properties:
            properties = {**properties, "sessionID": state.opencode_session_id}
        self._update_pending_state(state, event_type, properties)
        properties = self._tag_part_type(state, event_type, properties)

        kind, text, metadata = map_event(event_type, properties)
        if not text and not metadata.get("payload"):
            return

        if event_type == "question.asked":
            for q in properties.get("questions", []):
                q_text = q.get("question", "")
                if q_text.startswith("Plan at ") and " is complete." in q_text:
                    path = q_text[len("Plan at ") : q_text.find(" is complete.")]
                    try:
                        client = self._require_client()
                        resp = await client.get("/file/content", params={"path": path})
                        content = resp.get("content")
                        if content:
                            await self._emit_event(
                                session_id,
                                EventKind.AGENT_OUTPUT,
                                f"## Plan\n{content}",
                                {"status": SessionStatus.RUNNING},
                                SessionStatus.RUNNING,
                            )
                    except Exception as exc:
                        log.warning("failed to fetch plan file content: %s", exc)

        await self._emit_event(
            session_id,
            kind,
            text,
            metadata,
            metadata.get("status", SessionStatus.RUNNING),
        )

    def _resolve_state_for_event(
        self,
        event_type: str | None,
        properties: dict[str, Any],
    ) -> OpenCodeSessionState | None:
        remote_session_id = self._extract_session_id(properties)
        if remote_session_id:
            session_id = self._remote_sessions.get(remote_session_id)
            if session_id:
                return self._sessions.get(session_id)

        # SSE deltas may arrive keyed only by partID while the stream stays
        # open; fall back to the last known part->session mapping so output
        # still routes to the right transcript.
        if event_type and event_type.startswith("message.part."):
            part_id = properties.get("partID")
            if isinstance(part_id, str):
                session_id = self._part_sessions.get(part_id)
                if session_id:
                    return self._sessions.get(session_id)
        return None

    def _extract_session_id(self, properties: Any) -> str | None:
        # Only consult the known carriers OpenCode uses, in priority order.
        # Recursing through arbitrary nested dicts/lists would let a stray
        # field (e.g. an unrelated `sessionID` inside a tool's `metadata`)
        # mis-route an event.
        if not isinstance(properties, dict):
            return None
        direct = properties.get("sessionID")
        if isinstance(direct, str) and direct:
            return direct
        for key in ("info", "part"):
            nested = properties.get(key)
            if isinstance(nested, dict):
                nested_id = nested.get("sessionID")
                if isinstance(nested_id, str) and nested_id:
                    return nested_id
        return None

    def _update_pending_state(
        self,
        state: OpenCodeSessionState,
        event_type: str | None,
        properties: dict[str, Any],
    ) -> None:
        if event_type == "message.updated":
            info = properties.get("info")
            if isinstance(info, dict):
                agent = info.get("agent")
                if isinstance(agent, str) and agent != state.agent:
                    old_agent = state.agent
                    state.agent = agent
                    if self._on_agent_changed:
                        self._on_agent_changed(state.session_id, old_agent, agent)
            return

        if event_type == "permission.asked":
            permission_id = properties.get("id")
            if (
                isinstance(permission_id, str)
                and permission_id
                and permission_id not in state.pending_permission_ids
            ):
                state.pending_permission_ids.append(permission_id)
            return

        if event_type == "permission.replied":
            permission_id = properties.get("requestID")
            if isinstance(permission_id, str):
                state.pending_permission_ids = [
                    item
                    for item in state.pending_permission_ids
                    if item != permission_id
                ]
            return

        if event_type == "question.asked":
            request_id = properties.get("id")
            if (
                isinstance(request_id, str)
                and request_id
                and request_id not in state.pending_question_ids
            ):
                state.pending_question_ids.append(request_id)
            return

        if event_type in {"question.replied", "question.rejected"}:
            request_id = properties.get("requestID")
            if isinstance(request_id, str):
                state.pending_question_ids = [
                    item for item in state.pending_question_ids if item != request_id
                ]

    def _tag_part_type(
        self,
        state: OpenCodeSessionState,
        event_type: str | None,
        properties: dict[str, Any],
    ) -> dict[str, Any]:
        # message.part.updated arrives on each *-start before any deltas, so
        # by the time deltas land we know whether the part is reasoning or
        # text. message.part.delta only carries field="text" for both, so we
        # decorate it with the recorded type for downstream rendering.
        if event_type == "message.part.updated":
            part = properties.get("part")
            if isinstance(part, dict):
                part_id = part.get("id")
                part_type = part.get("type")
                if isinstance(part_id, str) and isinstance(part_type, str):
                    state.part_types[part_id] = part_type
                    self._part_sessions[part_id] = state.session_id
            return properties
        if event_type == "message.part.delta":
            part_id = properties.get("partID")
            if isinstance(part_id, str):
                part_type = state.part_types.get(part_id)
                if part_type:
                    return {**properties, "_waypoint_part_type": part_type}
        return properties

    def _register_session(self, state: OpenCodeSessionState) -> None:
        self._sessions[state.session_id] = state
        self._remote_sessions[state.opencode_session_id] = state.session_id

    async def start_session(
        self,
        session_id: str,
        cwd: str,
        model: str | None = None,
        agent: str | None = None,
        effort: str | None = None,
        title: str = "New Session",
        permission: list[dict[str, str]] | None = None,
    ) -> str:
        if not self._started:
            await self.start()

        real_session_id = await self._create_session(cwd, title, permission)
        state = OpenCodeSessionState(
            session_id=session_id,
            cwd=cwd,
            opencode_session_id=real_session_id,
            model=model,
            agent=agent,
            effort=effort,
        )
        self._register_session(state)
        await self._emit_event(
            session_id,
            EventKind.SYSTEM_NOTE,
            f"OpenCode session started ({real_session_id})",
            {"status": SessionStatus.IDLE},
            SessionStatus.IDLE,
        )
        return real_session_id

    async def _create_session(
        self,
        directory: str,
        title: str,
        permission: list[dict[str, str]] | None = None,
    ) -> str:
        client = self._require_client()
        payload: dict[str, Any] = {"title": title}
        if permission:
            payload["permission"] = permission

        try:
            data = await client.post(
                "/session",
                json_data=payload,
                params={"directory": directory} if directory else None,
            )
        except Exception as exc:
            log.error("failed to create session: %s", exc)
            raise OpenCodeError(f"failed to create session: {exc}") from exc

        session_id = data.get("id", "")
        if not session_id:
            log.error("invalid session ID returned: %s", data)
            raise OpenCodeError(f"invalid session ID returned: {session_id}")
        return session_id

    async def fork_session(
        self,
        session_id: str,
        cwd: str,
        opencode_session_id: str,
        model: str | None = None,
        agent: str | None = None,
        effort: str | None = None,
    ) -> str:
        if not self._started:
            await self.start()

        client = self._require_client()
        try:
            data = await client.post(
                f"/session/{opencode_session_id}/fork", json_data={}
            )
        except Exception as exc:
            log.error("failed to fork session: %s", exc)
            raise OpenCodeError(f"failed to fork session: {exc}") from exc

        new_real_session_id = data.get("id", "")
        if not new_real_session_id:
            log.error("invalid session ID returned on fork: %s", data)
            raise OpenCodeError(
                f"invalid session ID returned on fork: {new_real_session_id}"
            )

        state = OpenCodeSessionState(
            session_id=session_id,
            cwd=cwd,
            opencode_session_id=new_real_session_id,
            model=model,
            agent=agent,
            effort=effort,
        )
        self._register_session(state)
        return new_real_session_id

    async def restore_session(
        self,
        session_id: str,
        cwd: str,
        opencode_session_id: str,
        model: str | None = None,
        agent: str | None = None,
        effort: str | None = None,
    ) -> None:
        if not self._started:
            await self.start()

        remote = await self.get_session(opencode_session_id)
        if remote is None:
            raise OpenCodeError(
                f"opencode session {opencode_session_id} not found on server"
            )

        state = OpenCodeSessionState(
            session_id=session_id,
            cwd=cwd,
            opencode_session_id=opencode_session_id,
            model=model,
            agent=agent,
            effort=effort,
        )
        self._register_session(state)
        # The plugin records the user-facing restore/import note; the adapter
        # stays silent here so the transcript only shows one entry.

    async def send_input(self, session_id: str, text: str) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            raise OpenCodeError(f"session not found: {session_id}")
        model = self._split_model_ref(state.model)
        client = self._require_client()
        payload: dict[str, Any] = {
            "parts": [
                {
                    "type": "text",
                    "text": text,
                }
            ]
        }
        if model is not None:
            payload["model"] = model
        if state.effort:
            payload["variant"] = state.effort
        if state.agent:
            payload["agent"] = state.agent

        try:
            # `prompt_async` returns once the server has accepted the work —
            # results stream back via SSE. The accept itself is cheap, but
            # mark long_running so a slow-link burst of work tokens past
            # the control timeout doesn't cause us to spuriously kill it.
            await client.post(
                f"/session/{state.opencode_session_id}/prompt_async",
                json_data=payload,
                long_running=True,
            )
        except Exception as exc:
            raise OpenCodeError(f"failed to send message: {exc}") from exc

    async def list_commands(self, session_id: str) -> list[dict[str, Any]]:
        if not self._started:
            await self.start()
        if session_id not in self._sessions:
            raise OpenCodeError(f"session not found: {session_id}")
        client = self._require_client()
        try:
            data = await client.get("/command")
        except Exception as exc:
            raise OpenCodeError(f"failed to list commands: {exc}") from exc
        return data if isinstance(data, list) else []

    async def execute_command(
        self,
        session_id: str,
        command: str,
        arguments: str,
    ) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            raise OpenCodeError(f"session not found: {session_id}")
        client = self._require_client()
        payload: dict[str, Any] = {
            "command": command,
            "arguments": arguments,
        }
        if state.model:
            payload["model"] = state.model
        if state.agent:
            payload["agent"] = state.agent
        if state.effort:
            payload["variant"] = state.effort
        try:
            await client.post(
                f"/session/{state.opencode_session_id}/command",
                json_data=payload,
                long_running=True,
            )
        except Exception as exc:
            raise OpenCodeError(f"failed to execute command: {exc}") from exc

    def _split_model_ref(self, model: str | None) -> dict[str, str] | None:
        if not model or "/" not in model:
            return None
        provider_id, model_id = model.split("/", 1)
        if not provider_id or not model_id:
            return None
        return {"providerID": provider_id, "modelID": model_id}

    async def interrupt(self, session_id: str) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            return
        client = self._require_client()
        try:
            await client.post(f"/session/{state.opencode_session_id}/abort")
        except Exception:
            pass

    async def respond_to_permission(
        self,
        session_id: str,
        decision: str,
        text: str | None = None,
        approval_id: str | None = None,
    ) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        if not state.pending_permission_ids:
            return False
        reply = self._map_decision_to_reply(decision)

        if approval_id:
            if approval_id not in state.pending_permission_ids:
                return False
            permission_id = approval_id
        else:
            permission_id = state.pending_permission_ids[0]

        client = self._require_client()
        try:
            payload: dict[str, Any] = {"reply": reply}
            if text is not None:
                payload["message"] = text
            await client.post(f"/permission/{permission_id}/reply", json_data=payload)
        except Exception:
            return False
        state.pending_permission_ids.remove(permission_id)
        return True

    def _map_decision_to_reply(self, decision: str) -> str:
        return _normalize_decision(decision)

    async def set_model(self, session_id: str, model: str) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        state.model = model
        return True

    async def set_effort(self, session_id: str, effort: str | None) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        state.effort = effort
        return True

    async def set_agent(self, session_id: str, agent: str | None) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        state.agent = agent
        return True

    async def set_pre_plan_mode(self, session_id: str, mode: str | None) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        state.pre_plan_mode = mode
        return True

    def get_pre_plan_mode(self, session_id: str) -> str | None:
        state = self._sessions.get(session_id)
        return state.pre_plan_mode if state is not None else None

    async def set_session_permission(
        self, session_id: str, permission: list[dict[str, str]]
    ) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        client = self._require_client()
        try:
            await client.patch(
                f"/session/{state.opencode_session_id}",
                json_data={"permission": permission},
            )
            return True
        except Exception:
            return False

    async def compact_session(self, session_id: str) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            raise OpenCodeError(f"session not found: {session_id}")
        model = self._split_model_ref(state.model)
        if model is None:
            # Sessions created without an explicit model still have a model
            # attached server-side; re-fetch and use whatever it reports.
            sess = await self.get_session(state.opencode_session_id)
            if isinstance(sess, dict):
                sess_model = sess.get("model")
                if isinstance(sess_model, str):
                    model = self._split_model_ref(sess_model)
        if model is None:
            raise OpenCodeError("unable to resolve model for compaction")
        payload = {
            "providerID": model["providerID"],
            "modelID": model["modelID"],
            "auto": False,
        }
        client = self._require_client()
        try:
            # Summarization can take minutes on large transcripts; let
            # SSH/TCP keepalive be the only liveness signal.
            await client.post(
                f"/session/{state.opencode_session_id}/summarize",
                json_data=payload,
                long_running=True,
            )
        except Exception as exc:
            raise OpenCodeError(f"failed to compact session: {exc}") from exc

    async def list_sessions(self, directory: str | None = None) -> list[dict[str, Any]]:
        if not self._started:
            await self.start()
        client = self._require_client()
        params = {"directory": directory} if directory else None
        try:
            return await client.get("/session", params=params)
        except Exception:
            return []

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        client = self._require_client()
        try:
            return await client.get(f"/session/{session_id}")
        except Exception:
            return None

    async def list_providers(self) -> dict[str, Any]:
        if not self._started:
            await self.start()
        client = self._require_client()
        try:
            return await client.get("/provider")
        except Exception:
            return {"all": [], "default": {}, "connected": []}

    async def list_questions(self) -> list[dict[str, Any]]:
        client = self._require_client()
        try:
            return await client.get("/question")
        except Exception:
            return []

    async def answer_question(
        self,
        session_id: str,
        request_id: str,
        answers: list[list[str]],
    ) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        client = self._require_client()
        try:
            await client.post(
                f"/question/{request_id}/reply",
                json_data={"answers": answers},
            )
        except Exception:
            return False
        state.pending_question_ids = [
            item for item in state.pending_question_ids if item != request_id
        ]
        return True

    def current_question_id(self, session_id: str) -> str | None:
        state = self._sessions.get(session_id)
        if state is None or not state.pending_question_ids:
            return None
        return state.pending_question_ids[0]

    async def terminate_session(self, session_id: str) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        # Flip `closing` before any of the mutations below so an in-flight
        # `_dispatch_event` short-circuits and cannot re-populate state
        # while we're tearing it down.
        state.closing = True
        self._sessions.pop(session_id, None)
        self._remote_sessions.pop(state.opencode_session_id, None)
        state.pending_permission_ids.clear()
        state.pending_question_ids.clear()
        for part_id, owner in list(self._part_sessions.items()):
            if owner == session_id:
                self._part_sessions.pop(part_id, None)
        await self._emit_event(
            session_id,
            EventKind.SYSTEM_NOTE,
            "OpenCode session terminated",
            {"status": SessionStatus.EXITED},
            SessionStatus.EXITED,
        )
        return True

    async def shutdown(self) -> None:
        if self._sse_task is not None:
            self._sse_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._sse_task
            self._sse_task = None
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._server_process is not None:
            self._terminate_server_process()
            await self._await_server_process_exit()
            self._server_process = None
        self._sessions.clear()
        self._remote_sessions.clear()
        self._part_sessions.clear()
        self._started = False

    def terminal_snapshot(self, session_id: str) -> str:
        return ""

    def has_pending_approval(self, session_id: str) -> bool:
        state = self._sessions.get(session_id)
        return bool(state and state.pending_permission_ids)


def build_adapter(
    emit_event: EmitEvent,
    binary: str | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    workdir: str | None = None,
) -> OpenCodeAdapter:
    return OpenCodeAdapter(emit_event, binary, host, port, workdir=workdir)
