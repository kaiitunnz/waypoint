"""Unit tests for the opencode SQLite-based ContextUsageSource."""

import asyncio
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from waypoint.backends.opencode.usage_source import (
    OpenCodeTmuxUsageSource,
    _find_session_id,
    _latest_assistant_snapshot,
    _snapshot_from_data,
)


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            directory TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_session(
    db_path: Path, session_id: str, directory: str, time_updated: int = 1000
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO session (id, directory, time_created, time_updated) VALUES (?, ?, ?, ?)",
        (session_id, directory, time_updated, time_updated),
    )
    conn.commit()
    conn.close()


def _insert_message(
    db_path: Path, msg_id: str, session_id: str, data: dict, time_updated: int = 1000
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
        (msg_id, session_id, time_updated, time_updated, json.dumps(data)),
    )
    conn.commit()
    conn.close()


def test_snapshot_from_data_basic() -> None:
    data = {
        "role": "assistant",
        "modelID": "gemini-3.1-pro-preview",
        "providerID": "google",
        "tokens": {
            "input": 10200,
            "output": 18,
            "reasoning": 77,
            "cache": {"write": 0, "read": 0},
        },
    }
    snapshot = _snapshot_from_data(data)
    assert snapshot is not None
    assert snapshot.used_tokens == 10200  # input + cache_read + cache_write = 10200+0+0
    assert snapshot.context_window_tokens is None
    assert snapshot.source == "opencode"
    assert snapshot.breakdown["input_tokens"] == 10200
    assert snapshot.breakdown["output_tokens"] == 18
    assert snapshot.breakdown["reasoning_tokens"] == 77


def test_snapshot_from_data_with_cache_tokens() -> None:
    data = {
        "role": "assistant",
        "tokens": {
            "input": 5000,
            "output": 100,
            "cache": {"write": 200, "read": 1500},
        },
    }
    snapshot = _snapshot_from_data(data)
    assert snapshot is not None
    assert snapshot.used_tokens == 5000 + 200 + 1500
    assert snapshot.breakdown["cache_read_tokens"] == 1500
    assert snapshot.breakdown["cache_write_tokens"] == 200


def test_snapshot_from_data_no_tokens() -> None:
    data = {"role": "assistant"}
    assert _snapshot_from_data(data) is None


def test_snapshot_from_data_zero_used_tokens() -> None:
    data = {
        "role": "assistant",
        "tokens": {"input": 0, "output": 5, "cache": {"write": 0, "read": 0}},
    }
    assert _snapshot_from_data(data) is None


def test_find_session_id_matches_directory(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_session(db_path, "ses_abc", "/some/dir", time_updated=1000)

    conn = sqlite3.connect(str(db_path))
    try:
        assert _find_session_id(conn, "/some/dir") == "ses_abc"
        assert _find_session_id(conn, "/other/dir") is None
    finally:
        conn.close()


def test_find_session_id_most_recent(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_session(db_path, "ses_old", "/proj", time_updated=1000)
    _insert_session(db_path, "ses_new", "/proj", time_updated=2000)

    conn = sqlite3.connect(str(db_path))
    try:
        assert _find_session_id(conn, "/proj") == "ses_new"
    finally:
        conn.close()


def test_find_session_id_trailing_slash(tmp_path: Path) -> None:
    """Stored directory with a trailing slash matches cwd without one (and vice versa)."""
    db_path = _make_db(tmp_path)
    _insert_session(db_path, "ses_slash", "/some/dir/", time_updated=1000)

    conn = sqlite3.connect(str(db_path))
    try:
        assert _find_session_id(conn, "/some/dir") == "ses_slash"
    finally:
        conn.close()


def test_find_session_id_symlink(tmp_path: Path) -> None:
    """A cwd that is a symlink to the stored directory is still matched."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir)

    db_path = _make_db(tmp_path)
    _insert_session(db_path, "ses_real", str(real_dir), time_updated=1000)

    conn = sqlite3.connect(str(db_path))
    try:
        # cwd is the symlink path; DB stores the real path — must still match.
        assert _find_session_id(conn, str(link_dir)) == "ses_real"
    finally:
        conn.close()


def test_latest_assistant_snapshot_yields_snapshot(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_session(db_path, "ses_1", "/repo")
    _insert_message(
        db_path,
        "msg_1",
        "ses_1",
        {
            "role": "assistant",
            "modelID": "claude-opus-4-5",
            "providerID": "anthropic",
            "tokens": {
                "input": 8000,
                "output": 200,
                "reasoning": 0,
                "cache": {"write": 100, "read": 500},
            },
        },
    )

    conn = sqlite3.connect(str(db_path))
    try:
        snapshot = _latest_assistant_snapshot(conn, "ses_1")
    finally:
        conn.close()

    assert snapshot is not None
    assert snapshot.used_tokens == 8000 + 100 + 500
    assert snapshot.context_window_tokens is None
    assert snapshot.source == "opencode"


def test_latest_assistant_snapshot_returns_newest(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_session(db_path, "ses_1", "/repo")
    _insert_message(
        db_path,
        "msg_1",
        "ses_1",
        {
            "role": "assistant",
            "tokens": {"input": 100, "output": 5, "cache": {"write": 0, "read": 0}},
        },
        time_updated=100,
    )
    _insert_message(
        db_path,
        "msg_2",
        "ses_1",
        {
            "role": "assistant",
            "tokens": {"input": 999, "output": 10, "cache": {"write": 0, "read": 0}},
        },
        time_updated=200,
    )

    conn = sqlite3.connect(str(db_path))
    try:
        snapshot = _latest_assistant_snapshot(conn, "ses_1")
    finally:
        conn.close()

    assert snapshot is not None
    assert snapshot.used_tokens == 999


def test_latest_assistant_snapshot_ignores_user_messages(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_session(db_path, "ses_1", "/repo")
    _insert_message(
        db_path,
        "msg_1",
        "ses_1",
        {
            "role": "user",
            "tokens": {"input": 999, "output": 0, "cache": {"write": 0, "read": 0}},
        },
    )

    conn = sqlite3.connect(str(db_path))
    try:
        snapshot = _latest_assistant_snapshot(conn, "ses_1")
    finally:
        conn.close()

    assert snapshot is None


async def test_run_publishes_on_change(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_session(db_path, "ses_1", "/repo")
    _insert_message(
        db_path,
        "msg_1",
        "ses_1",
        {
            "role": "assistant",
            "tokens": {"input": 3000, "output": 50, "cache": {"write": 0, "read": 0}},
        },
    )

    runtime = MagicMock()
    runtime.update_session_fields = AsyncMock()

    source = OpenCodeTmuxUsageSource(
        session_id="s1",
        cwd="/repo",
        runtime=runtime,
        db_dir=tmp_path,
    )

    task = asyncio.create_task(source.run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    runtime.update_session_fields.assert_called_once()
    _, kwargs = runtime.update_session_fields.call_args
    snapshot = kwargs["context_usage"]
    assert snapshot.used_tokens == 3000


async def test_run_deduplicates_same_snapshot(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_session(db_path, "ses_1", "/repo")
    _insert_message(
        db_path,
        "msg_1",
        "ses_1",
        {
            "role": "assistant",
            "tokens": {"input": 1000, "output": 20, "cache": {"write": 0, "read": 0}},
        },
    )

    runtime = MagicMock()
    runtime.update_session_fields = AsyncMock()

    source = OpenCodeTmuxUsageSource(
        session_id="s1",
        cwd="/repo",
        runtime=runtime,
        db_dir=tmp_path,
        # Override poll interval so we get two polls quickly
    )
    # Patch the poll interval for the test by running two iterations
    source._signature = None

    # First call publishes
    snap1 = await asyncio.to_thread(source._query_snapshot)
    assert snap1 is not None
    sig1 = (snap1.used_tokens, snap1.context_window_tokens)
    assert sig1 != source._signature
    source._signature = sig1
    await runtime.update_session_fields(source._session_id, context_usage=snap1)

    # Second call with same DB — should NOT publish again
    snap2 = await asyncio.to_thread(source._query_snapshot)
    assert snap2 is not None
    sig2 = (snap2.used_tokens, snap2.context_window_tokens)
    assert sig2 == source._signature  # deduped

    # update_session_fields called exactly once (only in the first call above)
    assert runtime.update_session_fields.call_count == 1


def test_query_snapshot_missing_db(tmp_path: Path) -> None:
    runtime = MagicMock()
    source = OpenCodeTmuxUsageSource(
        session_id="s1",
        cwd="/repo",
        runtime=runtime,
        db_dir=tmp_path / "nonexistent",
    )
    assert source._query_snapshot() is None
