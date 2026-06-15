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


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo_with_worktree(tmp_path: Path, branch: str) -> tuple[Path, Path]:
    """Init a repo with one commit and a worktree on ``branch``; return both paths."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    worktree = tmp_path / f"repo-{branch.replace('/', '-')}"
    _git(repo, "worktree", "add", "-q", str(worktree), "-b", branch)
    return repo, worktree


def _branch_exists(repo: Path, branch: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", branch],
            capture_output=True,
        ).returncode
        == 0
    )


def _delete_worktree_session(
    tmp_path: Path, worktree: Path, *, prune_branches: bool
) -> None:
    from waypoint.runtime import SessionRuntime
    from waypoint.settings import Settings

    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    session = _make_session(
        settings, settings.sessions_dir, id="wt", worktree_path=str(worktree)
    )
    storage.create_session(session)
    asyncio.run(runtime.delete("wt", prune_branches=prune_branches))


def test_delete_prunes_merged_worktree_branch(tmp_path: Path) -> None:
    repo, worktree = _init_repo_with_worktree(tmp_path, "wq/job-t1")
    # The branch is at the integration tip — merged — so the safe `-d` prunes it.
    _delete_worktree_session(tmp_path, worktree, prune_branches=False)
    assert not worktree.exists()
    assert not _branch_exists(repo, "wq/job-t1")


def test_delete_keeps_unmerged_branch_without_prune(tmp_path: Path) -> None:
    repo, worktree = _init_repo_with_worktree(tmp_path, "wq/job-t2")
    (worktree / "work.txt").write_text("wip\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "-m", "wip")
    # Unmerged work: `-d` must refuse, leaving the branch for the lead to merge.
    _delete_worktree_session(tmp_path, worktree, prune_branches=False)
    assert not worktree.exists()
    assert _branch_exists(repo, "wq/job-t2")


def test_delete_force_prunes_unmerged_branch(tmp_path: Path) -> None:
    repo, worktree = _init_repo_with_worktree(tmp_path, "wq/job-t3")
    (worktree / "work.txt").write_text("wip\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "-m", "wip")
    # Crew teardown: --prune-branches force-deletes even the unmerged branch.
    _delete_worktree_session(tmp_path, worktree, prune_branches=True)
    assert not worktree.exists()
    assert not _branch_exists(repo, "wq/job-t3")


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
