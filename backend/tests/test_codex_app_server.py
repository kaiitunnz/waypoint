import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from waypoint.codex_app_server import CodexAppServerAdapter
from waypoint.schemas import EventKind, SessionStatus


@dataclass
class FakeThread:
    id: str


@dataclass
class FakeStartResponse:
    thread: FakeThread


@dataclass
class FakeTurn:
    id: str


@dataclass
class FakeTurnStartResponse:
    turn: FakeTurn


class FakeAppServerClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.notifications: asyncio.Queue[Any] = asyncio.Queue()
        self.approval_handler = None
        self.started = False
        self.initialized = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def initialize(self) -> None:
        self.initialized = True

    def close(self) -> None:
        self.closed = True

    def thread_start(self, params: dict[str, Any]) -> FakeStartResponse:
        self.calls.append(("thread_start", (params,)))
        return FakeStartResponse(FakeThread(id="thread-1"))

    def thread_resume(self, thread_id: str) -> dict[str, Any]:
        self.calls.append(("thread_resume", (thread_id,)))
        return {"thread_id": thread_id}

    def turn_start(self, thread_id: str, text: str) -> FakeTurnStartResponse:
        self.calls.append(("turn_start", (thread_id, text)))
        return FakeTurnStartResponse(FakeTurn(id="turn-1"))

    def turn_steer(self, thread_id: str, turn_id: str, text: str) -> None:
        self.calls.append(("turn_steer", (thread_id, turn_id, text)))

    def turn_interrupt(self, thread_id: str, turn_id: str) -> None:
        self.calls.append(("turn_interrupt", (thread_id, turn_id)))

    def next_notification(self) -> Any:
        # Synchronous call in adapter goes through asyncio.to_thread so this
        # blocks the worker thread until a notification is enqueued.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.notifications.get())
        finally:
            loop.close()


@dataclass
class FakeNotification:
    method: str
    payload: dict[str, Any]


def make_adapter(emitted: list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]]):
    async def emit(session_id, kind, text, metadata, status):
        emitted.append((session_id, kind, text, metadata, status))

    fake = FakeAppServerClient()

    def factory(cwd, approval_handler):
        fake.approval_handler = approval_handler
        return fake

    adapter = CodexAppServerAdapter(emit, client_factory=factory)
    return adapter, fake


@pytest.mark.asyncio
async def test_start_session_creates_thread() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    thread_id = await adapter.start_session("sess", "/tmp/work")
    assert thread_id == "thread-1"
    assert fake.started and fake.initialized
    assert fake.calls[0][0] == "thread_start"


@pytest.mark.asyncio
async def test_send_input_starts_then_steers_turn() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    state = adapter._sessions["sess"]
    # First send_input creates a turn and a stream task.
    await adapter.send_input("sess", "hello")
    assert state.active_turn_id == "turn-1"
    # Second send_input steers the existing turn instead of starting a new one.
    await adapter.send_input("sess", "more")
    methods = [call[0] for call in fake.calls]
    assert methods.count("turn_start") == 1
    assert methods.count("turn_steer") == 1
    # Cancel the dangling stream task so the loop can shut down cleanly.
    if state.stream_task is not None:
        state.stream_task.cancel()
        try:
            await state.stream_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_interrupt_calls_turn_interrupt_when_active() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    state = adapter._sessions["sess"]
    state.active_turn_id = "turn-X"
    await adapter.interrupt("sess")
    assert ("turn_interrupt", ("thread-1", "turn-X")) in fake.calls


@pytest.mark.asyncio
async def test_interrupt_noop_without_active_turn() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    fake.calls.clear()
    await adapter.interrupt("sess")
    assert not any(call[0] == "turn_interrupt" for call in fake.calls)


@pytest.mark.asyncio
async def test_respond_to_approval_resolves_pending() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    state = adapter._sessions["sess"]
    from waypoint.codex_app_server import PendingApproval

    pending = PendingApproval(method="item/commandExecution/requestApproval", params={"command": "ls"})
    state.pending_approval = pending
    handled = await adapter.respond_to_approval("sess", "approve")
    assert handled is True
    assert pending.event.is_set()
    assert pending.response == {"decision": "accept"}


@pytest.mark.asyncio
async def test_respond_to_approval_returns_false_when_idle() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    handled = await adapter.respond_to_approval("sess", "approve")
    assert handled is False


@pytest.mark.asyncio
async def test_restore_session_calls_thread_resume() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.restore_session("sess", "/tmp/work", "thread-99")
    assert ("thread_resume", ("thread-99",)) in fake.calls
    assert adapter._sessions["sess"].thread_id == "thread-99"


@pytest.mark.asyncio
async def test_terminate_session_closes_client_and_drops_state() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    state = adapter._sessions["sess"]
    state.active_turn_id = "turn-X"
    handled = await adapter.terminate_session("sess")
    assert handled is True
    assert "sess" not in adapter._sessions
    assert ("turn_interrupt", ("thread-1", "turn-X")) in fake.calls
    assert fake.closed is True


@pytest.mark.asyncio
async def test_terminate_session_returns_false_for_unknown_id() -> None:
    emitted: list = []
    adapter, _ = make_adapter(emitted)
    handled = await adapter.terminate_session("missing")
    assert handled is False


@pytest.mark.asyncio
async def test_terminal_snapshot_returns_command_fragments() -> None:
    emitted: list = []
    adapter, _ = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    state = adapter._sessions["sess"]
    state.terminal_fragments.extend(["one\n", "two\n"])
    assert adapter.terminal_snapshot("sess") == "one\ntwo\n"


def test_map_notification_agent_message_delta() -> None:
    adapter = CodexAppServerAdapter(lambda *_: None, client_factory=lambda *_: None)  # type: ignore[arg-type]
    kind, text, status = adapter._map_notification(
        "item/agentMessage/delta",
        {"delta": "hello"},
    )
    assert kind == EventKind.AGENT_OUTPUT
    assert text == "hello"
    assert status == SessionStatus.RUNNING


def test_map_notification_command_execution_started() -> None:
    adapter = CodexAppServerAdapter(lambda *_: None, client_factory=lambda *_: None)  # type: ignore[arg-type]
    kind, text, status = adapter._map_notification(
        "item/started",
        {"item": {"type": "commandExecution", "command": "ls -la"}},
    )
    assert kind == EventKind.TOOL_CALL
    assert "ls -la" in text


def test_format_item_completed_drops_agent_message_duplicate() -> None:
    adapter = CodexAppServerAdapter(lambda *_: None, client_factory=lambda *_: None)  # type: ignore[arg-type]
    kind, text, status = adapter._format_item_completed({"type": "agentMessage", "text": "hello"})
    assert kind is None
    assert text == ""
    assert status == SessionStatus.RUNNING


def test_extract_item_id_pulls_top_level_and_nested_ids() -> None:
    adapter = CodexAppServerAdapter(lambda *_: None, client_factory=lambda *_: None)  # type: ignore[arg-type]
    assert adapter._extract_item_id({"itemId": "abc", "delta": "x"}) == "abc"
    assert adapter._extract_item_id({"item": {"id": "xyz", "type": "agentMessage"}}) == "xyz"
    assert adapter._extract_item_id({}) is None


def test_map_decision_table() -> None:
    adapter = CodexAppServerAdapter(lambda *_: None, client_factory=lambda *_: None)  # type: ignore[arg-type]
    assert adapter._map_decision("approve") == "accept"
    assert adapter._map_decision("y") == "accept"
    assert adapter._map_decision("acceptForSession") == "acceptForSession"
    assert adapter._map_decision("cancel") == "cancel"
    assert adapter._map_decision("anything-else") == "decline"
