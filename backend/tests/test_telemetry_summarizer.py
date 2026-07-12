"""Tests for the NL-insight summarizer (CONTRACT-NL.md §3/§6).

Covers the whitelisted payload builder (no raw text/paths ever leaves the
boundary), tolerant reply parsing, and that a failing/unreachable agent
degrades to ``None`` rather than raising.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from waypoint.presets import PresetManager
from waypoint.schemas import (
    SessionPresetCreateRequest,
    SessionPresetSpec,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.telemetry.facts import (
    ContextSnapshotFact,
    FactDimensions,
    TelemetryFilter,
    TelemetryRange,
    ToolCallFact,
    ToolOutcome,
)
from waypoint.telemetry.nl import NLInsightRequest
from waypoint.telemetry.summarizer import (
    CodingAgentSummarizer,
    _parse_reply,
    _try_parse_json_object,
    assert_no_path_like_strings,
    build_nl_request,
)


class _FakeRuntime:
    """A ``run_oneshot``-only stand-in — the summarizer never touches anything else.

    ``presets`` defaults to ``None``; a test exercising ``telemetry_nl.preset``
    resolution passes a real ``PresetManager`` (the only other attribute the
    summarizer reads off the runtime).
    """

    def __init__(
        self,
        reply: str | None = None,
        *,
        raises: bool = False,
        presets: PresetManager | None = None,
    ) -> None:
        self.reply = reply
        self.raises = raises
        self.presets = presets
        self.calls: list[dict[str, Any]] = []

    async def run_oneshot(self, **kwargs: Any) -> str | None:
        self.calls.append(kwargs)
        if self.raises:
            raise RuntimeError("boom")
        return self.reply


def _dims() -> FactDimensions:
    return FactDimensions.model_validate(
        {
            "backend": "codex",
            "repo_name": "waypoint",
            "source": SessionSource.MANAGED,
            "transport": "tmux",
            "spawner_session_id": None,
            "is_child": False,
        }
    )


def _make_session(storage: Storage, session_id: str) -> datetime:
    now = datetime.now(UTC)
    storage.create_session(
        SessionRecord(
            id=session_id,
            backend="codex",
            source=SessionSource.MANAGED,
            transport="tmux",
            title=session_id,
            cwd="/tmp",
            repo_name="waypoint",
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path="/tmp/raw.log",
            structured_log_path="/tmp/events.jsonl",
        )
    )
    return now


def _range() -> TelemetryRange:
    now = datetime.now(UTC)
    return TelemetryRange(start=now - timedelta(days=7), end=now, tz="UTC")


# ── payload assembly / whitelist ───────────────────────────────────────────


def test_assert_no_path_like_strings_passes_safe_payload() -> None:
    assert_no_path_like_strings(
        {"session_id": "codex-abc123", "tool_name": "Read", "repo_name": "waypoint"}
    )


def test_assert_no_path_like_strings_raises_on_path() -> None:
    with pytest.raises(ValueError, match="path-like"):
        assert_no_path_like_strings({"cwd": "/home/user/projects/waypoint"})


def test_assert_no_path_like_strings_raises_on_home_relative_path() -> None:
    with pytest.raises(ValueError, match="path-like"):
        assert_no_path_like_strings("~/waypoint/backend/src")


def test_build_nl_request_payload_has_no_path_like_strings(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    _make_session(storage, "s1")
    settings = Settings(data_dir=tmp_path / "data")
    request = build_nl_request(storage, settings, _range(), TelemetryFilter())
    # build_nl_request already asserts this internally; re-assert explicitly
    # on the round-tripped JSON so a regression here fails this test directly.
    assert_no_path_like_strings(request.model_dump(mode="json"))


def test_build_nl_request_strips_insight_navigation_endpoints(tmp_path: Path) -> None:
    """A firing insight carries a ``click_through.endpoint`` like
    ``/api/telemetry/health`` — an API route, not a filesystem path. It must be
    stripped from the payload so the path-like privacy guard doesn't reject the
    whole request (regression: this 500'd live once a near-limit insight fired)."""
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    storage.telemetry.ingest_fact(
        ContextSnapshotFact(
            fact_id="s1:ctx",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            used_tokens=95000,
            window_tokens=100000,
            occupancy_percent=95.0,  # >= 90 critical → a context-pressure insight fires
        )
    )
    settings = Settings(data_dir=tmp_path / "data")
    # Must not raise (previously ValueError: path-like string … /api/telemetry/health).
    request = build_nl_request(storage, settings, _range(), TelemetryFilter())
    dumped = request.model_dump(mode="json")
    assert request.deterministic_insights, "expected a context-pressure insight to fire"

    def _has_key(obj: Any, key: str) -> bool:
        if isinstance(obj, dict):
            return key in obj or any(_has_key(v, key) for v in obj.values())
        if isinstance(obj, list):
            return any(_has_key(v, key) for v in obj)
        return False

    assert not _has_key(dumped, "click_through")


def test_build_nl_request_drilldown_samples_are_whitelisted(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    storage.telemetry.ingest_fact(
        ToolCallFact(
            fact_id="tool-1",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            tool_name="Read",
            outcome=ToolOutcome.SUCCEEDED,
        )
    )
    settings = Settings(data_dir=tmp_path / "data")
    request = build_nl_request(storage, settings, _range(), TelemetryFilter())
    assert len(request.drilldown_samples) == 1
    row = request.drilldown_samples[0]
    assert set(row) == {"session_id", "tool_name", "ts", "outcome", "model"}
    assert row["session_id"] == "s1"
    assert row["tool_name"] == "Read"


# ── reply parsing ──────────────────────────────────────────────────────────


def _request() -> NLInsightRequest:
    return NLInsightRequest(range=_range(), filters=TelemetryFilter())


def test_try_parse_json_object_tolerates_code_fence() -> None:
    raw = '```json\n{"prose": "hi", "evidence": [], "confidence": "low"}\n```'
    parsed = _try_parse_json_object(raw)
    assert parsed == {"prose": "hi", "evidence": [], "confidence": "low"}


def test_parse_reply_well_formed_json() -> None:
    raw = json.dumps(
        {
            "prose": "Token volume was flat this week.",
            "evidence": [
                {
                    "statement": "32k tokens used",
                    "metric": "overview.tokens.totals",
                    "value": "32000",
                    "click_through": {"endpoint": "/api/telemetry/tokens"},
                }
            ],
            "confidence": "medium",
        }
    )
    insight = _parse_reply(raw, _request(), backend="claude_code", model="haiku")
    assert insight is not None
    assert insight.prose == "Token volume was flat this week."
    assert insight.confidence == "medium"
    assert len(insight.evidence) == 1
    assert insight.evidence[0].metric == "overview.tokens.totals"
    assert insight.source_backend == "claude_code"
    assert insight.source_model == "haiku"


def test_parse_reply_falls_back_to_raw_prose_when_unparseable() -> None:
    insight = _parse_reply(
        "Just a plain sentence, not JSON.",
        _request(),
        backend="claude_code",
        model=None,
    )
    assert insight is not None
    assert insight.prose == "Just a plain sentence, not JSON."
    assert insight.evidence == []
    assert insight.confidence == "low"


def test_parse_reply_returns_none_for_empty_prose() -> None:
    raw = json.dumps({"prose": "   ", "evidence": [], "confidence": "high"})
    assert _parse_reply(raw, _request(), backend="claude_code", model=None) is None


def test_parse_reply_invalid_confidence_defaults_to_low() -> None:
    raw = json.dumps({"prose": "ok", "evidence": [], "confidence": "very sure"})
    insight = _parse_reply(raw, _request(), backend="claude_code", model=None)
    assert insight is not None
    assert insight.confidence == "low"


# ── CodingAgentSummarizer / graceful failure ───────────────────────────────


async def test_summarize_returns_none_when_run_oneshot_returns_none(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    runtime = _FakeRuntime(reply=None)
    summarizer = CodingAgentSummarizer(runtime, settings)  # type: ignore[arg-type]
    result = await summarizer.summarize(_request())
    assert result is None
    assert len(runtime.calls) == 1


async def test_summarize_never_raises_when_run_oneshot_raises(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    runtime = _FakeRuntime(raises=True)
    summarizer = CodingAgentSummarizer(runtime, settings)  # type: ignore[arg-type]
    result = await summarizer.summarize(_request())
    assert result is None


async def test_summarize_returns_insight_on_well_formed_reply(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    reply = json.dumps({"prose": "Quiet week.", "evidence": [], "confidence": "low"})
    runtime = _FakeRuntime(reply=reply)
    summarizer = CodingAgentSummarizer(runtime, settings)  # type: ignore[arg-type]
    result = await summarizer.summarize(_request())
    assert result is not None
    assert result.prose == "Quiet week."
    assert result.source_backend == settings.telemetry_nl.backend


# ── telemetry_nl.preset resolution (#6, iteration 4) ───────────────────────


def _make_preset_manager(tmp_path: Path) -> PresetManager:
    storage = Storage(tmp_path / "presets.sqlite")
    return PresetManager(storage)


async def test_summarize_resolves_launch_from_configured_preset(
    tmp_path: Path,
) -> None:
    presets = _make_preset_manager(tmp_path)
    presets.create(
        SessionPresetCreateRequest(
            name="telemetry-digest",
            spec=SessionPresetSpec(
                backend="codex",
                transport="tmux",
                model="o4-mini",
                permission_mode="auto",
                account_profile_id="work",
            ),
        )
    )
    settings = Settings(data_dir=tmp_path / "data")
    settings.telemetry_nl.preset = "telemetry-digest"
    # The individually configured fields must be overridden by the preset.
    settings.telemetry_nl.backend = "claude_code"
    settings.telemetry_nl.transport = "claude_tty"
    settings.telemetry_nl.model = "haiku"
    settings.telemetry_nl.account_profile = "default"

    reply = json.dumps({"prose": "Quiet week.", "evidence": [], "confidence": "low"})
    runtime = _FakeRuntime(reply=reply, presets=presets)
    summarizer = CodingAgentSummarizer(runtime, settings)  # type: ignore[arg-type]
    result = await summarizer.summarize(_request())

    assert result is not None
    assert len(runtime.calls) == 1
    call = runtime.calls[0]
    assert call["backend"] == "codex"
    assert call["transport"] == "tmux"
    assert call["model"] == "o4-mini"
    assert call["permission_mode"] == "auto"
    assert call["account_profile"] == "work"
    assert result.source_backend == "codex"
    assert result.source_model == "o4-mini"


async def test_summarize_preset_falls_back_to_individual_fields_when_unset(
    tmp_path: Path,
) -> None:
    """A preset that only sets ``model`` leaves the other launch fields to
    fall back to the individually configured ``telemetry_nl`` values."""
    presets = _make_preset_manager(tmp_path)
    presets.create(
        SessionPresetCreateRequest(
            name="model-only",
            spec=SessionPresetSpec(model="o4-mini"),
        )
    )
    settings = Settings(data_dir=tmp_path / "data")
    settings.telemetry_nl.preset = "model-only"
    settings.telemetry_nl.backend = "claude_code"
    settings.telemetry_nl.transport = "claude_tty"
    settings.telemetry_nl.account_profile = "default"

    runtime = _FakeRuntime(reply=None, presets=presets)
    summarizer = CodingAgentSummarizer(runtime, settings)  # type: ignore[arg-type]
    await summarizer.summarize(_request())

    call = runtime.calls[0]
    assert call["backend"] == "claude_code"
    assert call["transport"] == "claude_tty"
    assert call["model"] == "o4-mini"
    assert call["account_profile"] == "default"
    assert call["permission_mode"] is None


async def test_summarize_unknown_preset_falls_back_to_individual_fields(
    tmp_path: Path,
) -> None:
    presets = _make_preset_manager(tmp_path)
    settings = Settings(data_dir=tmp_path / "data")
    settings.telemetry_nl.preset = "does-not-exist"
    settings.telemetry_nl.backend = "claude_code"
    settings.telemetry_nl.model = "haiku"

    runtime = _FakeRuntime(reply=None, presets=presets)
    summarizer = CodingAgentSummarizer(runtime, settings)  # type: ignore[arg-type]
    await summarizer.summarize(_request())

    call = runtime.calls[0]
    assert call["backend"] == "claude_code"
    assert call["model"] == "haiku"


async def test_summarize_without_preset_uses_individual_fields_unchanged(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    reply = json.dumps({"prose": "ok", "evidence": [], "confidence": "low"})
    runtime = _FakeRuntime(reply=reply)  # no .presets access should occur
    summarizer = CodingAgentSummarizer(runtime, settings)  # type: ignore[arg-type]
    await summarizer.summarize(_request())

    call = runtime.calls[0]
    assert call["backend"] == settings.telemetry_nl.backend
    assert call["transport"] == settings.telemetry_nl.transport
    assert call["model"] == settings.telemetry_nl.model
    assert call["account_profile"] == settings.telemetry_nl.account_profile
    assert call["permission_mode"] is None
