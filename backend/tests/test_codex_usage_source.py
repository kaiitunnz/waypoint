"""Tests for the Codex rollout (tmux) context-usage source.

The rollout token_count event has no stable per-turn identity, so the source
must NOT feed the durable ledger; it discloses partial coverage instead while
the context-window snapshot keeps working.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from waypoint.backends.codex.usage_source import CodexRolloutUsageSource


def _session(**overrides: object) -> SimpleNamespace:
    base = {
        "id": "sess-1",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "session_token_usage": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _source(runtime: MagicMock) -> CodexRolloutUsageSource:
    return CodexRolloutUsageSource("sess-1", runtime)


@pytest.mark.asyncio
async def test_partial_coverage_published_once(tmp_path: Path) -> None:
    runtime = MagicMock()
    runtime.update_session_fields = AsyncMock()
    runtime.storage.get_session.return_value = _session()
    source = _source(runtime)

    await source._maybe_publish_partial_coverage()
    await source._maybe_publish_partial_coverage()

    runtime.update_session_fields.assert_called_once()
    aggregate = runtime.update_session_fields.call_args.kwargs["session_token_usage"]
    assert aggregate.coverage == "partial"
    assert aggregate.tracked_turns == 0
    assert aggregate.totals == {}
    assert aggregate.coverage_note is not None
    assert aggregate.observed_from == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_partial_coverage_skipped_when_aggregate_exists() -> None:
    # A thread previously driven over the structured transport already has a
    # real aggregate; the rollout source must not clobber it with partial.
    runtime = MagicMock()
    runtime.update_session_fields = AsyncMock()
    runtime.storage.get_session.return_value = _session(session_token_usage=object())
    source = _source(runtime)

    await source._maybe_publish_partial_coverage()

    runtime.update_session_fields.assert_not_called()
