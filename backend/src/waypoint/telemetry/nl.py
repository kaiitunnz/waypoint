"""Natural-language insight contract (opt-in AI summarizer over aggregates).

An optional summarizer turns the deterministic telemetry aggregates into a short
prose digest with labelled evidence, time range, and confidence. It is off by
default and sends only aggregate + redacted drill-down data to the configured
coding agent — never raw prompts, outputs, tool arguments, or paths.

This module is the seam: the ``Summarizer`` protocol keeps the generation
backend behind one interface (the default implementation drives a configured
coding agent via a one-shot managed session), and ``NLInsight`` /
``NLInsightRequest`` are the data contract the API, scheduler digest, and
frontend share. The concrete summarizer and its runtime plumbing live in
``telemetry/summarizer.py``.
"""

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

from waypoint.telemetry.facts import TelemetryFilter, TelemetryRange


class NLInsightEvidence(BaseModel):
    """A single claim's link back to the aggregate that supports it."""

    statement: str
    # Which dashboard aggregate/endpoint backs this claim, so the UI can deep
    # link to the exact filtered evidence rather than asserting it unsourced.
    metric: str
    value: str
    click_through: dict[str, Any] = Field(default_factory=dict)


class NLInstanceBullet(BaseModel):
    """A server-rendered instance health/capacity digest bullet (CONTRACT §FR-5).

    Unlike ``prose``, instance bullets are never free-form model text: the model
    selects a fixed claim template + allowlisted evidence ids, and the server
    fills the numbers and renders ``text`` itself. ``template_id`` records which
    claim was rendered so it stays auditable.
    """

    text: str
    template_id: str
    evidence: list[NLInsightEvidence] = Field(default_factory=list)


class NLInsight(BaseModel):
    """A generated natural-language digest over a range/filter.

    ``prose`` is the human-readable summary; every material claim it makes must
    also appear in ``evidence`` linked to its aggregate. ``confidence`` and the
    ``disclaimer`` keep it honest — an NL digest is an inference over measured
    facts, never itself a measured outcome. ``instance_bullets`` carries the
    server-rendered instance health/capacity claims (never free-form prose).
    """

    prose: str
    evidence: list[NLInsightEvidence] = Field(default_factory=list)
    range: TelemetryRange
    filters: TelemetryFilter
    confidence: str  # "low" | "medium" | "high"
    generated_at: datetime
    source_backend: str
    source_model: str | None = None
    disclaimer: str
    instance_bullets: list[NLInstanceBullet] = Field(default_factory=list)


class NLInsightRequest(BaseModel):
    """The whitelisted payload a summarizer receives.

    Assembled from facts/aggregates only. ``drilldown_samples`` carries redacted
    rows (session id, normalized tool name, timestamp, outcome, model) — never
    any raw text, tool arguments, filenames, or paths.
    """

    range: TelemetryRange
    filters: TelemetryFilter
    aggregates: dict[str, Any] = Field(default_factory=dict)
    deterministic_insights: list[dict[str, Any]] = Field(default_factory=list)
    drilldown_samples: list[dict[str, Any]] = Field(default_factory=list)


class Summarizer(Protocol):
    """Generates an :class:`NLInsight` from a whitelisted request.

    Provider-swappable so a future direct-API or local implementation drops in
    without touching callers. Implementations must degrade gracefully (return
    ``None``) rather than raise, so a generation failure never breaks the
    dashboard.
    """

    async def summarize(self, request: NLInsightRequest) -> NLInsight | None: ...
