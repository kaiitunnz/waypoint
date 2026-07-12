"""Tests for the NL-insight weekly digest cadence (CONTRACT-NL.md §4/§6).

``maybe_generate_nl_digest`` runs on the existing telemetry maintenance tick
(not a new recurring primitive) — these tests exercise its age-check gate
directly, stubbing ``run_oneshot`` so no real backend/CLI is needed.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from waypoint.runtime import SessionRuntime
from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.telemetry.facts import TelemetryFilter, TelemetryRange
from waypoint.telemetry.nl import NLInsight


def _make_runtime(tmp_path: Path) -> tuple[SessionRuntime, Storage]:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    return SessionRuntime(settings, storage), storage


def _stub_run_oneshot(
    runtime: SessionRuntime, reply: str | None
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_run_oneshot(**kwargs: Any) -> str | None:
        calls.append(kwargs)
        return reply

    runtime.run_oneshot = fake_run_oneshot  # type: ignore[method-assign]
    return calls


async def test_maybe_generate_nl_digest_skips_when_disabled(tmp_path: Path) -> None:
    runtime, storage = _make_runtime(tmp_path)
    runtime.settings.telemetry_nl.enabled = False
    calls = _stub_run_oneshot(runtime, json.dumps({"prose": "x", "evidence": []}))

    result = await runtime.maybe_generate_nl_digest()

    assert result is None
    assert calls == []
    assert storage.telemetry.get_nl_insight() is None


async def test_maybe_generate_nl_digest_generates_when_nothing_stored(
    tmp_path: Path,
) -> None:
    runtime, storage = _make_runtime(tmp_path)
    runtime.settings.telemetry_nl.enabled = True
    _stub_run_oneshot(
        runtime,
        json.dumps({"prose": "All quiet.", "evidence": [], "confidence": "low"}),
    )

    result = await runtime.maybe_generate_nl_digest()

    assert result is not None
    assert result.prose == "All quiet."
    assert storage.telemetry.get_nl_insight() is not None


async def test_maybe_generate_nl_digest_skips_when_stored_digest_is_fresh(
    tmp_path: Path,
) -> None:
    runtime, storage = _make_runtime(tmp_path)
    runtime.settings.telemetry_nl.enabled = True
    runtime.settings.telemetry_nl.interval_hours = 168
    fresh = NLInsight(
        prose="already generated",
        evidence=[],
        range=TelemetryRange(
            start=datetime.now(UTC) - timedelta(days=7), end=datetime.now(UTC), tz="UTC"
        ),
        filters=TelemetryFilter(),
        confidence="low",
        generated_at=datetime.now(UTC),
        source_backend="claude_code",
        disclaimer="d",
    )
    storage.telemetry.set_nl_insight(fresh.model_dump_json())
    calls = _stub_run_oneshot(runtime, json.dumps({"prose": "new", "evidence": []}))

    result = await runtime.maybe_generate_nl_digest()

    assert result is None
    assert calls == []
    stored = storage.telemetry.get_nl_insight()
    assert stored is not None
    assert NLInsight.model_validate_json(stored).prose == "already generated"


async def test_maybe_generate_nl_digest_regenerates_when_stale(tmp_path: Path) -> None:
    runtime, storage = _make_runtime(tmp_path)
    runtime.settings.telemetry_nl.enabled = True
    runtime.settings.telemetry_nl.interval_hours = 24
    stale = NLInsight(
        prose="old digest",
        evidence=[],
        range=TelemetryRange(
            start=datetime.now(UTC) - timedelta(days=14),
            end=datetime.now(UTC) - timedelta(days=7),
            tz="UTC",
        ),
        filters=TelemetryFilter(),
        confidence="low",
        generated_at=datetime.now(UTC) - timedelta(hours=48),
        source_backend="claude_code",
        disclaimer="d",
    )
    storage.telemetry.set_nl_insight(stale.model_dump_json())
    calls = _stub_run_oneshot(
        runtime,
        json.dumps({"prose": "fresh digest", "evidence": [], "confidence": "medium"}),
    )

    result = await runtime.maybe_generate_nl_digest()

    assert result is not None
    assert result.prose == "fresh digest"
    # Two one-shot calls: the usage-prose digest plus the separate prose-free
    # instance health/capacity selection call (PRD FR-5).
    assert len(calls) == 2
    stored = storage.telemetry.get_nl_insight()
    assert stored is not None
    assert NLInsight.model_validate_json(stored).prose == "fresh digest"


async def test_maybe_generate_nl_digest_corrupt_stored_row_is_treated_as_absent(
    tmp_path: Path,
) -> None:
    runtime, storage = _make_runtime(tmp_path)
    runtime.settings.telemetry_nl.enabled = True
    storage.telemetry.set_nl_insight("not valid json")
    _stub_run_oneshot(
        runtime, json.dumps({"prose": "recovered", "evidence": [], "confidence": "low"})
    )

    result = await runtime.maybe_generate_nl_digest()

    assert result is not None
    assert result.prose == "recovered"


async def test_generate_nl_digest_returns_none_when_summarizer_returns_none(
    tmp_path: Path,
) -> None:
    runtime, storage = _make_runtime(tmp_path)
    runtime.settings.telemetry_nl.enabled = True
    _stub_run_oneshot(runtime, None)

    result = await runtime.generate_nl_digest()

    assert result is None
    assert storage.telemetry.get_nl_insight() is None
