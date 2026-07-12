"""Instance health and capacity telemetry (PRD: instance-health-capacity).

The shared, read-only instance-snapshot definition backing the Telemetry
dashboard's Instance health & capacity section, its deterministic optimization
insights, bounded daily history, and NL integration. Aggregate-only and
local-only: no path, filename, content, or session identifier ever leaves this
subsystem.
"""

from waypoint.telemetry.instance.collect import collect_snapshot
from waypoint.telemetry.instance.insights import (
    INSTANCE_RANGE_KEY,
    compute_instance_insights,
    is_instance_signature,
)
from waypoint.telemetry.instance.model import (
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

__all__ = [
    "INSTANCE_RANGE_KEY",
    "CategoryFootprint",
    "DataQuality",
    "DatabaseReclaim",
    "FilesystemSignal",
    "InstanceCounts",
    "InstanceSnapshot",
    "RedundantLogCandidate",
    "StorageCategory",
    "StructuredLogBreakdown",
    "collect_snapshot",
    "compute_instance_insights",
    "is_instance_signature",
]
