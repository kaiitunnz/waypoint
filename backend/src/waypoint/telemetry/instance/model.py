"""The shared, canonical read-only instance-health snapshot contract.

This is the one definition every new dashboard measure derives from (the CLI
``maintenance stats`` may adopt it later). It is aggregate-only: no filesystem
path, filename, file content, session identifier, prompt, output, or secret ever
appears here — only category byte totals, counts, hygiene signals, and
data-quality state (PRD FR-1, NFR Privacy).

Canonical accounting (PRD appendix): the top-level footprint total is the sum of
six mutually-exclusive categories. Structured-log and redundant-log sizes are
overlays on the tree that owns them, never added again to the total.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class StorageCategory(StrEnum):
    """The mutually-exclusive footprint categories, in hard-link dedup order.

    A physical inode hard-linked across two categories is counted once, in the
    category that appears first here (PRD NFR Correctness).
    """

    DATABASE = "database"
    SQLITE_COMPANIONS = "sqlite_companions"
    LIVE_SESSIONS = "live_sessions"
    ORPHAN_SESSIONS = "orphan_sessions"
    ATTACHMENTS = "attachments"
    UNCLASSIFIED = "unclassified"


# Fixed accounting order; also the hard-link precedence order.
CATEGORY_ORDER: tuple[StorageCategory, ...] = tuple(StorageCategory)


class DataQuality(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class CategoryFootprint(BaseModel):
    category: StorageCategory
    bytes: int = 0
    entry_count: int = 0
    # A category whose measurement was truncated by a budget breach, or whose
    # root was unreadable, is partial; ``unavailable`` means it could not be
    # measured at all (e.g. the database file is absent/locked).
    partial: bool = False
    unavailable: bool = False


class StructuredLogBreakdown(BaseModel):
    """``events.jsonl`` bytes within a session tree — an overlay, not a category."""

    tree: StorageCategory  # LIVE_SESSIONS or ORPHAN_SESSIONS
    bytes: int = 0
    count: int = 0


class RedundantLogCandidate(BaseModel):
    """Inactive redundant structured logs (same predicate as clear-structured-logs).

    A candidate is an ``events.jsonl`` directly inside a session-directory child
    whose matching stored session is not RUNNING (orphan directories included,
    as they have no matching running session). ``orphan_overlap_bytes`` is the
    portion already owned by the orphan-session tree, where the orphan-pruning
    insight takes precedence (PRD FR-2).
    """

    bytes: int = 0
    count: int = 0
    running_excluded_count: int = 0
    orphan_overlap_bytes: int = 0
    orphan_overlap_count: int = 0


class DatabaseReclaim(BaseModel):
    """Read-only SQLite reclaimability, measured via PRAGMA without any write."""

    measured: bool = False
    page_size: int = 0
    page_count: int = 0
    freelist_count: int = 0
    free_bytes: int = 0
    free_percent: float = 0.0


class FilesystemSignal(BaseModel):
    """Opt-in host free-space for the volume holding the data directory.

    ``shutil.disk_usage`` yields only total/free; filesystem quota is not
    portably measurable, so the MVP reports free space + total only (PRD MVP #8,
    narrowed). Omitted entirely unless the operator enables the signals and the
    volume can be identified.
    """

    measured: bool = False
    total_bytes: int = 0
    free_bytes: int = 0


class InstanceCounts(BaseModel):
    """Privacy-safe counts that make the footprint interpretable (PRD FR-1)."""

    table_rows: dict[str, int] = Field(default_factory=dict)
    events_by_kind: dict[str, int] = Field(default_factory=dict)
    session_dir_count: int = 0
    orphan_dir_count: int = 0
    attachment_count: int = 0


class InstanceSnapshot(BaseModel):
    """A current, local-only instance-health snapshot (PRD FR-1)."""

    observed_at: datetime
    tz: str
    utc_offset_minutes: int = 0
    data_quality: DataQuality = DataQuality.COMPLETE
    categories: list[CategoryFootprint] = Field(default_factory=list)
    total_bytes: int = 0
    structured_logs: list[StructuredLogBreakdown] = Field(default_factory=list)
    redundant_logs: RedundantLogCandidate = Field(default_factory=RedundantLogCandidate)
    database: DatabaseReclaim = Field(default_factory=DatabaseReclaim)
    filesystem: FilesystemSignal = Field(default_factory=FilesystemSignal)
    counts: InstanceCounts = Field(default_factory=InstanceCounts)
    wal_bytes: int = 0
    # Human-readable notes about partial/unclassified/race conditions; never a path.
    notes: list[str] = Field(default_factory=list)

    def category(self, cat: StorageCategory) -> CategoryFootprint:
        for footprint in self.categories:
            if footprint.category == cat:
                return footprint
        return CategoryFootprint(category=cat)
