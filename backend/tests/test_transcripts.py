"""Native transcript availability policies (Phase 3b).

Exercises the per-backend artifact locator and the require_existing /
symlink_shared / copy_thread_on_switch policies against real claude_code/codex
plugins with temporary config dirs, through the TranscriptFilesystem seam
(local by default; a recording fake proves the policy code is IO-agnostic).
"""

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from waypoint.backends.base import BackendPlugin
from waypoint.backends.bootstrap import build_default_registry
from waypoint.backends.registry import reset_registry_for_tests
from waypoint.backends.transcript_fs import LocalTranscriptFilesystem
from waypoint.backends.transcripts import (
    ThreadAvailability,
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


def test_copy_thread_on_switch_reports_unpersisted_source(tmp_path: Path) -> None:
    result = ensure_thread_available(
        _plugin("codex"),
        _session("codex"),
        current_config_dir=str(tmp_path / "current"),
        target_config_dir=str(tmp_path / "target"),
        policy="copy_thread_on_switch",
        shared_transcript_dir=None,
        native_thread_store="sessions",
    )
    assert result == ThreadAvailability.UNPERSISTED


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


def test_symlink_shared_reports_unpersisted_when_shared_lacks_thread(
    tmp_path: Path,
) -> None:
    # An empty shared store means the source thread was never persisted. The
    # runtime decides whether the agent can safely start a fresh native thread.
    result = ensure_thread_available(
        _plugin("claude_code"),
        _session("claude_code"),
        current_config_dir=str(tmp_path / "current"),
        target_config_dir=str(tmp_path / "target"),
        policy="symlink_shared",
        shared_transcript_dir=str(tmp_path / "shared"),
        native_thread_store="projects",
    )
    assert result == ThreadAvailability.UNPERSISTED


def test_symlink_shared_reports_unpersisted_source_after_setup(tmp_path: Path) -> None:
    target = tmp_path / "target"
    result = ensure_thread_available(
        _plugin("codex"),
        _session("codex"),
        current_config_dir=str(tmp_path / "current"),
        target_config_dir=str(target),
        policy="symlink_shared",
        shared_transcript_dir=str(tmp_path / "shared"),
        native_thread_store="sessions",
    )
    assert result == ThreadAvailability.UNPERSISTED
    assert (target / "sessions").is_symlink()


def test_symlink_shared_rejects_unknown_source_before_target_setup(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    with pytest.raises(TranscriptUnavailableError, match="cannot determine"):
        ensure_thread_available(
            _plugin("codex"),
            _session("codex"),
            current_config_dir=None,
            target_config_dir=str(target),
            policy="symlink_shared",
            shared_transcript_dir=str(tmp_path / "shared"),
            native_thread_store="sessions",
        )
    assert not (target / "sessions").exists()


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
    # A shared directory ancestor (dup/) is fine to share; only the differing
    # leaf transcript is a genuine conflict.
    store = tmp_path / "config" / "projects"
    (store / "dup").mkdir(parents=True)
    (store / "dup" / "f.jsonl").write_text("orig")
    shared = tmp_path / "shared"
    (shared / "dup").mkdir(parents=True)
    (shared / "dup" / "f.jsonl").write_text("different")

    with pytest.raises(
        TranscriptUnavailableError, match=r"dup/f\.jsonl \(different regular files\)"
    ):
        setup_transcripts_symlink(store, shared)

    # Nothing moved: store is still the original real dir, no symlink, no backup.
    assert store.is_dir() and not store.is_symlink()
    assert (store / "dup" / "f.jsonl").read_text() == "orig"
    assert (shared / "dup" / "f.jsonl").read_text() == "different"
    assert not any(p.name.startswith("projects.bak-") for p in store.parent.iterdir())


def test_setup_merges_codex_date_trees(tmp_path: Path) -> None:
    # Both stores hold sessions/2026/... but their leaf rollouts differ — the
    # old top-level "2026" collision must no longer block the merge.
    store = tmp_path / "config" / "sessions"
    (store / "2026" / "07" / "10").mkdir(parents=True)
    (store / "2026" / "07" / "10" / "rollout-A.jsonl").write_text("thread-a")
    shared = tmp_path / "shared"
    (shared / "2026" / "01" / "01").mkdir(parents=True)
    (shared / "2026" / "01" / "01" / "rollout-B.jsonl").write_text("thread-b")

    actions = setup_transcripts_symlink(store, shared)

    assert store.is_symlink()
    assert (store / "2026" / "07" / "10" / "rollout-A.jsonl").read_text() == "thread-a"
    assert (store / "2026" / "01" / "01" / "rollout-B.jsonl").read_text() == "thread-b"
    # A deep intermediate directory created by the merge is pinned 0700, not just
    # the direct child — guards the os.makedirs mode-only-on-leaf trap.
    assert (shared / "2026" / "07").stat().st_mode & 0o777 == 0o700
    assert (shared / "2026" / "07" / "10").stat().st_mode & 0o777 == 0o700
    assert (
        shared / "2026" / "07" / "10" / "rollout-A.jsonl"
    ).stat().st_mode & 0o777 == 0o600
    backups = [p for p in store.parent.iterdir() if p.name.startswith("sessions.bak-")]
    assert len(backups) == 1
    assert any("migrated 1 files" in a for a in actions)
    assert any("0 deduplicated" in a for a in actions)


def test_setup_merges_claude_project_trees(tmp_path: Path) -> None:
    store = tmp_path / "config" / "projects"
    (store / "proj-a").mkdir(parents=True)
    (store / "proj-a" / f"{TID}.jsonl").write_text("a")
    shared = tmp_path / "shared"
    (shared / "proj-b").mkdir(parents=True)
    (shared / "proj-b" / "other.jsonl").write_text("b")

    setup_transcripts_symlink(store, shared)

    assert store.is_symlink()
    assert sorted(p.name for p in shared.iterdir()) == ["proj-a", "proj-b"]
    assert (shared / "proj-a" / f"{TID}.jsonl").read_text() == "a"


def test_setup_deduplicates_identical_leaf(tmp_path: Path) -> None:
    store = tmp_path / "config" / "sessions"
    (store / "d").mkdir(parents=True)
    (store / "d" / "same.jsonl").write_text("identical")
    (store / "d" / "new.jsonl").write_text("fresh")
    shared = tmp_path / "shared"
    (shared / "d").mkdir(parents=True)
    (shared / "d" / "same.jsonl").write_text("identical")

    actions = setup_transcripts_symlink(store, shared)

    assert store.is_symlink()
    assert (shared / "d" / "same.jsonl").read_text() == "identical"
    assert (shared / "d" / "new.jsonl").read_text() == "fresh"
    assert any("migrated 1 files (1 deduplicated" in a for a in actions)
    assert any(p.name.startswith("sessions.bak-") for p in store.parent.iterdir())


def test_setup_conflict_on_file_vs_directory(tmp_path: Path) -> None:
    store = tmp_path / "config" / "sessions"
    (store / "x").mkdir(parents=True)
    (store / "x" / "leaf").write_text("iamafile")
    shared = tmp_path / "shared"
    (shared / "x" / "leaf").mkdir(parents=True)  # dest is a directory

    with pytest.raises(
        TranscriptUnavailableError,
        match=r"x/leaf \(source file, shared directory\)",
    ):
        setup_transcripts_symlink(store, shared)
    assert store.is_dir() and not store.is_symlink()


def test_setup_symlink_dedup_and_conflict(tmp_path: Path) -> None:
    # Same-target child symlink dedups; a preserved symlink lands when the dest
    # is absent; a diverging target is a conflict.
    store = tmp_path / "config" / "sessions"
    store.mkdir(parents=True)
    (store / "same").symlink_to("target")
    (store / "kept").symlink_to("elsewhere")
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "same").symlink_to("target")

    actions = setup_transcripts_symlink(store, shared)
    assert store.is_symlink()
    assert os.readlink(shared / "kept") == "elsewhere"
    assert any("1 deduplicated" in a for a in actions)

    # A diverging symlink target is a conflict.
    store2 = tmp_path / "config2" / "sessions"
    store2.mkdir(parents=True)
    (store2 / "s").symlink_to("one")
    shared2 = tmp_path / "shared2"
    shared2.mkdir()
    (shared2 / "s").symlink_to("two")
    with pytest.raises(
        TranscriptUnavailableError, match=r"s \(different symlink targets\)"
    ):
        setup_transcripts_symlink(store2, shared2)


def test_setup_conflict_diagnostic_is_bounded_and_path_only(tmp_path: Path) -> None:
    store = tmp_path / "config" / "sessions"
    store.mkdir(parents=True)
    shared = tmp_path / "shared"
    shared.mkdir()
    for i in range(15):
        (store / f"f{i:02d}.jsonl").write_text(f"source-{i}")
        (shared / f"f{i:02d}.jsonl").write_text(f"shared-{i}")

    with pytest.raises(TranscriptUnavailableError) as excinfo:
        setup_transcripts_symlink(store, shared)
    msg = str(excinfo.value)
    assert "15 conflicting paths" in msg
    assert "... and 5 more" in msg  # bounded to first 10
    assert "f00.jsonl (different regular files)" in msg
    # Deterministic ordering: f09 listed, f10 not (only first 10 sorted paths).
    assert "f09.jsonl" in msg and "f10.jsonl" not in msg
    # No transcript contents leak into diagnostics.
    assert "source-" not in msg and "shared-" not in msg


def test_setup_verify_failure_leaves_source_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waypoint.backends import transcripts as tr

    # Distinct relative paths (no dest collisions) so the planner makes zero
    # filecmp calls — only the verify step does, which we force to fail.
    store = tmp_path / "config" / "sessions"
    (store / "d").mkdir(parents=True)
    (store / "d" / "a.jsonl").write_text("a")
    shared = tmp_path / "shared"
    shared.mkdir()

    monkeypatch.setattr(tr.filecmp, "cmp", lambda *a, **k: False)
    with pytest.raises(TranscriptUnavailableError, match="did not match its source"):
        setup_transcripts_symlink(store, shared)

    assert store.is_dir() and not store.is_symlink()
    assert (store / "d" / "a.jsonl").read_text() == "a"
    assert list(shared.iterdir()) == []  # shared untouched
    assert not any(p.name.startswith(".wp-migrate-") for p in shared.parent.iterdir())


def test_setup_mid_merge_failure_retains_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "config" / "sessions"
    store.mkdir(parents=True)
    for i in range(3):
        (store / f"f{i}.jsonl").write_text(f"x{i}")
    shared = tmp_path / "shared"
    shared.mkdir()

    real_rename = Path.rename
    calls = {"n": 0}

    def flaky_rename(self: Path, target: Any) -> Any:
        # Fail on the second staged move — shared is partially populated by then.
        if ".wp-migrate-" in str(self):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated mid-merge failure")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)
    with pytest.raises(OSError, match="simulated mid-merge failure"):
        setup_transcripts_symlink(store, shared)

    # Source intact; shared partially populated; staging retained for recovery.
    assert store.is_dir() and not store.is_symlink()
    assert {p.name for p in store.iterdir()} == {"f0.jsonl", "f1.jsonl", "f2.jsonl"}
    assert len(list(shared.glob("f*.jsonl"))) == 1
    staging = [p for p in shared.parent.iterdir() if p.name.startswith(".wp-migrate-")]
    assert len(staging) == 1
    assert len(list(staging[0].glob("f*.jsonl"))) >= 1


def test_setup_rerun_after_mid_merge_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waypoint.backends import transcripts as tr

    store = tmp_path / "config" / "sessions"
    store.mkdir(parents=True)
    for i in range(3):
        (store / f"f{i}.jsonl").write_text(f"x{i}")
    shared = tmp_path / "shared"
    shared.mkdir()

    # Unique timestamps so the failed run's staging and the re-run's staging/backup
    # don't collide on a same-second name.
    stamps = iter(f"ts{n}" for n in range(10))
    monkeypatch.setattr(tr, "_timestamp", lambda: next(stamps))

    real_rename = Path.rename
    state = {"fail": True, "n": 0}

    def flaky_rename(self: Path, target: Any) -> Any:
        if state["fail"] and ".wp-migrate-" in str(self):
            state["n"] += 1
            if state["n"] == 2:
                raise OSError("simulated mid-merge failure")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)
    with pytest.raises(OSError, match="simulated mid-merge failure"):
        setup_transcripts_symlink(store, shared)
    assert store.is_dir() and not store.is_symlink()
    assert len(list(shared.glob("f*.jsonl"))) == 1  # one leaf already moved

    # Operator simply re-runs: the already-moved leaf deduplicates, the rest copy.
    state["fail"] = False
    actions = setup_transcripts_symlink(store, shared)

    assert store.is_symlink()
    for i in range(3):
        assert (store / f"f{i}.jsonl").read_text() == f"x{i}"
    assert any("migrated 2 files (1 deduplicated" in a for a in actions)
    assert any(p.name.startswith("sessions.bak-") for p in store.parent.iterdir())


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


# ── tmux-wrapped delegation (composed pair) ─────────────────────────────────
#
# The account-profile switch passes the *transport-owning* plugin to
# ensure_thread_available (TmuxPlugin for a tmux-wrapped session), not the
# wrapped agent's own plugin. These mirror the require_existing /
# symlink_shared / copy_thread_on_switch tests above one-for-one, but with a
# TmuxPlugin instance standing in for the agent plugin — proving the
# delegation added to TmuxPlugin.native_thread_artifact_glob (which resolves
# the wrapped agent off the module-level registry singleton) makes the three
# transcript policies behave identically to a native session.


def _tmux_plugin() -> BackendPlugin:
    from waypoint.backends.tmux.plugin import TmuxPlugin

    reset_registry_for_tests()
    return TmuxPlugin()


def _tmux_session(backend: str) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id="s1",
        backend=backend,
        source=SessionSource.MANAGED,
        transport="tmux",
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


def test_tmux_wrapped_require_existing_noop_when_present(tmp_path: Path) -> None:
    target = tmp_path / "target"
    _write_claude_thread(target)
    ensure_thread_available(
        _tmux_plugin(),
        _tmux_session("claude_code"),
        current_config_dir=str(tmp_path / "current"),
        target_config_dir=str(target),
        policy="require_existing",
        shared_transcript_dir=None,
        native_thread_store="projects",
    )


def test_tmux_wrapped_require_existing_rejects_when_absent(tmp_path: Path) -> None:
    with pytest.raises(TranscriptUnavailableError, match="require_existing"):
        ensure_thread_available(
            _tmux_plugin(),
            _tmux_session("claude_code"),
            current_config_dir=str(tmp_path / "current"),
            target_config_dir=str(tmp_path / "target"),
            policy="require_existing",
            shared_transcript_dir=None,
            native_thread_store="projects",
        )


def test_tmux_wrapped_copy_thread_on_switch_copies_codex_rollout(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    target = tmp_path / "target"
    src = _write_codex_rollout(current)
    ensure_thread_available(
        _tmux_plugin(),
        _tmux_session("codex"),
        current_config_dir=str(current),
        target_config_dir=str(target),
        policy="copy_thread_on_switch",
        shared_transcript_dir=None,
        native_thread_store="sessions",
    )
    dest = target / src.relative_to(current)
    assert dest.is_file()
    assert oct(dest.stat().st_mode)[-3:] == "600"


def test_tmux_wrapped_symlink_shared_end_to_end_makes_thread_visible(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    (shared / "-repo-app").mkdir(parents=True)
    (shared / "-repo-app" / f"{TID}.jsonl").write_text("{}")
    target = tmp_path / "target"
    ensure_thread_available(
        _tmux_plugin(),
        _tmux_session("claude_code"),
        current_config_dir=str(tmp_path / "current"),
        target_config_dir=str(target),
        policy="symlink_shared",
        shared_transcript_dir=str(shared),
        native_thread_store="projects",
    )
    assert (target / "projects").is_symlink()


def test_tmux_wrapped_glob_delegates_for_claude_and_codex() -> None:
    plugin = _tmux_plugin()
    assert (
        plugin.native_thread_artifact_glob(_tmux_session("claude_code"))
        == f"projects/*/{TID}.jsonl"
    )
    assert (
        plugin.native_thread_artifact_glob(_tmux_session("codex"))
        == f"sessions/*/*/*/rollout-*-{TID}.jsonl"
    )


def test_tmux_wrapped_glob_none_for_attached_tmux_session() -> None:
    # session.backend == "tmux" (no wrapped agent) keeps the empty result.
    assert _tmux_plugin().native_thread_artifact_glob(_tmux_session("tmux")) is None


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
    # Config-dir expansion also dispatches through the seam (so a remote fs
    # expands against the remote home, not the backend host's), then discovery.
    assert fs.calls == [
        ("expanduser", (str(target),)),
        ("glob_artifacts", (session, plugin, str(target))),
    ]


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
