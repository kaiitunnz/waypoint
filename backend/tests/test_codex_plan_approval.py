"""Codex's plan approval lifecycle: the pending plan the feed lists and the
``approve_plan`` check share one derivation."""

import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from waypoint.backends.base import PlanApprovalLifecycle
from waypoint.backends.codex.plugin import (
    _PLAN_DECISIONS,
    CodexPlugin,
    _current_plan_text_for_item,
    _plan_id_for_event,
    _plan_text_for_event,
)
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    EventKind,
    EventRecord,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage


@pytest.fixture
def runtime(tmp_path: Path) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    return SessionRuntime(settings, Storage(settings.database_path))


@pytest.fixture
def plugin(runtime: SessionRuntime) -> CodexPlugin:
    plugin = runtime.registry.get("codex")
    assert isinstance(plugin, CodexPlugin)
    return plugin


def make_session(
    runtime: SessionRuntime,
    *,
    mode: str | None = "plan",
    status: SessionStatus = SessionStatus.IDLE,
    thread_id: str | None = "thread-1",
) -> SessionRecord:
    now = datetime.now(UTC)
    session = SessionRecord(
        id="sess",
        backend="codex",
        source=SessionSource.MANAGED,
        transport="codex_app_server",
        title="plan",
        cwd="/tmp",
        status=status,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
        permission_mode=mode,
        transport_state={"thread_id": thread_id} if thread_id else {},
    )
    return runtime.storage.create_session(session)


def seed_plan(
    runtime: SessionRuntime,
    plan_id: str | None = "plan-1",
    text: str = "1. Do it",
    *,
    thread_id: str | None = None,
    decisions: Any = None,
    envelope: bool = True,
) -> EventRecord:
    metadata: dict[str, Any] = {"item_type": "plan", "payload": {}}
    if plan_id:
        metadata["item_id"] = plan_id
    if envelope:
        metadata["plan"] = {
            "id": plan_id,
            "text": text,
            "source": "codex",
            **({"decisions": decisions} if decisions is not None else {}),
        }
    else:
        metadata["payload"]["item"] = {"id": plan_id, "type": "plan", "text": text}
    if thread_id:
        metadata["payload"]["threadId"] = thread_id
    return runtime.storage.append_event(
        EventRecord(
            session_id="sess",
            ts=datetime.now(UTC),
            kind=EventKind.SYSTEM_NOTE,
            text="plan",
            metadata=metadata,
            sequence=runtime.storage.next_sequence("sess"),
        )
    )


def decide(runtime: SessionRuntime, plan_id: str, decision: str = "decline") -> None:
    runtime.storage.append_event(
        EventRecord(
            session_id="sess",
            ts=datetime.now(UTC),
            kind=EventKind.SYSTEM_NOTE,
            text="decided",
            metadata={"plan_item_id": plan_id, "plan_decision": decision},
            sequence=runtime.storage.next_sequence("sess"),
        )
    )


def pending(runtime: SessionRuntime, plugin: CodexPlugin):
    return plugin.pending_plan_approval(runtime, runtime.get_session("sess"))


def test_codex_implements_the_plan_protocol(plugin: CodexPlugin) -> None:
    assert isinstance(plugin, PlanApprovalLifecycle)


def test_pending_plan_carries_what_an_inline_answer_needs(
    runtime: SessionRuntime, plugin: CodexPlugin
) -> None:
    make_session(runtime)
    event = seed_plan(runtime, text="1. Do it\n2. Test it")

    plan = pending(runtime, plugin)

    assert plan is not None
    assert (plan.plan_id, plan.text) == ("plan-1", "1. Do it\n2. Test it")
    assert plan.decisions == ["accept", "acceptForSession", "decline", "cancel"]
    assert plan.supports_note is True
    assert plan.availability == "actionable"
    assert (plan.created_at, plan.sequence) == (event.ts, event.sequence)


def test_plan_from_a_history_import_reads_the_item_payload(
    runtime: SessionRuntime, plugin: CodexPlugin
) -> None:
    make_session(runtime)
    seed_plan(runtime, "plan-h", "imported plan", envelope=False)

    plan = pending(runtime, plugin)

    assert plan is not None and (plan.plan_id, plan.text) == ("plan-h", "imported plan")


def test_only_plan_mode_sessions_have_a_pending_plan_and_skip_the_event_query(
    runtime: SessionRuntime, plugin: CodexPlugin
) -> None:
    make_session(runtime, mode="default")
    seed_plan(runtime)
    spy = MagicMock(wraps=runtime.storage.plan_item_events)
    runtime.storage.plan_item_events = spy  # type: ignore[method-assign]

    assert pending(runtime, plugin) is None
    assert spy.call_count == 0


def test_starting_session_reports_starting(
    runtime: SessionRuntime, plugin: CodexPlugin
) -> None:
    make_session(runtime, status=SessionStatus.STARTING)
    seed_plan(runtime)

    plan = pending(runtime, plugin)

    assert plan is not None and plan.availability == "starting"


def test_a_decision_after_the_plan_resolves_it_and_others_do_not(
    runtime: SessionRuntime, plugin: CodexPlugin
) -> None:
    make_session(runtime)
    seed_plan(runtime, "plan-1")
    decide(runtime, "other-plan")
    assert pending(runtime, plugin) is not None

    decide(runtime, "plan-1", "accept")

    assert pending(runtime, plugin) is None


def test_a_decision_before_a_replanned_item_does_not_resolve_it(
    runtime: SessionRuntime, plugin: CodexPlugin
) -> None:
    make_session(runtime)
    seed_plan(runtime, "plan-1")
    decide(runtime, "plan-1")
    seed_plan(runtime, "plan-1", "revised plan")

    plan = pending(runtime, plugin)

    assert plan is not None and plan.text == "revised plan"


def test_the_newest_plan_item_supersedes_older_ones(
    runtime: SessionRuntime, plugin: CodexPlugin
) -> None:
    make_session(runtime)
    seed_plan(runtime, "plan-1", "old")
    seed_plan(runtime, "plan-2", "new")

    plan = pending(runtime, plugin)

    assert plan is not None and plan.plan_id == "plan-2"
    assert _current_plan_text_for_item(runtime, "sess", "plan-1") == ""
    assert _current_plan_text_for_item(runtime, "sess", "plan-2") == "new"


def test_a_newer_plan_still_streaming_hides_an_older_undecided_one(
    runtime: SessionRuntime, plugin: CodexPlugin
) -> None:
    make_session(runtime)
    seed_plan(runtime, "plan-1", "old")
    seed_plan(runtime, "plan-2", "")

    assert pending(runtime, plugin) is None
    assert _current_plan_text_for_item(runtime, "sess", "plan-1") == ""
    assert _current_plan_text_for_item(runtime, "sess", "plan-2") == ""


def test_an_empty_update_falls_back_to_the_same_plans_earlier_text(
    runtime: SessionRuntime, plugin: CodexPlugin
) -> None:
    make_session(runtime)
    seed_plan(runtime, "plan-1", "complete text")
    seed_plan(runtime, "plan-1", "")

    plan = pending(runtime, plugin)

    assert plan is not None and plan.text == "complete text"


def test_a_newest_plan_item_without_an_id_leaves_no_current_plan(
    runtime: SessionRuntime, plugin: CodexPlugin
) -> None:
    make_session(runtime)
    seed_plan(runtime, "plan-1")
    seed_plan(runtime, None, "no id", envelope=False)

    assert pending(runtime, plugin) is None


def test_a_forks_copy_of_another_threads_plan_is_not_pending(
    runtime: SessionRuntime, plugin: CodexPlugin
) -> None:
    make_session(runtime, thread_id="fork-thread")
    seed_plan(runtime, thread_id="parent-thread")

    assert pending(runtime, plugin) is None
    # The approve path keeps accepting it: only the feed is stricter.
    assert _current_plan_text_for_item(runtime, "sess", "plan-1") == "1. Do it"


def test_a_plan_on_this_thread_or_without_a_thread_is_pending(
    runtime: SessionRuntime, plugin: CodexPlugin
) -> None:
    make_session(runtime, thread_id="thread-1")
    seed_plan(runtime, "plan-1", thread_id="thread-1")
    assert pending(runtime, plugin) is not None

    seed_plan(runtime, "plan-2")
    plan = pending(runtime, plugin)
    assert plan is not None and plan.plan_id == "plan-2"


@pytest.mark.parametrize(
    ("offered", "expected"),
    [
        (["cancel", "accept", "bogus"], ["accept", "cancel"]),
        (["bogus"], ["accept", "acceptForSession", "decline", "cancel"]),
        ("not-a-list", ["accept", "acceptForSession", "decline", "cancel"]),
        (None, ["accept", "acceptForSession", "decline", "cancel"]),
    ],
)
def test_decisions_are_the_offered_ones_in_canonical_order(
    runtime: SessionRuntime, plugin: CodexPlugin, offered: Any, expected: list[str]
) -> None:
    make_session(runtime)
    seed_plan(runtime, decisions=offered)

    plan = pending(runtime, plugin)

    assert plan is not None and plan.decisions == expected


def test_affects_plan_approval_recognizes_plan_items_and_decisions(
    plugin: CodexPlugin,
) -> None:
    def event(metadata: dict[str, Any]) -> EventRecord:
        return EventRecord(
            session_id="sess",
            ts=datetime.now(UTC),
            kind=EventKind.SYSTEM_NOTE,
            text="",
            metadata=metadata,
            sequence=1,
        )

    assert plugin.affects_plan_approval(event({"item_type": "plan"}))
    assert plugin.affects_plan_approval(
        event({"plan_item_id": "p", "plan_decision": "accept"})
    )
    assert not plugin.affects_plan_approval(event({"item_type": "agentMessage"}))
    assert not plugin.affects_plan_approval(event({}))


def test_storage_plan_queries_are_session_scoped_and_ordered(
    runtime: SessionRuntime,
) -> None:
    make_session(runtime)
    first = seed_plan(runtime, "plan-1")
    second = seed_plan(runtime, "plan-2")
    decide(runtime, "plan-1")

    assert [e.sequence for e in runtime.storage.plan_item_events("sess")] == [
        second.sequence,
        first.sequence,
    ]
    assert len(runtime.storage.plan_item_events("sess", limit=1)) == 1
    assert runtime.storage.plan_item_events("elsewhere") == []
    decisions = runtime.storage.plan_decision_events("sess", first.sequence)
    assert [e.metadata["plan_item_id"] for e in decisions] == ["plan-1"]
    assert runtime.storage.plan_decision_events("sess", second.sequence + 1) == []


def _full_scan_plan_text(runtime: SessionRuntime, plan_item_id: str) -> str:
    """Reference implementation: walk every event newest first."""
    decided: set[str] = set()
    for event in reversed(runtime.storage.list_events("sess")):
        if event.metadata.get("item_type") != "plan":
            decision_plan_id = event.metadata.get("plan_item_id")
            if (
                isinstance(decision_plan_id, str)
                and event.metadata.get("plan_decision") in _PLAN_DECISIONS
            ):
                decided.add(decision_plan_id)
            continue
        current_plan_id = _plan_id_for_event(event)
        if not current_plan_id or current_plan_id in decided:
            return ""
        if current_plan_id != plan_item_id:
            return ""
        text = _plan_text_for_event(event)
        if text:
            return text
    return ""


@pytest.mark.parametrize("seed", range(40))
def test_plan_lookup_matches_a_full_scan_of_the_events(
    runtime: SessionRuntime, seed: int
) -> None:
    rng = random.Random(seed)
    make_session(runtime)
    for _ in range(rng.randint(1, 8)):
        plan_id = rng.choice(["a", "b", "c", None])
        roll = rng.random()
        if roll < 0.55:
            seed_plan(
                runtime,
                plan_id,
                rng.choice(["", "text-1", "text-2"]),
                envelope=rng.random() < 0.5,
            )
        elif roll < 0.9:
            decide(runtime, plan_id or "a", rng.choice(["accept", "decline", "bogus"]))
        else:
            runtime.storage.append_event(
                EventRecord(
                    session_id="sess",
                    ts=datetime.now(UTC),
                    kind=EventKind.AGENT_OUTPUT,
                    text="chatter",
                    metadata={},
                    sequence=runtime.storage.next_sequence("sess"),
                )
            )

    for plan_item_id in ("a", "b", "c", "missing"):
        assert _current_plan_text_for_item(
            runtime, "sess", plan_item_id
        ) == _full_scan_plan_text(runtime, plan_item_id)
