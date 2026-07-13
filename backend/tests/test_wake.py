import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from waypoint.runtime import WAKE_INPUT_TEXT, SessionRuntime
from waypoint.schemas import (
    BoardPostRequest,
    SessionInputRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
    WakeRegisterRequest,
)
from waypoint.settings import Settings
from waypoint.storage import Storage


def make_runtime(tmp_path) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    return SessionRuntime(settings, storage)


def make_session(
    settings: Settings,
    session_id: str,
    status: SessionStatus = SessionStatus.IDLE,
) -> SessionRecord:
    session_dir = settings.sessions_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    return SessionRecord(
        id=session_id,
        backend="codex",
        source=SessionSource.MANAGED,
        transport="codex_app_server",
        title="Subscriber",
        cwd="/tmp/project",
        status=status,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=str(session_dir / "raw.log"),
        structured_log_path=str(session_dir / "events.jsonl"),
    )


class _ApprovalStub:
    """Minimal transport stub exposing only ``has_pending_approval``."""

    def __init__(self, pending: bool = False) -> None:
        self.pending = pending

    def has_pending_approval(self, session: SessionRecord) -> bool:
        return self.pending


def _record_wakes(runtime: SessionRuntime, monkeypatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def fake_handle_input(
        session_id: str, request: SessionInputRequest
    ) -> SessionRecord:
        calls.append((session_id, request.text))
        return runtime.storage.get_session(session_id)  # type: ignore[return-value]

    monkeypatch.setattr(runtime, "handle_input", fake_handle_input)
    return calls


def _register(runtime: SessionRuntime, session_id: str, **kwargs: Any) -> None:
    runtime.register_wake(session_id, WakeRegisterRequest(**kwargs))


async def _flush_wakes(runtime: SessionRuntime) -> None:
    # Wake delivery now runs in tracked background tasks (``_fire_wake``); a
    # dispatch task can spawn a delivery task, so drain to a fixpoint. The
    # ``sleep(0)`` lets each task's done-callback prune ``_wake_tasks`` before
    # the next round so the loop terminates.
    while runtime._wake_tasks:
        await asyncio.gather(*list(runtime._wake_tasks))
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_matching_board_post_wakes_idle_subscriber(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "codex-sub"))
    _register(runtime, "codex-sub", channel_globs=["tickets", "ticket-*"])
    calls = _record_wakes(runtime, monkeypatch)

    await runtime._dispatch_subscription_wakes(
        channel="tickets", is_inbox=False, actor_session_id=None
    )
    await _flush_wakes(runtime)

    assert calls == [("codex-sub", WAKE_INPUT_TEXT)]


@pytest.mark.asyncio
async def test_non_matching_channel_does_not_wake(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "codex-sub"))
    _register(runtime, "codex-sub", channel_globs=["ticket-*"])
    calls = _record_wakes(runtime, monkeypatch)

    await runtime._dispatch_subscription_wakes(
        channel="org", is_inbox=False, actor_session_id=None
    )
    await _flush_wakes(runtime)

    assert calls == []


@pytest.mark.asyncio
async def test_self_authored_board_post_does_not_wake_author(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "codex-author"))
    _register(runtime, "codex-author", channel_globs=["tickets"])
    calls = _record_wakes(runtime, monkeypatch)

    await runtime._dispatch_subscription_wakes(
        channel="tickets", is_inbox=False, actor_session_id="codex-author"
    )
    await _flush_wakes(runtime)

    assert calls == []


@pytest.mark.asyncio
async def test_broad_change_wakes_board_subscribers(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "codex-board"))
    runtime.storage.create_session(make_session(runtime.settings, "codex-inbox"))
    _register(runtime, "codex-board", channel_globs=["ticket-*"])
    _register(runtime, "codex-inbox", wake_on_inbox=True)
    calls = _record_wakes(runtime, monkeypatch)

    # ``channel=None`` is a broad board change: it wakes every board subscriber
    # but not an inbox-only subscriber.
    await runtime._dispatch_subscription_wakes(
        channel=None, is_inbox=False, actor_session_id=None
    )
    await _flush_wakes(runtime)

    assert calls == [("codex-board", WAKE_INPUT_TEXT)]


@pytest.mark.asyncio
async def test_self_inbox_mutation_does_not_wake_but_human_does(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "codex-mgr"))
    _register(runtime, "codex-mgr", wake_on_inbox=True)
    calls = _record_wakes(runtime, monkeypatch)

    # The manager filing/answering its own item: no self-wake (it owns the item
    # but is also the actor, so it is self-excluded).
    await runtime._dispatch_subscription_wakes(
        channel=None,
        is_inbox=True,
        actor_session_id="codex-mgr",
        owner_session_id="codex-mgr",
    )
    await _flush_wakes(runtime)
    assert calls == []

    # A human answer carries no session id: the item's owner (manager) is woken.
    await runtime._dispatch_subscription_wakes(
        channel=None,
        is_inbox=True,
        actor_session_id=None,
        owner_session_id="codex-mgr",
    )
    await _flush_wakes(runtime)
    assert calls == [("codex-mgr", WAKE_INPUT_TEXT)]


@pytest.mark.asyncio
async def test_inbox_wake_scoped_to_item_owner(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "codex-owner"))
    runtime.storage.create_session(make_session(runtime.settings, "codex-other"))
    _register(runtime, "codex-owner", wake_on_inbox=True)
    _register(runtime, "codex-other", wake_on_inbox=True)
    calls = _record_wakes(runtime, monkeypatch)

    # A human answer on the owner's item wakes only the owner, never another
    # inbox subscriber with no stake in that item.
    await runtime._dispatch_subscription_wakes(
        channel=None,
        is_inbox=True,
        actor_session_id=None,
        owner_session_id="codex-owner",
    )
    await _flush_wakes(runtime)
    assert calls == [("codex-owner", WAKE_INPUT_TEXT)]


@pytest.mark.asyncio
async def test_ownerless_inbox_mutation_wakes_nobody(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "codex-inbox"))
    _register(runtime, "codex-inbox", wake_on_inbox=True)
    calls = _record_wakes(runtime, monkeypatch)

    # A human-posted item (``from_session_id == ""``) owns no session, so its
    # mutation wakes no inbox subscriber.
    await runtime._dispatch_subscription_wakes(
        channel=None, is_inbox=True, actor_session_id=None, owner_session_id=None
    )
    await _flush_wakes(runtime)
    assert calls == []


@pytest.mark.asyncio
async def test_wake_in_flight_defers_concurrent_second(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "codex-sub"))
    _register(runtime, "codex-sub", channel_globs=["tickets"])

    # A slow delivery holds the in-flight slot; a second fire while it runs is
    # deferred to ``_pending_wakes`` rather than concurrently injected.
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def slow_handle_input(
        session_id: str, request: SessionInputRequest
    ) -> SessionRecord:
        calls.append(session_id)
        started.set()
        await release.wait()
        return runtime.storage.get_session(session_id)  # type: ignore[return-value]

    monkeypatch.setattr(runtime, "handle_input", slow_handle_input)

    runtime._fire_wake("codex-sub")
    await started.wait()
    assert runtime._wake_in_flight == {"codex-sub"}

    # Second fire while the first is in flight: deferred, not a second send.
    runtime._fire_wake("codex-sub")
    assert runtime._pending_wakes == {"codex-sub"}
    assert calls == ["codex-sub"]

    release.set()
    await _flush_wakes(runtime)
    # The deferred wake is delivered once the first completes — not stranded.
    assert calls == ["codex-sub", "codex-sub"]
    assert runtime._pending_wakes == set()
    assert runtime._wake_in_flight == set()


@pytest.mark.asyncio
async def test_deferred_wake_survives_failed_in_flight_delivery(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "codex-sub"))
    _register(runtime, "codex-sub", channel_globs=["tickets"])

    started = asyncio.Event()
    gate = asyncio.Event()
    calls: list[str] = []
    attempt = {"n": 0}

    async def flaky_handle_input(
        session_id: str, request: SessionInputRequest
    ) -> SessionRecord:
        calls.append(session_id)
        if attempt["n"] == 0:
            attempt["n"] += 1
            started.set()
            await gate.wait()
            raise RuntimeError("transient transport error")
        return runtime.storage.get_session(session_id)  # type: ignore[return-value]

    monkeypatch.setattr(runtime, "handle_input", flaky_handle_input)

    runtime._fire_wake("codex-sub")  # first delivery — will fail
    await started.wait()
    runtime._fire_wake("codex-sub")  # deferred behind the in-flight failure
    assert runtime._pending_wakes == {"codex-sub"}

    # The in-flight delivery now raises without changing the session's status,
    # so no broadcast edge is produced. The parked wake must not be stranded:
    # it is re-driven from the failed delivery's ``finally``.
    gate.set()
    await _flush_wakes(runtime)
    assert calls == ["codex-sub", "codex-sub"]
    assert runtime._pending_wakes == set()
    assert runtime._wake_in_flight == set()


@pytest.mark.asyncio
async def test_board_wake_ignores_inbox_only_subscriber(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "codex-inbox"))
    _register(runtime, "codex-inbox", wake_on_inbox=True)
    calls = _record_wakes(runtime, monkeypatch)

    await runtime._dispatch_subscription_wakes(
        channel="tickets", is_inbox=False, actor_session_id=None
    )
    await _flush_wakes(runtime)

    assert calls == []


@pytest.mark.asyncio
async def test_running_subscriber_burst_coalesces_and_fires_on_edge(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(
        make_session(runtime.settings, "codex-sub", status=SessionStatus.RUNNING)
    )
    _register(runtime, "codex-sub", channel_globs=["tickets"])
    calls = _record_wakes(runtime, monkeypatch)

    # Two matching posts while RUNNING: deferred, coalesced to a single owed wake.
    await runtime._dispatch_subscription_wakes(
        channel="tickets", is_inbox=False, actor_session_id=None
    )
    await runtime._dispatch_subscription_wakes(
        channel="tickets", is_inbox=False, actor_session_id=None
    )
    assert calls == []
    assert runtime._pending_wakes == {"codex-sub"}

    # Turn ends → deliverable edge → exactly one wake, and the debt is cleared.
    runtime.storage.update_session("codex-sub", status=SessionStatus.IDLE)
    runtime._drain_pending_wakes({"codex-sub"})
    assert runtime._pending_wakes == set()
    await _flush_wakes(runtime)
    assert calls == [("codex-sub", WAKE_INPUT_TEXT)]


@pytest.mark.asyncio
async def test_mid_approval_defers_then_fires_when_approval_clears(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(
        make_session(runtime.settings, "codex-sub", status=SessionStatus.WAITING_INPUT)
    )
    _register(runtime, "codex-sub", channel_globs=["tickets"])
    calls = _record_wakes(runtime, monkeypatch)

    stub = _ApprovalStub(pending=True)
    monkeypatch.setattr(runtime, "transport_for", lambda session: stub)

    # WAITING_INPUT while an approval is outstanding: never inject; defer.
    await runtime._dispatch_subscription_wakes(
        channel="tickets", is_inbox=False, actor_session_id=None
    )
    assert calls == []
    assert runtime._pending_wakes == {"codex-sub"}

    # Draining while the approval is still pending must not deliver.
    runtime._drain_pending_wakes({"codex-sub"})
    await _flush_wakes(runtime)
    assert calls == []
    assert runtime._pending_wakes == {"codex-sub"}

    # Approval resolved (still WAITING_INPUT, now the finished-turn state).
    stub.pending = False
    runtime._drain_pending_wakes({"codex-sub"})
    assert runtime._pending_wakes == set()
    await _flush_wakes(runtime)
    assert calls == [("codex-sub", WAKE_INPUT_TEXT)]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [SessionStatus.EXITED, SessionStatus.ERROR])
async def test_stopped_subscriber_is_not_resurrected(
    tmp_path, monkeypatch, status
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(
        make_session(runtime.settings, "codex-sub", status=status)
    )
    _register(runtime, "codex-sub", channel_globs=["tickets"])
    calls = _record_wakes(runtime, monkeypatch)

    await runtime._dispatch_subscription_wakes(
        channel="tickets", is_inbox=False, actor_session_id=None
    )
    await _flush_wakes(runtime)

    assert calls == []
    assert runtime._pending_wakes == set()


@pytest.mark.asyncio
async def test_pending_wake_dropped_when_session_stops(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(
        make_session(runtime.settings, "codex-sub", status=SessionStatus.RUNNING)
    )
    _register(runtime, "codex-sub", channel_globs=["tickets"])
    calls = _record_wakes(runtime, monkeypatch)

    await runtime._dispatch_subscription_wakes(
        channel="tickets", is_inbox=False, actor_session_id=None
    )
    assert runtime._pending_wakes == {"codex-sub"}

    # The session dies before ever reaching a deliverable edge: drop the debt,
    # never resurrect.
    runtime.storage.update_session("codex-sub", status=SessionStatus.EXITED)
    runtime._drain_pending_wakes({"codex-sub"})
    await _flush_wakes(runtime)
    assert calls == []
    assert runtime._pending_wakes == set()


@pytest.mark.asyncio
async def test_post_board_entry_wires_dispatch(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "codex-sub"))
    _register(runtime, "codex-sub", channel_globs=["tickets"])
    calls = _record_wakes(runtime, monkeypatch)

    await runtime.post_board_entry("tickets", BoardPostRequest(text="new ticket"))
    # Dispatch and delivery are tracked background tasks; drain them.
    await _flush_wakes(runtime)

    assert calls == [("codex-sub", WAKE_INPUT_TEXT)]


def test_subscriptions_removed_on_delete_session(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "codex-sub"))
    _register(runtime, "codex-sub", channel_globs=["tickets"], wake_on_inbox=True)
    assert runtime.storage.list_wake_subscriptions_for_session("codex-sub")

    runtime.storage.delete_session("codex-sub")

    assert runtime.storage.list_wake_subscriptions_for_session("codex-sub") == []
    assert runtime.storage.list_wake_subscriptions() == []


def test_register_list_unregister_roundtrip(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "codex-sub"))
    sub = runtime.register_wake(
        "codex-sub", WakeRegisterRequest(channel_globs=["ticket-*"], kinds=["done"])
    )
    assert sub.channel_globs == ["ticket-*"]
    assert sub.kinds == ["done"]
    assert [s.id for s in runtime.list_wakes("codex-sub")] == [sub.id]

    assert runtime.unregister_wake("codex-sub", sub.id) is True
    assert runtime.list_wakes("codex-sub") == []
    assert runtime.unregister_wake("codex-sub", sub.id) is False
