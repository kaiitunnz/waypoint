import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from waypoint.api import create_app
from waypoint.runtime import WAKE_INPUT_TEXT, PreparedInput, SessionRuntime
from waypoint.schemas import (
    HeldMessageOrigin,
    HeldMessageRecord,
    HeldReason,
    IdleMessageBatchMode,
    ScheduledMessageCreateRequest,
    ScheduledMessageStatus,
    ScheduledMessageTrigger,
    SessionInputRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.transports import InputBlockedError


def make_runtime(tmp_path: Path) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    return SessionRuntime(settings, Storage(settings.database_path))


def make_session(
    settings: Settings, session_id: str, focus: bool = False
) -> SessionRecord:
    session_dir = settings.sessions_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    return SessionRecord(
        id=session_id,
        backend="codex",
        source=SessionSource.MANAGED,
        transport="codex_app_server",
        title="Focused",
        cwd="/tmp/project",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        focus=focus,
        raw_log_path=str(session_dir / "raw.log"),
        structured_log_path=str(session_dir / "events.jsonl"),
    )


def focused_runtime(tmp_path: Path) -> SessionRuntime:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "s1"))
    runtime.set_focus("s1", True)
    return runtime


def record_dispatches(runtime: SessionRuntime, monkeypatch) -> list[str]:
    sent: list[str] = []

    async def fake_dispatch(prepared: PreparedInput) -> SessionRecord:
        sent.append(prepared.request.text)
        return prepared.session

    monkeypatch.setattr(runtime, "dispatch_input", fake_dispatch)
    return sent


def agent_send(text: str, **kwargs: Any) -> SessionInputRequest:
    return SessionInputRequest(text=text, sender_session_id="peer", **kwargs)


def upload(runtime: SessionRuntime, session_id: str) -> str:
    spec = runtime.attachments.save(
        session_id, data=b"hi", filename="note.txt", content_type="text/plain"
    )
    return spec.id


async def test_unfocused_session_delivers(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "s1"))
    sent = record_dispatches(runtime, monkeypatch)

    result = await runtime.held.deliver("s1", agent_send("hi"), HeldMessageOrigin.AGENT)

    assert isinstance(result, SessionRecord)
    assert sent == ["hi"]
    assert runtime.storage.list_held_messages("s1") == []


async def test_focused_session_holds(tmp_path, monkeypatch) -> None:
    runtime = focused_runtime(tmp_path)
    sent = record_dispatches(runtime, monkeypatch)
    runtime.storage.create_session(make_session(runtime.settings, "peer"))

    held = await runtime.held.deliver("s1", agent_send("hi"), HeldMessageOrigin.AGENT)

    assert sent == []
    assert [
        (r.id, r.text, r.sender_session_id, r.sender_title)
        for r in runtime.storage.list_held_messages("s1")
    ] == [(held.id, "hi", "peer", "Focused")]


async def test_hold_rejects_unknown_attachment(tmp_path) -> None:
    runtime = focused_runtime(tmp_path)

    with pytest.raises(HTTPException) as exc:
        await runtime.held.deliver(
            "s1", agent_send("hi", attachments=["0" * 32]), HeldMessageOrigin.AGENT
        )

    assert exc.value.status_code == 404
    assert runtime.storage.list_held_messages("s1") == []


async def test_wakes_coalesce(tmp_path, monkeypatch) -> None:
    runtime = focused_runtime(tmp_path)
    record_dispatches(runtime, monkeypatch)

    for _ in range(3):
        await runtime._deliver_wake("s1")

    held = runtime.storage.list_held_messages("s1")
    assert [(r.origin, r.text) for r in held] == [
        (HeldMessageOrigin.WAKE, WAKE_INPUT_TEXT)
    ]


async def test_scheduled_firing_is_held_and_marked_sent(tmp_path, monkeypatch) -> None:
    runtime = focused_runtime(tmp_path)
    sent = record_dispatches(runtime, monkeypatch)
    timed = runtime.scheduler.create_message_schedule(
        "s1", ScheduledMessageCreateRequest(text="timed", delay_seconds=0)
    )
    runtime.storage.update_scheduled_message(
        timed.id, scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    idle = runtime.scheduler.create_message_schedule(
        "s1",
        ScheduledMessageCreateRequest(
            text="idle",
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.WITH_PREVIOUS,
        ),
    )

    await runtime.scheduler._fire_due_schedules()

    assert sent == []
    held = runtime.storage.list_held_messages("s1")
    assert sorted(r.text for r in held) == ["idle", "timed"]
    assert {r.origin for r in held} == {HeldMessageOrigin.SCHEDULE}
    for schedule_id in (timed.id, idle.id):
        record = runtime.storage.get_scheduled_message(schedule_id)
        assert record is not None
        assert record.status == ScheduledMessageStatus.SENT


async def test_release_delivers_and_unpins(tmp_path, monkeypatch) -> None:
    runtime = focused_runtime(tmp_path)
    sent = record_dispatches(runtime, monkeypatch)
    attachment_id = upload(runtime, "s1")
    held = await runtime.held.deliver(
        "s1", agent_send("hi", attachments=[attachment_id]), HeldMessageOrigin.AGENT
    )
    assert runtime.attachments.held_referenced_ids("s1") == {attachment_id}

    await runtime.held.release(held.id)

    assert sent == ["hi"]
    assert runtime.storage.list_held_messages("s1") == []
    assert runtime.attachments.held_referenced_ids("s1") == set()


async def test_release_unknown_is_404(tmp_path) -> None:
    runtime = focused_runtime(tmp_path)

    with pytest.raises(HTTPException) as exc:
        await runtime.held.release("missing")

    assert exc.value.status_code == 404


async def test_prepare_failure_puts_message_back(tmp_path, monkeypatch) -> None:
    runtime = focused_runtime(tmp_path)
    held = await runtime.held.deliver("s1", agent_send("hi"), HeldMessageOrigin.AGENT)

    async def failing_prepare(*_: Any) -> PreparedInput:
        raise RuntimeError("reattach failed")

    monkeypatch.setattr(runtime, "prepare_input", failing_prepare)

    with pytest.raises(RuntimeError):
        await runtime.held.release(held.id)

    assert [r.model_dump() for r in runtime.storage.list_held_messages("s1")] == [
        held.model_dump()
    ]


async def test_dispatch_failure_consumes_message(tmp_path, monkeypatch) -> None:
    runtime = focused_runtime(tmp_path)
    held = await runtime.held.deliver("s1", agent_send("hi"), HeldMessageOrigin.AGENT)

    async def failing_dispatch(_: PreparedInput) -> SessionRecord:
        raise RuntimeError("send failed")

    monkeypatch.setattr(runtime, "dispatch_input", failing_dispatch)

    with pytest.raises(RuntimeError):
        await runtime.held.release(held.id)

    assert runtime.storage.list_held_messages("s1") == []


async def test_cancel_during_release_blocks_put_back(tmp_path, monkeypatch) -> None:
    runtime = focused_runtime(tmp_path)
    held = await runtime.held.deliver("s1", agent_send("hi"), HeldMessageOrigin.AGENT)
    gate = asyncio.Event()

    async def slow_failing_prepare(*_: Any) -> PreparedInput:
        await gate.wait()
        raise RuntimeError("reattach failed")

    monkeypatch.setattr(runtime, "prepare_input", slow_failing_prepare)
    release = asyncio.create_task(runtime.held.release(held.id))
    await asyncio.sleep(0)

    await runtime.held.cancel(held.id)
    gate.set()
    with pytest.raises(RuntimeError):
        await release

    assert runtime.storage.list_held_messages("s1") == []


async def test_cancel_after_prepare_skips_dispatch(tmp_path, monkeypatch) -> None:
    runtime = focused_runtime(tmp_path)
    sent = record_dispatches(runtime, monkeypatch)
    held = await runtime.held.deliver("s1", agent_send("hi"), HeldMessageOrigin.AGENT)
    gate = asyncio.Event()
    real_prepare = runtime.prepare_input

    async def slow_prepare(*args: Any) -> PreparedInput:
        await gate.wait()
        return await real_prepare(*args)

    monkeypatch.setattr(runtime, "prepare_input", slow_prepare)
    release = asyncio.create_task(runtime.held.release(held.id))
    await asyncio.sleep(0)

    await runtime.held.cancel(held.id)
    gate.set()
    await release

    assert sent == []
    assert runtime.storage.list_held_messages("s1") == []


async def test_cancel_while_dispatching_conflicts(tmp_path, monkeypatch) -> None:
    runtime = focused_runtime(tmp_path)
    held = await runtime.held.deliver("s1", agent_send("hi"), HeldMessageOrigin.AGENT)
    gate = asyncio.Event()

    async def slow_dispatch(prepared: PreparedInput) -> SessionRecord:
        await gate.wait()
        return prepared.session

    monkeypatch.setattr(runtime, "dispatch_input", slow_dispatch)
    release = asyncio.create_task(runtime.held.release(held.id))
    await asyncio.sleep(0.01)

    with pytest.raises(HTTPException) as exc:
        await runtime.held.cancel(held.id)
    gate.set()
    await release

    assert exc.value.status_code == 409


async def test_put_back_skipped_for_deleted_session(tmp_path, monkeypatch) -> None:
    runtime = focused_runtime(tmp_path)
    held = await runtime.held.deliver("s1", agent_send("hi"), HeldMessageOrigin.AGENT)

    async def prepare_after_delete(*_: Any) -> PreparedInput:
        runtime.storage.delete_session("s1")
        raise HTTPException(status_code=404, detail="session not found")

    monkeypatch.setattr(runtime, "prepare_input", prepare_after_delete)

    with pytest.raises(HTTPException):
        await runtime.held.release(held.id)

    assert runtime.storage.list_held_messages("s1") == []


async def test_release_all_in_order_and_cancel_mid_run(tmp_path, monkeypatch) -> None:
    runtime = focused_runtime(tmp_path)
    for text in ("one", "two", "three"):
        await runtime.held.deliver("s1", agent_send(text), HeldMessageOrigin.AGENT)
    held = runtime.storage.list_held_messages("s1")
    sent: list[str] = []

    async def dispatch(prepared: PreparedInput) -> SessionRecord:
        sent.append(prepared.request.text)
        if prepared.request.text == "one":
            await runtime.held.cancel(held[1].id)
        return prepared.session

    monkeypatch.setattr(runtime, "dispatch_input", dispatch)

    await runtime.held.release_all("s1")

    assert sent == ["one", "three"]
    assert runtime.storage.list_held_messages("s1") == []


async def test_cancel_all_unpins(tmp_path) -> None:
    runtime = focused_runtime(tmp_path)
    attachment_id = upload(runtime, "s1")
    await runtime.held.deliver(
        "s1", agent_send("a", attachments=[attachment_id]), HeldMessageOrigin.AGENT
    )
    await runtime.held.deliver("s1", agent_send("b"), HeldMessageOrigin.AGENT)

    await runtime.held.cancel_all("s1")

    assert runtime.storage.list_held_messages("s1") == []
    assert runtime.attachments.held_referenced_ids("s1") == set()


async def test_held_attachment_survives_sweep(tmp_path) -> None:
    runtime = focused_runtime(tmp_path)
    attachment_id = upload(runtime, "s1")
    await runtime.held.deliver(
        "s1", agent_send("a", attachments=[attachment_id]), HeldMessageOrigin.AGENT
    )

    runtime.attachments.sweep("s1", ttl_seconds=0)

    assert runtime.attachments.resolve("s1", attachment_id) is not None


async def test_startup_reconcile_drops_orphan_held_pins(tmp_path) -> None:
    runtime = focused_runtime(tmp_path)
    attachment_id = upload(runtime, "s1")
    runtime.attachments.mark_held_references("s1", "gone", [attachment_id])

    assert runtime.attachments.reconcile_held_references(
        runtime.storage.held_message_ids()
    )
    assert runtime.attachments.held_referenced_ids("s1") == set()


async def test_focus_persists_and_turning_off_keeps_held(tmp_path) -> None:
    runtime = focused_runtime(tmp_path)
    await runtime.held.deliver("s1", agent_send("hi"), HeldMessageOrigin.AGENT)

    runtime.set_focus("s1", False)

    session = runtime.storage.get_session("s1")
    assert session is not None and session.focus is False
    assert len(runtime.storage.list_held_messages("s1")) == 1


# ── API ──────────────────────────────────────────────────────────────────────


def _build(tmp_path: Path) -> tuple[Any, SessionRuntime, dict[str, str]]:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    context = app.state.context
    token = context.tokens.issue().token
    return app, context.runtime, {"Authorization": f"Bearer {token}"}


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_api_focus_hold_and_release(tmp_path, monkeypatch) -> None:
    app, runtime, auth = _build(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "s1"))
    sent = record_dispatches(runtime, monkeypatch)
    async with _client(app) as client:
        focus = await client.post("/api/sessions/s1/focus", headers=auth)
        assert focus.json()["session"]["focus"] is True

        human = await client.post(
            "/api/sessions/s1/input", json={"text": "typed"}, headers=auth
        )
        assert "held_message" not in human.json()

        agent = await client.post(
            "/api/sessions/s1/input",
            json={"text": "from peer", "sender_session_id": "peer"},
            headers=auth,
        )
        held_id = agent.json()["held_message"]["id"]
        assert sent == ["typed"]

        assert [r.id for r in runtime.storage.list_held_messages("s1")] == [held_id]

        released = await client.post(
            f"/api/held-messages/{held_id}/release", headers=auth
        )
        assert released.status_code == 200
        assert sent == ["typed", "from peer"]

        off = await client.delete("/api/sessions/s1/focus", headers=auth)
        assert off.json()["session"]["focus"] is False


async def test_api_cancel_endpoints(tmp_path) -> None:
    app, runtime, auth = _build(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "s1", focus=True))
    for text in ("a", "b", "c"):
        await runtime.held.deliver("s1", agent_send(text), HeldMessageOrigin.AGENT)
    first = runtime.storage.list_held_messages("s1")[0]
    async with _client(app) as client:
        one = await client.delete(f"/api/held-messages/{first.id}", headers=auth)
        assert one.status_code == 204
        assert len(runtime.storage.list_held_messages("s1")) == 2

        missing = await client.delete("/api/held-messages/nope", headers=auth)
        assert missing.status_code == 404

        everything = await client.delete("/api/sessions/s1/held-messages", headers=auth)
        assert everything.status_code == 204
    assert runtime.storage.list_held_messages("s1") == []


class BlockingTransport:
    is_structured = False

    def __init__(self) -> None:
        self.blocked = False
        self.pending = False

    def has_pending_approval(self, session: SessionRecord) -> bool:
        return self.pending

    async def input_blocked(self, session: SessionRecord) -> bool:
        return self.blocked or self.pending


def blocking_runtime(tmp_path: Path, monkeypatch) -> tuple[SessionRuntime, Any]:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "s1"))
    transport = BlockingTransport()
    monkeypatch.setattr(runtime, "transport_for", lambda session: transport)
    return runtime, transport


def record_blocked_dispatches(
    runtime: SessionRuntime, transport: BlockingTransport, monkeypatch
) -> list[str]:
    sent: list[str] = []

    async def fake_dispatch(prepared: PreparedInput) -> SessionRecord:
        if await transport.input_blocked(prepared.session):
            raise InputBlockedError()
        sent.append(prepared.request.text)
        return prepared.session

    monkeypatch.setattr(runtime, "dispatch_input", fake_dispatch)
    return sent


async def settle(runtime: SessionRuntime) -> None:
    while runtime.held._tasks:
        await asyncio.gather(*list(runtime.held._tasks))


async def test_blocked_session_holds_then_delivers_in_order(
    tmp_path, monkeypatch
) -> None:
    runtime, transport = blocking_runtime(tmp_path, monkeypatch)
    sent = record_blocked_dispatches(runtime, transport, monkeypatch)
    transport.pending = True

    first = await runtime.held.deliver("s1", agent_send("one"), HeldMessageOrigin.AGENT)
    await settle(runtime)
    transport.pending = False
    # Unblocked, but an item is still queued: the new send waits behind it.
    await runtime.held.deliver("s1", agent_send("two"), HeldMessageOrigin.AGENT)
    await settle(runtime)

    assert (
        isinstance(first, HeldMessageRecord) and first.hold_reason is HeldReason.DIALOG
    )
    assert sent == ["one", "two"]
    assert runtime.storage.list_held_messages("s1") == []
    assert "s1" not in runtime.held._deferred


async def test_pane_only_block_retries(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("waypoint.held.DEFER_RETRY_SECONDS", 0.01)
    runtime, transport = blocking_runtime(tmp_path, monkeypatch)
    sent = record_blocked_dispatches(runtime, transport, monkeypatch)
    transport.blocked = True

    await runtime.held.deliver("s1", agent_send("one"), HeldMessageOrigin.AGENT)
    await settle(runtime)
    assert sent == []
    transport.blocked = False
    await asyncio.sleep(0.05)
    await settle(runtime)

    assert sent == ["one"]


async def test_pending_approval_waits_for_an_edge(tmp_path, monkeypatch) -> None:
    runtime, transport = blocking_runtime(tmp_path, monkeypatch)
    sent = record_blocked_dispatches(runtime, transport, monkeypatch)
    transport.pending = True

    await runtime.held.deliver("s1", agent_send("one"), HeldMessageOrigin.AGENT)
    await settle(runtime)
    assert sent == [] and not runtime.held._retries
    transport.pending = False
    runtime.held.drain_deferred({"s1"})
    await settle(runtime)

    assert sent == ["one"]


async def test_focus_held_items_never_auto_release(tmp_path, monkeypatch) -> None:
    runtime, transport = blocking_runtime(tmp_path, monkeypatch)
    sent = record_blocked_dispatches(runtime, transport, monkeypatch)
    transport.pending = True
    await runtime.held.deliver("s1", agent_send("auto"), HeldMessageOrigin.AGENT)
    await settle(runtime)
    runtime.set_focus("s1", True)
    await runtime.held.deliver("s1", agent_send("focus"), HeldMessageOrigin.AGENT)

    transport.pending = False
    runtime.held.drain_deferred({"s1"})
    await settle(runtime)
    assert sent == []  # Focus holds the auto item too
    runtime.set_focus("s1", False)
    await settle(runtime)

    assert sent == ["auto"]
    assert [m.text for m in runtime.storage.list_held_messages("s1")] == ["focus"]


async def test_release_while_blocked_puts_the_item_back(tmp_path, monkeypatch) -> None:
    runtime, transport = blocking_runtime(tmp_path, monkeypatch)
    transport.pending = True
    held = await runtime.held.deliver("s1", agent_send("one"), HeldMessageOrigin.AGENT)
    await settle(runtime)

    async def blocked_dispatch(prepared: PreparedInput) -> SessionRecord:
        raise InputBlockedError()

    monkeypatch.setattr(runtime, "dispatch_input", blocked_dispatch)
    with pytest.raises(InputBlockedError):
        await runtime.held.release(held.id)

    assert [m.id for m in runtime.storage.list_held_messages("s1")] == [held.id]
    assert "s1" in runtime.held._deferred


async def test_start_seeds_sessions_with_auto_items(tmp_path, monkeypatch) -> None:
    runtime, transport = blocking_runtime(tmp_path, monkeypatch)
    transport.pending = True
    await runtime.held.deliver("s1", agent_send("one"), HeldMessageOrigin.AGENT)
    await settle(runtime)

    reopened = SessionRuntime(runtime.settings, runtime.storage)
    reopened.held.start()

    assert reopened.held._deferred == {"s1"}


async def test_dispatch_refuses_a_blocked_session_before_recording(
    tmp_path, monkeypatch
) -> None:
    runtime, transport = blocking_runtime(tmp_path, monkeypatch)
    transport.is_structured = False
    transport.blocked = True

    with pytest.raises(InputBlockedError):
        await runtime.handle_input("s1", SessionInputRequest(text="hi"))

    assert runtime.storage.list_events("s1") == []
    assert runtime.get_session("s1").status is SessionStatus.IDLE


def test_legacy_auto_release_rows_migrate_to_hold_reasons(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "s1"))
    now = datetime.now(UTC).isoformat()
    legacy = [
        ("a", HeldMessageOrigin.AGENT, True),
        ("w", HeldMessageOrigin.WAKE, True),
        ("f", HeldMessageOrigin.SCHEDULE, False),
    ]
    for held_id, origin, auto_release in legacy:
        body = {
            "id": held_id,
            "session_id": "s1",
            "origin": origin,
            "created_at": now,
            "auto_release": auto_release,
        }
        runtime.storage.connection.execute(
            "INSERT INTO held_messages (id, session_id, origin, created_at, body) "
            "VALUES (?, ?, ?, ?, ?)",
            (held_id, "s1", origin, now, json.dumps(body)),
        )
    runtime.storage.connection.commit()

    for _ in range(2):
        storage = Storage(runtime.settings.database_path)
        reasons = {m.id: m.hold_reason for m in storage.list_held_messages("s1")}
        assert reasons == {
            "a": HeldReason.DIALOG,
            "w": HeldReason.IDLE,
            "f": HeldReason.FOCUS,
        }


async def test_cancelled_drain_puts_the_item_back(tmp_path, monkeypatch) -> None:
    runtime, transport = blocking_runtime(tmp_path, monkeypatch)
    transport.pending = True
    held = await runtime.held.deliver("s1", agent_send("one"), HeldMessageOrigin.AGENT)
    await settle(runtime)
    started = asyncio.Event()

    async def stuck_prepare(
        session_id: str, request: SessionInputRequest
    ) -> PreparedInput:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError

    monkeypatch.setattr(runtime, "prepare_input", stuck_prepare)
    transport.pending = False
    runtime.held.drain_deferred({"s1"})
    await started.wait()
    await runtime.held.stop()

    assert [m.id for m in runtime.storage.list_held_messages("s1")] == [held.id]


async def test_stopped_queue_starts_no_drain(tmp_path, monkeypatch) -> None:
    runtime, transport = blocking_runtime(tmp_path, monkeypatch)
    sent = record_blocked_dispatches(runtime, transport, monkeypatch)
    transport.pending = True
    await runtime.held.deliver("s1", agent_send("one"), HeldMessageOrigin.AGENT)
    await settle(runtime)

    await runtime.held.stop()
    transport.pending = False
    runtime.held.drain_deferred({"s1"})
    await settle(runtime)

    assert sent == []
    assert [m.text for m in runtime.storage.list_held_messages("s1")] == ["one"]
