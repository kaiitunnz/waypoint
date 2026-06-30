"""Route-level tests for DELETE /api/backends/{backend}/threads/{thread_id}.

Exercises the API-layer gating (unsupported backend, in-use guard, not-found)
over the real FastAPI app via an in-process ASGI transport. The runtime
lifespan is not started; the route only reads storage/registry and calls the
plugin's on-disk delete.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from waypoint.api import create_app
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus
from waypoint.settings import Settings

_UID = "11111111-1111-1111-1111-111111111111"


def _build(tmp_path: Path, session_kw: dict[str, Any] | None = None) -> tuple[Any, str]:
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    context = app.state.context
    if session_kw is not None:
        now = datetime.now(UTC)
        session = SessionRecord(
            id="s1",
            backend="codex",
            source=SessionSource.MANAGED,
            title="t",
            cwd=str(tmp_path),
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(tmp_path / "raw.log"),
            structured_log_path=str(tmp_path / "events.jsonl"),
            **session_kw,
        )
        context.storage.create_session(session)
    token = context.tokens.issue().token
    return app, token


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_delete_unsupported_backend_is_400(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.delete(
            f"/api/backends/tmux/threads/{_UID}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 400  # tmux doesn't advertise the capability


async def test_delete_in_use_thread_is_409(tmp_path: Path) -> None:
    app, token = _build(tmp_path, session_kw={"transport_state": {"thread_id": _UID}})
    async with _client(app) as client:
        resp = await client.delete(
            f"/api/backends/codex/threads/{_UID}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409  # a live session still resumes from it
    assert "s1" in resp.json()["detail"]


async def test_delete_missing_thread_is_404(tmp_path: Path, monkeypatch: Any) -> None:
    # No session holds it and no rollout exists on disk → the plugin reports
    # nothing deleted, so the route returns 404 rather than a false success.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.delete(
            f"/api/backends/codex/threads/{_UID}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404
