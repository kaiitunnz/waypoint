"""Native transcript availability policies (Phase 3b).

Exercises the per-backend artifact locator and the local require_existing /
symlink_shared / copy_thread_on_switch policies against real claude_code/codex
plugins with temporary config dirs.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoint.backends.base import BackendPlugin
from waypoint.backends.bootstrap import build_default_registry
from waypoint.backends.transcripts import (
    TranscriptUnavailableError,
    ensure_symlink_shared,
    ensure_thread_available,
)
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus

TID = "11111111-1111-1111-1111-111111111111"


def _plugin(backend: str) -> BackendPlugin:
    return build_default_registry().get(backend)


def _session(backend: str) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id="s1",
        backend=backend,
        source=SessionSource.MANAGED,
        title="t",
        cwd="/repo/app",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/r",
        structured_log_path="/e",
        transport_state={"thread_id": TID},
    )


def _write_claude_thread(config_dir: Path, project: str = "-repo-app") -> Path:
    proj = config_dir / "projects" / project
    proj.mkdir(parents=True)
    path = proj / f"{TID}.jsonl"
    path.write_text("{}")
    return path


def _write_codex_rollout(config_dir: Path) -> Path:
    day = config_dir / "sessions" / "2026" / "07" / "08"
    day.mkdir(parents=True)
    path = day / f"rollout-2026-07-08T00-00-00-{TID}.jsonl"
    path.write_text("{}")
    return path


# ── require_existing ────────────────────────────────────────────────────────


def test_require_existing_noop_when_present(tmp_path: Path) -> None:
    target = tmp_path / "target"
    _write_claude_thread(target)
    # Already visible under the target -> returns without touching anything.
    ensure_thread_available(
        _plugin("claude_code"),
        _session("claude_code"),
        current_config_dir=str(tmp_path / "current"),
        target_config_dir=str(target),
        policy="require_existing",
        shared_transcript_dir=None,
        native_thread_store="projects",
    )


def test_require_existing_rejects_when_absent(tmp_path: Path) -> None:
    with pytest.raises(TranscriptUnavailableError, match="require_existing"):
        ensure_thread_available(
            _plugin("claude_code"),
            _session("claude_code"),
            current_config_dir=str(tmp_path / "current"),
            target_config_dir=str(tmp_path / "target"),
            policy="require_existing",
            shared_transcript_dir=None,
            native_thread_store="projects",
        )


# ── copy_thread_on_switch ───────────────────────────────────────────────────


def test_copy_thread_on_switch_copies_codex_rollout(tmp_path: Path) -> None:
    current = tmp_path / "current"
    target = tmp_path / "target"
    src = _write_codex_rollout(current)
    ensure_thread_available(
        _plugin("codex"),
        _session("codex"),
        current_config_dir=str(current),
        target_config_dir=str(target),
        policy="copy_thread_on_switch",
        shared_transcript_dir=None,
        native_thread_store="sessions",
    )
    dest = target / src.relative_to(current)
    assert dest.is_file()
    # Copied only the one artifact, with restrictive perms.
    assert oct(dest.stat().st_mode)[-3:] == "600"
    assert _plugin("codex").native_thread_artifacts(_session("codex"), str(target))


def test_copy_thread_on_switch_rejects_when_source_missing(tmp_path: Path) -> None:
    with pytest.raises(TranscriptUnavailableError, match="not found to copy"):
        ensure_thread_available(
            _plugin("codex"),
            _session("codex"),
            current_config_dir=str(tmp_path / "current"),
            target_config_dir=str(tmp_path / "target"),
            policy="copy_thread_on_switch",
            shared_transcript_dir=None,
            native_thread_store="sessions",
        )


# ── symlink_shared (guarded conversion) ─────────────────────────────────────


def test_symlink_shared_creates_symlink_when_missing(tmp_path: Path) -> None:
    store = tmp_path / "target" / "projects"
    shared = tmp_path / "shared"
    ensure_symlink_shared(store, shared)
    assert store.is_symlink()
    assert store.resolve() == shared.resolve()


def test_symlink_shared_idempotent_on_correct_symlink(tmp_path: Path) -> None:
    store = tmp_path / "target" / "projects"
    shared = tmp_path / "shared"
    ensure_symlink_shared(store, shared)
    ensure_symlink_shared(store, shared)  # no error on second run
    assert store.resolve() == shared.resolve()


def test_symlink_shared_rejects_wrong_symlink(tmp_path: Path) -> None:
    store = tmp_path / "target" / "projects"
    store.parent.mkdir(parents=True)
    store.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(TranscriptUnavailableError, match="not the configured"):
        ensure_symlink_shared(store, tmp_path / "shared")


def test_symlink_shared_replaces_empty_real_dir(tmp_path: Path) -> None:
    store = tmp_path / "target" / "projects"
    store.mkdir(parents=True)
    ensure_symlink_shared(store, tmp_path / "shared")
    assert store.is_symlink()


def test_symlink_shared_refuses_nonempty_real_dir(tmp_path: Path) -> None:
    store = tmp_path / "target" / "projects"
    store.mkdir(parents=True)
    (store / "keep.jsonl").write_text("{}")
    with pytest.raises(TranscriptUnavailableError, match="non-empty"):
        ensure_symlink_shared(store, tmp_path / "shared")


def test_symlink_shared_end_to_end_makes_thread_visible(tmp_path: Path) -> None:
    # Shared dir already holds the thread (the current profile also points here);
    # symlinking the target's projects dir at it makes the thread visible.
    shared = tmp_path / "shared"
    (shared / "-repo-app").mkdir(parents=True)
    (shared / "-repo-app" / f"{TID}.jsonl").write_text("{}")
    target = tmp_path / "target"
    ensure_thread_available(
        _plugin("claude_code"),
        _session("claude_code"),
        current_config_dir=str(tmp_path / "current"),
        target_config_dir=str(target),
        policy="symlink_shared",
        shared_transcript_dir=str(shared),
        native_thread_store="projects",
    )
    assert (target / "projects").is_symlink()
    assert _plugin("claude_code").native_thread_artifacts(
        _session("claude_code"), str(target)
    )


def test_symlink_shared_rechecks_and_rejects_when_shared_lacks_thread(
    tmp_path: Path,
) -> None:
    # Shared dir is empty, so after symlinking the thread still isn't visible;
    # the re-check turns that into a clear failure rather than a false success.
    with pytest.raises(TranscriptUnavailableError, match="still unavailable"):
        ensure_thread_available(
            _plugin("claude_code"),
            _session("claude_code"),
            current_config_dir=str(tmp_path / "current"),
            target_config_dir=str(tmp_path / "target"),
            policy="symlink_shared",
            shared_transcript_dir=str(tmp_path / "shared"),
            native_thread_store="projects",
        )
