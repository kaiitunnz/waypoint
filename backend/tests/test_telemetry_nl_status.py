"""Tests for the server-owned NL-digest regeneration status (CONTRACT-NL §5).

Exercises the single-flight launcher, same-range coalescing, divergent-range
reject-with-reason, id-guarded settle, boot reconciliation, and the status-marker
round-trip — all with ``run_oneshot`` stubbed so no real backend/CLI runs.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from waypoint.runtime import SessionRuntime
from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.telemetry.facts import TelemetryFilter, TelemetryRange
from waypoint.telemetry.nl import NLGenerationState, NLGenerationStatus, NLInsight


def _make_runtime(tmp_path: Path) -> tuple[SessionRuntime, Storage]:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    settings.telemetry_nl.enabled = True
    storage = Storage(settings.database_path)
    return SessionRuntime(settings, storage), storage


def _stub_run_oneshot(runtime: SessionRuntime, reply: str | None) -> None:
    async def fake_run_oneshot(**_kwargs: Any) -> str | None:
        return reply

    runtime.run_oneshot = fake_run_oneshot  # type: ignore[method-assign]


def _gated_run_oneshot(runtime: SessionRuntime, reply: str | None) -> asyncio.Event:
    """Stub ``run_oneshot`` that blocks on a gate so a run stays ``generating``."""
    gate = asyncio.Event()

    async def fake_run_oneshot(**_kwargs: Any) -> str | None:
        await gate.wait()
        return reply

    runtime.run_oneshot = fake_run_oneshot  # type: ignore[method-assign]
    return gate


def _range(days_ago_start: int, days_ago_end: int) -> TelemetryRange:
    now = datetime.now(UTC)
    return TelemetryRange(
        start=now - timedelta(days=days_ago_start),
        end=now - timedelta(days=days_ago_end),
        tz="UTC",
    )


async def test_status_marker_round_trips_and_clears(tmp_path: Path) -> None:
    _, storage = _make_runtime(tmp_path)
    status = NLGenerationStatus(
        status=NLGenerationState.GENERATING,
        generation_id="abc",
        requested_at=datetime.now(UTC),
        range=_range(7, 0),
        filters=TelemetryFilter(),
    )
    storage.telemetry.set_nl_insight_status(status.model_dump_json())

    stored = storage.telemetry.get_nl_insight_status()
    assert stored is not None
    assert NLGenerationStatus.model_validate_json(stored).generation_id == "abc"

    storage.telemetry.clear_nl_insight_status()
    assert storage.telemetry.get_nl_insight_status() is None


async def test_nl_generation_status_defaults_to_idle(tmp_path: Path) -> None:
    runtime, _ = _make_runtime(tmp_path)
    status = runtime.nl_generation_status()
    assert status.status is NLGenerationState.IDLE
    assert status.generation_id is None


async def test_start_coalesces_same_range(tmp_path: Path) -> None:
    runtime, _ = _make_runtime(tmp_path)
    gate = _gated_run_oneshot(runtime, json.dumps({"prose": "x", "evidence": []}))
    rng, flt = _range(7, 0), TelemetryFilter()

    first = await runtime.start_nl_digest_generation(rng, flt)
    task = runtime._nl_generation_task
    assert first.started is True
    assert first.status.status is NLGenerationState.GENERATING

    second = await runtime.start_nl_digest_generation(rng, flt)
    assert second.started is False
    assert second.coalesced is True
    assert second.requested_range_differs is False
    assert second.status.generation_id == first.status.generation_id
    # No second run was launched — the same task still owns the flight.
    assert runtime._nl_generation_task is task

    gate.set()
    assert task is not None
    await task
    assert runtime.nl_generation_status().status is NLGenerationState.IDLE


async def test_start_rejects_divergent_range(tmp_path: Path) -> None:
    runtime, _ = _make_runtime(tmp_path)
    gate = _gated_run_oneshot(runtime, json.dumps({"prose": "x", "evidence": []}))
    active_rng = _range(7, 0)

    first = await runtime.start_nl_digest_generation(active_rng, TelemetryFilter())
    task = runtime._nl_generation_task

    divergent = await runtime.start_nl_digest_generation(
        _range(30, 0), TelemetryFilter()
    )
    assert divergent.started is False
    assert divergent.coalesced is False
    assert divergent.requested_range_differs is True
    # The marker exposes the ACTIVE run's range, not the divergent request's.
    assert divergent.status.generation_id == first.status.generation_id
    assert divergent.status.range == active_rng
    assert runtime._nl_generation_task is task

    gate.set()
    assert task is not None
    await task


async def test_start_coalesce_distinguishes_filters(tmp_path: Path) -> None:
    runtime, _ = _make_runtime(tmp_path)
    gate = _gated_run_oneshot(runtime, json.dumps({"prose": "x", "evidence": []}))
    rng = _range(7, 0)

    await runtime.start_nl_digest_generation(rng, TelemetryFilter(backends=["codex"]))
    # Same range, different filters → divergent, not coalesced.
    differs = await runtime.start_nl_digest_generation(
        rng, TelemetryFilter(backends=["claude_code"])
    )
    assert differs.requested_range_differs is True
    assert differs.coalesced is False

    gate.set()
    task = runtime._nl_generation_task
    assert task is not None
    await task


async def test_success_settles_idle_and_persists(tmp_path: Path) -> None:
    runtime, storage = _make_runtime(tmp_path)
    _stub_run_oneshot(
        runtime, json.dumps({"prose": "done", "evidence": [], "confidence": "low"})
    )

    await runtime.start_nl_digest_generation(_range(7, 0), TelemetryFilter())
    task = runtime._nl_generation_task
    assert task is not None
    await task

    stored = storage.telemetry.get_nl_insight()
    assert stored is not None
    assert NLInsight.model_validate_json(stored).prose == "done"
    assert runtime.nl_generation_status().status is NLGenerationState.IDLE
    assert runtime._nl_active is None


async def test_failure_settles_failed_and_keeps_prior_digest(tmp_path: Path) -> None:
    runtime, storage = _make_runtime(tmp_path)
    prior = NLInsight(
        prose="prior digest",
        evidence=[],
        range=_range(14, 7),
        filters=TelemetryFilter(),
        confidence="low",
        generated_at=datetime.now(UTC),
        source_backend="claude_code",
        disclaimer="d",
    )
    storage.telemetry.set_nl_insight(prior.model_dump_json())
    _stub_run_oneshot(runtime, None)

    await runtime.start_nl_digest_generation(_range(7, 0), TelemetryFilter())
    task = runtime._nl_generation_task
    assert task is not None
    await task

    status = runtime.nl_generation_status()
    assert status.status is NLGenerationState.FAILED
    assert status.error
    # Prior digest untouched.
    stored = storage.telemetry.get_nl_insight()
    assert stored is not None
    assert NLInsight.model_validate_json(stored).prose == "prior digest"


async def test_superseded_late_settle_is_noop(tmp_path: Path) -> None:
    runtime, storage = _make_runtime(tmp_path)
    gate = _gated_run_oneshot(runtime, json.dumps({"prose": "x", "evidence": []}))

    await runtime.start_nl_digest_generation(_range(7, 0), TelemetryFilter())
    task = runtime._nl_generation_task

    # Simulate a delete/supersede replacing the marker with a different id while
    # the run is still in flight.
    replacement = NLGenerationStatus(
        status=NLGenerationState.GENERATING,
        generation_id="other-run",
        requested_at=datetime.now(UTC),
    )
    storage.telemetry.set_nl_insight_status(replacement.model_dump_json())

    gate.set()
    assert task is not None
    await task

    # The finished (now superseded) task must not overwrite the replacement.
    status = runtime.nl_generation_status()
    assert status.generation_id == "other-run"
    assert status.status is NLGenerationState.GENERATING


async def test_reconcile_settles_stale_generating(tmp_path: Path) -> None:
    runtime, storage = _make_runtime(tmp_path)
    stale = NLGenerationStatus(
        status=NLGenerationState.GENERATING,
        generation_id="dead-run",
        requested_at=datetime.now(UTC) - timedelta(hours=2),
        range=_range(7, 0),
        filters=TelemetryFilter(),
    )
    storage.telemetry.set_nl_insight_status(stale.model_dump_json())

    runtime._reconcile_nl_status()

    status = runtime.nl_generation_status()
    assert status.status is NLGenerationState.FAILED
    assert status.error == "interrupted by restart"


async def test_reconcile_leaves_terminal_marker_untouched(tmp_path: Path) -> None:
    runtime, storage = _make_runtime(tmp_path)
    failed = NLGenerationStatus(
        status=NLGenerationState.FAILED, generation_id="x", error="boom"
    )
    storage.telemetry.set_nl_insight_status(failed.model_dump_json())

    runtime._reconcile_nl_status()

    status = runtime.nl_generation_status()
    assert status.status is NLGenerationState.FAILED
    assert status.error == "boom"


async def test_reconcile_noop_when_absent(tmp_path: Path) -> None:
    runtime, storage = _make_runtime(tmp_path)
    runtime._reconcile_nl_status()
    assert storage.telemetry.get_nl_insight_status() is None


async def test_cancel_clears_active_and_task(tmp_path: Path) -> None:
    runtime, _ = _make_runtime(tmp_path)
    _gated_run_oneshot(runtime, json.dumps({"prose": "x", "evidence": []}))

    await runtime.start_nl_digest_generation(_range(7, 0), TelemetryFilter())
    assert runtime._nl_active is not None

    await runtime.cancel_nl_generation()

    assert runtime._nl_generation_task is None
    assert runtime._nl_active is None


async def test_cancel_does_not_clobber_a_run_started_during_await(
    tmp_path: Path,
) -> None:
    # DELETE (cancel) and a POST (start) are not serialized: a POST can claim a
    # fresh run while cancel is parked on `await task`. The cancel's post-await
    # clears must be identity-guarded so they never orphan that new run.
    runtime, _ = _make_runtime(tmp_path)
    _gated_run_oneshot(runtime, json.dumps({"prose": "a", "evidence": []}))
    await runtime.start_nl_digest_generation(_range(7, 0), TelemetryFilter())
    task_a = runtime._nl_generation_task

    # Force a real yield window inside A's settle so a start can interleave while
    # cancel_nl_generation is parked awaiting the cancelled task.
    publish_gate = asyncio.Event()
    orig_publish = runtime._publish_nl_status
    gated_once = [False]

    async def gated_publish(status: NLGenerationStatus) -> None:
        if not gated_once[0]:
            gated_once[0] = True
            await publish_gate.wait()
        await orig_publish(status)

    runtime._publish_nl_status = gated_publish  # type: ignore[method-assign]

    cancel_task = asyncio.create_task(runtime.cancel_nl_generation())
    await asyncio.sleep(0.05)  # A unwinds to its finally, clears the flag, parks
    assert runtime._nl_active is None

    # A POST claims a fresh run in the cancel's await window.
    _gated_run_oneshot(runtime, json.dumps({"prose": "b", "evidence": []}))
    await runtime.start_nl_digest_generation(_range(30, 0), TelemetryFilter())
    task_b = runtime._nl_generation_task
    run_b = runtime._nl_active
    assert task_b is not task_a
    assert run_b is not None

    publish_gate.set()
    await cancel_task

    # The new run must survive the older run's cancellation.
    assert runtime._nl_generation_task is task_b
    assert runtime._nl_active is run_b


async def test_cadence_shares_single_flight_guard(tmp_path: Path) -> None:
    runtime, storage = _make_runtime(tmp_path)
    gate = _gated_run_oneshot(
        runtime, json.dumps({"prose": "cadence", "evidence": [], "confidence": "low"})
    )

    # A manual regenerate is in flight...
    manual = await runtime.start_nl_digest_generation(_range(7, 0), TelemetryFilter())
    task = runtime._nl_generation_task

    # ...the cadence tick shares the guard rather than starting a second run.
    # (It defaults to the 7d preset, which differs from this hand-built range, so
    # it exercises the divergent-reject branch — either way, no second run: the
    # single-flight property is that _nl_generation_task is unchanged.)
    cadence = asyncio.create_task(runtime.maybe_generate_nl_digest())
    await asyncio.sleep(0.05)  # let the cadence reach the launcher + await the run
    assert runtime._nl_generation_task is task

    gate.set()
    result = await cadence
    assert result is not None
    assert result.prose == "cadence"
    assert runtime.nl_generation_status().status is NLGenerationState.IDLE
    # The manual trigger's id was the single flight the cadence joined.
    stored = storage.telemetry.get_nl_insight()
    assert stored is not None
    assert manual.status.status is NLGenerationState.GENERATING
