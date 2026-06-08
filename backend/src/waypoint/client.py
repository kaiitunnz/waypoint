"""Thin HTTP client for a running Waypoint backend.

Used by the ``waypoint sessions`` CLI (and the personal assistant, which
shells out to it) to inspect and manage sessions over the API rather than
spinning up a second in-process runtime against the same storage. Auth
resolves, in order: ``WAYPOINT_TOKEN`` env, the server-issued local token
file, then a password login (caching the result to the token file).
"""

import os
from pathlib import Path
from typing import Any, Self

import httpx

from waypoint.settings import Settings

CLI_TOKEN_FILENAME = "cli-token"


class WaypointError(RuntimeError):
    """Raised for transport failures and non-2xx API responses."""


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

    def list_sessions(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self._request("GET", "/api/sessions").json()[
            "sessions"
        ]
        return data

    def get_session(self, session_id: str) -> dict[str, Any]:
        data: dict[str, Any] = self._request(
            "GET", f"/api/sessions/{session_id}"
        ).json()["session"]
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
        title: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        permission_mode: str | None = None,
        args: list[str] | None = None,
    ) -> dict[str, Any]:
        body = {
            "backend": backend,
            "cwd": cwd,
            "launch_target_id": launch_target_id,
            "title": title,
            "model": model,
            "effort": effort,
            "permission_mode": permission_mode,
            "args": args or [],
        }
        data: dict[str, Any] = self._request("POST", "/api/sessions", json=body).json()[
            "session"
        ]
        return data

    def send_input(
        self, session_id: str, text: str, *, submit: bool = True
    ) -> dict[str, Any]:
        data: dict[str, Any] = self._request(
            "POST",
            f"/api/sessions/{session_id}/input",
            json={"text": text, "submit": submit},
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
