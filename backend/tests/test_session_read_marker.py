"""The per-session read marker: storage rules, migration, and the presence seam."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from waypoint.api import create_app
from waypoint.backends.tmux.adapter import TmuxTarget
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    EventKind,
    SessionAttachRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage

START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _session(session_id: str = "s1", last_event_at: datetime = START) -> SessionRecord:
    return SessionRecord(
        id=session_id,
        backend="claude_code",
        transport="claude_cli",
        source=SessionSource.MANAGED,
        title="Test Session",
        cwd="/tmp",
        status=SessionStatus.IDLE,
        created_at=START,
        updated_at=last_event_at,
        last_event_at=last_event_at,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
    )


def _unread(session: SessionRecord) -> bool:
    return session.read_at is not None and session.last_event_at > session.read_at


def _runtime(tmp_path: Path) -> tuple[SessionRuntime, Storage]:
    settings = Settings(data_dir=tmp_path / "data")
    storage = Storage(settings.database_path)
    return SessionRuntime(settings, storage), storage


async def _activity(runtime: SessionRuntime, session_id: str) -> None:
    await runtime._emit_adapter_event(
        session_id, EventKind.AGENT_OUTPUT, "hello", {}, SessionStatus.IDLE
    )


def test_new_session_starts_read(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    created = storage.create_session(_session())
    assert created.read_at is None
    stored = storage.get_session("s1")
    assert stored is not None
    assert stored.read_at == START
    assert not _unread(stored)


def test_existing_rows_start_read(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    storage = Storage(path)
    storage.create_session(_session())
    storage.connection.execute("UPDATE sessions SET read_at = NULL")
    storage.connection.commit()
    storage.close()

    reopened = Storage(path)
    session = reopened.get_session("s1")
    assert session is not None
    assert session.read_at == START


def test_mark_read_only_advances_and_reports_the_flip(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    storage.create_session(_session())
    later = START + timedelta(minutes=5)
    storage.connection.execute(
        "UPDATE sessions SET last_event_at = ? WHERE id = 's1'", (later.isoformat(),)
    )
    storage.connection.commit()

    # Behind the latest event: still unread, no flip.
    assert storage.mark_session_read("s1", START + timedelta(minutes=1)) is False
    session = storage.get_session("s1")
    assert session is not None
    assert _unread(session)

    assert storage.mark_session_read("s1", later) is True
    session = storage.get_session("s1")
    assert session is not None
    assert session.read_at == later
    assert not _unread(session)

    # Never moves back, and reading does not touch updated_at.
    assert storage.mark_session_read("s1", START) is False
    again = storage.get_session("s1")
    assert again is not None
    assert again.read_at == later
    assert again.updated_at == session.updated_at


def test_mark_read_unknown_session(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    assert storage.mark_session_read("nope", START) is False


async def test_activity_nobody_watched_is_unread(tmp_path: Path) -> None:
    runtime, storage = _runtime(tmp_path)
    storage.create_session(_session(last_event_at=datetime.now(UTC)))
    await _activity(runtime, "s1")
    session = storage.get_session("s1")
    assert session is not None
    assert _unread(session)


async def test_activity_while_viewed_is_read(tmp_path: Path) -> None:
    runtime, storage = _runtime(tmp_path)
    storage.create_session(_session(last_event_at=datetime.now(UTC)))
    runtime.register_presence("s1", "viewer-1")
    await _activity(runtime, "s1")
    session = storage.get_session("s1")
    assert session is not None
    assert not _unread(session)
    assert session.read_at == session.last_event_at


async def test_register_marks_unread_session_read_and_broadcasts(
    tmp_path: Path,
) -> None:
    runtime, storage = _runtime(tmp_path)
    storage.create_session(_session(last_event_at=datetime.now(UTC)))
    await _activity(runtime, "s1")
    runtime._dirty_session_states.clear()

    runtime.register_presence("s1", "viewer-1")
    session = storage.get_session("s1")
    assert session is not None
    assert not _unread(session)
    assert "s1" in runtime._dirty_session_states

    # A renewal with nothing new changes nothing and broadcasts nothing.
    runtime._dirty_session_states.clear()
    runtime.register_presence("s1", "viewer-1")
    assert runtime._dirty_session_states == set()


async def test_release_sets_marker_to_now_and_later_activity_is_unread(
    tmp_path: Path,
) -> None:
    runtime, storage = _runtime(tmp_path)
    storage.create_session(_session(last_event_at=datetime.now(UTC)))
    runtime.register_presence("s1", "viewer-1")
    await _activity(runtime, "s1")

    before = datetime.now(UTC)
    runtime.release_presence("s1", "viewer-1")
    released = storage.get_session("s1")
    assert released is not None
    assert released.read_at is not None
    assert released.read_at >= before
    assert not _unread(released)

    await _activity(runtime, "s1")
    unwatched = storage.get_session("s1")
    assert unwatched is not None
    assert _unread(unwatched)


async def test_marker_follows_until_the_last_viewer_leaves(tmp_path: Path) -> None:
    runtime, storage = _runtime(tmp_path)
    storage.create_session(_session(last_event_at=datetime.now(UTC)))
    runtime.register_presence("s1", "phone")
    runtime.register_presence("s1", "desk")
    runtime.release_presence("s1", "phone")
    await _activity(runtime, "s1")
    session = storage.get_session("s1")
    assert session is not None
    assert not _unread(session)

    runtime.release_presence("s1", "desk")
    await _activity(runtime, "s1")
    session = storage.get_session("s1")
    assert session is not None
    assert _unread(session)


def test_presence_on_unknown_session_is_404(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path)
    with pytest.raises(Exception) as caught:
        runtime.register_presence("nope", "viewer-1")
    assert getattr(caught.value, "status_code", None) == 404


async def test_presence_endpoints_move_the_marker_and_listing_carries_it(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    context = app.state.context
    context.storage.create_session(_session(last_event_at=datetime.now(UTC)))
    await _activity(context.runtime, "s1")
    headers = {"Authorization": f"Bearer {context.tokens.issue().token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        listing = (await client.get("/api/sessions?scope=live", headers=headers)).json()
        record = listing["sessions"][0]
        assert record["read_at"] is not None
        assert record["last_event_at"] > record["read_at"]

        resp = await client.post(
            "/api/sessions/s1/presence", json={"viewer_id": "v1"}, headers=headers
        )
        assert resp.status_code == 204
        listing = (await client.get("/api/sessions?scope=live", headers=headers)).json()
        record = listing["sessions"][0]
        assert record["last_event_at"] <= record["read_at"]

        resp = await client.delete("/api/sessions/s1/presence/v1", headers=headers)
        assert resp.status_code == 204
        listing = (await client.get("/api/sessions?scope=live", headers=headers)).json()
        assert (
            listing["sessions"][0]["last_event_at"] <= listing["sessions"][0]["read_at"]
        )


async def test_content_bump_on_heuristic_transport_follows_presence(
    tmp_path: Path,
) -> None:
    runtime, storage = _runtime(tmp_path)
    storage.create_session(_session(last_event_at=datetime.now(UTC)))
    runtime.register_presence("s1", "viewer-1")
    later = datetime.now(UTC)
    storage.update_session("s1", last_event_at=later)
    runtime._follow_presence("s1", later)
    session = storage.get_session("s1")
    assert session is not None
    assert not _unread(session)

    runtime.release_presence("s1", "viewer-1")
    after = datetime.now(UTC)
    storage.update_session("s1", last_event_at=after)
    runtime._follow_presence("s1", after)
    session = storage.get_session("s1")
    assert session is not None
    assert _unread(session)


class _RestoringPlugin:
    """A plugin whose restore records a lifecycle note, as the real ones do."""

    async def restore_session(self, runtime: SessionRuntime, session: Any) -> None:
        await runtime._record_system_event(session.id, "Restored the session")


def _quiet_restore(runtime: SessionRuntime, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_warm_command_completions", lambda *_, **__: None)
    monkeypatch.setattr(runtime, "_start_context_usage_source", lambda *_: None)


async def test_restoring_a_read_session_keeps_it_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, storage = _runtime(tmp_path)
    _quiet_restore(runtime, monkeypatch)
    storage.create_session(_session(last_event_at=datetime.now(UTC)))
    session = storage.get_session("s1")
    assert session is not None

    await runtime._restore_session_and_warm_completions(_RestoringPlugin(), session)

    restored = storage.get_session("s1")
    assert restored is not None
    assert restored.last_event_at > session.last_event_at
    assert not _unread(restored)

    await _activity(runtime, "s1")
    unwatched = storage.get_session("s1")
    assert unwatched is not None
    assert _unread(unwatched)


async def test_restoring_an_unread_session_leaves_it_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, storage = _runtime(tmp_path)
    _quiet_restore(runtime, monkeypatch)
    storage.create_session(_session(last_event_at=datetime.now(UTC)))
    await _activity(runtime, "s1")
    unread = storage.get_session("s1")
    assert unread is not None
    assert _unread(unread)

    await runtime._restore_session_and_warm_completions(_RestoringPlugin(), unread)

    restored = storage.get_session("s1")
    assert restored is not None
    assert _unread(restored)
    assert restored.read_at == unread.read_at


async def test_launched_session_counts_as_read_until_the_agent_acts(
    tmp_path: Path,
) -> None:
    runtime, storage = _runtime(tmp_path)
    storage.create_session(_session(last_event_at=datetime.now(UTC)))
    await runtime._record_system_event("s1", "Started the session")
    noted = storage.get_session("s1")
    assert noted is not None
    assert _unread(noted)

    launched = runtime._launched_read("s1")
    assert not _unread(launched)

    await _activity(runtime, "s1")
    acted = storage.get_session("s1")
    assert acted is not None
    assert _unread(acted)


async def test_attached_session_starts_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, storage = _runtime(tmp_path)
    runtime.settings.ensure_dirs()
    target = TmuxTarget(
        session="work",
        window="1",
        pane="%3",
        cwd=str(tmp_path),
        pane_dead=False,
        pane_pid=None,
    )

    async def describe_target(_: str) -> TmuxTarget:
        return target

    async def capture_snapshot(_: str, __: int) -> str:
        return ""

    async def pipe_output(_: str, __: Path) -> None:
        return None

    monkeypatch.setattr(runtime.tmux, "describe_target", describe_target)
    monkeypatch.setattr(runtime.tmux, "capture_snapshot", capture_snapshot)
    monkeypatch.setattr(runtime.tmux, "pipe_output", pipe_output)
    monkeypatch.setattr(runtime, "_ensure_monitor", lambda _: None)

    attached = await runtime.attach_tmux(
        SessionAttachRequest(tmux_target="work:1.0", backend_hint="codex")
    )

    assert attached.read_at is not None
    assert not _unread(attached)
    stored = storage.get_session(attached.id)
    assert stored is not None
    assert not _unread(stored)

    await _activity(runtime, attached.id)
    acted = storage.get_session(attached.id)
    assert acted is not None
    assert _unread(acted)
