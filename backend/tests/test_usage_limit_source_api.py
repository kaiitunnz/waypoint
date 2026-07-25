"""Route-level tests for the usage-limit-source API surface (ticket 1273)."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from waypoint.api import create_app
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus
from waypoint.settings import Settings


def _build(tmp_path: Path) -> tuple[Any, str]:
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    context = app.state.context
    token = context.tokens.issue().token
    return app, token


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_session(app: Any, session_id: str = "sess") -> None:
    now = datetime.now(UTC)
    app.state.context.storage.create_session(
        SessionRecord(
            id=session_id,
            backend="codex",
            source=SessionSource.MANAGED,
            title="Session",
            cwd="/tmp",
            status=SessionStatus.RUNNING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path="/tmp/raw.log",
            structured_log_path="/tmp/events.jsonl",
        )
    )


async def test_me_exposes_empty_usage_provider_options(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get("/api/me", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["usage_provider_options"] == []


async def test_usage_provider_options_endpoint(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get("/api/usage-provider-options", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json() == {"usage_provider_options": []}


async def test_set_source_malformed_returns_400(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    _seed_session(app)
    async with _client(app) as client:
        resp = await client.patch(
            "/api/sessions/sess/usage-limit-source",
            json={"usage_limit_source": "usage_provider", "usage_provider_id": "lumid"},
            headers=_auth(token),
        )
    assert resp.status_code == 400


async def test_set_source_provider_not_enabled_returns_409(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    _seed_session(app)
    async with _client(app) as client:
        resp = await client.patch(
            "/api/sessions/sess/usage-limit-source",
            json={
                "usage_limit_source": "usage_provider",
                "usage_provider_id": "lumid",
                "usage_provider_account_key": "hmac:v1:abc",
            },
            headers=_auth(token),
        )
    assert resp.status_code == 409
