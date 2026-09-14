"""Route-level tests for recurring schedule endpoints over the real app."""

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


async def test_create_recurring_schedule_returns_recurrence_fields(
    tmp_path: Path,
) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/schedules",
            json={
                "backend": "codex",
                "cwd": "/tmp/project",
                "cron": "0 9 * * 1-5",
                "timezone": "Asia/Singapore",
            },
            headers=_auth(token),
        )
    assert resp.status_code == 200
    schedule = resp.json()["schedule"]
    assert schedule["cron"] == "0 9 * * 1-5"
    assert schedule["timezone"] == "Asia/Singapore"
    assert schedule["status"] == "pending"
    assert schedule["last_run_at"] is None
    assert "launch_env" not in schedule


async def test_invalid_timing_returns_400_not_422(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    bad_bodies = [
        {"backend": "codex", "cwd": "/tmp/p"},  # no timing
        {"backend": "codex", "cwd": "/tmp/p", "cron": "* * * * *"},  # no tz
        {"backend": "codex", "cwd": "/tmp/p", "timezone": "UTC"},  # no cron
        {
            "backend": "codex",
            "cwd": "/tmp/p",
            "delay_seconds": 60,
            "cron": "* * * * *",
            "timezone": "UTC",
        },  # mixed
        {
            "backend": "codex",
            "cwd": "/tmp/p",
            "cron": "nope",
            "timezone": "UTC",
        },  # bad cron
    ]
    async with _client(app) as client:
        for body in bad_bodies:
            resp = await client.post("/api/schedules", json=body, headers=_auth(token))
            assert resp.status_code == 400, body


async def test_preview_endpoint(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        ok = await client.post(
            "/api/schedules/preview",
            json={"cron": "0 9 * * 1-5", "timezone": "Asia/Singapore", "count": 3},
            headers=_auth(token),
        )
        assert ok.status_code == 200
        occ = ok.json()["occurrences"]
        assert len(occ) == 3

        bad = await client.post(
            "/api/schedules/preview",
            json={"cron": "totally invalid", "timezone": "UTC"},
            headers=_auth(token),
        )
        assert bad.status_code == 400

        # A future start shifts the previewed occurrences to begin at it.
        started = await client.post(
            "/api/schedules/preview",
            json={
                "cron": "0 9 * * *",
                "timezone": "Asia/Singapore",
                "start_at": "2099-01-05T09:00",
                "count": 2,
            },
            headers=_auth(token),
        )
        assert started.status_code == 200
        assert started.json()["occurrences"][0] == "2099-01-05T01:00:00Z"


def _seed_session(
    app: Any, session_id: str, status: SessionStatus = SessionStatus.IDLE
) -> None:
    runtime = app.state.context.runtime
    session_dir = runtime.settings.sessions_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    runtime.storage.create_session(
        SessionRecord(
            id=session_id,
            backend="codex",
            source=SessionSource.MANAGED,
            transport="codex_app_server",
            title="t",
            cwd="/tmp/project",
            status=status,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(session_dir / "raw.log"),
            structured_log_path=str(session_dir / "events.jsonl"),
        )
    )


async def test_create_idle_message_schedule_returns_trigger(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    _seed_session(app, "sess-1", SessionStatus.RUNNING)
    async with _client(app) as client:
        resp = await client.post(
            "/api/sessions/sess-1/message-schedules",
            json={
                "text": "after this turn",
                "trigger": "idle",
                "idle_batch_mode": "with_previous",
            },
            headers=_auth(token),
        )
    assert resp.status_code == 200
    schedule = resp.json()["message_schedule"]
    assert schedule["trigger"] == "idle"
    assert schedule["idle_batch"] == 1
    assert schedule["wait_for_idle_transition"] is False


async def test_idle_message_rejects_timing_and_bad_combos(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    _seed_session(app, "sess-1", SessionStatus.RUNNING)
    bad_bodies = [
        {"text": "x", "trigger": "idle", "delay_seconds": 60},
        {"text": "x", "trigger": "idle", "scheduled_at": "2099-01-01T00:00:00Z"},
        {"text": "x", "trigger": "idle", "cron": "0 9 * * *", "timezone": "UTC"},
        {"text": "x", "idle_batch_mode": "with_previous", "delay_seconds": 60},
    ]
    async with _client(app) as client:
        for body in bad_bodies:
            resp = await client.post(
                "/api/sessions/sess-1/message-schedules",
                json=body,
                headers=_auth(token),
            )
            assert resp.status_code == 400, body


async def test_idle_message_conflict_on_terminal_session(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    _seed_session(app, "sess-dead", SessionStatus.EXITED)
    async with _client(app) as client:
        resp = await client.post(
            "/api/sessions/sess-dead/message-schedules",
            json={"text": "x", "trigger": "idle"},
            headers=_auth(token),
        )
    assert resp.status_code == 409


async def test_preview_requires_auth(tmp_path: Path) -> None:
    app, _ = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/schedules/preview",
            json={"cron": "0 9 * * *", "timezone": "UTC"},
        )
    assert resp.status_code == 401
