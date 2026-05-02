import asyncio
import codecs
import errno
import json
import logging
import os
import shutil
import signal
import socket
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from waypoint.backends.opencode.normalize import map_event
from waypoint.schemas import EventKind, SessionStatus

log = logging.getLogger("waypoint.opencode")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4096
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MODEL = "opencode/minimax-m2.5-free"

PERMISSION_REPLIES = {"once", "always", "reject"}

EmitEvent = Callable[
    [str, EventKind, str, dict[str, Any], SessionStatus],
    Any,
]


class OpenCodeError(RuntimeError):
    pass


def _port_in_use(host: str, port: int) -> bool:
    """Probe whether *host:port* is already bound (orphaned OpenCode etc.)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # SO_REUSEADDR keeps us from getting a false positive on a
        # TIME_WAIT socket left behind by the previous OpenCode boot.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            if exc.errno in {errno.EADDRINUSE, errno.EACCES}:
                return True
            raise
    return False


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
    closing: bool = False


class OpenCodeAdapter:
    def __init__(
        self,
        emit_event: EmitEvent,
        binary: str | None = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> None:
        self._emit_event = emit_event
        self._binary = binary
        self._host = host
        self._port = port
        self._sessions: dict[str, OpenCodeSessionState] = {}
        self._remote_sessions: dict[str, str] = {}
        self._base_url = f"http://{host}:{port}"
        self._server_process: asyncio.subprocess.Process | None = None
        self._sse_task: asyncio.Task[None] | None = None
        self._http: aiohttp.ClientSession | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        binary = self._binary or shutil.which("opencode")
        if binary is None:
            raise OpenCodeError("opencode binary not found on PATH")
        # Refuse to start if something is already listening on the port —
        # otherwise our `_wait_for_server` loop happily adopts whoever
        # answers /config (typically an orphan from a previous backend
        # crash), and that stale process never sees auth.json updates.
        if _port_in_use(self._host, self._port):
            raise OpenCodeError(
                f"opencode port {self._host}:{self._port} is already in use; "
                f"kill the orphan process before restarting"
            )
        log.info("starting opencode server on %s:%d", self._host, self._port)
        # Put the child in its own session so a SIGKILL of the backend
        # leaves OpenCode reachable via os.killpg() — and so SIGINT in
        # the foreground terminal doesn't leak into the subprocess.
        self._server_process = await asyncio.create_subprocess_exec(
            binary,
            "serve",
            f"--hostname={self._host}",
            f"--port={self._port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self._http = aiohttp.ClientSession()
        try:
            await self._wait_for_server()
        except Exception as exc:
            log.error("failed to start opencode server: %s", exc)
            await self._http.close()
            self._http = None
            self._terminate_server_process()
            raise OpenCodeError(f"failed to start opencode server: {exc}") from exc
        self._started = True
        self._sse_task = asyncio.create_task(self._listen_events())
        log.info("opencode server started successfully")

    def _terminate_server_process(self) -> None:
        """Kill the OpenCode subprocess and any of its descendants.

        With start_new_session=True the spawned process is a group leader
        (PGID == PID), so killpg reliably reaps anything OpenCode itself
        forked. Falls back to terminating the leader if the group is
        already gone (mid-shutdown race).
        """
        proc = self._server_process
        if proc is None or proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError:
            with suppress(ProcessLookupError):
                proc.terminate()

    async def _wait_for_server(self) -> None:
        client = self._require_http()
        deadline = time.monotonic() + 30
        while True:
            if time.monotonic() >= deadline:
                raise OpenCodeError("timeout waiting for opencode server")
            try:
                async with client.get(f"{self._base_url}/config") as resp:
                    if resp.status == 200:
                        return
            except Exception:
                pass
            await asyncio.sleep(0.5)

    def _require_http(self) -> aiohttp.ClientSession:
        if self._http is None:
            raise OpenCodeError("opencode http session not initialized")
        return self._http

    async def _listen_events(self) -> None:
        client = self._require_http()
        try:
            while True:
                try:
                    async with client.get(f"{self._base_url}/event") as resp:
                        resp.raise_for_status()
                        buffer: list[str] = []
                        leftover = ""
                        decoder = codecs.getincrementaldecoder("utf-8")()
                        while True:
                            chunk = await resp.content.read(8192)
                            if not chunk:
                                break
                            data = leftover + decoder.decode(chunk)
                            lines = data.split("\n")
                            leftover = lines[-1]
                            for line in lines[:-1]:
                                line = line.rstrip("\r")
                                if not line:
                                    payload = self._decode_sse_payload(buffer)
                                    buffer.clear()
                                    if payload is not None:
                                        await self._dispatch_event(payload)
                                    continue
                                if line.startswith(":"):
                                    continue
                                if line.startswith("data:"):
                                    buffer.append(line[5:].lstrip())

                        # Handle last bit of decoder state
                        data = leftover + decoder.decode(b"", final=True)
                        if data:
                            for line in data.split("\n"):
                                line = line.rstrip("\r")
                                if line and line.startswith("data:"):
                                    buffer.append(line[5:].lstrip())

                        payload = self._decode_sse_payload(buffer)
                        buffer.clear()
                        if payload is not None:
                            await self._dispatch_event(payload)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("sse connection failed, retrying in 1s")
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
        remote_session_id = self._extract_session_id(properties)
        if not remote_session_id:
            return
        session_id = self._remote_sessions.get(remote_session_id)
        if not session_id:
            return
        state = self._sessions.get(session_id)
        if state is None:
            return
        self._update_pending_state(state, event_type, properties)
        properties = self._tag_part_type(state, event_type, properties)

        kind, text, metadata = map_event(event_type, properties)
        if not text and not metadata.get("payload"):
            return

        await self._emit_event(
            session_id,
            kind,
            text,
            metadata,
            metadata.get("status", SessionStatus.RUNNING),
        )

    def _extract_session_id(self, value: Any) -> str | None:
        if isinstance(value, dict):
            session_id = value.get("sessionID")
            if isinstance(session_id, str) and session_id:
                return session_id
            for nested in value.values():
                found = self._extract_session_id(nested)
                if found:
                    return found
            return None
        if isinstance(value, list):
            for nested in value:
                found = self._extract_session_id(nested)
                if found:
                    return found
        return None

    def _update_pending_state(
        self,
        state: OpenCodeSessionState,
        event_type: str | None,
        properties: dict[str, Any],
    ) -> None:
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
        client = self._require_http()
        payload: dict[str, Any] = {"title": title}
        if permission:
            payload["permission"] = permission
        async with client.post(
            f"{self._base_url}/session",
            json=payload,
            params={"directory": directory} if directory else {},
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.error("failed to create session: %s - %s", resp.status, body)
                raise OpenCodeError(f"failed to create session: {resp.status} - {body}")
            data = await resp.json()
            session_id = data.get("id", "")
            if not session_id or not session_id.startswith("ses"):
                log.error("invalid session ID returned: %s", data)
                raise OpenCodeError(f"invalid session ID returned: {session_id}")
            return session_id

    async def restore_session(
        self,
        session_id: str,
        cwd: str,
        opencode_session_id: str,
        model: str | None = None,
        agent: str | None = None,
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
        )
        self._register_session(state)
        # The plugin records the user-facing restore/import note; the adapter
        # stays silent here so the transcript only shows one entry.

    async def send_input(self, session_id: str, text: str) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            raise OpenCodeError(f"session not found: {session_id}")
        model = self._split_model_ref(state.model)
        client = self._require_http()
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
        if state.agent:
            payload["agent"] = state.agent
        # /message blocks until the entire response streams; using
        # /prompt_async lets the POST return immediately so the composer
        # can flush, while OpenCode delivers the turn over SSE.
        async with client.post(
            f"{self._base_url}/session/{state.opencode_session_id}/prompt_async",
            json=payload,
        ) as resp:
            if resp.status not in {200, 204}:
                body = await resp.text()
                raise OpenCodeError(f"failed to send message: {resp.status} - {body}")

    def _split_model_ref(self, model: str | None) -> dict[str, str] | None:
        selected = model or DEFAULT_MODEL
        if "/" not in selected:
            return None
        provider_id, model_id = selected.split("/", 1)
        if not provider_id or not model_id:
            return None
        return {"providerID": provider_id, "modelID": model_id}

    async def interrupt(self, session_id: str) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            return
        client = self._require_http()
        await client.post(f"{self._base_url}/session/{state.opencode_session_id}/abort")

    async def respond_to_permission(self, session_id: str, decision: str) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        if not state.pending_permission_ids:
            return False
        reply = self._map_decision_to_reply(decision)
        permission_id = state.pending_permission_ids[0]
        client = self._require_http()
        async with client.post(
            f"{self._base_url}/session/{state.opencode_session_id}/permissions/{permission_id}",
            json={"reply": reply},
        ) as resp:
            if resp.status != 200:
                return False
        state.pending_permission_ids.pop(0)
        return True

    def _map_decision_to_reply(self, decision: str) -> str:
        # Waypoint's runtime sends approval decisions using shared aliases;
        # OpenCode's reply schema is strictly {once|always|reject}.
        normalized = decision.lower()
        if normalized in PERMISSION_REPLIES:
            return normalized
        aliases = {
            "approve": "once",
            "accept": "once",
            "yes": "once",
            "acceptforsession": "always",
            "decline": "reject",
            "deny": "reject",
            "cancel": "reject",
        }
        if normalized in aliases:
            return aliases[normalized]
        raise OpenCodeError(f"unsupported permission decision: {decision}")

    async def set_model(self, session_id: str, model: str) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        state.model = model
        return True

    async def set_session_permission(
        self, session_id: str, permission: list[dict[str, str]]
    ) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        client = self._require_http()
        async with client.patch(
            f"{self._base_url}/session/{state.opencode_session_id}",
            json={"permission": permission},
        ) as resp:
            return resp.status == 200

    async def compact_session(self, session_id: str) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            raise OpenCodeError(f"session not found: {session_id}")
        model = self._split_model_ref(state.model)
        if model is None:
            raise OpenCodeError("unable to resolve model for compaction")
        payload = {
            "providerID": model["providerID"],
            "modelID": model["modelID"],
            "auto": False,
        }
        client = self._require_http()
        async with client.post(
            f"{self._base_url}/session/{state.opencode_session_id}/summarize",
            json=payload,
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise OpenCodeError(
                    f"failed to compact session: {resp.status} - {body}"
                )

    async def list_sessions(self, directory: str | None = None) -> list[dict[str, Any]]:
        if not self._started:
            await self.start()
        client = self._require_http()
        params = {"directory": directory} if directory else {}
        async with client.get(
            f"{self._base_url}/session",
            params=params,
        ) as resp:
            if resp.status != 200:
                return []
            return await resp.json()

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        client = self._require_http()
        async with client.get(
            f"{self._base_url}/session/{session_id}",
        ) as resp:
            if resp.status != 200:
                return None
            return await resp.json()

    async def list_providers(self) -> dict[str, Any]:
        if not self._started:
            await self.start()
        client = self._require_http()
        async with client.get(f"{self._base_url}/provider") as resp:
            if resp.status != 200:
                return {"all": [], "default": {}, "connected": []}
            return await resp.json()

    async def list_questions(self) -> list[dict[str, Any]]:
        client = self._require_http()
        async with client.get(f"{self._base_url}/question") as resp:
            if resp.status != 200:
                return []
            return await resp.json()

    async def answer_question(
        self,
        session_id: str,
        request_id: str,
        answers: list[list[str]],
    ) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        client = self._require_http()
        async with client.post(
            f"{self._base_url}/question/{request_id}/reply",
            json={"answers": answers},
        ) as resp:
            if resp.status != 200:
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
        state = self._sessions.pop(session_id, None)
        if state is None:
            return False
        self._remote_sessions.pop(state.opencode_session_id, None)
        state.closing = True
        state.pending_permission_ids.clear()
        state.pending_question_ids.clear()
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
        if self._http is not None:
            await self._http.close()
            self._http = None
        if self._server_process is not None:
            self._terminate_server_process()
            try:
                await asyncio.wait_for(self._server_process.wait(), timeout=5)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(self._server_process.pid, signal.SIGKILL)
            self._server_process = None
        self._sessions.clear()
        self._remote_sessions.clear()
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
) -> OpenCodeAdapter:
    return OpenCodeAdapter(emit_event, binary, host, port)
