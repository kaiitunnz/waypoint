import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from waypoint.api import create_app
from waypoint.runtime import SessionRuntime
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus
from waypoint.settings import Settings
from waypoint.storage import Storage


def make_session(settings: Settings, session_id: str) -> SessionRecord:
    session_dir = settings.sessions_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    return SessionRecord(
        id=session_id,
        backend="codex",
        source=SessionSource.MANAGED,
        transport="codex_app_server",
        title="Tasks",
        cwd="/tmp/project",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=str(session_dir / "raw.log"),
        structured_log_path=str(session_dir / "events.jsonl"),
    )


def make_runtime(tmp_path: Path) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    return SessionRuntime(settings, Storage(settings.database_path))


def _build(tmp_path: Path) -> tuple[Any, SessionRuntime, dict[str, str]]:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    context = app.state.context
    token = context.tokens.issue().token
    return app, context.runtime, {"Authorization": f"Bearer {token}"}


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def test_new_session_is_not_dismissed(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "s1"))
    session = runtime.get_session("s1")
    assert session.task_dock_dismissed_sequence is None
    assert session.model_dump(mode="json")["task_dock_dismissed_sequence"] is None


def test_set_task_dock_dismissed_persists_and_publishes(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "s1"))

    updated = runtime.set_task_dock_dismissed("s1", 42)
    assert updated.task_dock_dismissed_sequence == 42
    assert "s1" in runtime._dirty_session_states

    reopened = Storage(runtime.settings.database_path)
    stored = reopened.get_session("s1")
    assert stored is not None
    assert stored.task_dock_dismissed_sequence == 42

    runtime._dirty_session_states.clear()
    assert runtime.set_task_dock_dismissed("s1", 42).task_dock_dismissed_sequence == 42
    assert "s1" not in runtime._dirty_session_states

    cleared = runtime.set_task_dock_dismissed("s1", None)
    assert cleared.task_dock_dismissed_sequence is None
    assert "s1" in runtime._dirty_session_states


def test_column_added_to_existing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    Storage(db_path).close()
    connection = sqlite3.connect(db_path)
    connection.execute("ALTER TABLE sessions DROP COLUMN task_dock_dismissed_sequence")
    connection.commit()
    connection.close()

    storage = Storage(db_path)
    columns = {
        row["name"] for row in storage.connection.execute("PRAGMA table_info(sessions)")
    }
    assert "task_dock_dismissed_sequence" in columns


async def test_api_dismiss_and_show(tmp_path: Path) -> None:
    app, runtime, auth = _build(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "s1"))
    async with _client(app) as client:
        dismissed = await client.post(
            "/api/sessions/s1/task-dock/dismiss", json={"sequence": 7}, headers=auth
        )
        assert dismissed.status_code == 200
        assert dismissed.json()["session"]["task_dock_dismissed_sequence"] == 7

        fetched = await client.get("/api/sessions/s1", headers=auth)
        assert fetched.json()["session"]["task_dock_dismissed_sequence"] == 7

        shown = await client.delete("/api/sessions/s1/task-dock/dismiss", headers=auth)
        assert shown.status_code == 200
        assert shown.json()["session"]["task_dock_dismissed_sequence"] is None


async def test_api_rejects_invalid_requests(tmp_path: Path) -> None:
    app, runtime, auth = _build(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "s1"))
    async with _client(app) as client:
        negative = await client.post(
            "/api/sessions/s1/task-dock/dismiss", json={"sequence": -1}, headers=auth
        )
        assert negative.status_code == 422

        missing = await client.post(
            "/api/sessions/nope/task-dock/dismiss", json={"sequence": 1}, headers=auth
        )
        assert missing.status_code == 404

        unauthenticated = await client.delete("/api/sessions/s1/task-dock/dismiss")
        assert unauthenticated.status_code == 401
