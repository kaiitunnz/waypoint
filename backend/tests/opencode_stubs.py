"""Stand-ins for an OpenCode server: its binary, an ssh and a 2.x API."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from aiohttp import web

from waypoint.backends.opencode.adapter import OpenCodeAdapter
from waypoint.backends.opencode.client import LocalOpenCodeClient
from waypoint.backends.opencode.server_auth import ServerAuth
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import EventKind, SessionStatus

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]

# The directory the fake server's adapter runs in.
WORKDIR = "/work"


@asynccontextmanager
async def serving(routes: dict[str, Handler]) -> AsyncIterator[str]:
    """Serve ``routes``, each for any method, and yield the base URL."""
    app = web.Application()
    for path, handler in routes.items():
        app.router.add_route("*", path, handler)
    runner = web.AppRunner(app, shutdown_timeout=0.1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{runner.addresses[0][1]}"
    finally:
        await runner.cleanup()


def script(path: Path, body: str) -> str:
    path.write_text(f"#!/usr/bin/env bash\n{body}")
    path.chmod(0o755)
    return str(path)


def fake_opencode(directory: Path, port: int, then: str = "") -> tuple[str, Path]:
    """A binary that records the server variables it got, announces ``port``,
    runs the shell lines in ``then`` and stays up."""
    seen = directory / "server.env"
    binary = script(
        directory / "opencode",
        f"env | grep '^OPENCODE_' | sort > '{seen}'\n"
        f'echo "server listening on http://127.0.0.1:{port}"\n'
        f"{then}"
        "exec sleep 30\n",
    )
    return binary, seen


def server_env(seen: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in seen.read_text().splitlines())


def loopback_ssh(directory: Path, opencode_bin: str) -> SshLaunchTargetConfig:
    """A target whose ssh runs the remote command here, logging its argv."""
    ssh = script(
        directory / "ssh",
        f"printf '%s\\n' \"$@\" >> '{directory / 'ssh.argv'}'\n"
        'exec bash -c "${@: -1}"\n',
    )
    return SshLaunchTargetConfig(
        id="ssh-1",
        name="Remote",
        ssh_destination="user@example.com",
        ssh_bin=ssh,
        remote_shell="",
        plugin_configs={"opencode": {"remote_bin": opencode_bin}},
    )


def model_entry(
    model_id: str = "big-pickle",
    *,
    provider: str = "opencode",
    variants: tuple[str, ...] = ("low", "high"),
    context: int = 200_000,
    status: str = "active",
) -> dict[str, Any]:
    return {
        "id": model_id,
        "providerID": provider,
        "name": model_id.title(),
        "type": "chat",
        "enabled": True,
        "status": status,
        "variants": [{"id": variant} for variant in variants],
        "limit": {"context": context},
    }


class FakeOpenCode:
    """A 2.x server in memory, recording the requests it gets."""

    def __init__(self, version: str = "2.4.1") -> None:
        self.version = version
        # (method, path, body or query)
        self.requests: list[tuple[str, str, Any]] = []
        # (method, path) -> the status to fail with.
        self.failures: dict[tuple[str, str], int] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.permissions: list[dict[str, Any]] = []
        self.forms: list[dict[str, Any]] = []
        self.active: set[str] = set()
        self.diffs: list[dict[str, Any]] = []
        self.models = [model_entry()]
        self.default_model: dict[str, Any] | None = model_entry()
        self.commands: list[dict[str, Any]] = [{"name": "init"}]
        self.skills: list[dict[str, Any]] = []
        # How many catalog listings answer empty, as plugins load.
        self.unsettled = 0
        # Migration states in order; the last one stays.
        self.migration: list[dict[str, Any]] = [{"status": "completed"}]
        self.page_size = 1000
        self.events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._created = 0

    def add_session(self, session_id: str, **info: Any) -> dict[str, Any]:
        session = {
            "id": session_id,
            "title": session_id,
            "location": {"directory": WORKDIR},
            "time": {"created": 1000, "updated": 2000},
            **info,
        }
        self.sessions[session_id] = session
        return session

    def calls(self, method: str, path: str) -> list[Any]:
        """What each ``method`` request for ``path`` carried."""
        return [
            payload
            for seen_method, seen_path, payload in self.requests
            if (seen_method, seen_path) == (method, path)
        ]

    def writes(self) -> list[tuple[str, str]]:
        return [(method, path) for method, path, _ in self.requests if method != "GET"]

    @asynccontextmanager
    async def serving(self) -> AsyncIterator[str]:
        try:
            async with serving({"/{path:.*}": self._handle}) as url:
                try:
                    yield url
                finally:
                    # Ends an open event stream.
                    self.events.put_nowait(None)
        finally:
            self.events = asyncio.Queue()

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        path, method = request.path, request.method
        body = (
            await request.json()
            if request.can_read_body
            else dict(request.query) or None
        )
        self.requests.append((method, path, body))
        if status := self.failures.get((method, path)):
            return web.json_response({"_tag": "Error"}, status=status)
        if path == "/api/event":
            return await self._stream(request)
        if path == "/config":
            return web.Response(text="<!doctype html>", content_type="text/html")
        parts = path.removeprefix("/api/").split("/")
        match method, parts:
            case "GET", ["info"]:
                return _json({"version": self.version})
            case "GET", ["experimental", "migration", "v1"]:
                state = self.migration[0]
                if len(self.migration) > 1:
                    self.migration.pop(0)
                return _json(state)
            case "GET", ["model"]:
                return self._catalog(self.models)
            case "GET", ["model", "default"]:
                return _json({"data": self.default_model})
            case "GET", ["command"]:
                return self._catalog(self.commands)
            case "GET", ["skill"]:
                return _json({"data": self.skills})
            case "GET", ["provider"]:
                return _json({"data": [{"id": "opencode", "name": "OpenCode Zen"}]})
            case "GET", ["session", "active"]:
                return _json(
                    {"data": {item: {"type": "running"} for item in self.active}}
                )
            case "GET", ["session"]:
                return self._list_sessions(dict(request.query))
            case "POST", ["session"]:
                self._created += 1
                info = self.add_session(f"ses_new{self._created}")
                assert isinstance(body, dict)
                info.update(
                    {key: body[key] for key in ("model", "agent") if key in body},
                    permissions=body.get("permissions", []),
                )
                return _json({"data": info})
            case _, ["session", session_id, *rest]:
                return self._session(method, session_id, rest, body, request)
        return web.json_response({}, status=404)

    def _session(
        self,
        method: str,
        session_id: str,
        rest: list[str],
        body: Any,
        request: web.Request,
    ) -> web.Response:
        info = self.sessions.get(session_id)
        if info is None:
            return web.json_response({"_tag": "SessionNotFoundError"}, status=404)
        match method, rest:
            case "GET", []:
                return _json({"data": info})
            case "GET", ["permission"]:
                return _json({"data": _asked_by(self.permissions, session_id)})
            case "GET", ["form"]:
                return _json({"data": _asked_by(self.forms, session_id)})
            case "PATCH", []:
                info.update(body)
            case "DELETE", []:
                del self.sessions[session_id]
            case "POST", ["fork"]:
                fork = self.add_session(
                    f"{session_id}_fork",
                    **{
                        key: info[key]
                        for key in ("model", "agent", "permissions")
                        if key in info
                    },
                )
                return _json({"data": fork})
            case "POST", ["model"]:
                info["model"] = body["model"]
            case "POST", ["agent"]:
                info["agent"] = body["agent"]
            case "POST", ["permission", request_id, "reply"]:
                if not self._settle(self.permissions, request_id):
                    return web.json_response({}, status=404)
            case "POST", ["form", form_id, "reply"]:
                if not self._settle(self.forms, form_id):
                    return web.json_response({}, status=409)
            case "DELETE", ["form", form_id]:
                if not self._settle(self.forms, form_id):
                    return web.json_response({}, status=404)
            case "POST", ["interrupt"]:
                return _json({"interrupted": True})
            case "POST", ["prompt" | "compact"]:
                return _json({"data": {}})
            case "POST", ["command"]:
                pass
            case "GET", ["diff"]:
                return _json({"data": self.diffs})
            case "GET", ["message"]:
                return self._page(
                    self.messages.get(session_id, []), dict(request.query)
                )
            case _:
                return web.json_response({}, status=404)
        return web.Response(status=204)

    def _settle(self, pending: list[dict[str, Any]], ask_id: str) -> bool:
        for item in pending:
            if item["id"] == ask_id:
                pending.remove(item)
                return True
        return False

    def _catalog(self, items: list[dict[str, Any]]) -> web.Response:
        if self.unsettled:
            self.unsettled -= 1
            return _json({"data": []})
        return _json({"data": items})

    def _list_sessions(self, query: dict[str, str]) -> web.Response:
        items = list(self.sessions.values())
        if parent := query.get("parentID"):
            items = [item for item in items if item.get("parentID") == parent]
        return self._page(items, query)

    def _page(self, items: list[dict[str, Any]], query: dict[str, str]) -> web.Response:
        start = int(query.get("cursor") or 0)
        end = start + self.page_size
        cursor = {"next": str(end)} if end < len(items) else {}
        return _json({"data": items[start:end], "cursor": cursor})

    async def _stream(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        queue = self.events
        event: dict[str, Any] | None = {"type": "server.connected", "data": {}}
        while event is not None:
            await response.write(f"data: {json.dumps(event)}\n\n".encode())
            event = await queue.get()
        return response


def _asked_by(asks: list[dict[str, Any]], session_id: str) -> list[dict[str, Any]]:
    return [item for item in asks if item["sessionID"] == session_id]


def _json(payload: Any) -> web.Response:
    return web.json_response(payload)


class Emitted:
    """The events an adapter emitted."""

    def __init__(self) -> None:
        self.events: list[tuple[EventKind, str, dict[str, Any], str]] = []
        self.statuses: list[SessionStatus | None] = []

    async def __call__(
        self,
        session_id: str,
        kind: EventKind,
        text: str,
        metadata: dict[str, Any],
        status: SessionStatus | None,
    ) -> None:
        self.events.append((kind, text, metadata, session_id))
        self.statuses.append(status)

    def texts(self) -> list[str]:
        return [text for _, text, _, _ in self.events]

    def of(self, kind: EventKind) -> list[tuple[str, dict[str, Any]]]:
        return [
            (text, metadata)
            for found, text, metadata, _ in self.events
            if found == kind
        ]

    def methods(self) -> list[str]:
        return [metadata["method"] for _, _, metadata, _ in self.events]

    def clear(self) -> None:
        self.events.clear()
        self.statuses.clear()


def event(kind: str, session_id: str = "ses_root", **data: Any) -> dict[str, Any]:
    return {"type": kind, "data": {"sessionID": session_id, **data}}


@asynccontextmanager
async def driving(
    fake: FakeOpenCode, **kwargs: Any
) -> AsyncIterator[tuple[OpenCodeAdapter, Emitted]]:
    """An adapter talking to ``fake``, as one whose server has started. Tests
    deliver events with ``adapter._handle_event``."""
    async with fake.serving() as url:
        emitted = Emitted()
        adapter = OpenCodeAdapter(emit_event=emitted, **kwargs)
        adapter._client = LocalOpenCodeClient(url, ServerAuth("secret"))
        adapter._directory = WORKDIR
        adapter._started = True
        try:
            yield adapter, emitted
        finally:
            await adapter.shutdown()
