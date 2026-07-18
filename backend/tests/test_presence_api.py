"""API coverage for the session-presence endpoints."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from waypoint.api import create_app
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus
from waypoint.settings import Settings
from waypoint.storage import Storage


def _seed_session(storage: Storage, tmp_path: Path, session_id: str) -> None:
    now = datetime.now(UTC)
    raw_log = tmp_path / f"{session_id}.log"
    raw_log.touch()
    storage.create_session(
        SessionRecord(
            id=session_id,
            backend="claude_code",
            transport="claude_cli",
            source=SessionSource.MANAGED,
            title="Test Session",
            cwd=str(tmp_path),
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(raw_log),
            transport_state={},
        )
    )


def _build(tmp_path: Path) -> tuple[Any, str]:
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    context = app.state.context
    _seed_session(context.storage, tmp_path, "sess1")
    token = context.tokens.issue().token
    return app, token


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_presence_requires_auth(tmp_path: Path) -> None:
    app, _ = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/sessions/sess1/presence", json={"viewer_id": "v1"}
        )
    assert resp.status_code == 401


async def test_touch_registers_and_release_clears(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    registry = app.state.context.runtime.session_presence
    async with _client(app) as client:
        resp = await client.post(
            "/api/sessions/sess1/presence",
            json={"viewer_id": "v1"},
            headers=_auth(token),
        )
        assert resp.status_code == 204
        assert registry.is_active("sess1") is True

        resp = await client.delete(
            "/api/sessions/sess1/presence/v1", headers=_auth(token)
        )
        assert resp.status_code == 204
        assert registry.is_active("sess1") is False


async def test_release_is_idempotent(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.delete(
            "/api/sessions/sess1/presence/never-registered", headers=_auth(token)
        )
    assert resp.status_code == 204


async def test_touch_unknown_session_404(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/sessions/nope/presence",
            json={"viewer_id": "v1"},
            headers=_auth(token),
        )
    assert resp.status_code == 404


async def test_viewer_id_must_be_bounded_and_non_empty(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        too_long = await client.post(
            "/api/sessions/sess1/presence",
            json={"viewer_id": "x" * 129},
            headers=_auth(token),
        )
        empty = await client.post(
            "/api/sessions/sess1/presence",
            json={"viewer_id": ""},
            headers=_auth(token),
        )
    assert too_long.status_code == 422
    assert empty.status_code == 422
