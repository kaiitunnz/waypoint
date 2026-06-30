"""Unit tests for CodexPlugin.delete_thread (local store)."""

import uuid
from pathlib import Path

import pytest

from waypoint.backends.codex.plugin import CodexPlugin


@pytest.fixture
def plugin() -> CodexPlugin:
    return CodexPlugin()


def _make_rollout(home: Path, thread_id: str) -> Path:
    day = home / "sessions" / "2026" / "06" / "30"
    day.mkdir(parents=True, exist_ok=True)
    rollout = day / f"rollout-2026-06-30T12-00-00-{thread_id}.jsonl"
    rollout.write_text("{}\n")
    return rollout


@pytest.mark.asyncio
async def test_delete_thread_local_removes_matching_rollout(
    plugin: CodexPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    thread_id = str(uuid.uuid4())
    rollout = _make_rollout(tmp_path, thread_id)

    assert await plugin.delete_thread(None, thread_id) is True
    assert not rollout.exists()


@pytest.mark.asyncio
async def test_delete_thread_local_missing_returns_false(
    plugin: CodexPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    kept = _make_rollout(tmp_path, str(uuid.uuid4()))

    assert await plugin.delete_thread(None, str(uuid.uuid4())) is False
    assert kept.exists()


@pytest.mark.asyncio
async def test_delete_thread_rejects_non_uuid(
    plugin: CodexPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    kept = _make_rollout(tmp_path, str(uuid.uuid4()))

    assert await plugin.delete_thread(None, "../../etc/passwd") is False
    assert await plugin.delete_thread(None, "not-a-uuid") is False
    assert kept.exists()
