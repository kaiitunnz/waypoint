"""Thin HTTP client for a running Waypoint backend.

Used by the ``waypoint sessions`` CLI (and the personal assistant, which
shells out to it) to inspect and manage sessions over the API rather than
spinning up a second in-process runtime against the same storage. Auth
resolves, in order: ``WAYPOINT_TOKEN`` env, the server-issued local token
file, then a password login (caching the result to the token file).
"""

import json
import os
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any, Self
from urllib.parse import quote

import httpx
from websockets.asyncio.client import connect as ws_connect

from waypoint.settings import Settings

CLI_TOKEN_FILENAME = "cli-token"


class WaypointError(RuntimeError):
    """Raised for transport failures and non-2xx API responses."""


def websocket_url(http_base_url: str, path: str, *, token: str) -> str:
    """Derive a ``ws(s)://`` URL with the auth token as a query param.

    The CLI authenticates the WebSocket endpoints by token query param rather
    than an ``Authorization`` header, so the resolved token is appended here.
    """
    scheme = "wss" if http_base_url.startswith("https") else "ws"
    host = http_base_url.split("://", 1)[-1].rstrip("/")
    return f"{scheme}://{host}{path}?token={quote(token)}"


def session_status_from_envelope(envelope: Mapping[str, Any]) -> str | None:
    """Status from a ``session_state`` envelope, or ``None`` for other types."""
    if envelope.get("type") != "session_state":
        return None
    payload = envelope.get("payload", {})
    session = payload.get("session") if isinstance(payload, Mapping) else None
    if not isinstance(session, Mapping):
        return None
    status = session.get("status")
    return status if isinstance(status, str) else None


def is_event_envelope(envelope: Mapping[str, Any]) -> bool:
    """Whether the envelope carries a transcript event (vs. a state update)."""
    return envelope.get("type") == "event"


def cli_token_path(settings: Settings) -> Path:
    return settings.data_dir / CLI_TOKEN_FILENAME


def write_cli_token(settings: Settings, token: str) -> Path:
    """Persist ``token`` to the local token file with 0600 permissions."""
    path = cli_token_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    path.chmod(0o600)
    return path


def base_url(settings: Settings) -> str:
    # The CLI runs on the same host as the server; a wildcard bind address
    # isn't connectable, so dial loopback instead.
    host = settings.host
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return f"http://{host}:{settings.port}"


class WaypointClient:
    def __init__(
        self,
        settings: Settings,
        *,
        token: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self._token = token
        self._client = client or httpx.Client(base_url=base_url(settings), timeout=30.0)
        self._owns_client = client is None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # ── auth ────────────────────────────────────────────────────────────

    def token(self) -> str:
        if self._token is None:
            self._token = self._resolve_token()
        return self._token

    def _resolve_token(self) -> str:
        env_token = os.environ.get("WAYPOINT_TOKEN")
        if env_token:
            return env_token
        path = cli_token_path(self.settings)
        if path.exists():
            cached = path.read_text(encoding="utf-8").strip()
            if cached:
                return cached
        return self._login()

    def _login(self) -> str:
        password = os.environ.get("WAYPOINT_PASSWORD") or self.settings.password
        try:
            response = self._client.post("/api/auth/login", json={"password": password})
        except httpx.HTTPError as exc:
            raise WaypointError(f"cannot reach Waypoint API: {exc}") from exc
        if response.status_code != httpx.codes.OK:
            raise WaypointError(
                "login failed (set WAYPOINT_PASSWORD or WAYPOINT_TOKEN): "
                f"{response.status_code}"
            )
        token = str(response.json()["token"])
        self._token = token
        try:
            write_cli_token(self.settings, token)
        except OSError:
            pass
        return token

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.token()}"}
        try:
            response = self._client.request(method, path, headers=headers, **kwargs)
            # A stale cached/env token gets one transparent re-login + retry.
            if response.status_code == httpx.codes.UNAUTHORIZED:
                self._token = None
                headers = {"Authorization": f"Bearer {self._login()}"}
                response = self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise WaypointError(f"{method} {path} failed: {exc}") from exc
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise WaypointError(
                f"{method} {path} -> {response.status_code}: {response.text}"
            )
        return response

    # ── sessions ────────────────────────────────────────────────────────

    def list_sessions(self, spawned_by: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if spawned_by is not None:
            params["spawned_by"] = spawned_by
        data: list[dict[str, Any]] = self._request(
            "GET", "/api/sessions", params=params if params else None
        ).json()["sessions"]
        return data

    def get_session(self, session_id: str) -> dict[str, Any]:
        data: dict[str, Any] = self._request(
            "GET", f"/api/sessions/{session_id}"
        ).json()["session"]
        return data

    async def stream_session_envelopes(
        self, session_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield decoded envelopes from the per-session WebSocket.

        The server pushes a ``session_state`` envelope on connect, then
        ``session_state`` and ``event`` envelopes as the session changes,
        until it closes the connection. Decoding only — callers decide which
        envelopes to act on (see ``session_status_from_envelope`` /
        ``is_event_envelope``).
        """
        url = websocket_url(
            str(self._client.base_url),
            f"/ws/sessions/{session_id}",
            token=self.token(),
        )
        async with ws_connect(url) as connection:
            async for message in connection:
                yield json.loads(message)

    def list_backends(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self._request("GET", "/api/backends").json()[
            "backends"
        ]
        return data

    def list_models(
        self,
        backend: str,
        *,
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if launch_target_id is not None:
            params["launch_target_id"] = launch_target_id
        if include_hidden:
            params["include_hidden"] = include_hidden
        data: dict[str, Any] = self._request(
            "GET", f"/api/backends/{backend}/models", params=params or None
        ).json()
        return data

    def list_threads(
        self, backend: str, *, launch_target_id: str | None = None
    ) -> list[dict[str, Any]]:
        params = (
            {"launch_target_id": launch_target_id}
            if launch_target_id is not None
            else None
        )
        data: list[dict[str, Any]] = self._request(
            "GET", f"/api/backends/{backend}/threads", params=params
        ).json()["threads"]
        return data

    def get_events(
        self,
        session_id: str,
        *,
        messages: int | None = None,
        before_sequence: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, int] = {}
        if messages is not None:
            params["messages"] = messages
        if before_sequence is not None:
            params["before_sequence"] = before_sequence
        data: dict[str, Any] = self._request(
            "GET", f"/api/sessions/{session_id}/events", params=params
        ).json()
        return data

    def create_session(
        self,
        *,
        backend: str,
        cwd: str,
        launch_target_id: str | None = None,
        launch_mode: str | None = None,
        transport: str | None = None,
        title: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        permission_mode: str | None = None,
        spawner_session_id: str | None = None,
        worktree_path: str | None = None,
        args: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "backend": backend,
            "cwd": cwd,
            "launch_target_id": launch_target_id,
            "title": title,
            "model": model,
            "effort": effort,
            "permission_mode": permission_mode,
            "spawner_session_id": spawner_session_id,
            "worktree_path": worktree_path,
            "args": args or [],
        }
        # Omit launch_mode when unset: it is a non-optional-with-default enum
        # field, so an explicit null is a 422 (mirrors create_schedule).
        if launch_mode is not None:
            body["launch_mode"] = launch_mode
        # Omit transport when unset so the request model's None default keeps
        # today's launch_mode-derived behavior.
        if transport is not None:
            body["transport"] = transport
        data: dict[str, Any] = self._request("POST", "/api/sessions", json=body).json()[
            "session"
        ]
        return data

    def import_thread(self, backend: str, body: dict[str, Any]) -> dict[str, Any]:
        data: dict[str, Any] = self._request(
            "POST", f"/api/backends/{backend}/sessions/import", json=body
        ).json()["session"]
        return data

    def upload_attachment(self, session_id: str, path: Path) -> dict[str, Any]:
        with path.open("rb") as handle:
            spec: dict[str, Any] = self._request(
                "POST",
                f"/api/sessions/{session_id}/attachments",
                files={"file": (path.name, handle)},
            ).json()
        return spec

    def send_input(
        self,
        session_id: str,
        text: str,
        *,
        submit: bool = True,
        attachments: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text, "submit": submit}
        if attachments:
            body["attachments"] = attachments
        try:
            return self._request(
                "POST",
                f"/api/sessions/{session_id}/input",
                json=body,
            ).json()["session"]
        except WaypointError as exc:
            if not isinstance(exc.__cause__, httpx.TimeoutException):
                raise
        # Timeout: the server may have already accepted the input. Check session status.
        try:
            session = self.get_session(session_id)
        except WaypointError:
            return {"id": session_id, "send": "unknown"}
        send_flag = "delivered" if session.get("status") == "running" else "unknown"
        return {**session, "send": send_flag}

    def answer_question(
        self,
        session_id: str,
        answer: str,
        *,
        tool_use_id: str | None = None,
        answers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        body = {
            "answer": answer,
            "tool_use_id": tool_use_id,
            "answers": answers,
        }
        data: dict[str, Any] = self._request(
            "POST", f"/api/sessions/{session_id}/answer-question", json=body
        ).json()["session"]
        return data

    def set_permission_mode(self, session_id: str, mode: str) -> dict[str, Any]:
        data: dict[str, Any] = self._request(
            "POST",
            f"/api/sessions/{session_id}/mode",
            json={"mode": mode},
        ).json()["session"]
        return data

    def interrupt(self, session_id: str) -> dict[str, Any]:
        data: dict[str, Any] = self._request(
            "POST", f"/api/sessions/{session_id}/interrupt"
        ).json()["session"]
        return data

    def terminate(self, session_id: str) -> dict[str, Any]:
        data: dict[str, Any] = self._request(
            "POST", f"/api/sessions/{session_id}/terminate"
        ).json()["session"]
        return data

    def delete(
        self, session_id: str, *, force: bool = False, prune_branches: bool = False
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if force:
            params["force"] = force
        if prune_branches:
            params["prune_branches"] = prune_branches
        data: dict[str, Any] = self._request(
            "DELETE",
            f"/api/sessions/{session_id}",
            params=params or None,
        ).json()
        return data

    def approve(
        self,
        session_id: str,
        decision: str,
        *,
        text: str | None = None,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = self._request(
            "POST",
            f"/api/sessions/{session_id}/approve",
            json={"decision": decision, "text": text, "approval_id": approval_id},
        ).json()["session"]
        return data

    # ── board ───────────────────────────────────────────────────────────

    def list_board_channels(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self._request("GET", "/api/board").json()[
            "channels"
        ]
        return data

    def read_board(
        self, channel: str, *, since: int | None = None, key: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if since is not None:
            params["since"] = since
        if key is not None:
            params["key"] = key
        data: list[dict[str, Any]] = self._request(
            "GET", f"/api/board/{channel}", params=params or None
        ).json()["entries"]
        return data

    def post_board(
        self,
        channel: str,
        text: str,
        *,
        key: str | None = None,
        author_session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = {
            "text": text,
            "key": key,
            "author_session_id": author_session_id,
            "metadata": metadata or {},
        }
        data: dict[str, Any] = self._request(
            "POST", f"/api/board/{channel}", json=body
        ).json()["entry"]
        return data

    def clear_board(self, channel: str, keep_last: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if keep_last is not None:
            params["keep_last"] = keep_last
        data: dict[str, Any] = self._request(
            "POST", f"/api/board/{channel}/clear", params=params or None
        ).json()
        return data

    def delete_board(self, channel: str) -> dict[str, Any]:
        data: dict[str, Any] = self._request("DELETE", f"/api/board/{channel}").json()
        return data

    def delete_board_entry(self, channel: str, entry_id: int) -> dict[str, Any]:
        data: dict[str, Any] = self._request(
            "DELETE", f"/api/board/{channel}/entries/{entry_id}"
        ).json()
        return data

    def update_board_entry(
        self,
        channel: str,
        entry_id: int,
        text: str | None = None,
        metadata: dict[str, Any] | None = None,
        merge: bool = False,
        unset: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"metadata": metadata or {}}
        if text is not None:
            body["text"] = text
        if merge:
            body["merge"] = True
        if unset:
            body["unset"] = list(unset)
        data: dict[str, Any] = self._request(
            "PATCH", f"/api/board/{channel}/entries/{entry_id}", json=body
        ).json()["entry"]
        return data

    # ── schedules ───────────────────────────────────────────────────────

    def list_schedules(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self._request("GET", "/api/schedules").json()[
            "schedules"
        ]
        return data

    def create_schedule(
        self,
        *,
        backend: str,
        cwd: str,
        launch_target_id: str | None = None,
        launch_mode: str | None = None,
        transport: str | None = None,
        title: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        permission_mode: str | None = None,
        initial_prompt: str | None = None,
        args: list[str] | None = None,
        delay_seconds: int | None = None,
        scheduled_at: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"backend": backend, "cwd": cwd, "args": args or []}
        # Omit unset optionals so the server's model defaults apply. Sending an
        # explicit null for a non-optional-with-default field like launch_mode
        # is a 422 (it is validated against the enum, default or not). transport
        # is optional-with-None, so omit it to keep the launch_mode behavior.
        optional = {
            "launch_target_id": launch_target_id,
            "launch_mode": launch_mode,
            "transport": transport,
            "title": title,
            "model": model,
            "effort": effort,
            "permission_mode": permission_mode,
            "initial_prompt": initial_prompt,
            "delay_seconds": delay_seconds,
            "scheduled_at": scheduled_at,
        }
        body.update(
            {key: value for key, value in optional.items() if value is not None}
        )
        data: dict[str, Any] = self._request(
            "POST", "/api/schedules", json=body
        ).json()["schedule"]
        return data

    def delete_schedule(self, schedule_id: str) -> dict[str, Any]:
        data: dict[str, Any] = self._request(
            "DELETE", f"/api/schedules/{schedule_id}"
        ).json()["schedule"]
        return data

    def clear_schedule_history(self) -> dict[str, Any]:
        data: dict[str, Any] = self._request(
            "POST", "/api/schedules/clear-history"
        ).json()
        return data

    # ── message schedules ────────────────────────────────────────────────

    def list_message_schedules(
        self, session_id: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if session_id is not None:
            params["session_id"] = session_id
        data: list[dict[str, Any]] = self._request(
            "GET", "/api/message-schedules", params=params if params else None
        ).json()["message_schedules"]
        return data

    def create_message_schedule(
        self,
        session_id: str,
        text: str,
        *,
        submit: bool = True,
        delay_seconds: int | None = None,
        scheduled_at: str | None = None,
        attachments: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text, "submit": submit}
        if delay_seconds is not None:
            body["delay_seconds"] = delay_seconds
        if scheduled_at is not None:
            body["scheduled_at"] = scheduled_at
        if attachments:
            body["attachments"] = attachments
        data: dict[str, Any] = self._request(
            "POST",
            f"/api/sessions/{session_id}/message-schedules",
            json=body,
        ).json()["message_schedule"]
        return data

    def delete_message_schedule(self, schedule_id: str) -> dict[str, Any]:
        data: dict[str, Any] = self._request(
            "DELETE", f"/api/message-schedules/{schedule_id}"
        ).json()["message_schedule"]
        return data

    def clear_message_schedule_history(
        self, session_id: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, str] = {}
        if session_id is not None:
            params["session_id"] = session_id
        data: dict[str, Any] = self._request(
            "POST", "/api/message-schedules/clear-history", params=params or None
        ).json()
        return data

    # ── usage ────────────────────────────────────────────────────────────

    def get_usage(self) -> dict[str, Any]:
        data: dict[str, Any] = self._request("GET", "/api/usage").json()
        return data

    def refresh_usage(self) -> dict[str, Any]:
        data: dict[str, Any] = self._request("POST", "/api/usage/refresh").json()
        return data
