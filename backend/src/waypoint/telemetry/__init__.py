"""Backend-neutral usage telemetry subsystem.

Turns Waypoint's already-normalized session/event/usage signals into a durable,
queryable history that powers the Telemetry dashboard. The fact contract in
``facts.py`` is the seam every other module (storage, ingestion, aggregation,
API, frontend) consumes; generic code never branches on backend id.
"""

from waypoint.telemetry.facts import (
    ApprovalDecision,
    ContextSnapshotFact,
    FactSource,
    LifecycleTransition,
    LimitSnapshotFact,
    SessionLifecycleFact,
    TelemetryFact,
    TelemetryFactKind,
    TelemetryFilter,
    TelemetryRange,
    ToolCallFact,
    ToolOutcome,
    TurnFact,
    TurnKind,
)
from waypoint.telemetry.store import TelemetryStore

__all__ = [
    "ApprovalDecision",
    "ContextSnapshotFact",
    "FactSource",
    "LifecycleTransition",
    "LimitSnapshotFact",
    "SessionLifecycleFact",
    "TelemetryFact",
    "TelemetryFactKind",
    "TelemetryFilter",
    "TelemetryRange",
    "TelemetryStore",
    "ToolCallFact",
    "ToolOutcome",
    "TurnFact",
    "TurnKind",
]
