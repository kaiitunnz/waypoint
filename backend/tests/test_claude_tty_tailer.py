"""Unit tests for TranscriptTailer context-usage publishing."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from waypoint.backends.claude_tty.tailer import TranscriptTailer
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus


def _make_session(session_id: str = "sess-1") -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id=session_id,
        backend="claude_tty",
        source=SessionSource.MANAGED,
        transport="claude_tty",
        title="test",
        cwd="/tmp",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/structured.log",
        transport_state={
            "tmux_session": session_id,
            "tmux_window": "0",
            "tmux_pane": "%0",
            "thread_id": "thread-1",
        },
    )


def _make_runtime(session: SessionRecord) -> MagicMock:
    runtime = MagicMock()
    runtime.storage.get_session.return_value = session
    runtime._emit_adapter_event = AsyncMock()
    runtime.update_session_fields = AsyncMock()
    return runtime


def _make_tailer(runtime: MagicMock, session_id: str = "sess-1") -> TranscriptTailer:
    plugin = MagicMock()
    plugin._pending_questions = {}
    return TranscriptTailer(
        session_id=session_id,
        session_uuid="thread-1",
        cwd="/nonexistent",
        runtime=runtime,
        plugin=plugin,
    )


def _jsonl(*records: dict) -> bytes:
    return b"\n".join(json.dumps(r).encode() for r in records) + b"\n"


def _assistant_record(
    message_id: str = "msg_1",
    model: str = "claude-sonnet-4-5",
    usage: dict | None = None,
    stop_reason: str = "end_turn",
) -> dict:
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "model": model,
            "content": [{"type": "text", "text": "hello"}],
            "stop_reason": stop_reason,
            "usage": usage or {"input_tokens": 10, "output_tokens": 5},
        },
    }


@pytest.mark.asyncio
async def test_drain_publishes_context_usage_on_assistant_record() -> None:
    session = _make_session()
    runtime = _make_runtime(session)
    tailer = _make_tailer(runtime)

    record = _assistant_record(
        usage={"input_tokens": 15, "cache_read_input_tokens": 4, "output_tokens": 6}
    )
    data = _jsonl(record)

    with patch.object(tailer, "_read_new_bytes", return_value=data):
        await tailer._drain()

    runtime.update_session_fields.assert_called_once()
    call_kwargs = runtime.update_session_fields.call_args
    assert call_kwargs.args[0] == "sess-1"
    snapshot = call_kwargs.kwargs["context_usage"]
    # input_tokens(15) + cache_read_input_tokens(4) = 19 used tokens
    assert snapshot.used_tokens == 19
    assert snapshot.context_window_tokens == 200_000
    assert snapshot.source == "claude_code"


@pytest.mark.asyncio
async def test_drain_dedupes_unchanged_context_usage() -> None:
    session = _make_session()
    runtime = _make_runtime(session)
    tailer = _make_tailer(runtime)

    usage = {"input_tokens": 10, "output_tokens": 5}
    record1 = _assistant_record(message_id="msg_1", usage=usage)
    record2 = _assistant_record(message_id="msg_2", usage=usage)
    data = _jsonl(record1, record2)

    with patch.object(tailer, "_read_new_bytes", return_value=data):
        await tailer._drain()

    # Same (used_tokens, context_window_tokens) — only one publish
    assert runtime.update_session_fields.call_count == 1


@pytest.mark.asyncio
async def test_drain_publishes_again_when_usage_changes() -> None:
    session = _make_session()
    runtime = _make_runtime(session)
    tailer = _make_tailer(runtime)

    record1 = _assistant_record(
        message_id="msg_1", usage={"input_tokens": 10, "output_tokens": 5}
    )
    data1 = _jsonl(record1)
    with patch.object(tailer, "_read_new_bytes", return_value=data1):
        await tailer._drain()

    assert runtime.update_session_fields.call_count == 1
    first_used = runtime.update_session_fields.call_args.kwargs[
        "context_usage"
    ].used_tokens

    record2 = _assistant_record(
        message_id="msg_2", usage={"input_tokens": 20, "output_tokens": 8}
    )
    data2 = _jsonl(record2)
    with patch.object(tailer, "_read_new_bytes", return_value=data2):
        await tailer._drain()

    assert runtime.update_session_fields.call_count == 2
    second_used = runtime.update_session_fields.call_args.kwargs[
        "context_usage"
    ].used_tokens
    assert second_used > first_used
