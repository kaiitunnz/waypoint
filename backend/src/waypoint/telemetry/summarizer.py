"""``CodingAgentSummarizer`` — the default ``Summarizer`` (CONTRACT-NL.md §3).

Drives a configured coding agent through ``runtime.run_oneshot`` to turn a
whitelisted payload of telemetry aggregates + redacted drill-down rows into a
short prose digest with labelled evidence and a confidence level. Never
raises: a bad reply, a timeout, or the agent being unreachable all degrade to
``None`` rather than breaking the dashboard.

``telemetry_nl.mode == "headless"`` (a raw one-shot subprocess, allowed but
never the default per CONTRACT-NL.md §1) is not implemented yet — it falls
back to the same ``managed`` generation path via ``run_oneshot`` with a
warning log, rather than silently doing nothing or raising. A real headless
path is a follow-up.
"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.telemetry import aggregate
from waypoint.telemetry import insights as telemetry_insights
from waypoint.telemetry.facts import TelemetryFactKind, TelemetryFilter, TelemetryRange
from waypoint.telemetry.nl import NLInsight, NLInsightEvidence, NLInsightRequest

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.telemetry.summarizer")

# Bounded so the payload stays small and the redaction surface stays reviewable.
DRILLDOWN_SAMPLE_LIMIT = 20
GENERATION_TIMEOUT_SECONDS = 180.0

# Flags 2+ path segments (``/a/b``) or a ``~/...`` home-relative path — the NL
# payload must never carry a real filesystem path (CONTRACT-NL.md §3).
_PATH_LIKE_PATTERN = re.compile(r"(?:/[^/\s\"']+){2,}|~[/\\][^\s\"']+")

_DISCLAIMER = (
    "AI-generated summary of the deterministic telemetry aggregates above — "
    "an inference over measured facts, not itself a measured outcome. Verify "
    "against the linked evidence before treating it as ground truth."
)

_INSTRUCTION_PROMPT = """You are writing a short natural-language digest of your reader's own AI coding-agent usage telemetry, from the JSON payload below. The reader IS the Waypoint operator whose activity this describes.

Rules — follow exactly:
- Address the reader directly in the second person ("you", "your"). Never refer to them in the third person ("the user", "they") — this is their own dashboard.
- Every material claim in "prose" must be backed by an item in "evidence" that names the aggregate field it came from.
- State the time range and any active filters explicitly in the prose.
- Set "confidence" to "low", "medium", or "high" based on how well the data supports the summary — use "low" when data is sparse, meter coverage is well under 100%, or the range is nearly empty.
- Never present an inference (a trend, a comparison, a recommendation) as a measured fact — phrase those as inferences, clearly distinct from counted totals.
- Do not invent any number that is not present in the payload.

Respond with ONLY a JSON object of exactly this shape and nothing else:
{"prose": "...", "evidence": [{"statement": "...", "metric": "...", "value": "...", "click_through": {}}], "confidence": "low|medium|high"}

Telemetry payload:
"""


def assert_no_path_like_strings(value: Any, *, _at: str = "$") -> None:
    """Raise if any string leaf in ``value`` looks like a filesystem path.

    A defensive boundary assertion (CONTRACT-NL.md §3): every aggregate field
    this payload draws from is already privacy-safe (basename repo names,
    bare tool names, no raw text), so this should never trip — it exists to
    fail closed if a future change ever leaks one, rather than ship it.
    """
    if isinstance(value, str):
        if _PATH_LIKE_PATTERN.search(value):
            raise ValueError(f"path-like string in NL payload at {_at}: {value!r}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            assert_no_path_like_strings(item, _at=f"{_at}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_path_like_strings(item, _at=f"{_at}[{index}]")


def _redact_drilldown_row(item: Any) -> dict[str, Any]:
    """Whitelist a drilldown row to exactly (session_id, tool_name, ts, outcome, model)."""
    return {
        "session_id": item.session_id,
        "tool_name": item.tool_name,
        "ts": item.occurred_at.isoformat(),
        "outcome": item.outcome,
        "model": item.model,
    }


def build_nl_request(
    storage: Storage, settings: Settings, rng: TelemetryRange, flt: TelemetryFilter
) -> NLInsightRequest:
    """Assemble the whitelisted payload a summarizer receives.

    Pulled entirely from the PR1 aggregate layer (facts/rollups, already
    privacy-scrubbed) — never from a transcript. ``drilldown_samples`` is
    bounded to ``DRILLDOWN_SAMPLE_LIMIT`` redacted tool-call rows. The fully
    serialized result is asserted path-free before it's returned.
    """
    overview = aggregate.build_overview(storage, settings, rng, flt)
    tokens = aggregate.build_tokens(storage, rng, flt)
    activity = aggregate.build_activity(storage, rng, flt)
    health = aggregate.build_health(storage, settings, rng, flt)
    deterministic = telemetry_insights.compute_insights(storage, settings, rng, flt)
    drilldown = aggregate.build_drilldown(
        storage, rng, flt, TelemetryFactKind.TOOL_CALL, 1, DRILLDOWN_SAMPLE_LIMIT
    )
    request = NLInsightRequest(
        range=rng,
        filters=flt,
        aggregates={
            "overview": _strip_navigation(overview.model_dump(mode="json")),
            "tokens": _strip_navigation(tokens.model_dump(mode="json")),
            "activity": _strip_navigation(activity.model_dump(mode="json")),
            "health": _strip_navigation(health.model_dump(mode="json")),
        },
        deterministic_insights=[
            _strip_navigation(i.model_dump(mode="json")) for i in deterministic
        ],
        drilldown_samples=[_redact_drilldown_row(item) for item in drilldown.items],
    )
    assert_no_path_like_strings(request.model_dump(mode="json"))
    return request


def _strip_navigation(obj: Any) -> Any:
    """Drop ``click_through`` navigation hints recursively.

    They carry API route strings (``/api/telemetry/health``) that the summarizer
    never needs and that the path-like privacy guard would (correctly, for a
    filesystem path) reject. Removing them keeps the guard strict while feeding
    the model only facts, not frontend navigation.
    """
    if isinstance(obj, dict):
        return {k: _strip_navigation(v) for k, v in obj.items() if k != "click_through"}
    if isinstance(obj, list):
        return [_strip_navigation(v) for v in obj]
    return obj


def _strip_code_fence(text: str) -> str:
    """Tolerate a ```json ... ``` fence, which coding agents commonly wrap replies in."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _try_parse_json_object(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_evidence(raw_items: Any) -> list[NLInsightEvidence]:
    evidence: list[NLInsightEvidence] = []
    if not isinstance(raw_items, list):
        return evidence
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        click_through = item.get("click_through")
        try:
            evidence.append(
                NLInsightEvidence(
                    statement=str(item.get("statement", "")),
                    metric=str(item.get("metric", "")),
                    value=str(item.get("value", "")),
                    click_through=(
                        click_through if isinstance(click_through, dict) else {}
                    ),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return evidence


def _build_insight(
    *,
    prose: str,
    evidence: list[NLInsightEvidence],
    confidence: str,
    request: NLInsightRequest,
    backend: str,
    model: str | None,
) -> NLInsight | None:
    prose = prose.strip()
    if not prose:
        return None
    return NLInsight(
        prose=prose,
        evidence=evidence,
        range=request.range,
        filters=request.filters,
        confidence=confidence if confidence in ("low", "medium", "high") else "low",
        generated_at=datetime.now(UTC),
        source_backend=backend,
        source_model=model,
        disclaimer=_DISCLAIMER,
    )


def _parse_reply(
    raw: str, request: NLInsightRequest, *, backend: str, model: str | None
) -> NLInsight | None:
    """Parse the agent's reply, tolerating anything short of empty prose.

    A well-formed JSON reply is preferred; a reply that fails to parse still
    becomes an ``NLInsight`` with the raw text as prose, no evidence, and
    "low" confidence (CONTRACT-NL.md §3) rather than being discarded.
    """
    parsed = _try_parse_json_object(raw)
    if parsed is None:
        return _build_insight(
            prose=raw,
            evidence=[],
            confidence="low",
            request=request,
            backend=backend,
            model=model,
        )
    return _build_insight(
        prose=str(parsed.get("prose", "")),
        evidence=_parse_evidence(parsed.get("evidence")),
        confidence=str(parsed.get("confidence", "low")),
        request=request,
        backend=backend,
        model=model,
    )


class CodingAgentSummarizer:
    """Default ``Summarizer``: drives ``settings.telemetry_nl``'s configured agent."""

    def __init__(self, runtime: "SessionRuntime", settings: Settings) -> None:
        self._runtime = runtime
        self._settings = settings

    async def summarize(self, request: NLInsightRequest) -> NLInsight | None:
        config = self._settings.telemetry_nl
        if config.mode == "headless":
            log.warning(
                "telemetry_nl.mode=headless is not implemented yet; "
                "falling back to the managed one-shot path"
            )
        try:
            payload = json.dumps(request.model_dump(mode="json"), indent=2)
            raw = await self._runtime.run_oneshot(
                backend=config.backend,
                transport=config.transport,
                model=config.model,
                account_profile=config.account_profile,
                instruction=_INSTRUCTION_PROMPT,
                payload=payload,
                timeout_s=GENERATION_TIMEOUT_SECONDS,
            )
            if not raw:
                return None
            return _parse_reply(
                raw, request, backend=config.backend, model=config.model
            )
        except Exception:  # noqa: BLE001
            log.warning("NL-insight summarize failed", exc_info=True)
            return None
