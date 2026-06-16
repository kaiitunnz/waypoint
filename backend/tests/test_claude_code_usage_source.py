"""Unit tests for TranscriptContextUsageSource."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from waypoint.backends.claude_code.usage_source import TranscriptContextUsageSource


def _make_runtime() -> MagicMock:
    runtime = MagicMock()
    runtime.update_session_fields = AsyncMock()
    return runtime


def _make_source(
    runtime: MagicMock,
    tmp_path: Path,
    session_id: str = "sess-1",
    session_uuid: str = "uuid-1",
) -> TranscriptContextUsageSource:
    cwd = str(tmp_path)
    source = TranscriptContextUsageSource(
        session_id=session_id,
        session_uuid=session_uuid,
        cwd=cwd,
        runtime=runtime,
    )
    return source


def _jsonl(*records: dict[str, object]) -> bytes:
    return b"\n".join(json.dumps(r).encode() for r in records) + b"\n"


def _assistant_record(
    model: str = "claude-sonnet-4-5",
    usage: dict | None = None,
) -> dict:
    return {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "model": model,
            "content": [{"type": "text", "text": "hello"}],
            "stop_reason": "end_turn",
            "usage": usage or {"input_tokens": 10, "output_tokens": 5},
        },
    }


@pytest.mark.asyncio
async def test_drain_publishes_context_usage_on_assistant_record(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime()
    source = _make_source(runtime, tmp_path)

    record = _assistant_record(
        usage={"input_tokens": 15, "cache_read_input_tokens": 4, "output_tokens": 6}
    )
    data = _jsonl(record)

    with patch.object(source, "_read_new_bytes", return_value=data):
        await source._drain()

    runtime.update_session_fields.assert_called_once()
    call_args = runtime.update_session_fields.call_args
    assert call_args.args[0] == "sess-1"
    snapshot = call_args.kwargs["context_usage"]
    # input_tokens(15) + cache_read_input_tokens(4) = 19 used tokens
    assert snapshot.used_tokens == 19
    assert snapshot.context_window_tokens == 200_000
    assert snapshot.source == "claude_code"


@pytest.mark.asyncio
async def test_drain_dedupes_unchanged_context_usage(tmp_path: Path) -> None:
    runtime = _make_runtime()
    source = _make_source(runtime, tmp_path)

    usage = {"input_tokens": 10, "output_tokens": 5}
    record1 = _assistant_record(usage=usage)
    record2 = _assistant_record(usage=usage)
    data = _jsonl(record1, record2)

    with patch.object(source, "_read_new_bytes", return_value=data):
        await source._drain()

    # Same (used_tokens, context_window_tokens) — only one publish
    assert runtime.update_session_fields.call_count == 1


@pytest.mark.asyncio
async def test_drain_publishes_again_when_usage_changes(tmp_path: Path) -> None:
    runtime = _make_runtime()
    source = _make_source(runtime, tmp_path)

    record1 = _assistant_record(usage={"input_tokens": 10, "output_tokens": 5})
    record2 = _assistant_record(usage={"input_tokens": 20, "output_tokens": 5})
    data = _jsonl(record1, record2)

    with patch.object(source, "_read_new_bytes", return_value=data):
        await source._drain()

    assert runtime.update_session_fields.call_count == 2
    snapshots = [
        c.kwargs["context_usage"] for c in runtime.update_session_fields.call_args_list
    ]
    assert snapshots[0].used_tokens == 10
    assert snapshots[1].used_tokens == 20


@pytest.mark.asyncio
async def test_drain_skips_non_assistant_records(tmp_path: Path) -> None:
    runtime = _make_runtime()
    source = _make_source(runtime, tmp_path)

    data = _jsonl(
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "system", "content": "init"},
    )

    with patch.object(source, "_read_new_bytes", return_value=data):
        await source._drain()

    runtime.update_session_fields.assert_not_called()


@pytest.mark.asyncio
async def test_drain_skips_unknown_model(tmp_path: Path) -> None:
    runtime = _make_runtime()
    source = _make_source(runtime, tmp_path)

    # Model not in the Claude catalogue — context window is unknown, so no publish
    record = _assistant_record(
        model="gpt-4",
        usage={"input_tokens": 10, "output_tokens": 5},
    )
    data = _jsonl(record)

    with patch.object(source, "_read_new_bytes", return_value=data):
        await source._drain()

    runtime.update_session_fields.assert_not_called()


@pytest.mark.asyncio
async def test_drain_tolerates_missing_file(tmp_path: Path) -> None:
    runtime = _make_runtime()
    source = _make_source(runtime, tmp_path)

    # _read_new_bytes returns empty bytes when the file doesn't exist
    with patch.object(source, "_read_new_bytes", return_value=b""):
        await source._drain()

    runtime.update_session_fields.assert_not_called()
