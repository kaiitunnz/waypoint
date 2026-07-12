"""The backend-neutral telemetry fact contract.

Every fact is derived from an already-normalized signal (an ``EventRecord``, a
``SessionRecord`` field update, or a ``TokenUsageRecord``) and carries a stable,
source-owned identity so ingestion is idempotent under retransmission/replay and
a later revision replaces an earlier one. Generic code (ingest, store, aggregate,
API) consumes these types and never branches on backend id; the only
backend-specific work is at each agent's normalization boundary (model-at-turn on
the token ledger; typed tool outcome).

Field names are chosen to be OpenTelemetry-GenAI alignable where a stable
attribute exists (FR-10), but this internal contract is authoritative and no
third-party exporter is wired in the MVP.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

# ── Vocabularies ─────────────────────────────────────────────────────────────


class TelemetryFactKind(StrEnum):
    """The fact table a fact belongs to."""

    SESSION_LIFECYCLE = "session_lifecycle"
    TURN = "turn"
    TOOL_CALL = "tool_call"
    CONTEXT_SNAPSHOT = "context_snapshot"
    LIMIT_SNAPSHOT = "limit_snapshot"


class LifecycleTransition(StrEnum):
    """An explicit session state transition that occurred at a point in time.

    Lifecycle *counts* use these transitions, never merely the latest status
    (Appendix: Session). A point-in-time "active" count is derived separately as
    the set of sessions whose latest transition at the range end is one of
    STARTING/RUNNING/IDLE/WAITING.
    """

    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    IDLE = "idle"
    WAITING = "waiting"
    INTERRUPTED = "interrupted"
    EXITED = "exited"
    ERROR = "error"


# The transitions that make a session "active" at a point in time (FR-3).
ACTIVE_TRANSITIONS: frozenset[LifecycleTransition] = frozenset(
    {
        LifecycleTransition.STARTING,
        LifecycleTransition.RUNNING,
        LifecycleTransition.IDLE,
        LifecycleTransition.WAITING,
    }
)


class TurnKind(StrEnum):
    USER = "user"  # a submitted user input (draft/non-submitted excluded)
    AGENT = "agent"  # a backend-native turn/message with a stable identity


class ToolOutcome(StrEnum):
    """Terminal classification of a tool call.

    ``UNKNOWN`` means the backend could not establish an outcome; it is never
    shown as success and is excluded from failure-rate denominators (Appendix:
    Tool-outcome coverage). Absent data must map to ``UNKNOWN``, not ``SUCCEEDED``.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"


# The outcomes that count as terminally classified for coverage/failure-rate.
TERMINAL_OUTCOMES: frozenset[ToolOutcome] = frozenset(
    {
        ToolOutcome.SUCCEEDED,
        ToolOutcome.FAILED,
        ToolOutcome.CANCELLED,
        ToolOutcome.TIMED_OUT,
    }
)


class ApprovalDecision(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    DECLINED = "declined"


class FactSource(StrEnum):
    """What produced a fact (part of its stable identity + provenance).

    Backend-derived facts use the backend id as the source string; ``RUNTIME`` is
    used for facts a generic runtime seam derives directly (e.g. session-created).
    """

    RUNTIME = "runtime"


# ── Shared denormalized dimensions ───────────────────────────────────────────


class FactDimensions(BaseModel):
    """Session-level filter dimensions stamped onto every fact at ingest.

    These are immutable-per-session (FR-1 filters that are session attributes),
    so stamping them inline lets filtered range queries and daily rollups avoid a
    join to ``sessions``. Tags are the one exception — they live in a normalized
    ``telemetry_fact_tag`` side table because ``sessions.tags`` is a JSON blob
    (see CONTRACT.md).
    """

    backend: str
    # Basename only, never a full path (FR-9 excludes paths). ``None`` = no repo.
    repo_name: str | None = None
    source: str  # SessionSource value (managed/attached_tmux/assistant/…)
    transport: str
    spawner_session_id: str | None = None
    is_child: bool = False


# ── The fact envelope + typed facts ──────────────────────────────────────────

_SCHEMA_VERSION: Literal[1] = 1


class TelemetryFactBase(BaseModel):
    """Common identity/provenance every fact carries.

    ``fact_id`` is the stable, source-owned identity; the store's unique key is
    ``(kind, source, fact_id)``. ``revision`` orders replacements: a fact replaces
    the canonical value of a known ``(kind, source, fact_id)`` only when its
    revision is strictly greater. A fact whose source cannot supply a stable id is
    marked ``partial=True`` and excluded from totals that require deduplication.
    """

    fact_id: str
    source: str
    kind: TelemetryFactKind
    revision: int = 0
    partial: bool = False
    session_id: str
    occurred_at: datetime
    schema_version: Literal[1] = _SCHEMA_VERSION
    dims: FactDimensions


class SessionLifecycleFact(TelemetryFactBase):
    kind: Literal[TelemetryFactKind.SESSION_LIFECYCLE] = (
        TelemetryFactKind.SESSION_LIFECYCLE
    )
    transition: LifecycleTransition


class TurnFact(TelemetryFactBase):
    kind: Literal[TelemetryFactKind.TURN] = TelemetryFactKind.TURN
    turn_kind: TurnKind
    # Concrete model at the observed turn (FR-4 "actual model at turn time").
    # Exact for agent turns (from the token record); best-effort for user turns
    # (session resolved_model as-of the event). ``None`` when unknown.
    model_at_turn: str | None = None
    effort_at_turn: str | None = None


class ToolCallFact(TelemetryFactBase):
    kind: Literal[TelemetryFactKind.TOOL_CALL] = TelemetryFactKind.TOOL_CALL
    # Bare normalized tool name (``Read``/``Bash``) — never the invocation/args.
    tool_name: str
    tool_category: str | None = None
    outcome: ToolOutcome = ToolOutcome.UNKNOWN
    duration_ms: int | None = None
    model_at_turn: str | None = None
    # Present only for approval-bearing tool events; drives FR-5 approval counts.
    approval_decision: ApprovalDecision | None = None


class ContextSnapshotFact(TelemetryFactBase):
    kind: Literal[TelemetryFactKind.CONTEXT_SNAPSHOT] = (
        TelemetryFactKind.CONTEXT_SNAPSHOT
    )
    used_tokens: int
    window_tokens: int | None = None
    # 0–100; ``None`` when the window is unknown (unavailable, not zero).
    occupancy_percent: float | None = None


class LimitSnapshotFact(TelemetryFactBase):
    """A provider rate-limit snapshot — account-scoped, NOT session-attributable.

    ``session_id`` carries the session the snapshot was observed through (for
    provenance) but limit aggregates group by ``(backend, account_key, window_id)``
    and the dashboard hides the limit card when any session-scoping filter is
    active (FR-1). ``account_key`` is a stable pseudonymous digest of the
    resolved account identity (never a raw email/org, FR-9); ``account_label``
    carries the human-readable name and is only ever surfaced by the API when
    the ``telemetry_local_labels`` setting opts into local labels.

    ``profile_label`` is different: it's the session's local, user-chosen
    ``account_profile_label`` (or ``"Default"`` when the session has no
    ``account_profile_id``) — never the raw OAuth email/org — so it's FR-9-safe
    to show by default and is the primary, always-shown display name for an
    account group.
    """

    kind: Literal[TelemetryFactKind.LIMIT_SNAPSHOT] = TelemetryFactKind.LIMIT_SNAPSHOT
    account_key: str
    account_label: str | None = None
    profile_label: str | None = None
    window_id: str
    window_label: str | None = None
    used_percent: float
    resets_at: datetime | None = None


TelemetryFact = Annotated[
    SessionLifecycleFact
    | TurnFact
    | ToolCallFact
    | ContextSnapshotFact
    | LimitSnapshotFact,
    Field(discriminator="kind"),
]


# ── Query inputs (shared by every /api/telemetry endpoint) ───────────────────


class TelemetryRange(BaseModel):
    """A resolved query window.

    Interpreted in the displayed Waypoint-host timezone with an inclusive
    ``start`` and exclusive ``end`` instant (Appendix: Date range). ``tz`` is the
    host tz's abbreviated name (``tzname()``) echoed back as a human label;
    ``utc_offset_minutes`` is the deterministic numeric offset (minutes east of
    UTC) the UI uses to render the correct host-tz calendar day, since ``tz`` is
    not a valid JS ``timeZone``. Defaults to ``0`` (UTC) for ranges constructed
    outside the request path.
    """

    start: datetime
    end: datetime
    tz: str
    utc_offset_minutes: int = 0


class TelemetryFilter(BaseModel):
    """The filter set every attributable summary/chart responds to (FR-1).

    ``None``/empty means unconstrained on that dimension. ``parent_scope`` selects
    top-level vs. child sessions; when filtering to a specific parent the
    include/exclude-descendants choice rides ``include_descendants`` (FR-7).
    """

    backends: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    repos: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    transports: list[str] = Field(default_factory=list)
    parent_scope: Literal["all", "top_level", "children"] = "all"
    parent_session_id: str | None = None
    include_descendants: bool = True

    def has_session_scoping(self) -> bool:
        """True when a non-time/non-backend filter is active.

        The provider-limit card/chart is hidden in this case because limit data
        is account-wide, not session-attributable (FR-1, FR-6).
        """
        return bool(
            self.models
            or self.repos
            or self.tags
            or self.sources
            or self.transports
            or self.parent_scope != "all"
            or self.parent_session_id
        )
