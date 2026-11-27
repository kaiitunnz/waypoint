"""The pending-asks feed carries a claude_tty ask's call verification."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from waypoint.backends.events import InteractionEnvelope
from waypoint.pending_asks import _approval_ask, _plan_request_ask
from waypoint.schemas import EventKind, EventRecord


def _event(**metadata: Any) -> EventRecord:
    return EventRecord(
        session_id="s1",
        ts=datetime.now(UTC),
        kind=EventKind.APPROVAL_REQUEST,
        text="Approve Bash command:\nls",
        metadata={"tool_name": "Bash", "tool_input": {"command": "ls"}, **metadata},
        sequence=1,
    )


def _session() -> MagicMock:
    session = MagicMock()
    session.id = "s1"
    session.title = "t"
    session.backend = "claude_code"
    session.spawner_session_id = None
    return session


def _capabilities() -> MagicMock:
    capabilities = MagicMock()
    capabilities.approval_decisions = ["approve", "decline"]
    capabilities.supports_approval_note = False
    return capabilities


def _approval(**metadata: Any) -> Any:
    event = _event(**metadata)
    return _approval_ask(_session(), event, {}, "a1", _capabilities()).approval


def test_verification_is_copied_from_the_request() -> None:
    verified = _approval(call_verified=True, tool_use_id="toolu_1", subagent="web")
    assert (verified.call_verified, verified.tool_use_id, verified.subagent) == (
        True,
        "toolu_1",
        "web",
    )
    unverified = _approval(call_verified=False)
    assert (unverified.call_verified, unverified.tool_use_id) == (False, None)


def test_a_transport_that_names_its_calls_says_nothing() -> None:
    native = _approval(tool_use_id="toolu_1")
    assert (native.call_verified, native.tool_use_id, native.subagent) == (
        None,
        None,
        None,
    )


def test_plan_items_carry_verification() -> None:
    interaction = InteractionEnvelope(
        kind="plan_approval", request_id="a1", title="Approve plan", body="# Plan"
    ).to_metadata()
    ask = _plan_request_ask(
        _session(),
        _event(call_verified=True),
        interaction,
        "a1",
        "# Plan",
        _capabilities(),
    )
    assert (ask.plan.call_verified, ask.plan.tool_use_id) == (True, None)
