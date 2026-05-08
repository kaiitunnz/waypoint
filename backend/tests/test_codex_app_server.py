import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from waypoint.backends.codex.adapter import CodexAppServerAdapter
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
                            "skills": [
                                {
                                    "description": "Humanize prose",
                                    "enabled": True,
                                    "name": "humanizer",
                                    "path": "/tmp/work/.codex/skills/humanizer/SKILL.md",
                                    "scope": "repo",
                                    "shortDescription": "Humanize",
                                }
                            ],
                        }
                    ]
                }
            )
        raise AssertionError(f"unexpected request: {method}")

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


def make_adapter(
    emitted: list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]],
):
    async def emit(session_id, kind, text, metadata, status):
        emitted.append((session_id, kind, text, metadata, status))

    fake = FakeAppServerClient()

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

    override = FakeAppServerClient()

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
        state.stream_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await state.stream_task


@pytest.mark.asyncio
async def test_list_models_uses_transient_client() -> None:
    """Discovery spawns a fresh client and closes it after the model_list
    round-trip."""
    emitted: list = []
    adapter, _ = make_adapter(emitted)

    transient = FakeAppServerClient()

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
        state.stream_task.cancel()
        try:
            await state.stream_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_list_skills_requests_current_session_cwd() -> None:
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

    fake.calls.clear()
    await adapter.send_input("sess", "second")

    methods = [call[0] for call in fake.calls]
    assert methods.count("turn_start") == 1
    assert methods.count("turn_steer") == 0
    assert state.active_turn_id == "turn-1"

    if state.stream_task is not None:
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


@pytest.mark.asyncio
async def test_respond_to_approval_returns_false_when_idle() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    handled = await adapter.respond_to_approval("sess", "approve")
    assert handled is False


@pytest.mark.asyncio
async def test_compact_thread_invokes_sdk_and_drains_until_compacted() -> None:
    emitted: list = []
    adapter, fake = make_adapter(emitted)
    await adapter.start_session("sess", "/tmp/work")
    fake.calls.clear()

    def thread_compact(thread_id: str):
        fake.calls.append(("thread_compact", (thread_id,)))
        return {}

    fake.thread_compact = thread_compact

    await adapter.compact_thread("sess")
    state = adapter._sessions["sess"]
    assert ("thread_compact", ("thread-1",)) in fake.calls
    assert state.stream_task is not None

    await fake.notifications.put(
        FakeNotification(method="thread/compacted", payload={})
    )
    await state.stream_task
    assert state.stream_task is None
    kinds = [item[1] for item in emitted]
    assert EventKind.SYSTEM_NOTE in kinds
    assert any("compacted" in item[2].lower() for item in emitted)


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
    # next_notification holds the transport lock, so doing so deadlocks. The
    # client.close() above is what unblocks the streaming task.
    assert all(call[0] != "turn_interrupt" for call in fake.calls)


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


def test_terminal_snapshot_returns_empty_for_inactive_session() -> None:
    # Terminated/exited Codex sessions are popped from `_sessions` but the
    # session record still lives in storage; the API still calls into us when
    # the user opens the session detail page. Mirror the Claude adapter's
    # graceful empty-string fallback instead of raising.
    emitted: list = []
    adapter, _ = make_adapter(emitted)
    assert adapter.terminal_snapshot("never-started") == ""


@pytest.mark.asyncio
async def test_streamed_tool_result_suppresses_duplicate_completed_event() -> None:
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


def test_map_notification_agent_message_delta() -> None:
    from waypoint.backends.codex.normalize import map_notification

    kind, text, status = map_notification(
        "item/agentMessage/delta",
        {"delta": "hello"},
    )
    assert kind == EventKind.AGENT_OUTPUT
    assert text == "hello"
    assert status == SessionStatus.RUNNING


def test_map_notification_command_execution_started() -> None:
    from waypoint.backends.codex.normalize import map_notification

    kind, text, status = map_notification(
        "item/started",
        {"item": {"type": "commandExecution", "command": "ls -la"}},
    )
    assert kind == EventKind.TOOL_CALL
    assert "ls -la" in text


def test_map_notification_file_change_patch_updated_has_preview() -> None:
    from waypoint.backends.codex.normalize import (
        diff_preview_for_notification,
        map_notification,
    )

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
    from waypoint.backends.codex.normalize import map_notification

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
    from waypoint.backends.codex.normalize import map_notification

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
    from waypoint.backends.codex.normalize import map_notification

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
    from waypoint.backends.codex.normalize import map_notification

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
