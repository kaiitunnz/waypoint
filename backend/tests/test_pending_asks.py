import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from waypoint import pending_asks
from waypoint.backends.events import PendingPlanApproval
from waypoint.pending_asks import TEXT_LIMIT
from waypoint.questions import QuestionDecline, QuestionLiveness
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    EventKind,
    EventRecord,
    PendingAsk,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.transports.base import TransportAdapter


class FakePlugin:
    """A question-capable plugin that can also report a plan."""

    question_parks_turn = True

    def __init__(self) -> None:
        self.liveness: dict[str, QuestionLiveness] = {}
        self.plan: PendingPlanApproval | None = None
        self.plan_modes: set[str | None] | None = None

    def question_liveness(
        self, runtime: Any, session: SessionRecord, tool_use_ids: list[str]
    ) -> dict[str, QuestionLiveness]:
        return {
            tid: self.liveness.get(tid, QuestionLiveness.ACTIONABLE)
            for tid in tool_use_ids
        }

    async def decline_question(
        self, runtime: Any, session: SessionRecord, tool_use_id: str
    ) -> QuestionDecline:
        return QuestionDecline.TURN_RESUMES

    def pending_plan_approval(
        self, runtime: Any, session: SessionRecord
    ) -> PendingPlanApproval | None:
        if self.plan_modes is not None and session.permission_mode not in (
            self.plan_modes
        ):
            return None
        return self.plan

    def affects_plan_approval(self, event: EventRecord) -> bool:
        return event.metadata.get("item_type") == "plan"


class PlainPlugin(FakePlugin):
    """A plugin without the plan protocol."""

    pending_plan_approval = None  # type: ignore[assignment]
    affects_plan_approval = None  # type: ignore[assignment]


class FakeTransport(TransportAdapter):
    is_structured = True

    def __init__(self) -> None:
        self.ids: tuple[str, ...] | None = ()
        self.pending = False

    async def send_input(self, session, text, attachments=None) -> None: ...

    async def interrupt(self, session) -> None: ...

    async def terminate(self, session) -> None: ...

    async def respond_to_approval(
        self, session, decision, text, approval_id=None
    ) -> bool:
        return True

    def has_pending_approval(self, session: SessionRecord) -> bool:
        return self.pending if self.ids is None else bool(self.ids)

    def pending_approval_ids(self, session: SessionRecord) -> tuple[str, ...] | None:
        return self.ids


class Env:
    def __init__(self, runtime: SessionRuntime, plugin: FakePlugin) -> None:
        self.runtime = runtime
        self.plugin = plugin
        self.transport = FakeTransport()
        self.queue = runtime.broadcast.subscribe_global()

    def published(self) -> list[dict[str, Any]]:
        out = []
        while not self.queue.empty():
            message = self.queue.get_nowait()
            if message["type"] == "pending_asks":
                out.append(message["payload"])
        return out


def add_session(
    runtime: SessionRuntime,
    session_id: str = "s1",
    *,
    status: SessionStatus = SessionStatus.WAITING_INPUT,
    source: SessionSource = SessionSource.MANAGED,
    title: str = "work",
    backend: str = "claude_code",
    transport: str = "claude_cli",
    permission_mode: str | None = None,
    spawner: str | None = None,
) -> None:
    session_dir = runtime.settings.sessions_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    runtime.storage.create_session(
        SessionRecord(
            id=session_id,
            backend=backend,
            source=source,
            transport=transport,
            title=title,
            cwd="/tmp",
            status=status,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(session_dir / "raw.log"),
            structured_log_path=str(session_dir / "events.jsonl"),
            permission_mode=permission_mode,
            spawner_session_id=spawner,
        )
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    runtime = SessionRuntime(settings, Storage(settings.database_path))
    plugin = FakePlugin()
    result = Env(runtime, plugin)
    monkeypatch.setattr(runtime.registry, "plugin_for", lambda session: plugin)
    monkeypatch.setattr(runtime, "transport_for", lambda session: result.transport)
    add_session(runtime)
    return result


QUESTIONS = [
    {
        "question": "Which database?",
        "header": "DB",
        "multiSelect": True,
        "options": [
            {"label": "Postgres (Recommended)", "description": "Relational"},
            {"label": "SQLite  (recommended) "},
            {"label": "Redis"},
        ],
    }
]


async def ask(
    runtime: SessionRuntime,
    tool_use_id: str,
    questions: Any = QUESTIONS,
    *,
    session_id: str = "s1",
    path: str = "payload",
) -> None:
    metadata: dict[str, Any] = {
        "tool_name": "AskUserQuestion",
        "tool_use_id": tool_use_id,
    }
    if path == "payload":
        metadata["payload"] = {"input": {"questions": questions}}
    else:
        metadata["tool_input"] = {"questions": questions}
    await runtime._emit_adapter_event(
        session_id,
        EventKind.TOOL_CALL,
        "AskUserQuestion",
        metadata,
        SessionStatus.WAITING_INPUT,
    )


async def approval(
    runtime: SessionRuntime,
    approval_id: str,
    *,
    session_id: str = "s1",
    text: str = "Approve Bash command:\nls",
    tool_name: str | None = "Bash",
    tool_input: dict[str, Any] | None = None,
    interaction: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    metadata: dict[str, Any] = {
        "approval_id": approval_id,
        "interaction": interaction
        or {
            "kind": "approval",
            "request_id": approval_id,
            "title": f"Approve {tool_name}",
        },
        **(extra or {}),
    }
    if tool_name is not None:
        metadata["tool_name"] = tool_name
        metadata["tool_input"] = (
            tool_input if tool_input is not None else {"command": "ls"}
        )
    await runtime._emit_adapter_event(
        session_id,
        EventKind.APPROVAL_REQUEST,
        text,
        metadata,
        SessionStatus.WAITING_INPUT,
    )


async def note(
    runtime: SessionRuntime,
    text: str,
    metadata: dict[str, Any] | None = None,
    *,
    session_id: str = "s1",
) -> None:
    await runtime._record_system_event(session_id, text, metadata=metadata)


async def start(env: Env) -> None:
    """Switch the feed on (first scan) and discard that snapshot's frame."""
    await env.runtime.asks.snapshot()
    env.published()


async def items(env: Env) -> list[PendingAsk]:
    await env.runtime.asks.flush(set())
    return (await env.runtime.asks.snapshot()).items


# ── Items ───────────────────────────────────────────────────────────────


async def test_question_item_carries_the_inline_answer_payload(env: Env) -> None:
    await ask(env.runtime, "q1")
    add_session(env.runtime, "child", spawner="s1", title="child work")

    snapshot = await env.runtime.asks.snapshot()

    [item] = snapshot.items
    assert item.id == "question:s1:q1"
    assert item.kind == "question"
    assert (item.session_id, item.session_title, item.session_backend) == (
        "s1",
        "work",
        "claude_code",
    )
    assert item.spawner_session_id is None
    assert item.availability == "actionable"
    assert item.title == "Which database?"
    assert item.sequence == 1
    assert item.question is not None
    assert item.question.tool_use_id == "q1"
    [prompt] = item.question.questions
    assert (prompt.question, prompt.header, prompt.multi_select) == (
        "Which database?",
        "DB",
        True,
    )
    assert [(o.label, o.description, o.recommended) for o in prompt.options] == [
        ("Postgres (Recommended)", "Relational", True),
        ("SQLite  (recommended) ", None, True),
        ("Redis", None, False),
    ]
    assert item.approval is None and item.plan is None


async def test_question_reads_tool_input_and_skips_unparseable_calls(env: Env) -> None:
    await ask(env.runtime, "from-tool-input", path="tool_input")
    await ask(env.runtime, "no-questions", questions=[])
    await ask(env.runtime, "bad-entries", questions=["x", {"options": []}])

    assert [i.id for i in await items(env)] == ["question:s1:from-tool-input"]


async def test_subagent_sessions_are_included_with_their_spawner(env: Env) -> None:
    add_session(env.runtime, "child", spawner="s1")
    await ask(env.runtime, "q1", session_id="child")

    [item] = (await env.runtime.asks.snapshot()).items

    assert (item.session_id, item.spawner_session_id) == ("child", "s1")


async def test_question_availability_passes_through_and_closed_is_hidden(
    env: Env,
) -> None:
    for tool_use_id in ("a", "b", "c"):
        await ask(env.runtime, tool_use_id)
    env.plugin.liveness = {"b": QuestionLiveness.STARTING, "c": QuestionLiveness.CLOSED}

    got = (await env.runtime.asks.snapshot()).items

    assert [(i.id, i.availability) for i in got] == [
        ("question:s1:a", "actionable"),
        ("question:s1:b", "starting"),
    ]


async def test_approval_item_carries_a_bounded_tool_call(env: Env) -> None:
    env.transport.ids = ("a1",)
    long = "x" * (TEXT_LIMIT + 500)
    await approval(
        env.runtime,
        "a1",
        text=f"Approve Bash command:\n{long}",
        tool_input={
            "command": long,
            "nested": {"items": [long, "short"]},
            "ok": "fine",
        },
        interaction={
            "kind": "approval",
            "request_id": "a1",
            "title": "Approve Bash",
            "body": f"Approve Bash command:\n{long}",
        },
    )

    [item] = (await env.runtime.asks.snapshot()).items

    assert item.kind == "approval" and item.availability == "actionable"
    assert item.title == "Approve Bash"
    assert item.approval is not None
    approval_payload = item.approval
    assert approval_payload.approval_id == "a1"
    assert approval_payload.tool_name == "Bash"
    assert approval_payload.tool_input_truncated is True
    tool_input = approval_payload.tool_input
    assert tool_input is not None
    assert tool_input["command"] == "x" * TEXT_LIMIT + "…"
    assert tool_input["nested"]["items"] == ["x" * TEXT_LIMIT + "…", "short"]
    assert tool_input["ok"] == "fine"
    assert len(approval_payload.summary) == TEXT_LIMIT + 1
    assert item.body is not None and len(item.body) == TEXT_LIMIT + 1
    assert approval_payload.decisions == ["approve", "decline"]
    assert approval_payload.supports_note is True


async def test_short_tool_input_is_not_flagged(env: Env) -> None:
    env.transport.ids = ("a1",)
    await approval(env.runtime, "a1")

    [item] = (await env.runtime.asks.snapshot()).items

    assert item.approval is not None
    assert item.approval.tool_input == {"command": "ls"}
    assert item.approval.tool_input_truncated is False


async def test_approval_without_a_tool_reads_the_nested_shape(env: Env) -> None:
    env.transport.ids = ("p1",)
    await env.runtime._emit_adapter_event(
        "s1",
        EventKind.APPROVAL_REQUEST,
        "Bash: bash (ls)",
        {
            "approval": {
                "approval_id": "p1",
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
            },
            "interaction": {
                "kind": "approval",
                "request_id": "p1",
                "title": "Bash: bash",
            },
        },
        SessionStatus.WAITING_INPUT,
    )

    [item] = (await env.runtime.asks.snapshot()).items

    assert item.approval is not None
    assert (item.approval.approval_id, item.approval.tool_name) == ("p1", "Bash")
    assert item.approval.tool_input == {"command": "ls"}


async def test_toolless_approval_uses_its_text(env: Env) -> None:
    env.transport.ids = ("c1",)
    await approval(
        env.runtime,
        "c1",
        text="Approve command: ls",
        tool_name=None,
        interaction={
            "kind": "approval",
            "request_id": "c1",
            "title": "Approve command: ls",
        },
        extra={"method": "item/commandExecution/requestApproval"},
    )

    [item] = (await env.runtime.asks.snapshot()).items

    assert item.title == "Approve command: ls"
    assert item.approval is not None
    assert item.approval.tool_name is None and item.approval.tool_input is None
    assert item.approval.summary == "Approve command: ls"


async def test_plan_approval_event_becomes_a_plan_item_with_whole_text(
    env: Env,
) -> None:
    env.transport.ids = ("plan-1",)
    plan = "# Plan\n" + "step\n" * 1500
    await approval(
        env.runtime,
        "plan-1",
        text="Approve plan and exit plan mode",
        tool_name="ExitPlanMode",
        tool_input={"plan": plan},
        interaction={
            "kind": "plan_approval",
            "request_id": "plan-1",
            "plan_item_id": "plan-1",
            "title": "Approve plan",
            "body": plan,
        },
    )

    [item] = (await env.runtime.asks.snapshot()).items

    assert item.kind == "plan_approval" and item.id == "plan_approval:s1:plan-1"
    assert item.body is None and item.approval is None
    assert item.plan is not None
    assert item.plan.text == plan and len(plan) > TEXT_LIMIT
    assert (item.plan.plan_id, item.plan.resolve_via) == ("plan-1", "approve")
    assert item.plan.decisions == ["approve", "decline"]


async def test_plan_approval_without_plan_text_degrades_to_an_approval(
    env: Env,
) -> None:
    env.transport.ids = ("plan-1",)
    await approval(
        env.runtime,
        "plan-1",
        tool_name="ExitPlanMode",
        tool_input={"plan": ""},
        interaction={
            "kind": "plan_approval",
            "request_id": "plan-1",
            "title": "Approve plan",
        },
    )

    [item] = (await env.runtime.asks.snapshot()).items

    assert item.kind == "approval" and item.plan is None
    assert item.approval is not None and item.approval.approval_id == "plan-1"


def plugin_plan(
    plan_id: str = "plan-a", availability: str = "actionable"
) -> PendingPlanApproval:
    return PendingPlanApproval(
        plan_id=plan_id,
        text="1. Do the thing",
        decisions=["accept", "decline"],
        supports_note=True,
        availability=availability,
        created_at=datetime.now(UTC),
        sequence=7,
    )


async def test_plugin_plan_item_resolves_through_approve_plan(env: Env) -> None:
    env.plugin.plan = plugin_plan(availability="starting")

    [item] = (await env.runtime.asks.snapshot()).items

    assert item.kind == "plan_approval" and item.id == "plan_approval:s1:plan-a"
    assert item.availability == "starting" and item.sequence == 7
    assert item.plan is not None
    assert (item.plan.plan_id, item.plan.resolve_via, item.plan.supports_note) == (
        "plan-a",
        "approve_plan",
        True,
    )
    assert item.plan.decisions == ["accept", "decline"]


async def test_plan_protocol_is_optional(env: Env, monkeypatch) -> None:
    plain = PlainPlugin()
    monkeypatch.setattr(env.runtime.registry, "plugin_for", lambda session: plain)
    plain.plan = plugin_plan()  # never asked: the plugin lacks the protocol

    assert (await env.runtime.asks.snapshot()).items == []


# ── Staleness ───────────────────────────────────────────────────────────


async def test_question_resolutions_drop_only_their_question(env: Env) -> None:
    for tool_use_id in ("answered", "cancelled", "closed", "paired", "open"):
        await ask(env.runtime, tool_use_id)
    await start(env)
    await env.runtime._record_user_event(
        "s1",
        "yes",
        submit=True,
        extra_metadata={"kind": "ask_user_question_answer", "tool_use_id": "answered"},
    )
    await note(
        env.runtime,
        "x",
        {"kind": "ask_user_question_cancelled", "tool_use_id": "cancelled"},
    )
    await note(
        env.runtime,
        "x",
        {"kind": "ask_user_question_closed", "tool_use_id": "closed"},
    )
    await env.runtime._emit_adapter_event(
        "s1",
        EventKind.TOOL_RESULT,
        "done",
        {"tool_use_id": "paired"},
        SessionStatus.RUNNING,
    )

    assert [i.id for i in await items(env)] == ["question:s1:open"]


async def test_approval_drops_on_resolution_notes(env: Env) -> None:
    env.transport.ids = ("answered", "invalidated", "timed-out", "open")
    for approval_id in ("answered", "invalidated", "timed-out", "open"):
        await approval(env.runtime, approval_id)
    await start(env)

    await note(
        env.runtime, "Approval response sent: approve", {"approval_id": "answered"}
    )
    await note(
        env.runtime,
        "expired",
        {"method": "approval.invalidated", "approval_id": "invalidated"},
    )
    await note(env.runtime, "Approval timed out", {"approval_id": "timed-out"})

    assert [i.id for i in await items(env)] == ["approval:s1:open"]


async def test_approval_resolves_by_nested_id(env: Env) -> None:
    env.transport.ids = ("p1",)
    await env.runtime._emit_adapter_event(
        "s1",
        EventKind.APPROVAL_REQUEST,
        "Bash: ls",
        {
            "approval": {"approval_id": "p1"},
            "interaction": {"kind": "approval", "request_id": "p1"},
        },
        SessionStatus.WAITING_INPUT,
    )
    await start(env)
    assert len(await items(env)) == 1

    env.transport.ids = ()
    await note(env.runtime, "Approval response sent: approve", {"approval_id": "p1"})

    assert await items(env) == []


async def test_dead_approval_events_are_not_listed_next_to_a_live_one(
    env: Env,
) -> None:
    await approval(env.runtime, "dead")
    await approval(env.runtime, "live")
    env.transport.ids = ("live",)

    assert [i.id for i in await items(env)] == ["approval:s1:live"]


async def test_transport_without_ids_falls_back_to_the_boolean(env: Env) -> None:
    await approval(env.runtime, "a1")
    env.transport.ids = None
    env.transport.pending = False
    await start(env)
    assert await items(env) == []

    env.transport.pending = True
    env.runtime.asks.mark_dirty("s1")

    assert [i.id for i in await items(env)] == ["approval:s1:a1"]


async def test_approvals_need_a_structured_transport(env: Env) -> None:
    env.transport.ids = ("a1",)
    env.transport.is_structured = False
    await approval(env.runtime, "a1")

    assert (await env.runtime.asks.snapshot()).items == []


async def test_telemetry_one_shots_are_excluded(env: Env) -> None:
    add_session(env.runtime, "oneshot", source=SessionSource.TELEMETRY)
    await ask(env.runtime, "q1", session_id="oneshot")

    assert (await env.runtime.asks.snapshot()).items == []


async def test_exited_session_items_drop_and_return_on_reattach(env: Env) -> None:
    await ask(env.runtime, "q1")
    await start(env)

    env.runtime.storage.update_session("s1", status=SessionStatus.EXITED)
    await env.runtime.broadcast_session_list()
    assert await items(env) == []

    # A reattach changes the status without any ask-relevant event.
    env.runtime.storage.update_session("s1", status=SessionStatus.IDLE)
    await env.runtime.asks.flush({"s1"})
    assert [i.id for i in env.runtime.asks._current] == ["question:s1:q1"]


async def test_terminate_event_drops_a_plan_only_session(env: Env) -> None:
    env.plugin.plan = plugin_plan()
    await start(env)
    assert len(env.runtime.asks._current) == 1

    await env.runtime._record_system_event(
        "s1", "Session terminated", status=SessionStatus.EXITED
    )
    # The broadcast loop reports the session whose state changed.
    await env.runtime.asks.flush({"s1"})

    assert env.runtime.asks._current == []
    assert env.published()[-1]["items"] == []


async def test_deleted_session_items_drop_and_are_published(env: Env) -> None:
    await ask(env.runtime, "q1")
    await start(env)

    env.runtime.asks.forget("s1")
    env.runtime.storage.delete_session("s1")
    await env.runtime.asks.flush(set())

    [payload] = env.published()
    assert payload["items"] == []


async def test_error_session_keeps_questions_but_drops_approvals(env: Env) -> None:
    env.transport.ids = ("a1",)
    await ask(env.runtime, "q-unreachable")
    await ask(env.runtime, "q-closed")
    await approval(env.runtime, "a1")
    env.plugin.plan = plugin_plan()
    env.plugin.liveness = {
        "q-unreachable": QuestionLiveness.UNAVAILABLE,
        "q-closed": QuestionLiveness.CLOSED,
    }
    env.runtime.storage.update_session("s1", status=SessionStatus.ERROR)

    got = (await env.runtime.asks.snapshot()).items

    assert [(i.id, i.availability) for i in got] == [
        ("question:s1:q-unreachable", "unavailable")
    ]


# ── Ordering and revisions ──────────────────────────────────────────────


async def test_items_order_oldest_first_with_stable_tie_breaks(env: Env) -> None:
    add_session(env.runtime, "s0")
    stamp = datetime.now(UTC)
    for session_id, sequence, offset in (
        ("s1", 1, 5),
        ("s0", 2, 5),
        ("s1", 3, 0),
        ("s0", 1, 5),
    ):
        env.runtime.storage.append_event(
            EventRecord(
                session_id=session_id,
                ts=stamp + timedelta(seconds=offset),
                kind=EventKind.TOOL_CALL,
                text="AskUserQuestion",
                metadata={
                    "tool_name": "AskUserQuestion",
                    "tool_use_id": f"{session_id}-{sequence}",
                    "payload": {"input": {"questions": QUESTIONS}},
                },
                sequence=sequence,
            )
        )

    got = [i.id for i in (await env.runtime.asks.snapshot()).items]

    assert got == [
        "question:s1:s1-3",
        "question:s0:s0-1",
        "question:s0:s0-2",
        "question:s1:s1-1",
    ]


async def test_revision_bumps_only_when_the_list_changes(env: Env) -> None:
    first = await env.runtime.asks.snapshot()
    again = await env.runtime.asks.snapshot()
    assert (again.epoch, again.revision) == (first.epoch, first.revision)

    await ask(env.runtime, "q1")
    after = await env.runtime.asks.snapshot()
    assert after.epoch == first.epoch and after.revision == first.revision + 1

    await env.runtime.asks.flush({"s1"})
    assert (await env.runtime.asks.snapshot()).revision == after.revision


# ── Publication ─────────────────────────────────────────────────────────


async def test_flush_publishes_a_snapshot_only_when_it_changed(env: Env) -> None:
    await start(env)

    await ask(env.runtime, "q1")
    await env.runtime.asks.flush({"s1"})
    [payload] = env.published()
    assert [i["id"] for i in payload["items"]] == ["question:s1:q1"]
    assert payload["epoch"] == env.runtime.asks.epoch

    await env.runtime.asks.flush({"s1"})
    assert env.published() == []


async def test_a_snapshot_read_does_not_starve_the_publisher(env: Env) -> None:
    await start(env)
    await ask(env.runtime, "q1")

    # A GET (or a ws connect) consumes the dirty set before the tick runs.
    assert len((await env.runtime.asks.snapshot()).items) == 1
    await env.runtime.asks.flush(set())

    [payload] = env.published()
    assert len(payload["items"]) == 1


async def test_flush_is_inert_until_something_reads_the_feed(env: Env) -> None:
    await ask(env.runtime, "q1")

    await env.runtime.asks.flush({"s1"})

    assert env.published() == []
    assert env.runtime.asks._entries == {}


async def test_title_change_is_published(env: Env) -> None:
    await ask(env.runtime, "q1")
    await start(env)

    env.runtime.storage.update_session("s1", title="renamed")
    await env.runtime.broadcast_session_list()
    await env.runtime.asks.flush(set())

    [payload] = env.published()
    assert payload["items"][0]["session_title"] == "renamed"


async def test_a_failing_session_does_not_hide_the_others(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_session(env.runtime, "bad")
    await ask(env.runtime, "q1")
    await ask(env.runtime, "q2", session_id="bad")
    healthy = env.plugin

    def plugin_for(session: SessionRecord) -> FakePlugin:
        if session.id == "bad":
            raise KeyError("legacy row")
        return healthy

    monkeypatch.setattr(env.runtime.registry, "plugin_for", plugin_for)

    assert [i.id for i in (await env.runtime.asks.snapshot()).items] == [
        "question:s1:q1"
    ]


async def test_gates_run_before_any_event_query(env: Env) -> None:
    await ask(env.runtime, "q1")
    await approval(env.runtime, "a1")
    spy = MagicMock(wraps=env.runtime.storage.list_approval_events)
    env.runtime.storage.list_approval_events = spy  # type: ignore[method-assign]

    # A question-only session: the transport reports no live approvals.
    env.transport.ids = ()
    await env.runtime.asks.snapshot()
    assert spy.call_count == 0

    env.transport.ids = ("a1",)
    env.runtime.asks.mark_dirty("s1")
    await items(env)
    assert spy.call_count == 1


async def test_open_approvals_are_cached_between_ask_events(env: Env) -> None:
    env.transport.ids = ("a1",)
    await approval(env.runtime, "a1")
    await start(env)
    spy = MagicMock(wraps=env.runtime.storage.list_approval_events)
    env.runtime.storage.list_approval_events = spy  # type: ignore[method-assign]

    # State churn and explicit re-checks reuse the cached open events.
    env.runtime.asks.mark_dirty("s1")
    await items(env)
    env.runtime.asks.mark_watched_dirty()
    await items(env)
    assert spy.call_count == 0

    await approval(env.runtime, "a2")
    env.transport.ids = ("a1", "a2")
    await items(env)
    assert spy.call_count == 1


async def test_idle_sessions_are_not_rederived_under_state_churn(env: Env) -> None:
    add_session(env.runtime, "idle", status=SessionStatus.IDLE)
    await start(env)
    spy = MagicMock(wraps=env.runtime.questions.open_questions)
    env.runtime.questions.open_questions = spy  # type: ignore[method-assign]

    for _ in range(3):
        await env.runtime.asks.flush({"idle"})

    assert spy.call_count == 0


async def test_reading_the_feed_leaves_the_question_tracker_alone(env: Env) -> None:
    await ask(env.runtime, "q1")
    events_before = list(env.runtime.storage.list_events("s1"))
    dirty_before = set(env.runtime.questions._dirty)

    await env.runtime.asks.snapshot()

    assert env.runtime.questions._open_ids == {}
    assert env.runtime.questions._dirty == dirty_before
    assert env.runtime.storage.list_events("s1") == events_before


async def test_mode_change_adds_and_drops_a_plan(env: Env) -> None:
    env.plugin.plan = plugin_plan()
    env.plugin.plan_modes = {"plan"}
    await start(env)
    assert env.runtime.asks._current == []

    env.runtime.storage.update_session("s1", permission_mode="plan")
    env.runtime.asks.mark_dirty("s1")
    assert [i.id for i in await items(env)] == ["plan_approval:s1:plan-a"]

    env.runtime.storage.update_session("s1", permission_mode="default")
    env.runtime.asks.mark_dirty("s1")
    assert await items(env) == []


async def test_a_mode_change_without_a_hook_still_rederives_on_state_churn(
    env: Env,
) -> None:
    env.plugin.plan = plugin_plan()
    env.plugin.plan_modes = {"plan"}
    await start(env)

    env.runtime.storage.update_session("s1", permission_mode="plan")
    await env.runtime.asks.flush({"s1"})

    assert [i.id for i in env.runtime.asks._current] == ["plan_approval:s1:plan-a"]


async def test_plan_item_events_refresh_a_plugin_plan(env: Env) -> None:
    await start(env)
    env.plugin.plan = plugin_plan()
    await env.runtime._emit_adapter_event(
        "s1",
        EventKind.SYSTEM_NOTE,
        "Completed plan",
        {"item_type": "plan"},
        SessionStatus.IDLE,
    )

    assert [i.id for i in await items(env)] == ["plan_approval:s1:plan-a"]


async def test_silent_dialog_adoption_surfaces_through_mark_dirty(env: Env) -> None:
    await approval(env.runtime, "a1")
    await start(env)
    assert env.runtime.asks._current == []

    env.transport.ids = ("a1",)
    env.runtime.asks.mark_dirty("s1")

    assert [i.id for i in await items(env)] == ["approval:s1:a1"]


# ── First scan ──────────────────────────────────────────────────────────


async def test_a_cancelled_first_read_does_not_leave_a_partial_feed(env: Env) -> None:
    for session_id in ("a", "b", "c"):
        add_session(env.runtime, session_id)
        await ask(env.runtime, f"q-{session_id}", session_id=session_id)
    await ask(env.runtime, "q1")

    reader = asyncio.create_task(env.runtime.asks.snapshot())
    await asyncio.sleep(0)
    reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader
    assert not env.runtime.asks._scanned

    # A tick mid-scan folds the change in and publishes nothing partial.
    await env.runtime.asks.flush({"s1"})
    assert env.published() == []

    snapshot = await env.runtime.asks.snapshot()
    assert sorted(i.session_id for i in snapshot.items) == ["a", "b", "c", "s1"]


async def test_state_changes_during_the_scan_are_applied_afterwards(env: Env) -> None:
    add_session(env.runtime, "a")
    await ask(env.runtime, "q-a", session_id="a")

    reader = asyncio.create_task(env.runtime.asks.snapshot())
    await asyncio.sleep(0)
    env.runtime.storage.update_session("a", status=SessionStatus.EXITED)
    await env.runtime.asks.flush({"a"})
    snapshot = await reader

    assert all(i.session_id != "a" for i in snapshot.items)


def test_the_feed_is_backend_neutral() -> None:
    source = Path(pending_asks.__file__).read_text().lower()
    for literal in ("claude", "codex", "opencode", "tmux", "exitplanmode"):
        assert literal not in source
    # Plugin-private event keys stay behind ``PlanApprovalLifecycle``.
    for key in ("item_type", "plan_decision"):
        assert key not in source
