"""Unit tests for TranscriptContextUsageSource."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from waypoint.backends.claude_code.usage_source import TranscriptContextUsageSource


def _make_runtime(session_model: str | None = None) -> MagicMock:
    runtime = MagicMock()
    runtime.update_session_fields = AsyncMock()
    runtime.publish_token_usage_record = AsyncMock()
    # The source reads the session's configured model fresh on each publish to
    # resolve the context window (the transcript only carries the resolved API
    # id, which loses the ``[1m]`` marker). Tests reassign
    # ``get_session.return_value`` to simulate a dynamic model change mid-run.
    runtime.storage.get_session.return_value = SimpleNamespace(model=session_model)
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

    # The same assistant record also feeds the durable per-turn ledger, keyed
    # on the provider message id, with a summed (non-overlapping) grand total.
    runtime.publish_token_usage_record.assert_called_once()
    record = runtime.publish_token_usage_record.call_args.args[1]
    assert record.record_id == "msg_1"
    assert record.totals == {
        "input_tokens": 15,
        "cache_read_tokens": 4,
        "output_tokens": 6,
    }
    assert record.display_total_tokens == 25


@pytest.mark.asyncio
async def test_distinct_messages_each_publish_token_record(tmp_path: Path) -> None:
    # Two distinct turns with identical usage: the snapshot dedupes (nothing
    # changed to display), but each distinct message id must still be recorded
    # so the aggregate counts both turns.
    runtime = _make_runtime()
    source = _make_source(runtime, tmp_path)
    usage = {"input_tokens": 10, "output_tokens": 5}
    rec1 = _assistant_record(usage=usage)
    rec2 = _assistant_record(usage=usage)
    rec2["message"]["id"] = "msg_2"
    data = _jsonl(rec1, rec2)

    with patch.object(source, "_read_new_bytes", return_value=data):
        await source._drain()

    assert runtime.update_session_fields.call_count == 1
    assert runtime.publish_token_usage_record.call_count == 2
    ids = [
        c.args[1].record_id for c in runtime.publish_token_usage_record.call_args_list
    ]
    assert ids == ["msg_1", "msg_2"]


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


@pytest.mark.asyncio
async def test_session_model_overrides_transcript_for_1m_window(tmp_path: Path) -> None:
    # Session configured with the [1m] alias must yield the 1M window even though
    # the transcript's resolved API id (claude-opus-4-8) normalizes to base opus.
    runtime = _make_runtime(session_model="opus[1m]")
    source = _make_source(runtime, tmp_path)
    record = _assistant_record(
        model="claude-opus-4-8",
        usage={"input_tokens": 20, "cache_read_input_tokens": 5, "output_tokens": 3},
    )
    with patch.object(source, "_read_new_bytes", return_value=_jsonl(record)):
        await source._drain()
    snapshot = runtime.update_session_fields.call_args.kwargs["context_usage"]
    assert snapshot.context_window_tokens == 1_000_000


@pytest.mark.asyncio
async def test_dynamic_model_change_updates_window(tmp_path: Path) -> None:
    # A model switch mid-session must be reflected: the window follows the
    # session's current model, read fresh on each publish.
    runtime = _make_runtime(session_model="sonnet[1m]")
    source = _make_source(runtime, tmp_path)
    rec1 = _assistant_record(
        model="claude-sonnet-4-6", usage={"input_tokens": 11, "output_tokens": 1}
    )
    with patch.object(source, "_read_new_bytes", return_value=_jsonl(rec1)):
        await source._drain()
    assert (
        runtime.update_session_fields.call_args.kwargs[
            "context_usage"
        ].context_window_tokens
        == 1_000_000
    )
    # Switch the configured model to the base alias; next publish uses 200k.
    runtime.storage.get_session.return_value = SimpleNamespace(model="sonnet")
    rec2 = _assistant_record(
        model="claude-sonnet-4-6", usage={"input_tokens": 99, "output_tokens": 1}
    )
    with patch.object(source, "_read_new_bytes", return_value=_jsonl(rec2)):
        await source._drain()
    assert (
        runtime.update_session_fields.call_args.kwargs[
            "context_usage"
        ].context_window_tokens
        == 200_000
    )
