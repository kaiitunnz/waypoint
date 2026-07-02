from datetime import UTC, datetime
from typing import Any, cast

import pytest

from waypoint.backends.opencode.adapter import OpenCodeAdapter, OpenCodeError
from waypoint.backends.opencode.normalize import historical_events_from_messages
from waypoint.backends.opencode.plugin import OpenCodePlugin
from waypoint.schemas import EventKind, EventRecord, SessionRecord


def _build_adapter() -> OpenCodeAdapter:
    async def _emit(*args: object, **kwargs: object) -> None:
        return None

    return OpenCodeAdapter(emit_event=_emit)


# ─── adapter.get_session_messages ───


@pytest.mark.asyncio
async def test_get_session_messages_returns_the_list() -> None:
    adapter = _build_adapter()
    adapter._started = True

    class _FakeClient:
        async def get(self, path: str, params: dict[str, str] | None = None) -> Any:
            assert path == "/session/ses_1/message"
            return [{"info": {"role": "user"}, "parts": []}]

    adapter._client = cast(Any, _FakeClient())

    messages = await adapter.get_session_messages("ses_1")

    assert messages == [{"info": {"role": "user"}, "parts": []}]


@pytest.mark.asyncio
async def test_get_session_messages_raises_on_client_failure() -> None:
    adapter = _build_adapter()
    adapter._started = True

    class _FailingClient:
        async def get(self, path: str, params: dict[str, str] | None = None) -> Any:
            raise RuntimeError("connection refused")

    adapter._client = cast(Any, _FailingClient())

    with pytest.raises(OpenCodeError):
        await adapter.get_session_messages("ses_1")


# ─── normalize.historical_events_from_messages ───


def test_user_turn_becomes_user_input_event() -> None:
    messages = [
        {
            "info": {"role": "user", "sessionID": "ses_1", "time": {"created": 1000}},
            "parts": [
                {"id": "p_user", "type": "text", "text": "fix the bug"},
            ],
        }
    ]

    events = historical_events_from_messages(messages)

    assert len(events) == 1
    ts, kind, text, metadata = events[0]
    assert kind == EventKind.USER_INPUT
    assert text == "fix the bug"
    assert ts == datetime.fromtimestamp(1.0, tz=UTC)
    assert metadata["submit"] is True


def test_user_turn_with_no_text_parts_is_skipped() -> None:
    messages = [
        {
            "info": {"role": "user", "sessionID": "ses_1", "time": {"created": 1000}},
            "parts": [
                {"id": "p_file", "type": "file", "mime": "image/png", "url": "data:..."}
            ],
        }
    ]

    assert historical_events_from_messages(messages) == []


def test_assistant_text_part_carries_full_body_and_item_id() -> None:
    messages = [
        {
            "info": {
                "role": "assistant",
                "sessionID": "ses_1",
                "time": {"created": 1000, "completed": 2000},
            },
            "parts": [
                {
                    "id": "p_text",
                    "sessionID": "ses_1",
                    "type": "text",
                    "text": "here is the fix",
                    "time": {"start": 1200, "end": 1800},
                },
            ],
        }
    ]

    events = historical_events_from_messages(messages)

    assert len(events) == 1
    ts, kind, text, metadata = events[0]
    assert kind == EventKind.AGENT_OUTPUT
    assert text == "here is the fix"
    assert ts == datetime.fromtimestamp(1.8, tz=UTC)
    assert metadata["item_id"] == "p_text"
    assert metadata["method"] == "message.part.delta.text"
    assert "item_kind" not in metadata


def test_assistant_reasoning_part_is_tagged_distinctly_from_text() -> None:
    messages = [
        {
            "info": {
                "role": "assistant",
                "sessionID": "ses_1",
                "time": {"created": 1000},
            },
            "parts": [
                {
                    "id": "p_reason",
                    "sessionID": "ses_1",
                    "type": "reasoning",
                    "text": "thinking it through",
                    "time": {"start": 1000, "end": 1100},
                },
            ],
        }
    ]

    events = historical_events_from_messages(messages)

    assert len(events) == 1
    _, kind, text, metadata = events[0]
    assert kind == EventKind.AGENT_OUTPUT
    assert text == "thinking it through"
    assert metadata["method"] == "message.part.delta.reasoning"
    assert metadata["item_kind"] == "reasoning"


def test_assistant_tool_part_reuses_map_tool_event_and_keeps_tool_use_id() -> None:
    messages = [
        {
            "info": {
                "role": "assistant",
                "sessionID": "ses_1",
                "time": {"created": 1000},
            },
            "parts": [
                {
                    "id": "p_tool",
                    "sessionID": "ses_1",
                    "type": "tool",
                    "tool": "Read",
                    "callID": "call_1",
                    "state": {
                        "status": "completed",
                        "input": {"path": "/etc/hosts"},
                        "output": "127.0.0.1 localhost",
                        "time": {"start": 1000, "end": 1500},
                    },
                },
            ],
        }
    ]

    events = historical_events_from_messages(messages)

    # A completed tool snapshot is replayed as a paired TOOL_CALL + TOOL_RESULT
    # (the live SSE path emits both across state updates), sharing tool_use_id.
    assert [kind for _, kind, _, _ in events] == [
        EventKind.TOOL_CALL,
        EventKind.TOOL_RESULT,
    ]
    call_ts, _, call_text, call_meta = events[0]
    assert call_text.startswith("Read(")
    assert call_meta["tool_use_id"] == "call_1"
    assert call_ts == datetime.fromtimestamp(1.0, tz=UTC)
    result_ts, _, result_text, result_meta = events[1]
    assert result_text.startswith("Result for Read:")
    assert result_meta["tool_use_id"] == "call_1"
    assert result_meta["item_id"] == "call_1"
    assert result_ts == datetime.fromtimestamp(1.5, tz=UTC)


def test_ordered_thread_preserves_user_then_assistant_order() -> None:
    messages = [
        {
            "info": {"role": "user", "sessionID": "ses_1", "time": {"created": 1000}},
            "parts": [{"id": "p1", "type": "text", "text": "hello"}],
        },
        {
            "info": {
                "role": "assistant",
                "sessionID": "ses_1",
                "time": {"created": 2000},
            },
            "parts": [
                {
                    "id": "p2",
                    "sessionID": "ses_1",
                    "type": "tool",
                    "tool": "Bash",
                    "callID": "call_1",
                    "state": {"status": "completed", "input": {}, "output": "ok"},
                },
                {
                    "id": "p3",
                    "sessionID": "ses_1",
                    "type": "text",
                    "text": "done",
                    "time": {"start": 2500},
                },
            ],
        },
    ]

    events = historical_events_from_messages(messages)

    kinds = [kind for _, kind, _, _ in events]
    assert kinds == [
        EventKind.USER_INPUT,
        EventKind.TOOL_CALL,
        EventKind.TOOL_RESULT,
        EventKind.AGENT_OUTPUT,
    ]


# ─── plugin.import_thread wiring ───


@pytest.mark.asyncio
async def test_import_thread_seeds_history_before_the_imported_note() -> None:
    plugin = OpenCodePlugin()

    class FakeAdapter:
        async def get_session(self, session_id: str) -> dict[str, object] | None:
            return {"id": session_id, "title": "Imported", "directory": "/repo"}

        async def restore_session(
            self, session_id, cwd, opencode_session_id, **kwargs
        ) -> None:
            return None

        async def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
            return [
                {
                    "info": {
                        "role": "user",
                        "sessionID": session_id,
                        "time": {"created": 1000},
                    },
                    "parts": [{"id": "p1", "type": "text", "text": "hi"}],
                }
            ]

    fake_adapter = FakeAdapter()

    async def fake_get_or_create_adapter(
        runtime, launch_target_id, cwd, custom_args=(), **kw
    ):
        return fake_adapter

    plugin._get_or_create_adapter = fake_get_or_create_adapter  # type: ignore[method-assign]

    class FakeStorage:
        def __init__(self) -> None:
            self.sessions: list[SessionRecord] = []

        def list_sessions(self) -> list[SessionRecord]:
            return list(self.sessions)

        def create_session(self, session: SessionRecord) -> None:
            self.sessions.append(session)

        def update_session(self, session_id: str, **kwargs: Any) -> SessionRecord:
            session = self.sessions[-1]
            for key, value in kwargs.items():
                setattr(session, key, value)
            return session

    calls: list[str] = []

    class FakeRuntime:
        def __init__(self) -> None:
            self.storage = FakeStorage()
            self.seeded_events: list[EventRecord] = []

        def _generate_session_id(self, backend_id: str) -> str:
            return f"{backend_id}-1"

        def _session_dir(self, session_id: str):
            from pathlib import Path

            path = Path("/tmp") / session_id
            path.mkdir(parents=True, exist_ok=True)
            return path

        async def _record_system_event(self, *args: Any, **kwargs: Any) -> None:
            calls.append("note")

        async def seed_thread_history(self, session_id, reader, *, enabled) -> int:
            assert enabled is True
            events = await reader()
            self.seeded_events.extend(events)
            calls.append("seed")
            return len(events)

        def get_session(self, session_id: str) -> SessionRecord:
            return self.storage.sessions[-1]

    runtime: Any = FakeRuntime()
    request = type(
        "Req",
        (),
        {
            "thread_id": "ses_1",
            "launch_target_id": None,
            "cwd": "/repo",
            "import_history": True,
        },
    )()

    result = await plugin.import_thread(runtime, request)

    assert calls == ["seed", "note"]
    assert len(runtime.seeded_events) == 1
    seeded = runtime.seeded_events[0]
    assert seeded.session_id == result.id
    assert seeded.kind == EventKind.USER_INPUT
    assert seeded.text == "hi"


@pytest.mark.asyncio
async def test_import_thread_skips_history_when_disabled() -> None:
    plugin = OpenCodePlugin()

    class FakeAdapter:
        async def get_session(self, session_id: str) -> dict[str, object] | None:
            return {"id": session_id, "title": "Imported", "directory": "/repo"}

        async def restore_session(
            self, session_id, cwd, opencode_session_id, **kwargs
        ) -> None:
            return None

        async def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
            raise AssertionError("history should not be read when import_history=False")

    fake_adapter = FakeAdapter()

    async def fake_get_or_create_adapter(
        runtime, launch_target_id, cwd, custom_args=(), **kw
    ):
        return fake_adapter

    plugin._get_or_create_adapter = fake_get_or_create_adapter  # type: ignore[method-assign]

    class FakeStorage:
        def __init__(self) -> None:
            self.sessions: list[SessionRecord] = []

        def list_sessions(self) -> list[SessionRecord]:
            return list(self.sessions)

        def create_session(self, session: SessionRecord) -> None:
            self.sessions.append(session)

        def update_session(self, session_id: str, **kwargs: Any) -> SessionRecord:
            session = self.sessions[-1]
            for key, value in kwargs.items():
                setattr(session, key, value)
            return session

    class FakeRuntime:
        def __init__(self) -> None:
            self.storage = FakeStorage()

        def _generate_session_id(self, backend_id: str) -> str:
            return f"{backend_id}-1"

        def _session_dir(self, session_id: str):
            from pathlib import Path

            path = Path("/tmp") / session_id
            path.mkdir(parents=True, exist_ok=True)
            return path

        async def _record_system_event(self, *args: Any, **kwargs: Any) -> None:
            return None

        async def seed_thread_history(self, session_id, reader, *, enabled) -> int:
            assert enabled is False
            return 0

        def get_session(self, session_id: str) -> SessionRecord:
            return self.storage.sessions[-1]

    runtime: Any = FakeRuntime()
    request = type(
        "Req",
        (),
        {
            "thread_id": "ses_1",
            "launch_target_id": None,
            "cwd": "/repo",
            "import_history": False,
        },
    )()

    result = await plugin.import_thread(runtime, request)

    assert result.id == "opencode-1"
