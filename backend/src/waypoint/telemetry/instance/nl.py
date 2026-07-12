"""NL integration for instance health & capacity (PRD FR-5).

The privacy/grounding contract that differs from the usage-prose summarizer:
the model never writes instance facts as free-form prose. It receives a bounded,
path-free aggregate and a fixed menu of claim templates + allowlisted evidence
identifiers, and returns ONLY structured ``{claim_template_id, evidence_ids}``
selections. The server validates each selection, fills every number and
navigation link from the aggregate, and renders the final bullet text itself —
model-supplied numbers, routes, and free-form prose are never trusted. A
recommendation template is only permitted when its matching deterministic
insight already fired, so the NL can explain a condition but never invent a
diagnosis or action. Invalid/incomplete selections, and claims whose data is
unavailable/partial, are omitted.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from waypoint.telemetry.api_models import Insight
from waypoint.telemetry.instance.model import (
    DataQuality,
    InstanceSnapshot,
    StorageCategory,
)
from waypoint.telemetry.nl import NLInsightEvidence, NLInstanceBullet

_ENDPOINT = "/api/telemetry/instance"
_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def format_bytes(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


class InstanceNLAggregate(BaseModel):
    """The bounded, path-free instance payload the summarizer receives.

    Numbers, fixed category names, and enum states only — no path, filename,
    content, session id, or command argument.
    """

    observed_at: datetime
    data_quality: DataQuality
    total_bytes: int
    category_bytes: dict[str, int] = Field(default_factory=dict)
    largest_category: str | None = None
    largest_category_bytes: int = 0
    orphan_dir_count: int = 0
    session_dir_count: int = 0
    attachment_count: int = 0
    attachment_bytes: int = 0
    redundant_log_count: int = 0
    redundant_log_bytes: int = 0
    db_free_bytes: int = 0
    db_free_percent: float = 0.0
    db_measured: bool = False
    fired_insights: list[str] = Field(default_factory=list)


class _Evidence(BaseModel):
    statement: str
    value: str
    focus: str


class _Template(BaseModel):
    text: str  # server-owned format string
    allowed_evidence: frozenset[str]
    focus: str
    requires_insight: str | None = None  # only allowed if this insight fired


# The fixed claim menu. ``requires_insight`` gates recommendation templates on
# the deterministic insight having fired.
INSTANCE_CLAIM_TEMPLATES: dict[str, _Template] = {
    "total_footprint": _Template(
        text="Waypoint-managed storage totals {total_bytes} across {category_count} categories.",
        allowed_evidence=frozenset({"total_bytes"}),
        focus="overview",
    ),
    "largest_category": _Template(
        text="The largest category is {largest_category} at {largest_category_bytes}.",
        allowed_evidence=frozenset({"largest_category", "total_bytes"}),
        focus="overview",
    ),
    "data_quality_note": _Template(
        text="This snapshot is {data_quality} as of the shown observation time.",
        allowed_evidence=frozenset({"data_quality"}),
        focus="overview",
    ),
    "orphan_review": _Template(
        text=(
            "{orphan_dir_count} orphaned session director{orphan_plural} hold "
            "{orphan_bytes}; review the maintenance dry-run before pruning — "
            "deletion is never automatic."
        ),
        allowed_evidence=frozenset({"orphan_dir_count", "orphan_bytes"}),
        focus="orphans",
        requires_insight="orphan_data",
    ),
    "redundant_logs": _Template(
        text=(
            "{redundant_log_count} inactive events.jsonl log{log_plural} hold "
            "{redundant_log_bytes} and can be cleared; running-session logs are "
            "excluded."
        ),
        allowed_evidence=frozenset({"redundant_log_count", "redundant_log_bytes"}),
        focus="logs",
        requires_insight="redundant_logs",
    ),
    "vacuum_candidate": _Template(
        text=(
            "The database has {db_free_bytes} of free pages ({db_free_percent}); "
            "a VACUUM may reclaim space but is an operator decision, not a "
            "guaranteed saving."
        ),
        allowed_evidence=frozenset({"db_free_bytes", "db_free_percent"}),
        focus="database",
        requires_insight="database_vacuum",
    ),
}


def build_instance_nl_aggregate(
    snapshot: InstanceSnapshot, insights: list[Insight]
) -> InstanceNLAggregate:
    category_bytes = {c.category.value: c.bytes for c in snapshot.categories}
    largest = max(snapshot.categories, key=lambda c: c.bytes, default=None)
    attach = snapshot.category(StorageCategory.ATTACHMENTS)
    actionable_log_count = (
        snapshot.redundant_logs.count - snapshot.redundant_logs.orphan_overlap_count
    )
    actionable_log_bytes = (
        snapshot.redundant_logs.bytes - snapshot.redundant_logs.orphan_overlap_bytes
    )
    return InstanceNLAggregate(
        observed_at=snapshot.observed_at,
        data_quality=snapshot.data_quality,
        total_bytes=snapshot.total_bytes,
        category_bytes=category_bytes,
        largest_category=largest.category.value if largest and largest.bytes else None,
        largest_category_bytes=largest.bytes if largest else 0,
        orphan_dir_count=snapshot.counts.orphan_dir_count,
        session_dir_count=snapshot.counts.session_dir_count,
        attachment_count=snapshot.counts.attachment_count,
        attachment_bytes=attach.bytes,
        redundant_log_count=max(0, actionable_log_count),
        redundant_log_bytes=max(0, actionable_log_bytes),
        db_free_bytes=snapshot.database.free_bytes,
        db_free_percent=snapshot.database.free_percent,
        db_measured=snapshot.database.measured,
        fired_insights=[i.type for i in insights],
    )


def _evidence_table(agg: InstanceNLAggregate) -> dict[str, _Evidence]:
    return {
        "total_bytes": _Evidence(
            statement="Total managed storage",
            value=format_bytes(agg.total_bytes),
            focus="overview",
        ),
        "largest_category": _Evidence(
            statement="Largest storage category",
            value=(
                f"{agg.largest_category} ({format_bytes(agg.largest_category_bytes)})"
                if agg.largest_category
                else ""
            ),
            focus="overview",
        ),
        "data_quality": _Evidence(
            statement="Snapshot data quality",
            value=agg.data_quality.value,
            focus="overview",
        ),
        "orphan_dir_count": _Evidence(
            statement="Orphaned session directories",
            value=str(agg.orphan_dir_count),
            focus="orphans",
        ),
        "orphan_bytes": _Evidence(
            statement="Orphaned session bytes",
            value=format_bytes(agg.category_bytes.get("orphan_sessions", 0)),
            focus="orphans",
        ),
        "redundant_log_count": _Evidence(
            statement="Redundant log candidates",
            value=str(agg.redundant_log_count),
            focus="logs",
        ),
        "redundant_log_bytes": _Evidence(
            statement="Redundant log bytes",
            value=format_bytes(agg.redundant_log_bytes),
            focus="logs",
        ),
        "db_free_bytes": _Evidence(
            statement="Database free pages",
            value=format_bytes(agg.db_free_bytes),
            focus="database",
        ),
        "db_free_percent": _Evidence(
            statement="Database free-page percent",
            value=f"{agg.db_free_percent * 100:.0f}%",
            focus="database",
        ),
    }


def _render_values(agg: InstanceNLAggregate) -> dict[str, str]:
    return {
        "total_bytes": format_bytes(agg.total_bytes),
        "category_count": str(len(agg.category_bytes)),
        "largest_category": agg.largest_category or "",
        "largest_category_bytes": format_bytes(agg.largest_category_bytes),
        "data_quality": agg.data_quality.value,
        "orphan_dir_count": str(agg.orphan_dir_count),
        "orphan_plural": "y" if agg.orphan_dir_count == 1 else "ies",
        "orphan_bytes": format_bytes(agg.category_bytes.get("orphan_sessions", 0)),
        "redundant_log_count": str(agg.redundant_log_count),
        "log_plural": "" if agg.redundant_log_count == 1 else "s",
        "redundant_log_bytes": format_bytes(agg.redundant_log_bytes),
        "db_free_bytes": format_bytes(agg.db_free_bytes),
        "db_free_percent": f"{agg.db_free_percent * 100:.0f}%",
    }


def _claim_data_available(template_id: str, agg: InstanceNLAggregate) -> bool:
    if agg.data_quality == DataQuality.UNAVAILABLE:
        return False
    if template_id == "largest_category":
        return agg.largest_category is not None
    if template_id == "total_footprint":
        return agg.total_bytes > 0
    return True


def render_instance_bullets(
    selections: object, agg: InstanceNLAggregate
) -> list[NLInstanceBullet]:
    """Validate model selections and server-render each bullet.

    Drops any selection with an unknown template, an evidence id outside the
    template's allowlist, a recommendation whose deterministic insight did not
    fire, or a claim whose driving data is unavailable. Never reads a
    model-supplied number, route, or free-form string.
    """
    if not isinstance(selections, list):
        return []
    evidence_table = _evidence_table(agg)
    values = _render_values(agg)
    bullets: list[NLInstanceBullet] = []
    used_templates: set[str] = set()
    for selection in selections:
        if not isinstance(selection, dict):
            continue
        template_id = selection.get("template_id")
        if not isinstance(template_id, str):
            continue
        template = INSTANCE_CLAIM_TEMPLATES.get(template_id)
        if template is None or template_id in used_templates:
            continue
        if (
            template.requires_insight is not None
            and template.requires_insight not in agg.fired_insights
        ):
            continue
        if not _claim_data_available(template_id, agg):
            continue
        raw_ids = selection.get("evidence_ids")
        evidence_ids = [
            e
            for e in (raw_ids if isinstance(raw_ids, list) else [])
            if isinstance(e, str) and e in template.allowed_evidence
        ]
        try:
            text = template.text.format(**values)
        except (KeyError, IndexError):
            continue
        evidence = [
            NLInsightEvidence(
                statement=evidence_table[e].statement,
                metric=e,
                value=evidence_table[e].value,
                click_through={"endpoint": _ENDPOINT, "focus": evidence_table[e].focus},
            )
            for e in evidence_ids
            if e in evidence_table and evidence_table[e].value
        ]
        bullets.append(
            NLInstanceBullet(text=text, template_id=template_id, evidence=evidence)
        )
        used_templates.add(template_id)
    return bullets
