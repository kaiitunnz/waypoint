"""Route-level tests for recurring schedule endpoints over the real app."""

from pathlib import Path
from typing import Any

import httpx

from waypoint.api import create_app
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


async def test_preview_requires_auth(tmp_path: Path) -> None:
    app, _ = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/schedules/preview",
            json={"cron": "0 9 * * *", "timezone": "UTC"},
        )
    assert resp.status_code == 401
