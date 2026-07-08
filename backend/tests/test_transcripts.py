"""Native transcript availability policies (Phase 3b).

Exercises the per-backend artifact locator and the require_existing /
symlink_shared / copy_thread_on_switch policies against real claude_code/codex
plugins with temporary config dirs, through the TranscriptFilesystem seam
(local by default; a recording fake proves the policy code is IO-agnostic).
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from waypoint.backends.base import BackendPlugin
from waypoint.backends.bootstrap import build_default_registry
from waypoint.backends.transcript_fs import LocalTranscriptFilesystem
from waypoint.backends.transcripts import (
    TranscriptUnavailableError,
    ensure_symlink_shared,
    ensure_thread_available,
    setup_transcripts_symlink,
)
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus

TID = "11111111-1111-1111-1111-111111111111"


class _RecordingFilesystem:
    """Wraps ``LocalTranscriptFilesystem``, logging every call.

    Proves ``transcripts.py`` policy logic dispatches through ``fs`` rather
    than reaching for ``pathlib``/``shutil`` directly — the property a remote
    implementation depends on.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._inner = LocalTranscriptFilesystem()

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)

        def _recording(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args))
            return target(*args, **kwargs)

        return _recording


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


def test_copy_thread_on_switch_leaves_preexisting_dirs_untouched(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    target = tmp_path / "target"
    _write_codex_rollout(current)
    # A pre-existing target config root with a non-0700 mode must not be
    # re-chmod'd by the copy — only newly-created dirs are locked down.
    target.mkdir()
    target.chmod(0o755)
    ensure_thread_available(
        _plugin("codex"),
        _session("codex"),
        current_config_dir=str(current),
        target_config_dir=str(target),
        policy="copy_thread_on_switch",
        shared_transcript_dir=None,
        native_thread_store="sessions",
    )
    assert oct(target.stat().st_mode)[-3:] == "755"
    # ...but a dir this copy created is owner-only.
    assert oct((target / "sessions").stat().st_mode)[-3:] == "700"


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


# ── setup_transcripts_symlink (accounts setup-transcripts) ──────────────────


def test_setup_creates_symlink_when_store_missing(tmp_path: Path) -> None:
    store = tmp_path / "config" / "projects"
    shared = tmp_path / "shared"
    actions = setup_transcripts_symlink(store, shared)
    assert store.is_symlink()
    assert store.resolve() == shared.resolve()
    assert any("linked" in a for a in actions)


def test_setup_is_noop_on_correct_symlink(tmp_path: Path) -> None:
    store = tmp_path / "config" / "projects"
    shared = tmp_path / "shared"
    setup_transcripts_symlink(store, shared)
    actions = setup_transcripts_symlink(store, shared)
    assert store.is_symlink()
    assert any("already links" in a for a in actions)


def test_setup_rejects_symlink_to_other_target(tmp_path: Path) -> None:
    store = tmp_path / "config" / "projects"
    other = tmp_path / "other"
    other.mkdir()
    store.parent.mkdir(parents=True)
    store.symlink_to(other)
    with pytest.raises(TranscriptUnavailableError, match="not the configured"):
        setup_transcripts_symlink(store, tmp_path / "shared")


def test_setup_replaces_empty_real_dir(tmp_path: Path) -> None:
    store = tmp_path / "config" / "projects"
    store.mkdir(parents=True)
    shared = tmp_path / "shared"
    setup_transcripts_symlink(store, shared)
    assert store.is_symlink()
    assert store.resolve() == shared.resolve()


def test_setup_migrates_populated_dir_with_backup(tmp_path: Path) -> None:
    store = tmp_path / "config" / "projects"
    (store / "proj").mkdir(parents=True)
    (store / "proj" / "a.jsonl").write_text("thread-a")
    (store / "top.jsonl").write_text("thread-top")
    shared = tmp_path / "shared"

    actions = setup_transcripts_symlink(store, shared)

    # Store is now the symlink; contents are visible through it and in shared.
    assert store.is_symlink()
    assert (store / "proj" / "a.jsonl").read_text() == "thread-a"
    assert sorted(p.name for p in shared.iterdir()) == ["proj", "top.jsonl"]
    # Perms pinned: 0700 dirs, 0600 files.
    assert (shared / "proj").stat().st_mode & 0o777 == 0o700
    assert (shared / "top.jsonl").stat().st_mode & 0o777 == 0o600
    # A complete backup of the original dir is kept.
    backups = [p for p in store.parent.iterdir() if p.name.startswith("projects.bak-")]
    assert len(backups) == 1
    assert (backups[0] / "top.jsonl").read_text() == "thread-top"
    assert any("backed up" in a for a in actions)


def test_setup_refuses_conflict_and_does_not_mutate(tmp_path: Path) -> None:
    store = tmp_path / "config" / "projects"
    (store / "dup").mkdir(parents=True)
    (store / "dup" / "f.jsonl").write_text("orig")
    shared = tmp_path / "shared"
    (shared / "dup").mkdir(parents=True)

    with pytest.raises(TranscriptUnavailableError, match="dup"):
        setup_transcripts_symlink(store, shared)

    # Nothing moved: store is still the original real dir, no symlink, no backup.
    assert store.is_dir() and not store.is_symlink()
    assert (store / "dup" / "f.jsonl").read_text() == "orig"
    assert not any(p.name.startswith("projects.bak-") for p in store.parent.iterdir())


# ── native_thread_artifact_glob (discovery-pattern contract) ───────────────


def test_native_thread_artifact_glob_claude() -> None:
    pattern = _plugin("claude_code").native_thread_artifact_glob(
        _session("claude_code")
    )
    assert pattern == f"projects/*/{TID}.jsonl"


def test_native_thread_artifact_glob_codex() -> None:
    pattern = _plugin("codex").native_thread_artifact_glob(_session("codex"))
    assert pattern == f"sessions/*/*/*/rollout-*-{TID}.jsonl"


def test_native_thread_artifact_glob_none_without_thread_id() -> None:
    now = datetime.now(UTC)
    session = SessionRecord(
        id="s1",
        backend="claude_code",
        source=SessionSource.MANAGED,
        title="t",
        cwd="/repo/app",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/r",
        structured_log_path="/e",
        transport_state={},
    )
    assert _plugin("claude_code").native_thread_artifact_glob(session) is None


def test_native_thread_artifact_glob_opencode_always_none() -> None:
    assert _plugin("opencode").native_thread_artifact_glob(_session("opencode")) is None


def test_glob_artifacts_matches_native_thread_artifacts(tmp_path: Path) -> None:
    # The pattern-driven Path.glob discovery LocalTranscriptFilesystem uses must
    # find exactly what the per-backend native_thread_artifacts locator finds.
    config_dir = tmp_path / "config"
    src = _write_claude_thread(config_dir)
    plugin = _plugin("claude_code")
    session = _session("claude_code")
    fs = LocalTranscriptFilesystem()
    assert fs.glob_artifacts(session, plugin, str(config_dir)) == [str(src)]
    assert [
        str(p) for p in plugin.native_thread_artifacts(session, str(config_dir))
    ] == [str(src)]


# ── TranscriptFilesystem seam ───────────────────────────────────────────────


def test_ensure_thread_available_dispatches_through_custom_fs(tmp_path: Path) -> None:
    # A drop-in fs (a recorder here; a remote implementation in a later phase)
    # must be able to drive the whole require_existing policy on its own —
    # proof the policy code never falls back to pathlib/shutil directly.
    target = tmp_path / "target"
    _write_claude_thread(target)
    fs = _RecordingFilesystem()
    plugin = _plugin("claude_code")
    session = _session("claude_code")
    ensure_thread_available(
        plugin,
        session,
        current_config_dir=str(tmp_path / "current"),
        target_config_dir=str(target),
        policy="require_existing",
        shared_transcript_dir=None,
        native_thread_store="projects",
        fs=fs,
    )
    assert fs.calls == [("glob_artifacts", (session, plugin, str(target)))]


def test_copy_thread_on_switch_through_custom_fs_matches_default(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    target = tmp_path / "target"
    src = _write_codex_rollout(current)
    fs = _RecordingFilesystem()
    ensure_thread_available(
        _plugin("codex"),
        _session("codex"),
        current_config_dir=str(current),
        target_config_dir=str(target),
        policy="copy_thread_on_switch",
        shared_transcript_dir=None,
        native_thread_store="sessions",
        fs=fs,
    )
    dest = target / src.relative_to(current)
    assert dest.is_file()
    assert oct(dest.stat().st_mode)[-3:] == "600"
    # Every mutating op the policy performed went through the recorder, not a
    # pathlib/shutil call the seam bypassed.
    op_names = [name for name, _ in fs.calls]
    assert "copy_file" in op_names
    assert "mkdir" in op_names


def test_symlink_shared_through_custom_fs_matches_default(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    (shared / "-repo-app").mkdir(parents=True)
    (shared / "-repo-app" / f"{TID}.jsonl").write_text("{}")
    target = tmp_path / "target"
    fs = _RecordingFilesystem()
    ensure_thread_available(
        _plugin("claude_code"),
        _session("claude_code"),
        current_config_dir=str(tmp_path / "current"),
        target_config_dir=str(target),
        policy="symlink_shared",
        shared_transcript_dir=str(shared),
        native_thread_store="projects",
        fs=fs,
    )
    assert (target / "projects").is_symlink()
    op_names = [name for name, _ in fs.calls]
    assert "symlink" in op_names
    assert "is_symlink" in op_names
