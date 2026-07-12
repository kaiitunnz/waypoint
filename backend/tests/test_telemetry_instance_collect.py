"""Contract tests for the instance-health snapshot collector (PRD FR-1/FR-2).

Builds synthetic data directories against a live WAL database and asserts the
canonical accounting: the six mutually-exclusive categories, overlays never
summed into the total, hard-link inodes counted once across categories, the
redundant-log predicate (running excluded, orphans included), and partial/
unavailable labeling on failure.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.telemetry.instance.collect import collect_snapshot
from waypoint.telemetry.instance.model import DataQuality, StorageCategory
from waypoint.telemetry.instance.roconn import budgeted_query, open_readonly
from waypoint.telemetry.instance.walk import FootprintWalker


def _settings(tmp_path: Path, **kw: object) -> Settings:
    return Settings(data_dir=tmp_path / "data", telemetry_enabled=True, **kw)


def _storage(settings: Settings) -> Storage:
    settings.ensure_dirs()
    return Storage(settings.database_path)


def _insert_session(storage: Storage, session_id: str, status: str) -> None:
    now = datetime.now(UTC).isoformat()
    storage.connection.execute(
        """
        INSERT INTO sessions
          (id, backend, source, title, cwd, status, created_at, updated_at,
           last_event_at, raw_log_path, structured_log_path)
        VALUES (?, 'claude_code', 'user', 't', '/x', ?, ?, ?, ?, '', '')
        """,
        (session_id, status, now, now, now),
    )
    storage.connection.commit()


def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def _cat(snapshot: object, category: StorageCategory) -> int:
    return snapshot.category(category).bytes  # type: ignore[attr-defined]


# ── read-only connection (review #10: live WAL DB PRAGMA reads) ────────────


def test_open_readonly_missing_db(tmp_path: Path) -> None:
    with open_readonly(tmp_path / "nope.db") as conn:
        assert conn is None


def test_open_readonly_live_wal_pragmas(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    storage = _storage(settings)
    try:
        with open_readonly(settings.database_path) as conn:
            assert conn is not None
            for pragma in ("page_size", "page_count", "freelist_count"):
                rows = budgeted_query(conn, f"PRAGMA {pragma}")
                assert rows is not None and int(rows[0][0]) >= 0
    finally:
        storage.close()


# ── walker (symlink skip, hard-link dedup, budget truncation) ──────────────


def test_walk_skips_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    _write(root / "a.bin", 100)
    (root / "link").symlink_to(root / "a.bin")
    fp = FootprintWalker(set()).walk(root)
    assert fp.bytes == 100 and fp.file_count == 1


def test_walk_hardlink_deduped_across_walks(tmp_path: Path) -> None:
    a = tmp_path / "a" / "f.bin"
    _write(a, 200)
    b = tmp_path / "b" / "f.bin"
    b.parent.mkdir(parents=True)
    os.link(a, b)  # hard link, same inode
    seen: set[tuple[int, int]] = set()
    first = FootprintWalker(seen).walk(tmp_path / "a")
    second = FootprintWalker(seen).walk(tmp_path / "b")
    assert first.bytes == 200
    assert second.bytes == 0  # same inode already counted in the first tree


def test_walk_entry_budget_truncates(tmp_path: Path) -> None:
    root = tmp_path / "many"
    for i in range(20):
        _write(root / f"f{i}.bin", 1)
    fp = FootprintWalker(set(), entry_budget=5).walk(root)
    assert fp.truncated is True
    assert fp.file_count <= 5


# ── collector accounting ───────────────────────────────────────────────────


def test_canonical_total_is_sum_of_categories(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    storage = _storage(settings)
    try:
        _insert_session(storage, "live1", "running")
        _insert_session(storage, "idle1", "idle")
        # live session trees
        _write(settings.sessions_dir / "live1" / "events.jsonl", 500)
        _write(settings.sessions_dir / "idle1" / "events.jsonl", 300)
        _write(settings.sessions_dir / "idle1" / "misc.txt", 50)
        # orphan tree (no matching session id)
        _write(settings.sessions_dir / "ghost" / "events.jsonl", 700)
        # attachments (one sidecar => attachment_count 1)
        _write(settings.attachments_dir / "live1" / (f"{'a' * 32}.json"), 40)
        _write(settings.attachments_dir / "live1" / "blob.txt", 60)
        # unclassified entry directly under data dir
        _write(settings.data_dir / "stray.log", 90)

        snap = collect_snapshot(settings)

        assert snap.total_bytes == sum(c.bytes for c in snap.categories)
        # overlays are not added on top of the total
        overlay_bytes = sum(s.bytes for s in snap.structured_logs)
        assert overlay_bytes > 0
        assert snap.total_bytes < sum(c.bytes for c in snap.categories) + overlay_bytes
        assert _cat(snap, StorageCategory.ORPHAN_SESSIONS) == 700
        assert _cat(snap, StorageCategory.LIVE_SESSIONS) == 500 + 300 + 50
        assert _cat(snap, StorageCategory.UNCLASSIFIED) == 90
        assert snap.counts.attachment_count == 1
        assert snap.counts.orphan_dir_count == 1
        assert snap.counts.session_dir_count == 3
    finally:
        storage.close()


def test_redundant_log_predicate_running_excluded_orphan_included(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    storage = _storage(settings)
    try:
        _insert_session(storage, "run", "running")
        _insert_session(storage, "done", "exited")
        _write(settings.sessions_dir / "run" / "events.jsonl", 111)
        _write(settings.sessions_dir / "done" / "events.jsonl", 222)
        _write(settings.sessions_dir / "orphan" / "events.jsonl", 333)

        snap = collect_snapshot(settings)
        r = snap.redundant_logs
        # running session's log excluded; done + orphan are candidates
        assert r.running_excluded_count == 1
        assert r.count == 2
        assert r.bytes == 222 + 333
        # orphan overlap reported separately
        assert r.orphan_overlap_count == 1
        assert r.orphan_overlap_bytes == 333
    finally:
        storage.close()


def test_hardlink_across_categories_counted_once(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    storage = _storage(settings)
    try:
        _insert_session(storage, "s1", "idle")
        live_file = settings.sessions_dir / "s1" / "big.bin"
        _write(live_file, 1000)
        # hard link the same inode into the attachments tree
        link = settings.attachments_dir / "s1" / "big.bin"
        link.parent.mkdir(parents=True)
        os.link(live_file, link)

        snap = collect_snapshot(settings)
        live = _cat(snap, StorageCategory.LIVE_SESSIONS)
        attach = _cat(snap, StorageCategory.ATTACHMENTS)
        # inode counted once, in the earlier category (live sessions)
        assert live == 1000
        assert attach == 0
    finally:
        storage.close()


def test_missing_directories_do_not_fail(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    storage = _storage(settings)
    try:
        snap = collect_snapshot(settings)
        # empty but present dirs => complete, zero footprint
        assert snap.data_quality in (DataQuality.COMPLETE, DataQuality.PARTIAL)
        assert _cat(snap, StorageCategory.ORPHAN_SESSIONS) == 0
    finally:
        storage.close()


def test_database_unreadable_is_partial(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    # No database file at all: DB facts unavailable, classification unknown.
    _write(settings.sessions_dir / "whatever" / "events.jsonl", 10)
    snap = collect_snapshot(settings)
    assert snap.data_quality == DataQuality.PARTIAL
    assert snap.category(StorageCategory.DATABASE).unavailable is True
    # classification unknown => orphan tree unavailable, dirs treated as live
    assert snap.category(StorageCategory.ORPHAN_SESSIONS).unavailable is True


def test_fs_signal_opt_in(tmp_path: Path) -> None:
    off_settings = _settings(tmp_path)
    off_settings.ensure_dirs()
    off = collect_snapshot(off_settings)
    assert off.filesystem.measured is False
    on_settings = _settings(tmp_path, telemetry_instance_fs_signals=True)
    on_settings.ensure_dirs()
    on = collect_snapshot(on_settings)
    assert on.filesystem.measured is True
    assert on.filesystem.total_bytes > 0
