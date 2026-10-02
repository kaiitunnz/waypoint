import asyncio
import base64
import queue
import threading
import weakref
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai_codex.client import CodexClient
from openai_codex.errors import (
    InvalidRequestError,
    MethodNotFoundError,
    ServerBusyError,
)
from openai_codex.generated.v2_all import AgentMessageThreadItem

from waypoint.backends.codex import adapter as adapter_module
from waypoint.backends.codex.adapter import (
    CodexAppServerAdapter,
    CodexCompactingError,
    CodexSessionState,
    _context_usage_snapshot_from_thread_token_usage,
)
from waypoint.backends.codex.normalize import tool_result_is_error
from waypoint.backends.codex.subagents import (
    REPORT_UNAVAILABLE,
    SUBAGENT_REPORT_KEY,
    ReportSource,
    read_report,
)
from waypoint.backends.codex.transport import input_http_error
from waypoint.schemas import EventKind, SessionStatus


@pytest.mark.parametrize(
    ("item_type", "item", "expected"),
    [
        ("commandExecution", {"status": "completed", "exitCode": 0}, False),
        ("commandExecution", {"status": "failed", "exitCode": 1}, True),
        ("commandExecution", {"status": "declined"}, True),
        ("commandExecution", {"exitCode": 0}, False),
        ("commandExecution", {"exitCode": 2}, True),
        ("commandExecution", {"status": "inProgress"}, None),
        ("commandExecution", {}, None),
        ("fileChange", {"status": "completed"}, False),
        ("fileChange", {"status": "failed"}, True),
        ("mcpToolCall", {"status": "completed"}, False),
        ("mcpToolCall", {"status": "failed"}, True),
        ("dynamicToolCall", {"status": "failed"}, True),
        ("collabAgentToolCall", {"status": "completed"}, False),
        ("collabAgentToolCall", {"status": "failed"}, True),
        ("webSearch", {"status": "completed"}, None),
        ("agentMessage", {"status": "completed"}, None),
        (None, {"status": "completed"}, None),
    ],
)
def test_tool_result_is_error_maps_codex_outcomes(
    item_type: str | None, item: dict[str, Any], expected: bool | None
) -> None:
    assert tool_result_is_error(item_type, item) is expected


@dataclass
class FakeThread:
    id: str


@dataclass
class FakeStartResponse:
    thread: FakeThread
    model: str | None = None


@dataclass
class FakeTurn:
    id: str


@dataclass
class FakeTurnStartResponse:
    turn: FakeTurn


_CLOSED = object()


class NotificationQueue:
    """Thread-safe stand-in for the SDK's notification routing."""

    def __init__(self) -> None:
        self._queue: queue.Queue[Any] = queue.Queue()

    def put_nowait(self, item: Any) -> None:
        self._queue.put(item)

    async def put(self, item: Any) -> None:
        self._queue.put(item)

    def get_blocking(self) -> Any:
        return self._queue.get()


_LIVE_FAKES: "weakref.WeakSet[FakeCodexClient]" = weakref.WeakSet()


@pytest.fixture(autouse=True)
async def _close_fake_clients() -> AsyncIterator[None]:
    """Unpark worker threads still reading a fake client so the test loop's
    executor can shut down."""
    yield
    for fake in list(_LIVE_FAKES):
        fake.close()


class FakeCodexClient:
    def __init__(self) -> None:
        _LIVE_FAKES.add(self)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.notifications = NotificationQueue()
        self.global_notifications = NotificationQueue()
        self.approval_handler: (
            Callable[[str, dict[str, Any] | None], dict[str, Any]] | None
        ) = None
        self.started = False
        self.initialized = False
        self.closed = False
        self.start_model: str | None = None
        self.turn_notification_ids: list[str] = []
        self.unregistered_turn_notification_ids: list[str] = []
        self.child_threads: dict[str, Any] = {}
        self.paginated_reads_error: Exception | None = None
        self.skill_payload: dict[str, Any] = {
            "description": "Humanize prose",
            "enabled": True,
            "name": "humanizer",
            "path": "/tmp/work/.codex/skills/humanizer/SKILL.md",
            "pluginId": None,
            "scope": "repo",
            "shortDescription": "Humanize",
        }

    def start(self) -> None:
        self.started = True

    def initialize(self) -> None:
        self.initialized = True

    def close(self) -> None:
        self.closed = True
        self.notifications.put_nowait(_CLOSED)
        self.global_notifications.put_nowait(_CLOSED)

    def thread_start(self, params: dict[str, Any]) -> FakeStartResponse:
        self.calls.append(("thread_start", (params,)))
        return FakeStartResponse(FakeThread(id="thread-1"), model=self.start_model)

    def thread_resume(self, thread_id: str) -> dict[str, Any]:
        self.calls.append(("thread_resume", (thread_id,)))
        return {"thread_id": thread_id}

    def turn_start(
        self,
        thread_id: str,
        text: Any,
        params: dict[str, Any] | None = None,
    ) -> FakeTurnStartResponse:
        if params is None:
            self.calls.append(("turn_start", (thread_id, text)))
        else:
            self.calls.append(("turn_start", (thread_id, text, params)))
        return FakeTurnStartResponse(FakeTurn(id="turn-1"))

    def model_list(self, include_hidden: bool = False) -> Any:
        self.calls.append(("model_list", (include_hidden,)))
        return SimpleNamespace(data=[], next_cursor=None)

    def turn_steer(self, thread_id: str, turn_id: str, text: Any) -> None:
        self.calls.append(("turn_steer", (thread_id, turn_id, text)))

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        response_model,
    ) -> Any:
        self.calls.append(("request", (method, params)))
        if method == "skills/list":
            return response_model.model_validate(
                {
                    "data": [
                        {
                            "cwd": "/tmp/work",
                            "errors": [],
                            "skills": [self.skill_payload],
                        }
                    ]
                }
            )
        if method in {"thread/turns/list", "thread/items/list"}:
            if self.paginated_reads_error is not None:
                raise self.paginated_reads_error
            turns = self._child_thread(params["threadId"]).turns
            if method == "thread/turns/list":
                return SimpleNamespace(data=turns[-1:])
            [turn] = [turn for turn in turns if turn.id == params["turnId"]]
            return SimpleNamespace(
                data=[
                    SimpleNamespace(item=entry.root) for entry in reversed(turn.items)
                ]
            )
        raise AssertionError(f"unexpected request: {method}")

    def turn_interrupt(self, thread_id: str, turn_id: str) -> None:
        self.calls.append(("turn_interrupt", (thread_id, turn_id)))

    def thread_read(self, thread_id: str, include_turns: bool = False) -> Any:
        self.calls.append(("thread_read", (thread_id, include_turns)))
        return SimpleNamespace(thread=self._child_thread(thread_id))

    def _child_thread(self, thread_id: str) -> Any:
        thread = self.child_threads.get(thread_id)
        if isinstance(thread, BaseException):
            raise thread
        if callable(thread):
            thread = thread()
        if thread is None:
            raise RuntimeError(f"no thread {thread_id}")
        return thread

    def next_notification(self) -> Any:
        return self._next_notification(self.global_notifications)

    def next_turn_notification(self, turn_id: str) -> Any:
        self.turn_notification_ids.append(turn_id)
        return self._next_notification(self.notifications)

    def unregister_turn_notifications(self, turn_id: str) -> None:
        self.unregistered_turn_notification_ids.append(turn_id)

    def _next_notification(self, source: NotificationQueue) -> Any:
        # Synchronous calls in adapter go through asyncio.to_thread so this
        # blocks the worker thread until a notification is enqueued; close()
        # unblocks it the way the real client fails pending reads.
        notification = source.get_blocking()
        if notification is _CLOSED:
            source.put_nowait(_CLOSED)
            raise RuntimeError("client closed")
        return notification


@dataclass
class FakeNotification:
    method: str
    payload: dict[str, Any]


def make_adapter(
    emitted: list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]],
):
    async def emit(session_id, kind, text, metadata, status):
        emitted.append((session_id, kind, text, metadata, status))

    fake = FakeCodexClient()

    def factory(cwd, approval_handler):
        fake.approval_handler = approval_handler
        fake.calls.append(("factory", (cwd,)))
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
    assert fake.calls[0] == ("factory", ("/tmp/work",))
    assert fake.calls[1][0] == "thread_start"


@pytest.mark.asyncio
async def test_start_session_uses_explicit_cwd() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    thread_id = await adapter.start_session("sess", "~/remote-work")

    assert thread_id == "thread-1"
    assert fake.calls[0] == ("factory", ("~/remote-work",))
    assert fake.calls[1] == ("thread_start", ({"cwd": "~/remote-work"},))


@pytest.mark.asyncio
async def test_start_session_uses_factory_override() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)

    override = FakeCodexClient()

    def override_factory(cwd, approval_handler):
        override.approval_handler = approval_handler
        override.calls.append(("override_factory", (cwd,)))
        return override

    thread_id = await adapter.start_session("sess", "~/remote-work", override_factory)

    assert thread_id == "thread-1"
    assert fake.calls == []
    assert override.calls[0] == ("override_factory", ("~/remote-work",))
    assert override.calls[1] == ("thread_start", ({"cwd": "~/remote-work"},))


@pytest.mark.asyncio
async def test_start_session_passes_model_to_thread_start() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)

    thread_id = await adapter.start_session("sess", "/tmp/work", model="gpt-5")

    assert thread_id == "thread-1"
    assert fake.calls[1] == ("thread_start", ({"cwd": "/tmp/work", "model": "gpt-5"},))
    assert adapter.session_model("sess") == "gpt-5"


@pytest.mark.asyncio
async def test_start_session_remembers_app_server_default_model() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    fake.start_model = "gpt-5.3-codex"

    await adapter.start_session("sess", "/tmp/work")

    assert adapter.session_model("sess") == "gpt-5.3-codex"


@pytest.mark.asyncio
async def test_set_model_persists_and_re_emits_on_turn_start() -> None:
    """Codex model is a per-turn override that the SDK persists; waypoint must
    still re-emit it on every turn_start so a restart can't drop it."""
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    state = adapter._sessions["sess"]
    assert adapter.session_model("sess") is None

    await adapter.set_model("sess", "gpt-5")
    assert adapter.session_model("sess") == "gpt-5"

    await adapter.send_input("sess", "hello")
    turn_calls = [call for call in fake.calls if call[0] == "turn_start"]
    assert turn_calls == [
        ("turn_start", (state.thread_id, "hello", {"model": "gpt-5"})),
    ]
    if state.stream_task:
        fake.close()
        state.stream_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await state.stream_task


@pytest.mark.asyncio
async def test_send_input_per_turn_params_override_session_model() -> None:
    """Caller-supplied turn_params win over the sticky session model."""
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work", model="gpt-5")
    state = adapter._sessions["sess"]

    await adapter.send_input("sess", "hi", {"model": "gpt-5-fast"})
    turn_calls = [call for call in fake.calls if call[0] == "turn_start"]
    assert turn_calls[-1] == (
        "turn_start",
        (state.thread_id, "hi", {"model": "gpt-5-fast"}),
    )
    if state.stream_task:
        fake.close()
        state.stream_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await state.stream_task


@pytest.mark.asyncio
async def test_list_models_uses_transient_client() -> None:
    """Discovery spawns a fresh client and closes it after the model_list
    round-trip."""
    emitted: list = []
    adapter, _ = make_adapter(emitted)

    transient = FakeCodexClient()

    def transient_factory(cwd, approval_handler):
        transient.approval_handler = approval_handler
        transient.calls.append(("transient_factory", (cwd,)))
        return transient

    response = await adapter.list_models(
        cwd="~/proj",
        client_factory_override=transient_factory,
        include_hidden=True,
    )

    assert transient.started and transient.initialized and transient.closed
    assert ("model_list", (True,)) in transient.calls
    assert response.data == []


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
        fake.close()
        state.stream_task.cancel()
        try:
            await state.stream_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_list_skills_omits_null_plugin_id_and_requests_current_session_cwd() -> (
    None
):
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")

    skills = await adapter.list_skills("sess", force_reload=True)

    assert fake.calls[-1] == (
        "request",
        ("skills/list", {"cwds": ["/tmp/work"], "forceReload": True}),
    )
    assert skills == [
        {
            "dependencies": None,
            "description": "Humanize prose",
            "enabled": True,
            "interface": None,
            "name": "humanizer",
            "path": "/tmp/work/.codex/skills/humanizer/SKILL.md",
            "scope": "repo",
            "shortDescription": "Humanize",
        }
    ]


@pytest.mark.asyncio
async def test_list_skills_preserves_plugin_id() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    fake.skill_payload["pluginId"] = "waypoint-plugin"
    await adapter.start_session("sess", "/tmp/work")

    skills = await adapter.list_skills("sess")

    assert skills[0]["pluginId"] == "waypoint-plugin"


@pytest.mark.asyncio
async def test_send_input_items_starts_structured_turn() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    state = adapter._sessions["sess"]
    items = [
        {"type": "skill", "name": "humanizer", "path": "/tmp/SKILL.md"},
        {"type": "text", "text": "please rewrite"},
    ]

    await adapter.send_input_items("sess", items)

    assert ("turn_start", (state.thread_id, items)) in fake.calls
    if state.stream_task is not None:
        fake.close()
        state.stream_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await state.stream_task


@pytest.mark.asyncio
async def test_send_input_items_accepts_per_turn_params() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    state = adapter._sessions["sess"]
    items = [{"type": "text", "text": "plan this"}]
    params = {"collaborationMode": {"mode": "plan"}}

    await adapter.send_input_items("sess", items, turn_params=params)

    assert ("turn_start", (state.thread_id, items, params)) in fake.calls
    if state.stream_task is not None:
        fake.close()
        state.stream_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await state.stream_task


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
async def test_send_input_after_interrupt_starts_fresh_turn() -> None:
    # Reproduces the issue #12 flow programmatically: interrupt clears the
    # in-flight turn via turn/completed[status=interrupted], the session state
    # stays put, and the next send_input takes the turn_start branch (not
    # turn_steer) because active_turn_id was reset.
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    state = adapter._sessions["sess"]

    await adapter.send_input("sess", "first")
    assert state.active_turn_id == "turn-1"
    assert state.stream_task is not None

    await adapter.interrupt("sess")
    assert ("turn_interrupt", ("thread-1", "turn-1")) in fake.calls

    fake.notifications.put_nowait(
        FakeNotification(
            "turn/completed",
            {"turn": {"id": "turn-1", "status": "interrupted"}},
        )
    )
    await state.stream_task

    assert state.active_turn_id is None
    assert state.stream_task is None
    assert "sess" in adapter._sessions
    assert fake.turn_notification_ids == ["turn-1"]
    assert fake.unregistered_turn_notification_ids == ["turn-1"]

    fake.calls.clear()
    fake.turn_notification_ids.clear()
    fake.unregistered_turn_notification_ids.clear()
    await adapter.send_input("sess", "second")

    methods = [call[0] for call in fake.calls]
    assert methods.count("turn_start") == 1
    assert methods.count("turn_steer") == 0
    assert state.active_turn_id == "turn-1"

    if state.stream_task is not None:
        fake.close()
        state.stream_task.cancel()
        try:
            await state.stream_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_respond_to_approval_resolves_pending() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    state = adapter._sessions["sess"]
    from waypoint.backends.codex.adapter import PendingApproval

    pending = PendingApproval(
        method="item/commandExecution/requestApproval", params={"command": "ls"}
    )
    state.pending_approval = pending
    handled = await adapter.respond_to_approval("sess", "approve")
    assert handled is True
    assert pending.event.is_set()
    assert pending.response == {"decision": "accept"}


class ParkedStreamClient(FakeCodexClient):
    """A client whose turn stream blocks until closed, like a long reasoning
    item with no notifications."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def next_turn_notification(self, turn_id: str) -> Any:
        self.turn_notification_ids.append(turn_id)
        self.release.wait()
        raise RuntimeError("client closed")

    def close(self) -> None:
        super().close()
        self.release.set()


def make_parked_adapter(
    emitted: list | None = None,
) -> tuple[CodexAppServerAdapter, ParkedStreamClient]:
    async def emit(session_id, kind, text, metadata, status):
        if emitted is not None:
            emitted.append((session_id, kind, text, metadata, status))

    fake = ParkedStreamClient()

    def factory(cwd, approval_handler):
        fake.approval_handler = approval_handler
        return fake

    return CodexAppServerAdapter(emit, client_factory=factory), fake


@pytest.mark.asyncio
async def test_steer_and_interrupt_do_not_wait_for_a_parked_stream() -> None:
    adapter, fake = make_parked_adapter()
    await adapter.start_session("sess", "/tmp/work")
    await adapter.send_input("sess", "start")
    await asyncio.sleep(0.05)
    assert fake.turn_notification_ids == ["turn-1"]

    await asyncio.wait_for(adapter.send_input("sess", "steer"), timeout=2)
    await asyncio.wait_for(adapter.interrupt("sess"), timeout=2)

    names = [call[0] for call in fake.calls]
    assert names[-2:] == ["turn_steer", "turn_interrupt"]
    assert await adapter.terminate_session("sess") is True


@pytest.mark.asyncio
async def test_interrupt_cancels_a_pending_approval_first() -> None:
    emitted: list = []
    adapter, fake = make_parked_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    await adapter.send_input("sess", "start")
    state = adapter._sessions["sess"]
    interrupt_saw_resolved: list[bool] = []

    def turn_interrupt(thread_id: str, turn_id: str) -> None:
        pending = state.pending_approval
        interrupt_saw_resolved.append(pending is None or pending.event.is_set())

    fake.turn_interrupt = turn_interrupt  # type: ignore[method-assign]
    # The SDK calls the approval handler on its reader thread and blocks there.
    handler = fake.approval_handler
    assert handler is not None
    reader: asyncio.Task[dict[str, Any]] = asyncio.create_task(
        asyncio.to_thread(
            handler,
            "item/commandExecution/requestApproval",
            {"itemId": "cmd-1", "command": "ls"},
        )
    )
    while state.pending_approval is None:
        await asyncio.sleep(0.01)

    await asyncio.wait_for(adapter.interrupt("sess"), timeout=2)

    assert await asyncio.wait_for(reader, timeout=2) == {"decision": "cancel"}
    assert interrupt_saw_resolved == [True]
    assert state.pending_approval is None
    assert [
        metadata["method"]
        for _, kind, _, metadata, _ in emitted
        if kind == EventKind.SYSTEM_NOTE
    ] == ["approval.invalidated"]
    # A late approve on the already-cancelled request is a no-op.
    assert await adapter.respond_to_approval("sess", "approve") is False
    await adapter.terminate_session("sess")


@pytest.mark.asyncio
async def test_interrupt_tolerates_no_active_turn_after_approval_cancel() -> None:
    adapter, fake = make_parked_adapter()
    await adapter.start_session("sess", "/tmp/work")
    await adapter.send_input("sess", "start")
    state = adapter._sessions["sess"]

    def turn_interrupt(thread_id: str, turn_id: str) -> None:
        raise RuntimeError("no active turn to interrupt")

    fake.turn_interrupt = turn_interrupt  # type: ignore[method-assign]
    handler = fake.approval_handler
    assert handler is not None
    reader: asyncio.Task[dict[str, Any]] = asyncio.create_task(
        asyncio.to_thread(
            handler,
            "item/fileChange/requestApproval",
            {"itemId": "patch-1"},
        )
    )
    while state.pending_approval is None:
        await asyncio.sleep(0.01)

    await asyncio.wait_for(adapter.interrupt("sess"), timeout=2)
    assert await asyncio.wait_for(reader, timeout=2) == {"decision": "cancel"}

    # Without an approval to cancel, an interrupt failure still surfaces.
    with pytest.raises(RuntimeError):
        await adapter.interrupt("sess")
    await adapter.terminate_session("sess")


@pytest.mark.asyncio
async def test_respond_to_approval_returns_false_when_idle() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    handled = await adapter.respond_to_approval("sess", "approve")
    assert handled is False


def _compaction_notifications(turn_id: str) -> list[FakeNotification]:
    item = {"type": "contextCompaction", "id": "cc1"}
    payload = {"item": item, "threadId": "thread-1", "turnId": turn_id}
    return [
        FakeNotification(
            "turn/started", {"threadId": "thread-1", "turn": {"id": turn_id}}
        ),
        FakeNotification("item/started", dict(payload)),
        FakeNotification("item/completed", dict(payload)),
        FakeNotification(
            "turn/completed",
            {"threadId": "thread-1", "turn": {"id": turn_id, "status": "completed"}},
        ),
    ]


def _patch_compaction_turn(
    monkeypatch: pytest.MonkeyPatch,
    turn_id: str | None,
    gate: threading.Event | None = None,
) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    def start(
        client: Any, thread_id: str, timeout: float, cancelled: Any = None
    ) -> str | None:
        calls.append((thread_id, timeout))
        if gate is not None:
            gate.wait(timeout=2)
        return turn_id

    monkeypatch.setattr(adapter_module, "start_compaction_turn", start)
    return calls


@pytest.mark.asyncio
async def test_compact_thread_streams_the_compaction_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    calls = _patch_compaction_turn(monkeypatch, "turn-c")
    for notification in _compaction_notifications("turn-c"):
        fake.notifications.put_nowait(notification)

    await adapter.compact_thread("sess")
    state = adapter._sessions["sess"]
    assert state.compacting is True
    stream = state.stream_task
    assert stream is not None
    await asyncio.wait_for(stream, timeout=2)

    assert calls == [("thread-1", adapter_module.COMPACTION_TURN_TIMEOUT_SECONDS)]
    assert fake.turn_notification_ids[0] == "turn-c"
    texts = [entry[2] for entry in emitted]
    assert "Compacting context" in texts
    assert "Context compacted" in texts
    assert emitted[-1][4] is SessionStatus.IDLE
    assert state.compacting is False
    assert state.active_turn_id is None


@pytest.mark.asyncio
async def test_input_during_compaction_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    _patch_compaction_turn(monkeypatch, "turn-c")
    await adapter.compact_thread("sess")

    with pytest.raises(CodexCompactingError):
        await adapter.send_input("sess", "hello")
    with pytest.raises(CodexCompactingError):
        await adapter.send_input_items("sess", [{"type": "text", "text": "hi"}])
    with pytest.raises(RuntimeError, match="already compacting"):
        await adapter.compact_thread("sess")
    assert all(call[0] not in {"turn_start", "turn_steer"} for call in fake.calls)


@pytest.mark.asyncio
async def test_interrupt_during_compaction_interrupts_its_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    gate = threading.Event()
    _patch_compaction_turn(monkeypatch, "turn-c", gate)
    await adapter.compact_thread("sess")
    state = adapter._sessions["sess"]

    # Before the turn id is known the interrupt is held, then sent on capture.
    await adapter.interrupt("sess")
    assert state.interrupt_pending is True
    gate.set()
    for _ in range(100):
        if ("turn_interrupt", ("thread-1", "turn-c")) in fake.calls:
            break
        await asyncio.sleep(0.01)
    assert ("turn_interrupt", ("thread-1", "turn-c")) in fake.calls
    assert state.interrupt_pending is False

    await adapter.interrupt("sess")
    assert fake.calls.count(("turn_interrupt", ("thread-1", "turn-c"))) == 2


@pytest.mark.asyncio
async def test_compaction_without_a_turn_settles_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list = []
    adapter, _ = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    _patch_compaction_turn(monkeypatch, None)
    await adapter.compact_thread("sess")
    state = adapter._sessions["sess"]
    stream = state.stream_task
    assert stream is not None
    await asyncio.wait_for(stream, timeout=2)

    assert emitted[-1][2] == "Compaction started; progress is unavailable"
    assert emitted[-1][4] is SessionStatus.IDLE
    assert state.compacting is False
    await adapter.send_input("sess", "next")


@pytest.mark.asyncio
async def test_compact_thread_rejects_when_turn_active() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    adapter._sessions["sess"].active_turn_id = "turn-9"

    with pytest.raises(
        RuntimeError, match="cannot compact while a codex turn is active"
    ):
        await adapter.compact_thread("sess")


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
    assert fake.closed is True
    # turn_interrupt must NOT be issued during termination — the in-flight
    # next_*_notification holds the transport lock, so doing so deadlocks. The
    # client.close() above is what unblocks the streaming task.
    assert all(call[0] != "turn_interrupt" for call in fake.calls)


@pytest.mark.asyncio
async def test_terminate_session_returns_false_for_unknown_id() -> None:
    emitted: list = []
    adapter, _ = make_adapter(emitted)
    handled = await adapter.terminate_session("missing")
    assert handled is False


@pytest.mark.asyncio
async def test_streamed_command_completed_carries_outcome_for_telemetry() -> None:
    emitted: list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]] = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")

    fake.notifications.put_nowait(
        FakeNotification(
            "item/commandExecution/outputDelta",
            {"itemId": "cmd-1", "delta": "line one\n"},
        )
    )
    fake.notifications.put_nowait(
        FakeNotification(
            "item/completed",
            {
                "item": {
                    "id": "cmd-1",
                    "type": "commandExecution",
                    "command": "pytest",
                    "aggregatedOutput": "line one\n",
                    "status": "completed",
                    "exitCode": 0,
                }
            },
        )
    )
    fake.notifications.put_nowait(
        FakeNotification(
            "turn/completed",
            {"turn": {"id": "turn-1", "status": "completed"}},
        )
    )

    await adapter.send_input("sess", "run pytest")
    state = adapter._sessions["sess"]
    if state.stream_task is not None:
        await state.stream_task

    tool_results = [entry for entry in emitted if entry[1] == EventKind.TOOL_RESULT]
    # The streamed delta still renders output; the completed event is kept
    # (not suppressed) because it carries the terminal outcome telemetry needs.
    # The frontend merges the pair by item_id, so this is not a visible dup.
    assert len(tool_results) == 2
    assert tool_results[0][2] == "line one\n"
    assert tool_results[0][3]["method"] == "item/commandExecution/outputDelta"
    assert "is_error" not in tool_results[0][3]
    completed = tool_results[1][3]
    assert completed["method"] == "item/completed"
    assert completed["item_id"] == "cmd-1"
    assert completed["is_error"] is False
    assert fake.turn_notification_ids == ["turn-1", "turn-1", "turn-1"]
    assert fake.unregistered_turn_notification_ids == ["turn-1"]


@pytest.mark.asyncio
async def test_streamed_command_completed_without_outcome_stays_suppressed() -> None:
    emitted: list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]] = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")

    fake.notifications.put_nowait(
        FakeNotification(
            "item/commandExecution/outputDelta",
            {"itemId": "cmd-1", "delta": "line one\n"},
        )
    )
    fake.notifications.put_nowait(
        FakeNotification(
            "item/completed",
            {
                "item": {
                    "id": "cmd-1",
                    "type": "commandExecution",
                    "command": "pytest",
                    "aggregatedOutput": "line one\n",
                    # No terminal status/exitCode → no outcome → still a pure
                    # transcript duplicate, so it stays suppressed.
                    "status": "inProgress",
                }
            },
        )
    )
    fake.notifications.put_nowait(
        FakeNotification(
            "turn/completed",
            {"turn": {"id": "turn-1", "status": "completed"}},
        )
    )

    await adapter.send_input("sess", "run pytest")
    state = adapter._sessions["sess"]
    if state.stream_task is not None:
        await state.stream_task

    tool_results = [entry for entry in emitted if entry[1] == EventKind.TOOL_RESULT]
    assert len(tool_results) == 1
    assert tool_results[0][2] == "line one\n"


@pytest.mark.asyncio
async def test_streamed_file_change_completed_preserves_diff_preview() -> None:
    emitted: list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]] = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")

    fake.notifications.put_nowait(
        FakeNotification(
            "item/fileChange/outputDelta",
            {
                "itemId": "file-1",
                "delta": "Success. Updated the following files:\nM app.py\n",
            },
        )
    )
    fake.notifications.put_nowait(
        FakeNotification(
            "item/completed",
            {
                "item": {
                    "id": "file-1",
                    "type": "fileChange",
                    "status": "completed",
                    "changes": [
                        {
                            "path": "app.py",
                            "kind": "update",
                            "diff": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
                        }
                    ],
                }
            },
        )
    )
    fake.notifications.put_nowait(
        FakeNotification(
            "turn/completed",
            {"turn": {"id": "turn-1", "status": "completed"}},
        )
    )

    await adapter.send_input("sess", "edit app.py")
    state = adapter._sessions["sess"]
    if state.stream_task is not None:
        await state.stream_task

    tool_results = [entry for entry in emitted if entry[1] == EventKind.TOOL_RESULT]
    assert len(tool_results) == 2
    assert tool_results[1][3]["diff_preview"]["files"][0]["path"] == "app.py"


@pytest.mark.asyncio
async def test_turn_plan_updated_emits_todo_list_event() -> None:
    """Codex's update_plan plan is normalised into a canonical todo_list event
    (TOOL_RESULT + item_type), keyed by turnId, so the shared todo dock/card
    renders it like any other backend."""
    emitted: list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]] = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")

    fake.notifications.put_nowait(
        FakeNotification(
            "turn/plan/updated",
            {
                "turnId": "turn-1",
                "explanation": "Dummy plan.",
                "plan": [
                    {"step": "First task", "status": "completed"},
                    {"step": "Second task", "status": "inProgress"},
                    {"step": "Third task", "status": "pending"},
                ],
            },
        )
    )
    fake.notifications.put_nowait(
        FakeNotification(
            "turn/completed",
            {"turn": {"id": "turn-1", "status": "completed"}},
        )
    )

    await adapter.send_input("sess", "make a plan")
    state = adapter._sessions["sess"]
    if state.stream_task is not None:
        await state.stream_task

    todo_events = [
        entry for entry in emitted if entry[3].get("item_type") == "todo_list"
    ]
    assert len(todo_events) == 1
    kind, _text, metadata = todo_events[0][1], todo_events[0][2], todo_events[0][3]
    assert kind == EventKind.TOOL_RESULT
    assert metadata["item_id"] == "turn-1"
    assert metadata["payload"]["item"]["items"] == [
        {"text": "First task", "status": "completed"},
        {"text": "Second task", "status": "in_progress"},
        {"text": "Third task", "status": "pending"},
    ]


def test_plan_todo_items_maps_codex_statuses() -> None:
    from waypoint.backends.codex.normalize import plan_todo_items

    items = plan_todo_items(
        [
            {"step": "a", "status": "completed"},
            {"step": "b", "status": "inProgress"},
            {"step": "c", "status": "pending"},
            {"step": "d", "status": "weird"},
            "not-a-dict",
        ]
    )
    assert items == [
        {"text": "a", "status": "completed"},
        {"text": "b", "status": "in_progress"},
        {"text": "c", "status": "pending"},
        {"text": "d", "status": "pending"},
    ]


def test_map_notification_turn_plan_updated_is_todo_result() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    kind, text, status = map_notification(
        "turn/plan/updated",
        {"plan": [{"step": "a", "status": "completed"}], "turnId": "t1"},
    )
    assert kind == EventKind.TOOL_RESULT
    assert text == "- a [completed]"
    assert status == SessionStatus.RUNNING


def test_map_notification_agent_message_delta() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    kind, text, status = map_notification(
        "item/agentMessage/delta",
        {"delta": "hello"},
    )
    assert kind == EventKind.AGENT_OUTPUT
    assert text == "hello"
    assert status == SessionStatus.RUNNING


def test_map_notification_command_execution_started() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    kind, text, status = map_notification(
        "item/started",
        {"item": {"type": "commandExecution", "command": "ls -la"}},
    )
    assert kind == EventKind.TOOL_CALL
    assert "ls -la" in text


def test_map_notification_collab_spawn_started_surfaces_prompt() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    kind, text, status = map_notification(
        "item/started",
        {
            "item": {
                "type": "collabAgentToolCall",
                "tool": "spawnAgent",
                "prompt": "Review the worktree diff for correctness.",
                "agentsStates": {"t1": {"message": None, "status": "pendingInit"}},
            }
        },
    )
    assert kind == EventKind.TOOL_CALL
    assert text.startswith("spawnAgent")
    assert "Review the worktree diff for correctness." in text


def test_map_notification_collab_wait_completed_surfaces_subagent_report() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    kind, text, status = map_notification(
        "item/completed",
        {
            "item": {
                "type": "collabAgentToolCall",
                "tool": "wait",
                "prompt": None,
                "agentsStates": {
                    "t1": {"message": "**Verdict:** Looks good", "status": "completed"}
                },
            }
        },
    )
    assert kind == EventKind.TOOL_RESULT
    assert text.startswith("wait")
    assert "**Verdict:** Looks good" in text


def test_map_notification_collab_wait_without_message_falls_back_to_tool_name() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    kind, text, status = map_notification(
        "item/started",
        {"item": {"type": "collabAgentToolCall", "tool": "wait", "agentsStates": {}}},
    )
    assert kind == EventKind.TOOL_CALL
    assert text == "wait"


def test_map_notification_collab_completed_without_message_falls_back() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    kind, text, status = map_notification(
        "item/completed",
        {
            "item": {
                "type": "collabAgentToolCall",
                "tool": "spawnAgent",
                "agentsStates": {"t1": {"message": None, "status": "pendingInit"}},
            }
        },
    )
    assert kind == EventKind.TOOL_RESULT
    assert text == "spawnAgent"


def test_map_notification_collab_wait_joins_multiple_subagent_reports() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    kind, text, status = map_notification(
        "item/completed",
        {
            "item": {
                "type": "collabAgentToolCall",
                "tool": "wait",
                "agentsStates": {
                    "t1": {"message": "first report", "status": "completed"},
                    "t2": {"message": "second report", "status": "completed"},
                },
            }
        },
    )
    assert kind == EventKind.TOOL_RESULT
    assert text == "wait\n\nfirst report\n\nsecond report"


def test_map_notification_file_change_patch_updated_has_preview() -> None:
    from waypoint.backends.codex.event_registry import map_notification
    from waypoint.backends.codex.normalize import diff_preview_for_notification

    payload = {
        "itemId": "item_1",
        "changes": [
            {
                "path": "app.py",
                "kind": {"type": "update", "move_path": None},
                "diff": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
            }
        ],
    }

    kind, text, status = map_notification("item/fileChange/patchUpdated", payload)
    preview = diff_preview_for_notification("item/fileChange/patchUpdated", payload)

    assert kind == EventKind.TOOL_RESULT
    assert text == "File changes updated: app.py"
    assert status == SessionStatus.RUNNING
    assert preview is not None
    assert preview.phase == "proposed"
    assert preview.files[0].path == "app.py"
    assert preview.files[0].change_type == "update"
    assert preview.total_additions == 1
    assert preview.total_deletions == 1


def test_codex_file_change_preview_handles_add_and_delete_content() -> None:
    from waypoint.backends.codex.normalize import diff_preview_for_notification

    preview = diff_preview_for_notification(
        "item/fileChange/patchUpdated",
        {
            "itemId": "item_1",
            "changes": [
                {
                    "path": "created.py",
                    "kind": {"type": "add"},
                    "diff": "print('created')\n",
                },
                {
                    "path": "removed.py",
                    "kind": {"type": "delete"},
                    "diff": "print('removed')\n",
                },
            ],
        },
    )

    assert preview is not None
    assert preview.files[0].path == "created.py"
    assert preview.files[0].change_type == "add"
    assert preview.files[0].additions == 1
    assert preview.files[1].path == "removed.py"
    assert preview.files[1].change_type == "delete"
    assert preview.files[1].deletions == 1


def test_codex_file_change_preview_infers_type_from_unified_diff() -> None:
    from waypoint.backends.codex.normalize import diff_preview_for_notification

    preview = diff_preview_for_notification(
        "item/fileChange/patchUpdated",
        {
            "itemId": "item_1",
            "changes": [
                {
                    "path": "app.py",
                    "diff": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
                }
            ],
        },
    )

    assert preview is not None
    assert preview.files[0].change_type == "update"


def test_codex_apply_patch_approval_preview_handles_legacy_file_changes() -> None:
    from waypoint.backends.codex.normalize import diff_preview_for_approval

    preview = diff_preview_for_approval(
        "applyPatchApproval",
        {
            "fileChanges": {
                "app.py": {
                    "type": "update",
                    "unified_diff": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
                    "move_path": None,
                }
            }
        },
    )

    assert preview is not None
    assert preview.phase == "proposed"
    assert preview.files[0].path == "app.py"
    assert preview.files[0].change_type == "update"


def test_map_notification_todo_list_updated() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    kind, text, status = map_notification(
        "item/updated",
        {
            "item": {
                "type": "todo_list",
                "items": [
                    {"text": "Inspect session events", "completed": True},
                    {"text": "Render spinner", "completed": False},
                ],
            }
        },
    )
    assert kind == EventKind.TOOL_RESULT
    assert text == "[x] Inspect session events\n[ ] Render spinner"
    assert status == SessionStatus.RUNNING


def test_format_todo_list_renders_markers_and_skips_blanks() -> None:
    from waypoint.backends.codex.normalize import format_todo_list

    text = format_todo_list(
        {
            "items": [
                {"text": "First", "completed": True},
                {"text": "  ", "completed": False},
                {"text": "Second", "completed": False},
            ]
        }
    )
    assert text == "[x] First\n[ ] Second"


def test_format_todo_list_empty_returns_placeholder() -> None:
    from waypoint.backends.codex.normalize import format_todo_list

    assert format_todo_list({"items": []}) == "Todo list"
    assert format_todo_list({}) == "Todo list"


def test_format_item_started_routes_todo_list_as_tool_call() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    kind, text, status = map_notification(
        "item/started",
        {
            "item": {
                "type": "todo_list",
                "items": [{"text": "Step one", "completed": False}],
            }
        },
    )
    assert kind == EventKind.TOOL_CALL
    assert text == "[ ] Step one"
    assert status == SessionStatus.RUNNING


def test_format_item_completed_routes_todo_list_as_tool_result() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    kind, text, status = map_notification(
        "item/completed",
        {
            "item": {
                "type": "todo_list",
                "status": "completed",
                "items": [{"text": "Step one", "completed": True}],
            }
        },
    )
    assert kind == EventKind.TOOL_RESULT
    assert text == "[x] Step one"
    # Item completion does not signal end of turn; only turn/completed should
    # drop session status off RUNNING.
    assert status == SessionStatus.RUNNING


def test_format_item_completed_drops_agent_message_duplicate() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    kind, text, status = map_notification(
        "item/completed",
        {"item": {"type": "agentMessage", "text": "hello"}},
    )
    assert kind is None
    assert text == ""
    assert status == SessionStatus.RUNNING


def test_extract_item_id_pulls_top_level_and_nested_ids() -> None:
    from waypoint.backends.codex.normalize import extract_item_id

    assert extract_item_id({"itemId": "abc", "delta": "x"}) == "abc"
    assert extract_item_id({"item": {"id": "xyz", "type": "agentMessage"}}) == "xyz"
    assert extract_item_id({}) is None


def test_map_decision_table() -> None:
    adapter = CodexAppServerAdapter(lambda *_: None, client_factory=lambda *_: None)  # type: ignore[arg-type]
    assert adapter._map_decision("approve") == "accept"
    assert adapter._map_decision("y") == "accept"
    assert adapter._map_decision("acceptForSession") == "acceptForSession"
    assert adapter._map_decision("cancel") == "cancel"
    assert adapter._map_decision("anything-else") == "decline"


def test_context_usage_snapshot_uses_thread_token_usage_totals() -> None:
    snapshot = _context_usage_snapshot_from_thread_token_usage(
        {
            "tokenUsage": {
                "last": {
                    "totalTokens": 4096,
                    "inputTokens": 2048,
                    "cachedInputTokens": 256,
                    "outputTokens": 1536,
                    "reasoningOutputTokens": 256,
                },
                "modelContextWindow": 8192,
            }
        }
    )

    assert snapshot is not None
    assert snapshot.used_tokens == 4096
    assert snapshot.context_window_tokens == 8192
    assert snapshot.source == "codex"
    assert snapshot.breakdown == {
        "input_tokens": 2048,
        "cached_input_tokens": 256,
        "output_tokens": 1536,
        "reasoning_output_tokens": 256,
    }
    assert isinstance(snapshot.updated_at, datetime)
    assert snapshot.updated_at.tzinfo is UTC


@pytest.mark.asyncio
async def test_context_usage_snapshot_deduplicates_repeated_updates() -> None:
    calls: list[tuple[str, dict[str, Any], bool]] = []

    async def _emit(*args: object, **kwargs: object) -> None:
        return None

    async def on_session_update(
        session_id: str, updates: dict[str, Any], publish: bool
    ) -> Any:
        calls.append((session_id, updates, publish))
        return None

    fake = FakeCodexClient()
    adapter = CodexAppServerAdapter(
        _emit,
        on_session_update=on_session_update,
        client_factory=lambda *_: cast(CodexClient, fake),
    )
    state = CodexSessionState(
        session_id="sess",
        cwd="/tmp",
        client=cast(CodexClient, fake),
        request_lock=asyncio.Lock(),
        thread_id="thread-1",
    )
    snapshot = _context_usage_snapshot_from_thread_token_usage(
        {
            "tokenUsage": {
                "last": {
                    "totalTokens": 4096,
                    "inputTokens": 2048,
                    "cachedInputTokens": 256,
                    "outputTokens": 1536,
                    "reasoningOutputTokens": 256,
                },
                "modelContextWindow": 8192,
            }
        }
    )
    assert snapshot is not None

    await adapter._publish_context_usage(state, snapshot)
    await adapter._publish_context_usage(state, snapshot)

    assert calls == [
        ("sess", {"context_usage": snapshot.model_dump(mode="json")}, True)
    ]


@pytest.mark.asyncio
async def test_token_usage_record_uses_turn_id_and_provider_total() -> None:
    records: list[tuple[str, Any, bool]] = []

    async def _emit(*args: object, **kwargs: object) -> None:
        return None

    async def on_token_usage(session_id: str, record: Any, publish: bool) -> Any:
        records.append((session_id, record, publish))
        return None

    fake = FakeCodexClient()
    adapter = CodexAppServerAdapter(
        _emit,
        on_token_usage=on_token_usage,
        client_factory=lambda *_: cast(CodexClient, fake),
    )
    state = CodexSessionState(
        session_id="sess",
        cwd="/tmp",
        client=cast(CodexClient, fake),
        request_lock=asyncio.Lock(),
        thread_id="thread-1",
    )
    snapshot = _context_usage_snapshot_from_thread_token_usage(
        {
            "tokenUsage": {
                "last": {
                    "totalTokens": 4096,
                    "inputTokens": 2048,
                    "cachedInputTokens": 256,
                    "outputTokens": 1536,
                    "reasoningOutputTokens": 256,
                },
                "modelContextWindow": 8192,
            }
        }
    )
    assert snapshot is not None

    await adapter._publish_token_usage(state, "turn-7", snapshot)

    assert len(records) == 1
    session_id, record, _ = records[0]
    assert session_id == "sess"
    assert record.record_id == "turn-7"
    # cachedInputTokens is a subset of inputTokens, so the provider totalTokens
    # is the safe grand total — never the category sum.
    assert record.display_total_tokens == 4096
    assert record.totals["input_tokens"] == 2048
    assert record.totals["cached_input_tokens"] == 256

    # An empty turn id is never aggregated.
    records.clear()
    await adapter._publish_token_usage(state, "", snapshot)
    assert records == []


@pytest.mark.asyncio
async def test_token_usage_record_threads_sticky_model_and_effort() -> None:
    records: list[tuple[str, Any, bool]] = []

    async def _emit(*args: object, **kwargs: object) -> None:
        return None

    async def on_token_usage(session_id: str, record: Any, publish: bool) -> Any:
        records.append((session_id, record, publish))
        return None

    fake = FakeCodexClient()
    adapter = CodexAppServerAdapter(
        _emit,
        on_token_usage=on_token_usage,
        client_factory=lambda *_: cast(CodexClient, fake),
    )
    state = CodexSessionState(
        session_id="sess",
        cwd="/tmp",
        client=cast(CodexClient, fake),
        request_lock=asyncio.Lock(),
        thread_id="thread-1",
        model="gpt-5-codex",
        effort="high",
    )
    snapshot = _context_usage_snapshot_from_thread_token_usage(
        {
            "tokenUsage": {
                "last": {"totalTokens": 100, "inputTokens": 80, "outputTokens": 20},
                "modelContextWindow": 8192,
            }
        }
    )
    assert snapshot is not None

    await adapter._publish_token_usage(state, "turn-1", snapshot)

    assert len(records) == 1
    record = records[0][1]
    assert record.model == "gpt-5-codex"
    assert record.effort == "high"


def _async_question_item(item_id: str = "call_q1") -> dict[str, Any]:
    return {
        "type": "agentMessage",
        "id": item_id,
        "text": "Which identity?\n- A\n- B",
        "phase": "final_answer",
        "delivery": "async",
        "questions": [{"title": "Which identity?", "options": ["A", "B"]}],
    }


def _item_notification(method: str, item: dict[str, Any]) -> FakeNotification:
    return FakeNotification(
        method=method,
        payload={"item": item, "threadId": "thread-1", "turnId": "turn-1"},
    )


def _turn_completed() -> FakeNotification:
    return FakeNotification(
        method="turn/completed",
        payload={
            "threadId": "thread-1",
            "turn": {"id": "turn-1", "status": "completed"},
        },
    )


async def _run_turn(
    open_ids: set[str], *notifications: FakeNotification
) -> list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]]:
    emitted: list = []

    async def emit(session_id, kind, text, metadata, status):
        emitted.append((session_id, kind, text, metadata, status))

    fake = FakeCodexClient()
    adapter = CodexAppServerAdapter(
        emit,
        client_factory=lambda *_: cast(CodexClient, fake),
        open_question_ids=lambda session_id: open_ids,
    )
    await adapter.start_session("sess", "/tmp/work")
    await adapter.send_input("sess", "go")
    state = adapter._sessions["sess"]
    stream = state.stream_task
    assert stream is not None
    for notification in notifications:
        fake.notifications.put_nowait(notification)
    await asyncio.wait_for(stream, timeout=2)
    return emitted


@pytest.mark.asyncio
async def test_async_question_emits_one_question_card_and_keeps_running() -> None:
    item = _async_question_item()
    emitted = await _run_turn(
        set(),
        _item_notification("item/started", item),
        _item_notification("item/completed", item),
        _turn_completed(),
    )

    cards = [entry for entry in emitted if entry[1] == EventKind.TOOL_CALL]
    assert len(cards) == 1
    _, _, text, metadata, status = cards[0]
    assert text == "Need your input"
    assert status == SessionStatus.RUNNING
    assert metadata["tool_name"] == "AskUserQuestion"
    assert metadata["tool_use_id"] == "call_q1"
    assert metadata["payload"]["input"] == {
        "questions": [
            {"question": "Which identity?", "options": [{"label": "A"}, {"label": "B"}]}
        ]
    }
    assert metadata["payload"]["threadId"] == "thread-1"
    assert metadata["interaction"]["kind"] == "question"
    assert not [entry for entry in emitted if entry[1] == EventKind.AGENT_OUTPUT]


@pytest.mark.parametrize(
    ("open_ids", "expected"),
    [({"call_q1"}, SessionStatus.WAITING_INPUT), (set(), SessionStatus.IDLE)],
)
@pytest.mark.asyncio
async def test_turn_end_waits_only_on_its_own_open_question(
    open_ids: set[str], expected: SessionStatus
) -> None:
    item = _async_question_item()
    emitted = await _run_turn(
        open_ids, _item_notification("item/completed", item), _turn_completed()
    )
    assert emitted[-1][2] == "Turn completed"
    assert emitted[-1][4] == expected


@pytest.mark.asyncio
async def test_turn_end_ignores_open_questions_from_earlier_turns() -> None:
    emitted = await _run_turn({"call_old"}, _turn_completed())
    assert emitted[-1][4] == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_async_message_without_questions_is_agent_output() -> None:
    item = {
        "type": "agentMessage",
        "id": "msg_async",
        "text": "Still working on the migration.",
        "delivery": "async",
    }
    emitted = await _run_turn(
        set(),
        _item_notification("item/started", item),
        _item_notification("item/completed", item),
        _turn_completed(),
    )
    outputs = [entry for entry in emitted if entry[1] == EventKind.AGENT_OUTPUT]
    assert [entry[2] for entry in outputs] == ["Still working on the migration."]


@pytest.mark.asyncio
async def test_approval_raised_during_an_interrupt_is_cancelled_at_once() -> None:
    emitted: list = []
    adapter, fake = make_parked_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    await adapter.send_input("sess", "start")
    state = adapter._sessions["sess"]
    handler = fake.approval_handler
    assert handler is not None
    state.interrupting = True

    response = await asyncio.wait_for(
        asyncio.to_thread(
            handler, "item/commandExecution/requestApproval", {"itemId": "cmd-2"}
        ),
        timeout=2,
    )

    assert response == {"decision": "cancel"}
    assert state.pending_approval is None
    await asyncio.sleep(0.05)
    assert not [entry for entry in emitted if entry[1] == EventKind.APPROVAL_REQUEST]
    state.interrupting = False
    await adapter.terminate_session("sess")


def _codex_error(
    message: str, will_retry: bool | None, details: str | None = None
) -> FakeNotification:
    payload: dict[str, Any] = {
        "error": {"message": message, "additionalDetails": details},
        "threadId": "thread-1",
        "turnId": "turn-1",
    }
    if will_retry is not None:
        payload["willRetry"] = will_retry
    return FakeNotification("error", payload)


def _agent_delta(text: str) -> FakeNotification:
    return FakeNotification("item/agentMessage/delta", {"delta": text})


async def _run_turn_errors(
    *notifications: FakeNotification,
) -> list[tuple[str, SessionStatus]]:
    emitted = await _run_turn(set(), *notifications)
    return [
        (text, status)
        for _, _, text, metadata, status in emitted
        if metadata.get("method") == "error"
    ]


def test_map_notification_retryable_error_keeps_running_with_details() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    notification = _codex_error("Reconnecting... 2/5", True, "unauthorized (401)")
    kind, text, status = map_notification("error", notification.payload)

    assert kind == EventKind.SYSTEM_NOTE
    assert text == "Reconnecting... 2/5 — unauthorized (401)"
    assert status == SessionStatus.RUNNING


def test_map_notification_error_skips_details_repeating_message() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    notification = _codex_error("unauthorized (401)", False, "unauthorized (401)")
    _, text, _ = map_notification("error", notification.payload)

    assert text == "unauthorized (401)"


def test_map_notification_error_without_will_retry_is_error() -> None:
    from waypoint.backends.codex.event_registry import map_notification

    notification = _codex_error("boom", None)
    kind, text, status = map_notification("error", notification.payload)

    assert kind == EventKind.SYSTEM_NOTE
    assert text == "boom"
    assert status == SessionStatus.ERROR


@pytest.mark.asyncio
async def test_retry_streak_emits_one_running_note() -> None:
    errors = await _run_turn_errors(
        _codex_error("Reconnecting... 1/5", True, "unauthorized (401)"),
        _codex_error("Reconnecting... 2/5", True, "unauthorized (401)"),
        _codex_error("Reconnecting... 3/5", True, "unauthorized (401)"),
        _agent_delta("recovered"),
        _turn_completed(),
    )

    assert errors == [
        ("Reconnecting... 1/5 — unauthorized (401)", SessionStatus.RUNNING)
    ]


@pytest.mark.asyncio
async def test_retry_streaks_split_by_other_notifications() -> None:
    errors = await _run_turn_errors(
        _codex_error("Reconnecting... 1/5", True),
        _codex_error("Reconnecting... 2/5", True),
        _agent_delta("partial"),
        _codex_error("Reconnecting... 1/5", True),
        _codex_error("Reconnecting... 2/5", True),
        _turn_completed(),
    )

    assert errors == [
        ("Reconnecting... 1/5", SessionStatus.RUNNING),
        ("Reconnecting... 1/5", SessionStatus.RUNNING),
    ]


@pytest.mark.asyncio
async def test_retry_streak_ending_in_final_error_reports_error() -> None:
    errors = await _run_turn_errors(
        _codex_error("Reconnecting... 1/5", True, "unauthorized (401)"),
        _codex_error("Reconnecting... 5/5", True, "unauthorized (401)"),
        _codex_error("unauthorized (401)", False),
        FakeNotification(
            "turn/completed", {"turn": {"id": "turn-1", "status": "failed"}}
        ),
    )

    assert errors == [
        ("Reconnecting... 1/5 — unauthorized (401)", SessionStatus.RUNNING),
        ("unauthorized (401)", SessionStatus.ERROR),
    ]


def _reasoning(item_id: str = "rs1", **fields: Any) -> dict[str, Any]:
    return {"type": "reasoning", "id": item_id, "summary": [], "content": [], **fields}


def _turn_payload(**fields: Any) -> dict[str, Any]:
    return {"threadId": "thread-1", "turnId": "turn-1", **fields}


@pytest.mark.asyncio
async def test_streamed_reasoning_is_not_repeated_on_completion() -> None:
    item = _reasoning(summary=["Plan.", "Act."])
    emitted = await _run_turn(
        set(),
        _item_notification("item/started", _reasoning()),
        FakeNotification(
            "item/reasoning/summaryTextDelta",
            _turn_payload(itemId="rs1", delta="Plan.", summaryIndex=0),
        ),
        FakeNotification(
            "item/reasoning/summaryPartAdded",
            _turn_payload(itemId="rs1", summaryIndex=1),
        ),
        FakeNotification(
            "item/reasoning/summaryTextDelta",
            _turn_payload(itemId="rs1", delta="Act.", summaryIndex=1),
        ),
        _item_notification("item/completed", item),
        _turn_completed(),
    )
    thinking = [entry for entry in emitted if entry[3].get("item_kind") == "reasoning"]
    assert "".join(entry[2] for entry in thinking) == "Plan.\n\nAct."
    assert {entry[3]["item_id"] for entry in thinking} == {"rs1"}
    assert all(entry[1] is EventKind.AGENT_OUTPUT for entry in thinking)


@pytest.mark.asyncio
async def test_unstreamed_reasoning_summary_is_emitted_once_on_completion() -> None:
    emitted = await _run_turn(
        set(),
        _item_notification("item/completed", _reasoning(summary=["Only this."])),
        _turn_completed(),
    )
    thinking = [entry for entry in emitted if entry[3].get("item_kind") == "reasoning"]
    assert [entry[2] for entry in thinking] == ["Only this."]


@pytest.mark.asyncio
async def test_empty_reasoning_and_user_echo_stay_out_of_the_transcript() -> None:
    emitted = await _run_turn(
        set(),
        FakeNotification(
            "turn/started", {"threadId": "thread-1", "turn": {"id": "turn-1"}}
        ),
        _item_notification(
            "item/started", {"type": "userMessage", "id": "u1", "content": []}
        ),
        _item_notification(
            "item/completed", {"type": "userMessage", "id": "u1", "content": []}
        ),
        _item_notification("item/started", _reasoning()),
        _item_notification("item/completed", _reasoning()),
        _turn_completed(),
    )
    assert [(entry[2], entry[3].get("visibility")) for entry in emitted] == [
        ("Turn started", "detail"),
        ("Reasoning", "detail"),
        ("Turn completed", "detail"),
    ]


@pytest.mark.asyncio
async def test_generated_image_is_captured_and_not_persisted_inline() -> None:
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\nrest").decode()
    item = {
        "type": "imageGeneration",
        "id": "ig1",
        "status": "completed",
        "result": encoded,
        "revisedPrompt": "A circle",
        "savedPath": None,
        "failure": None,
    }
    emitted = await _run_turn(
        set(), _item_notification("item/completed", item), _turn_completed()
    )
    _, kind, text, metadata, _ = emitted[0]
    assert (kind, text) == (EventKind.TOOL_RESULT, "completed")
    assert metadata["tool_name"] == "ImageGeneration"
    assert metadata["payload"]["item"]["result"] == ""
    assert metadata["capture_inline_blobs"][0]["base64"] == encoded


@pytest.mark.asyncio
async def test_pump_emits_turnless_notifications_without_status() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    fake.global_notifications.put_nowait(
        FakeNotification("warning", {"message": "Other thread", "threadId": "t-x"})
    )
    fake.global_notifications.put_nowait(
        FakeNotification("warning", {"message": "Heads up", "threadId": "thread-1"})
    )
    fake.global_notifications.put_nowait(
        FakeNotification("thread/status/changed", {"threadId": "thread-1"})
    )
    for _ in range(100):
        if emitted:
            break
        await asyncio.sleep(0.01)
    assert len(emitted) == 1
    _, kind, text, metadata, status = emitted[0]
    assert (kind, text, status) == (EventKind.SYSTEM_NOTE, "Heads up", None)
    assert "status" not in metadata
    assert metadata["visibility"] == "important"

    pump = adapter._sessions["sess"].notification_thread
    assert pump is not None and pump.is_alive()
    await adapter.terminate_session("sess")
    await asyncio.to_thread(pump.join, 2)
    assert not pump.is_alive()


@pytest.mark.asyncio
async def test_unknown_notification_logs_once_per_method(
    caplog: pytest.LogCaptureFixture,
) -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    with caplog.at_level("WARNING", logger="waypoint.codex"):
        for _ in range(2):
            fake.global_notifications.put_nowait(
                FakeNotification("future/thing", {"secret": "value", "n": 1})
            )
        fake.global_notifications.put_nowait(
            FakeNotification("warning", {"message": "done"})
        )
        for _ in range(100):
            if emitted:
                break
            await asyncio.sleep(0.01)
    lines = [r.getMessage() for r in caplog.records if "future/thing" in r.getMessage()]
    assert lines == [
        "codex notification future/thing has no registry entry (keys: n, secret)"
    ]
    assert "value" not in lines[0]


def test_input_refused_while_compacting_maps_to_conflict() -> None:
    conflict = input_http_error(CodexCompactingError())
    assert conflict.status_code == 409
    assert conflict.detail == (
        "Codex is compacting the conversation; send again when it finishes"
    )
    assert input_http_error(RuntimeError("bad")).status_code == 400


@pytest.mark.asyncio
async def test_deprecation_notice_logs_once_per_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    with caplog.at_level("WARNING", logger="waypoint.codex"):
        for _ in range(2):
            fake.global_notifications.put_nowait(
                FakeNotification("deprecationNotice", {"summary": "Old call"})
            )
        for _ in range(100):
            if len(emitted) == 2:
                break
            await asyncio.sleep(0.01)
    assert [entry[3]["visibility"] for entry in emitted] == ["detail", "detail"]
    lines = [r.getMessage() for r in caplog.records if "Old call" in r.getMessage()]
    assert lines == ["codex deprecationNotice: Old call"]


@pytest.mark.asyncio
async def test_idle_sessions_do_not_hold_shared_pool_workers() -> None:
    async def emit(*_args: Any) -> None:
        return None

    adapter = CodexAppServerAdapter(
        emit, client_factory=lambda *_: cast(CodexClient, FakeCodexClient())
    )
    for index in range(40):
        await adapter.start_session(f"sess-{index}", "/tmp/work")
    assert await asyncio.wait_for(asyncio.to_thread(lambda: 7), timeout=2) == 7
    await adapter.shutdown()


@pytest.mark.asyncio
async def test_failed_thread_rpc_disposes_of_the_client() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)

    def thread_resume(thread_id: str) -> Any:
        raise RuntimeError("no such thread")

    fake.thread_resume = thread_resume
    with pytest.raises(RuntimeError, match="no such thread"):
        await adapter.restore_session("sess", "/tmp/work", "thread-9")
    assert "sess" not in adapter._sessions
    assert fake.closed is True


@pytest.mark.asyncio
async def test_compaction_reservation_blocks_a_second_request() -> None:
    emitted: list = []
    adapter, _ = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")

    adapter.begin_compaction("sess")
    with pytest.raises(RuntimeError, match="already compacting"):
        adapter.begin_compaction("sess")
    with pytest.raises(CodexCompactingError):
        await adapter.send_input("sess", "hi")

    adapter.abandon_compaction("sess")
    adapter.begin_compaction("sess")


@pytest.mark.asyncio
async def test_compaction_waits_out_a_starting_turn() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    gate = threading.Event()
    original_turn_start = fake.turn_start

    def slow_turn_start(*args: Any) -> FakeTurnStartResponse:
        gate.wait(timeout=2)
        return original_turn_start(*args)

    fake.turn_start = slow_turn_start
    sending = asyncio.create_task(adapter.send_input("sess", "go"))
    for _ in range(100):
        if adapter._sessions["sess"].starting_turns:
            break
        await asyncio.sleep(0.01)

    with pytest.raises(RuntimeError, match="turn is active"):
        adapter.begin_compaction("sess")
    gate.set()
    await sending
    state = adapter._sessions["sess"]
    assert state.active_turn_id == "turn-1"
    assert state.starting_turns == 0
    assert state.compacting is False


def _child_thread(
    *messages: tuple[str, str | None], status: str = "completed", turn_id: str = "a"
) -> Any:
    items = [
        SimpleNamespace(
            root=AgentMessageThreadItem.model_validate(
                {
                    "type": "agentMessage",
                    "id": f"m{index}",
                    "text": text,
                    "phase": phase,
                }
            )
        )
        for index, (text, phase) in enumerate(messages)
    ]
    return SimpleNamespace(
        turns=[SimpleNamespace(id=turn_id, items=items, status=status)]
    )


def _activity(kind: str, item_id: str, thread_id: str = "thread-1") -> Any:
    return FakeNotification(
        "item/completed",
        {
            "threadId": thread_id,
            "turnId": "turn-1",
            "item": {
                "type": "subAgentActivity",
                "id": item_id,
                "kind": kind,
                "agentPath": "/root/reviewer",
                "agentThreadId": "child-1",
            },
        },
    )


async def _run_subagent_turn(
    notifications: list[Any], child: Any = None
) -> tuple[list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]], Any]:
    emitted: list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]] = []
    adapter, fake = make_adapter(emitted)
    if child is not None:
        fake.child_threads["child-1"] = child
    await adapter.start_session("sess", "/tmp/work")
    for notification in notifications:
        fake.notifications.put_nowait(notification)
    fake.notifications.put_nowait(
        FakeNotification(
            "turn/completed", {"turn": {"id": "turn-1", "status": "completed"}}
        )
    )
    await adapter.send_input("sess", "review it")
    state = adapter._sessions["sess"]
    if state.stream_task is not None:
        await state.stream_task
    return [entry for entry in emitted if entry[2] != "Turn completed"], fake


@pytest.mark.asyncio
async def test_subagent_spawn_and_message_are_subagent_tool_entries() -> None:
    emitted, fake = await _run_subagent_turn(
        [_activity("started", "call-1"), _activity("interacted", "call-2")]
    )

    assert [(entry[1], entry[2]) for entry in emitted] == [
        (EventKind.TOOL_RESULT, "Spawned reviewer"),
        (EventKind.TOOL_RESULT, "Messaged reviewer"),
    ]
    assert all(entry[3]["tool_name"] == "Subagent" for entry in emitted)
    assert not [call for call in fake.calls if call[0] in {"thread_read", "request"}]


@pytest.mark.asyncio
async def test_subagent_completion_is_a_task_card_with_the_child_report() -> None:
    child = _child_thread(
        ("Looking now.", "commentary"),
        ("Verdict: approve.", "final_answer"),
        ("Trailing note.", "commentary"),
    )
    emitted, fake = await _run_subagent_turn(
        [_activity("completed", "subagent-completed-a")], child
    )

    assert (
        "request",
        (
            "thread/items/list",
            {
                "threadId": "child-1",
                "turnId": "a",
                "limit": 50,
                "sortDirection": "desc",
            },
        ),
    ) in fake.calls
    [(_, kind, text, metadata, _)] = emitted
    assert kind is EventKind.SYSTEM_NOTE
    assert text == 'Agent "reviewer" finished'
    assert metadata["method"] == "task_notification"
    assert metadata["item_id"] == "subagent-completed-a"
    card = metadata["task_notification"]
    assert card["id"] == "subagent-completed-a"
    assert card["status"] == "completed"
    assert card["task_id"] == "child-1"
    assert card["result_preview"] == "Verdict: approve."
    assert SUBAGENT_REPORT_KEY not in metadata["payload"]["item"]


@pytest.mark.asyncio
async def test_subagent_report_falls_back_to_a_completed_turns_last_message() -> None:
    emitted, _ = await _run_subagent_turn(
        [_activity("completed", "subagent-completed-a")],
        _child_thread(("First.", None), ("Findings.", "commentary")),
    )

    assert emitted[0][3]["task_notification"]["result_preview"] == "Findings."


@pytest.mark.asyncio
async def test_interrupted_subagent_progress_is_not_its_report() -> None:
    emitted, _ = await _run_subagent_turn(
        [_activity("interrupted", "call-9")],
        _child_thread(("Still looking.", "commentary"), status="interrupted"),
    )

    card = emitted[0][3]["task_notification"]
    assert card["status"] == "stopped"
    assert card["summary"] == 'Agent "reviewer" stopped'
    assert card["result_preview"] is None
    assert card["output_unavailable_reason"] == REPORT_UNAVAILABLE


@pytest.mark.asyncio
async def test_interrupted_subagent_keeps_a_final_answer() -> None:
    emitted, _ = await _run_subagent_turn(
        [_activity("interrupted", "call-9")],
        _child_thread(("Partial verdict.", "final_answer"), status="interrupted"),
    )

    assert emitted[0][3]["task_notification"]["result_preview"] == "Partial verdict."


@pytest.mark.asyncio
async def test_long_live_subagent_report_spills_to_an_attachment() -> None:
    report = "r" * 5000
    emitted, _ = await _run_subagent_turn(
        [_activity("completed", "subagent-completed-a")],
        _child_thread((report, "final_answer")),
    )

    metadata = emitted[0][3]
    assert metadata["task_notification"]["result_truncated"] is True
    assert metadata["task_notification"]["output_available"] is True
    assert metadata["capture_inline_blobs"][0]["text"] == report


@pytest.mark.asyncio
async def test_failed_subagent_report_read_yields_an_unavailable_card() -> None:
    emitted, _ = await _run_subagent_turn(
        [_activity("completed", "subagent-completed-a")], RuntimeError("boom")
    )

    card = emitted[0][3]["task_notification"]
    assert card["result_preview"] is None
    assert card["output_available"] is False
    assert card["output_unavailable_reason"] == REPORT_UNAVAILABLE


@pytest.mark.asyncio
async def test_slow_subagent_report_read_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module, "REPORT_FETCH_TIMEOUT_SECONDS", 0.05)
    release = threading.Event()

    def slow_read() -> Any:
        release.wait(5)
        return _child_thread(("Too late.", "final_answer"))

    started = asyncio.get_running_loop().time()
    emitted, _ = await _run_subagent_turn(
        [_activity("completed", "subagent-completed-a")], slow_read
    )
    assert asyncio.get_running_loop().time() - started < 2
    release.set()

    card = emitted[0][3]["task_notification"]
    assert card["output_unavailable_reason"] == REPORT_UNAVAILABLE


@pytest.mark.asyncio
async def test_subagent_activity_from_another_thread_is_ignored() -> None:
    emitted, fake = await _run_subagent_turn(
        [
            _activity("started", "call-1", thread_id="fork-thread"),
            _activity("completed", "subagent-completed-a", thread_id="fork-thread"),
        ],
        _child_thread(("Done.", "final_answer")),
    )

    assert emitted == []
    assert not [call for call in fake.calls if call[0] in {"thread_read", "request"}]


@pytest.mark.asyncio
async def test_long_subagent_report_is_not_spilled_when_capture_is_off() -> None:
    emitted: list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]] = []

    async def emit(session_id, kind, text, metadata, status):
        emitted.append((session_id, kind, text, metadata, status))

    fake = FakeCodexClient()
    fake.child_threads["child-1"] = _child_thread(("r" * 5000, "final_answer"))
    adapter = CodexAppServerAdapter(
        emit,
        client_factory=lambda cwd, handler: cast(CodexClient, fake),
        task_output_capture_enabled=False,
    )
    await adapter.start_session("sess", "/tmp/work")
    fake.notifications.put_nowait(_activity("completed", "subagent-completed-a"))
    fake.notifications.put_nowait(
        FakeNotification(
            "turn/completed", {"turn": {"id": "turn-1", "status": "completed"}}
        )
    )
    await adapter.send_input("sess", "review it")
    state = adapter._sessions["sess"]
    if state.stream_task is not None:
        await state.stream_task

    [metadata] = [entry[3] for entry in emitted if "task_notification" in entry[3]]
    assert "capture_inline_blobs" not in metadata
    card = metadata["task_notification"]
    assert card["result_truncated"] is True
    assert card["output_unavailable_reason"] == "output capture is disabled"


@pytest.mark.asyncio
async def test_late_subagent_completion_on_the_pump_becomes_a_task_card() -> None:
    emitted: list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]] = []
    adapter, fake = make_adapter(emitted)
    fake.child_threads["child-1"] = _child_thread(
        ("Late verdict.", "final_answer"), turn_id="late"
    )
    await adapter.start_session("sess", "/tmp/work")

    fake.global_notifications.put_nowait(
        _activity("completed", "subagent-completed-late")
    )
    for _ in range(200):
        if emitted:
            break
        await asyncio.sleep(0.01)

    [(_, kind, _text, metadata, status)] = emitted
    assert kind is EventKind.SYSTEM_NOTE
    assert status is None
    assert metadata["task_notification"]["result_preview"] == "Late verdict."


@pytest.mark.asyncio
async def test_subagent_report_falls_back_to_thread_read_on_an_older_cli() -> None:
    emitted: list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]] = []
    adapter, fake = make_adapter(emitted)
    fake.paginated_reads_error = InvalidRequestError(-32600, "unknown variant")
    fake.child_threads["child-1"] = _child_thread(("Old CLI verdict.", "final_answer"))
    await adapter.start_session("sess", "/tmp/work")
    fake.notifications.put_nowait(_activity("completed", "subagent-completed-a"))
    fake.notifications.put_nowait(
        FakeNotification(
            "turn/completed", {"turn": {"id": "turn-1", "status": "completed"}}
        )
    )
    await adapter.send_input("sess", "review it")
    state = adapter._sessions["sess"]
    if state.stream_task is not None:
        await state.stream_task

    assert ("thread_read", ("child-1", True)) in fake.calls
    [card] = [e[3]["task_notification"] for e in emitted if "task_notification" in e[3]]
    assert card["result_preview"] == "Old CLI verdict."


@pytest.mark.asyncio
async def test_transient_report_read_error_does_not_fall_back_to_full_history() -> None:
    emitted: list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]] = []
    adapter, fake = make_adapter(emitted)
    fake.paginated_reads_error = ServerBusyError(-32001, "busy")
    fake.child_threads["child-1"] = _child_thread(("Verdict.", "final_answer"))
    await adapter.start_session("sess", "/tmp/work")
    fake.notifications.put_nowait(_activity("completed", "subagent-completed-a"))
    fake.notifications.put_nowait(
        FakeNotification(
            "turn/completed", {"turn": {"id": "turn-1", "status": "completed"}}
        )
    )
    await adapter.send_input("sess", "review it")
    state = adapter._sessions["sess"]
    if state.stream_task is not None:
        await state.stream_task

    assert not [call for call in fake.calls if call[0] == "thread_read"]
    [card] = [e[3]["task_notification"] for e in emitted if "task_notification" in e[3]]
    assert card["output_unavailable_reason"] == REPORT_UNAVAILABLE


@pytest.mark.asyncio
async def test_completion_reads_the_turn_it_reports_on() -> None:
    child = SimpleNamespace(
        turns=[
            _child_thread(("Old verdict.", "final_answer"), turn_id="a").turns[0],
            _child_thread(
                ("Busy again.", "commentary"), status="inProgress", turn_id="b"
            ).turns[0],
        ]
    )
    emitted, _ = await _run_subagent_turn(
        [_activity("completed", "subagent-completed-a")], child
    )

    assert emitted[0][3]["task_notification"]["result_preview"] == "Old verdict."


class _ReportClient:
    """Answers the paginated reads with one canned turn, or a given error."""

    def __init__(self, turn: Any = None, error: Exception | None = None) -> None:
        self.turn = turn
        self.error = error
        self.thread_reads = 0

    def request(self, method: str, params: dict[str, Any], *, response_model) -> Any:
        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=[self.turn])

    def thread_read(self, thread_id: str, include_turns: bool = False) -> Any:
        self.thread_reads += 1
        return SimpleNamespace(thread=SimpleNamespace(turns=[self.turn]))


def _turn_at(started_at: int, text: str) -> Any:
    turn = _child_thread((text, "final_answer")).turns[0]
    turn.started_at = started_at
    return turn


def test_stopping_an_idle_subagent_shows_no_earlier_report() -> None:
    finished = _turn_at(1, "Old verdict.")
    client = cast(CodexClient, _ReportClient(finished))
    assert read_report(client, ReportSource("child", None, interrupted=True)) is None
    running = _child_thread(("Partial verdict.", "final_answer"), status="interrupted")
    client = cast(CodexClient, _ReportClient(running.turns[0]))
    assert (
        read_report(client, ReportSource("child", None, interrupted=True))
        == "Partial verdict."
    )


def test_newest_turn_started_after_the_bound_is_not_the_report() -> None:
    client = cast(CodexClient, _ReportClient(_turn_at(200, "Later task.")))
    assert read_report(client, ReportSource("child", None, 100)) is None
    assert read_report(client, ReportSource("child", None, 300)) == "Later task."
    assert read_report(client, ReportSource("child", None)) == "Later task."


@pytest.mark.parametrize(
    ("error", "falls_back"),
    [
        (InvalidRequestError(-32600, "Invalid request: unknown variant `x`"), True),
        (MethodNotFoundError(-32601, "not supported yet"), True),
        (InvalidRequestError(-32600, "thread not loaded: child"), False),
    ],
)
def test_only_an_unsupported_paginated_read_falls_back(
    error: Exception, falls_back: bool
) -> None:
    fake = _ReportClient(_turn_at(1, "Verdict."), error)
    client = cast(CodexClient, fake)
    if falls_back:
        assert read_report(client, ReportSource("child", None)) == "Verdict."
    else:
        with pytest.raises(InvalidRequestError):
            read_report(client, ReportSource("child", None))
    assert fake.thread_reads == (1 if falls_back else 0)


@pytest.mark.asyncio
async def test_hung_report_read_pauses_reads_only_while_it_is_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module, "REPORT_FETCH_TIMEOUT_SECONDS", 0.05)
    emitted: list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]] = []
    adapter, fake = make_adapter(emitted)
    unblock = threading.Event()
    read_threads: list[str] = []

    def hung_read() -> Any:
        read_threads.append(threading.current_thread().name)
        unblock.wait(5)
        return _child_thread(("Too late.", "final_answer"))

    fake.child_threads["child-1"] = hung_read
    await adapter.start_session("sess", "/tmp/work")
    for item_id in ("subagent-completed-a", "subagent-completed-b"):
        fake.notifications.put_nowait(_activity("completed", item_id))
    fake.notifications.put_nowait(
        FakeNotification(
            "turn/completed", {"turn": {"id": "turn-1", "status": "completed"}}
        )
    )
    await adapter.send_input("sess", "review it")
    state = adapter._sessions["sess"]
    if state.stream_task is not None:
        await state.stream_task

    cards = [e[3]["task_notification"] for e in emitted if "task_notification" in e[3]]
    assert [card["output_unavailable_reason"] for card in cards] == [
        REPORT_UNAVAILABLE,
        REPORT_UNAVAILABLE,
    ]
    # After the first read timed out, the second is skipped without waiting.
    assert len(read_threads) == 1
    assert state.stuck_report_reads == 1
    assert read_threads[0].startswith("codex-subagent-report")
    unblock.set()
    for _ in range(200):
        if not state.stuck_report_reads:
            break
        await asyncio.sleep(0.01)
    assert state.stuck_report_reads == 0

    fake.child_threads["child-1"] = _child_thread(("Back again.", "final_answer"))
    report = await adapter._read_subagent_report(state, ReportSource("child-1", "a"))
    assert report == "Back again."
    assert await adapter.terminate_session("sess")
