"""Tests for worktree_path field on sessions: storage round-trip and delete cleanup."""

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from waypoint.schemas import SessionRecord, SessionSource, SessionStatus
from waypoint.storage import Storage


def _make_session(settings: Any, tmp_path: Path, **overrides: Any) -> SessionRecord:
    session_dir = tmp_path / overrides.get("id", "sess")
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    return SessionRecord(
        id=overrides.get("id", "sess"),
        backend=overrides.get("backend", "codex"),
        source=SessionSource.MANAGED,
        title="Test",
        cwd=overrides.get("cwd", "/tmp/project"),
        status=overrides.get("status", SessionStatus.EXITED),
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=str(session_dir / "raw.log"),
        structured_log_path=str(session_dir / "events.jsonl"),
        worktree_path=overrides.get("worktree_path"),
    )


def test_worktree_path_round_trips_through_storage(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    session = SessionRecord(
        id="wt-1",
        backend="codex",
        source=SessionSource.MANAGED,
        title="Worktree session",
        cwd="/repos/myrepo-feat",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
        worktree_path="/repos/myrepo-feat",
    )
    storage.create_session(session)
    loaded = storage.get_session("wt-1")
    assert loaded is not None
    assert loaded.worktree_path == "/repos/myrepo-feat"

    listed = storage.list_sessions()
    assert any(s.worktree_path == "/repos/myrepo-feat" for s in listed)


def test_worktree_path_none_by_default(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    session = SessionRecord(
        id="no-wt",
        backend="codex",
        source=SessionSource.MANAGED,
        title="Plain session",
        cwd="/tmp",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
    )
    storage.create_session(session)
    loaded = storage.get_session("no-wt")
    assert loaded is not None
    assert loaded.worktree_path is None


def test_delete_removes_worktree(tmp_path: Path) -> None:
    from waypoint.runtime import SessionRuntime
    from waypoint.settings import Settings

    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)

    worktree = str(tmp_path / "myrepo-feat")
    session_dir = settings.sessions_dir / "wt-del"
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    session = SessionRecord(
        id="wt-del",
        backend="codex",
        source=SessionSource.MANAGED,
        title="Worktree del",
        cwd=worktree,
        status=SessionStatus.EXITED,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=str(session_dir / "raw.log"),
        structured_log_path=str(session_dir / "events.jsonl"),
        worktree_path=worktree,
    )
    storage.create_session(session)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    with patch("waypoint.runtime.subprocess.run", side_effect=fake_run):
        asyncio.run(runtime.delete("wt-del"))

    assert any(
        c == ["git", "worktree", "remove", "--force", worktree] for c in calls
    ), f"git worktree remove not called; got: {calls}"
    assert storage.get_session("wt-del") is None


def test_delete_skips_worktree_removal_when_none(tmp_path: Path) -> None:
    from waypoint.runtime import SessionRuntime
    from waypoint.settings import Settings

    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)

    session_dir = settings.sessions_dir / "plain-del"
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    session = SessionRecord(
        id="plain-del",
        backend="codex",
        source=SessionSource.MANAGED,
        title="Plain del",
        cwd="/tmp",
        status=SessionStatus.EXITED,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=str(session_dir / "raw.log"),
        structured_log_path=str(session_dir / "events.jsonl"),
    )
    storage.create_session(session)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    with patch("waypoint.runtime.subprocess.run", side_effect=fake_run):
        asyncio.run(runtime.delete("plain-del"))

    assert not any("worktree" in c for c in calls), f"unexpected worktree call: {calls}"
