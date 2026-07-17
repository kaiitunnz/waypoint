"""Collect an :class:`InstanceSnapshot` from the local data directory.

Metadata/stat operations only — never reads attachment, log, or transcript
content (PRD NFR Performance). Database facts come from a dedicated read-only,
no-migration connection; footprint bytes come from bounded, symlink-safe,
hard-link-deduped tree walks. Any category that cannot be measured degrades to
partial/unavailable without hiding the others (PRD FR-2).
"""

import os
import re
import shutil
import sqlite3
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from waypoint.settings import Settings
from waypoint.telemetry.instance.model import (
    CATEGORY_ORDER,
    CategoryFootprint,
    DatabaseReclaim,
    DataQuality,
    FilesystemSignal,
    InstanceCounts,
    InstanceSnapshot,
    RedundantLogCandidate,
    StorageCategory,
    StructuredLogBreakdown,
)
from waypoint.telemetry.instance.roconn import budgeted_query, open_readonly
from waypoint.telemetry.instance.walk import FootprintWalker, TreeFootprint
from waypoint.telemetry.query import host_tz_name, host_utc_offset_minutes

# Attachment sidecar stems (see ``attachments.py`` ``_ATTACHMENT_ID``): one JSON
# sidecar per stored attachment, so their count is the attachment count.
_ATTACHMENT_ID = re.compile(r"[0-9a-f]{32}")
_RUNNING_STATUS = "running"
_STRUCTURED_LOG_NAME = "events.jsonl"


@dataclass
class _DbReadout:
    ok: bool = False
    valid_ids: set[str] | None = None  # None => classification unknown
    running_ids: set[str] = field(default_factory=set)
    table_rows: dict[str, int] = field(default_factory=dict)
    table_bytes: dict[str, int] = field(default_factory=dict)
    events_by_kind: dict[str, int] = field(default_factory=dict)
    reclaim: DatabaseReclaim = field(default_factory=DatabaseReclaim)


# dbstat scans every page, so it runs well past the per-query lock budget; the
# whole collection is already an off-request-path, multi-second walk, so this
# one measurement gets a wider ceiling and degrades to empty (sizes hidden)
# rather than blocking when it cannot finish.
_DBSTAT_BUDGET_MS = 4000


def _read_database(db_path: Path) -> _DbReadout:
    out = _DbReadout()
    with open_readonly(db_path) as conn:
        if conn is None:
            return out
        sessions = budgeted_query(conn, "SELECT id, status FROM sessions")
        if sessions is None:
            return out
        out.ok = True
        out.valid_ids = {row["id"] for row in sessions}
        out.running_ids = {
            row["id"] for row in sessions if row["status"] == _RUNNING_STATUS
        }
        tables = budgeted_query(
            conn, "SELECT name FROM sqlite_master WHERE type='table'"
        )
        for row in tables or ():
            name = row["name"]
            if not name.replace("_", "").isalnum():  # defensive: skip odd names
                continue
            counted = budgeted_query(conn, f"SELECT COUNT(*) AS n FROM {name}")
            if counted:
                out.table_rows[name] = int(counted[0]["n"])
        kinds = budgeted_query(
            conn, "SELECT kind, COUNT(*) AS n FROM events GROUP BY kind"
        )
        for row in kinds or ():
            out.events_by_kind[row["kind"]] = int(row["n"])
        out.table_bytes = _read_table_bytes(conn)
        out.reclaim = _read_reclaim(conn)
    return out


def _read_table_bytes(conn: sqlite3.Connection) -> dict[str, int]:
    """Measured page usage per table (its btree plus its indexes), via dbstat.

    Attributes every index's pages to its owning table so a table's size is its
    whole on-disk footprint. Returns an empty mapping when dbstat is absent from
    the SQLite build or the scan exceeds its budget — the caller then reports
    record counts without sizes rather than a fabricated breakdown.
    """
    schema = budgeted_query(
        conn,
        "SELECT name, tbl_name, type FROM sqlite_schema WHERE type IN ('table', 'index')",
    )
    owner = {
        row["name"]: row["tbl_name"] for row in schema or () if row["type"] == "index"
    }
    pages = budgeted_query(
        conn,
        "SELECT name, SUM(pgsize) AS b FROM dbstat GROUP BY name",
        budget_ms=_DBSTAT_BUDGET_MS,
    )
    if pages is None:
        return {}
    table_bytes: dict[str, int] = {}
    for row in pages:
        table = owner.get(row["name"], row["name"])
        table_bytes[table] = table_bytes.get(table, 0) + int(row["b"])
    return table_bytes


def _read_reclaim(conn: sqlite3.Connection) -> DatabaseReclaim:
    values: dict[str, int] = {}
    for pragma in ("page_size", "page_count", "freelist_count"):
        rows = budgeted_query(conn, f"PRAGMA {pragma}")
        if not rows:
            return DatabaseReclaim(measured=False)
        values[pragma] = int(rows[0][0])
    page_size = values["page_size"]
    page_count = values["page_count"]
    freelist = values["freelist_count"]
    free_bytes = page_size * freelist
    free_percent = (freelist / page_count) if page_count else 0.0
    return DatabaseReclaim(
        measured=True,
        page_size=page_size,
        page_count=page_count,
        freelist_count=freelist,
        free_bytes=free_bytes,
        free_percent=free_percent,
    )


def _lstat_size(path: Path) -> int | None:
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return None
    return st.st_size


def _seed_inode(seen: set[tuple[int, int]], path: Path) -> None:
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return
    if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
        seen.add((st.st_dev, st.st_ino))


def collect_snapshot(
    settings: Settings, *, now: datetime | None = None
) -> InstanceSnapshot:
    observed_at = now or datetime.now(UTC)
    data_dir = settings.data_dir
    db_path = settings.database_path
    wal_path = db_path.parent / (db_path.name + "-wal")
    shm_path = db_path.parent / (db_path.name + "-shm")
    sessions_dir = settings.sessions_dir
    attachments_dir = settings.attachments_dir

    notes: list[str] = []
    seen: set[tuple[int, int]] = set()
    for companion in (db_path, wal_path, shm_path):
        _seed_inode(seen, companion)

    db = _read_database(db_path)
    if not db.ok:
        notes.append("database could not be read read-only; DB facts unavailable")

    # ── database + SQLite companions ──────────────────────────────────────
    db_size = _lstat_size(db_path)
    database_fp = CategoryFootprint(
        category=StorageCategory.DATABASE,
        bytes=db_size or 0,
        entry_count=1 if db_size is not None else 0,
        unavailable=db_size is None,
    )
    wal_size = _lstat_size(wal_path) or 0
    shm_size = _lstat_size(shm_path) or 0
    companion_bytes = wal_size + shm_size
    companion_fp = CategoryFootprint(
        category=StorageCategory.SQLITE_COMPANIONS,
        bytes=companion_bytes,
        entry_count=(1 if wal_size else 0) + (1 if shm_size else 0),
    )

    # ── session trees (live vs orphan) + structured-log overlays ──────────
    live_fp = TreeFootprint()
    orphan_fp = TreeFootprint()
    live_logs = StructuredLogBreakdown(tree=StorageCategory.LIVE_SESSIONS)
    orphan_logs = StructuredLogBreakdown(tree=StorageCategory.ORPHAN_SESSIONS)
    redundant = RedundantLogCandidate()
    session_dir_count = 0
    orphan_dir_count = 0
    classification_unknown = db.valid_ids is None
    live_walker = FootprintWalker(seen)
    orphan_walker = FootprintWalker(seen)

    if sessions_dir.exists() and not sessions_dir.is_symlink():
        for child in _iter_dirs(sessions_dir):
            session_dir_count += 1
            is_orphan = db.valid_ids is not None and child.name not in db.valid_ids
            if is_orphan:
                orphan_dir_count += 1
                orphan_fp.merge(orphan_walker.walk(child))
            else:
                live_fp.merge(live_walker.walk(child))
            _account_structured_log(
                child,
                is_orphan=is_orphan,
                is_running=child.name in db.running_ids,
                live_logs=live_logs,
                orphan_logs=orphan_logs,
                redundant=redundant,
            )
    elif not sessions_dir.exists():
        notes.append("session directory does not exist")

    if classification_unknown and session_dir_count:
        notes.append(
            "session classification unknown (DB unreadable); "
            "all session directories reported as live"
        )

    live_cat = CategoryFootprint(
        category=StorageCategory.LIVE_SESSIONS,
        bytes=live_fp.bytes,
        entry_count=live_fp.file_count,
        partial=live_fp.truncated or classification_unknown,
    )
    orphan_cat = CategoryFootprint(
        category=StorageCategory.ORPHAN_SESSIONS,
        bytes=orphan_fp.bytes,
        entry_count=orphan_fp.file_count,
        partial=orphan_fp.truncated,
        unavailable=classification_unknown,
    )

    # ── attachments ───────────────────────────────────────────────────────
    attach_fp = TreeFootprint()
    attachment_count = 0

    def _count_attachment(path: Path, _st: os.stat_result) -> None:
        nonlocal attachment_count
        if path.suffix == ".json" and _ATTACHMENT_ID.fullmatch(path.stem):
            attachment_count += 1

    if attachments_dir.exists() and not attachments_dir.is_symlink():
        attach_fp = FootprintWalker(seen).walk(attachments_dir, _count_attachment)
    attach_cat = CategoryFootprint(
        category=StorageCategory.ATTACHMENTS,
        bytes=attach_fp.bytes,
        entry_count=attach_fp.file_count,
        partial=attach_fp.truncated,
    )

    # ── unclassified managed-data entries ─────────────────────────────────
    unclassified_fp, unclassified_notes = _collect_unclassified(
        data_dir,
        claimed={db_path, wal_path, shm_path, sessions_dir, attachments_dir},
        seen=seen,
    )
    notes.extend(unclassified_notes)
    unclassified_cat = CategoryFootprint(
        category=StorageCategory.UNCLASSIFIED,
        bytes=unclassified_fp.bytes,
        entry_count=unclassified_fp.file_count,
        partial=unclassified_fp.truncated,
    )

    categories = [
        database_fp,
        companion_fp,
        live_cat,
        orphan_cat,
        attach_cat,
        unclassified_cat,
    ]
    categories.sort(key=lambda c: CATEGORY_ORDER.index(c.category))
    total_bytes = sum(c.bytes for c in categories)

    all_unavailable = all(c.unavailable for c in categories)
    any_degraded = any(c.partial or c.unavailable for c in categories) or not db.ok
    if all_unavailable:
        quality = DataQuality.UNAVAILABLE
    elif any_degraded:
        quality = DataQuality.PARTIAL
    else:
        quality = DataQuality.COMPLETE

    structured_logs = [
        log for log in (live_logs, orphan_logs) if log.count or log.bytes
    ]

    filesystem = _collect_filesystem(settings, data_dir)

    return InstanceSnapshot(
        observed_at=observed_at,
        tz=host_tz_name(),
        utc_offset_minutes=host_utc_offset_minutes(),
        data_quality=quality,
        categories=categories,
        total_bytes=total_bytes,
        structured_logs=structured_logs,
        redundant_logs=redundant,
        database=db.reclaim,
        filesystem=filesystem,
        counts=InstanceCounts(
            table_rows=db.table_rows,
            table_bytes=db.table_bytes,
            events_by_kind=db.events_by_kind,
            session_dir_count=session_dir_count,
            orphan_dir_count=orphan_dir_count,
            attachment_count=attachment_count,
        ),
        wal_bytes=wal_size,
        notes=notes,
    )


def _iter_dirs(root: Path) -> list[Path]:
    dirs: list[Path] = []
    try:
        for child in root.iterdir():
            try:
                if child.is_dir() and not child.is_symlink():
                    dirs.append(child)
            except OSError:
                continue
    except OSError:
        return dirs
    return dirs


def _account_structured_log(
    child: Path,
    *,
    is_orphan: bool,
    is_running: bool,
    live_logs: StructuredLogBreakdown,
    orphan_logs: StructuredLogBreakdown,
    redundant: RedundantLogCandidate,
) -> None:
    log_path = child / _STRUCTURED_LOG_NAME
    size = _lstat_size(log_path)
    if size is None:
        return
    breakdown = orphan_logs if is_orphan else live_logs
    breakdown.bytes += size
    breakdown.count += 1
    if is_running:
        redundant.running_excluded_count += 1
        return
    # Not RUNNING => a redundant-log cleanup candidate (orphans included).
    redundant.bytes += size
    redundant.count += 1
    if is_orphan:
        redundant.orphan_overlap_bytes += size
        redundant.orphan_overlap_count += 1


def _collect_unclassified(
    data_dir: Path, *, claimed: set[Path], seen: set[tuple[int, int]]
) -> tuple[TreeFootprint, list[str]]:
    fp = TreeFootprint()
    notes: list[str] = []
    if not data_dir.exists():
        return fp, ["data directory does not exist"]
    walker = FootprintWalker(seen)
    try:
        entries = list(data_dir.iterdir())
    except OSError:
        return fp, ["data directory could not be listed"]
    for entry in entries:
        if entry in claimed:
            continue
        try:
            if entry.is_symlink():
                notes.append("skipped a symlinked entry under the data directory")
                continue
            if entry.is_dir():
                fp.merge(walker.walk(entry))
            elif entry.is_file():
                size = _lstat_size(entry)
                if size is not None:
                    fp.bytes += size
                    fp.file_count += 1
        except OSError:
            fp.truncated = True
    return fp, notes


def _collect_filesystem(settings: Settings, data_dir: Path) -> FilesystemSignal:
    if not settings.telemetry_instance_fs_signals:
        return FilesystemSignal(measured=False)
    try:
        usage = shutil.disk_usage(data_dir)
    except OSError:
        return FilesystemSignal(measured=False)
    return FilesystemSignal(
        measured=True, total_bytes=usage.total, free_bytes=usage.free
    )
