"""Unit tests for the ``inbox wait`` engine and its parsers/exit codes."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import typer

from waypoint.cli import (
    INBOX_GONE_EXIT_CODE,
    WAIT_TIMEOUT_EXIT_CODE,
    _inbox_condition_met,
    _wait_for_inbox,
    inbox_wait_exit_code,
    parse_inbox_wait_until,
)
from waypoint.client import WaypointError


class _FakeClient:
    """Stands in for WaypointClient in the wait engine.

    ``frames`` drives ``stream_inbox_envelopes`` (empty list → WS returns
    nothing so the engine falls back to polling). ``poll_items`` is consumed
    in order by ``get_inbox``; ``get_error`` makes ``get_inbox`` raise.
    """

    def __init__(
        self,
        *,
        frames: list[dict[str, Any]] | None = None,
        poll_items: list[dict[str, Any]] | None = None,
        get_error: WaypointError | None = None,
    ) -> None:
        self._frames = frames if frames is not None else []
        self._poll_items = poll_items or []
        self._get_error = get_error

    async def stream_inbox_envelopes(
        self, item_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        for frame in self._frames:
            yield frame

    def get_inbox(self, item_id: str) -> dict[str, Any]:
        if self._get_error is not None:
            raise self._get_error
        if self._poll_items:
            return self._poll_items.pop(0)
        return {"id": item_id, "version": 0, "status": "open"}


def _frame(item: dict[str, Any] | None, *, deleted: bool = False) -> dict[str, Any]:
    return {
        "type": "inbox_update",
        "payload": {"deleted": deleted, "item": item},
    }


def test_parse_until_defaults_to_resolved() -> None:
    assert parse_inbox_wait_until(None) == frozenset({"resolved"})


def test_parse_until_accepts_valid() -> None:
    assert parse_inbox_wait_until("resolved,update") == frozenset(
        {"resolved", "update"}
    )


def test_parse_until_rejects_unknown() -> None:
    with pytest.raises(typer.BadParameter):
        parse_inbox_wait_until("done")


def test_exit_codes() -> None:
    assert inbox_wait_exit_code("resolved") == 0
    assert inbox_wait_exit_code("update") == 0
    assert inbox_wait_exit_code("timeout") == WAIT_TIMEOUT_EXIT_CODE
    assert inbox_wait_exit_code("gone") == INBOX_GONE_EXIT_CODE


def test_condition_met_resolved() -> None:
    item = {"status": "resolved", "version": 3}
    assert _inbox_condition_met(item, frozenset({"resolved"}), 0) == "resolved"


def test_condition_met_update_past_baseline() -> None:
    item = {"status": "open", "version": 4}
    assert _inbox_condition_met(item, frozenset({"update"}), 3) == "update"


def test_condition_not_met_at_baseline() -> None:
    item = {"status": "open", "version": 3}
    assert _inbox_condition_met(item, frozenset({"update"}), 3) is None


async def test_ws_resolves_on_hydration_snapshot() -> None:
    client = _FakeClient(frames=[_frame({"version": 0, "status": "resolved"})])
    result = await _wait_for_inbox(
        client, "i1", frozenset({"resolved"}), None, timeout=1.0
    )
    assert result.outcome == "resolved"
    assert result.item is not None


async def test_ws_gone_on_deleted_frame() -> None:
    client = _FakeClient(frames=[_frame(None, deleted=True)])
    result = await _wait_for_inbox(
        client, "i1", frozenset({"resolved"}), None, timeout=1.0
    )
    assert result.outcome == "gone"
    assert result.item is None


async def test_ws_update_uses_connect_baseline() -> None:
    # Hydration frame at v2 sets the baseline; the next change at v3 fires update.
    client = _FakeClient(
        frames=[
            _frame({"version": 2, "status": "open"}),
            _frame({"version": 3, "status": "open"}),
        ]
    )
    result = await _wait_for_inbox(
        client, "i1", frozenset({"update"}), None, timeout=1.0
    )
    assert result.outcome == "update"
    assert result.item is not None
    assert result.item["version"] == 3


async def test_update_since_already_exceeded_short_circuits() -> None:
    # Explicit --since below the current version returns immediately.
    client = _FakeClient(frames=[_frame({"version": 5, "status": "open"})])
    result = await _wait_for_inbox(client, "i1", frozenset({"update"}), 3, timeout=1.0)
    assert result.outcome == "update"


async def test_poll_fallback_detects_gone() -> None:
    # Empty WS frames → fall back to polling; a 404 there means gone.
    client = _FakeClient(frames=[], get_error=WaypointError("nope", status_code=404))
    result = await _wait_for_inbox(
        client, "i1", frozenset({"resolved"}), None, timeout=1.0
    )
    assert result.outcome == "gone"


async def test_poll_fallback_resolves() -> None:
    client = _FakeClient(frames=[], poll_items=[{"version": 1, "status": "resolved"}])
    result = await _wait_for_inbox(
        client, "i1", frozenset({"resolved"}), None, timeout=1.0
    )
    assert result.outcome == "resolved"


async def test_timeout_returns_timeout_outcome() -> None:
    # WS yields nothing and poll never satisfies; a tiny timeout fires.
    client = _FakeClient(frames=[], poll_items=[])
    result = await _wait_for_inbox(
        client, "i1", frozenset({"resolved"}), None, timeout=0.05
    )
    assert result.outcome == "timeout"


def test_wait_engine_is_awaitable_via_asyncio_run() -> None:
    client = _FakeClient(frames=[_frame({"version": 0, "status": "resolved"})])
    result = asyncio.run(
        _wait_for_inbox(client, "i1", frozenset({"resolved"}), None, 1.0)
    )
    assert result.outcome == "resolved"
