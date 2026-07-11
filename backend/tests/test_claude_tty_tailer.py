"""Unit tests for TranscriptTailer context-usage publishing."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from waypoint.backends.claude_tty.byte_source import TranscriptRead
from waypoint.backends.claude_tty.tailer import TranscriptTailer, transcript_path
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus


class _FakeSource:
    """A byte source that hands over fed bytes once, modeling a growing file."""

    def __init__(self) -> None:
        self._pending = b""
        self._size = 0

    def feed(self, data: bytes) -> None:
        self._pending = data

    def read_from(
        self, offset: int, *, metadata_only: bool = False, force: bool = False
    ) -> TranscriptRead:
        data = b"" if metadata_only else self._pending
        self._pending = b""
        self._size += len(data)
        return TranscriptRead(
            observed=True, data=data, size=self._size, identity=(1, 1)
        )


def test_transcript_path_honors_config_dir() -> None:
    # Regression: a profile-scoped claude_tty session writes its transcript
    # under its CLAUDE_CONFIG_DIR; the tailer must resolve the same path, or it
    # reads the default ~/.claude, sees no records, and the session hangs in
    # "running" while the pane shows real output.
    uuid = "00000000-0000-0000-0000-000000000001"
    default = transcript_path("/repo/app", uuid)
    scoped = transcript_path("/repo/app", uuid, "/home/me/.claude-work")
    assert scoped != default
    assert str(scoped).startswith("/home/me/.claude-work/projects/")
    assert scoped.name == f"{uuid}.jsonl"


def _make_session(
    session_id: str = "sess-1",
    resolved_model: str | None = None,
    effort: str | None = None,
) -> SessionRecord:
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
        resolved_model=resolved_model,
        effort=effort,
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
    runtime.publish_token_usage_record = AsyncMock()
    runtime._record_system_event = AsyncMock()
    return runtime


def _make_tailer(
    runtime: MagicMock, session_id: str = "sess-1"
) -> tuple[TranscriptTailer, _FakeSource]:
    plugin = MagicMock()
    plugin._pending_questions = {}
    source = _FakeSource()
    tailer = TranscriptTailer(
        session_id=session_id,
        source=source,
        runtime=runtime,
        plugin=plugin,
    )
    return tailer, source


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
    tailer, source = _make_tailer(runtime)

    record = _assistant_record(
        usage={"input_tokens": 15, "cache_read_input_tokens": 4, "output_tokens": 6}
    )
    source.feed(_jsonl(record))
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
async def test_drain_publishes_token_record_with_resolved_model_and_effort() -> None:
    session = _make_session(resolved_model="claude-sonnet-4-5", effort="high")
    runtime = _make_runtime(session)
    tailer, source = _make_tailer(runtime)

    record = _assistant_record(
        usage={"input_tokens": 15, "cache_read_input_tokens": 4, "output_tokens": 6}
    )
    source.feed(_jsonl(record))
    await tailer._drain()

    runtime.publish_token_usage_record.assert_called_once()
    published = runtime.publish_token_usage_record.call_args.args[1]
    assert published.model == "claude-sonnet-4-5"
    assert published.effort == "high"


@pytest.mark.asyncio
async def test_token_record_prefers_message_model_over_stale_session_model() -> None:
    # The session's resolved_model reflects the *latest* turn, not necessarily
    # this one — a transcript replayed from offset 0 after a resume must not
    # have every earlier turn rewritten onto the current model.
    session = _make_session(resolved_model="claude-opus-4-8", effort="high")
    runtime = _make_runtime(session)
    tailer, source = _make_tailer(runtime)

    record = _assistant_record(
        model="claude-sonnet-4-5",
        usage={"input_tokens": 10, "output_tokens": 5},
    )
    source.feed(_jsonl(record))
    await tailer._drain()

    published = runtime.publish_token_usage_record.call_args.args[1]
    assert published.model == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_drain_dedupes_unchanged_context_usage() -> None:
    session = _make_session()
    runtime = _make_runtime(session)
    tailer, source = _make_tailer(runtime)

    usage = {"input_tokens": 10, "output_tokens": 5}
    record1 = _assistant_record(message_id="msg_1", usage=usage)
    record2 = _assistant_record(message_id="msg_2", usage=usage)
    source.feed(_jsonl(record1, record2))
    await tailer._drain()

    # Same (used_tokens, context_window_tokens) — only one publish
    assert runtime.update_session_fields.call_count == 1


@pytest.mark.asyncio
async def test_drain_publishes_again_when_usage_changes() -> None:
    session = _make_session()
    runtime = _make_runtime(session)
    tailer, source = _make_tailer(runtime)

    record1 = _assistant_record(
        message_id="msg_1", usage={"input_tokens": 10, "output_tokens": 5}
    )
    source.feed(_jsonl(record1))
    await tailer._drain()

    assert runtime.update_session_fields.call_count == 1
    first_used = runtime.update_session_fields.call_args.kwargs[
        "context_usage"
    ].used_tokens

    record2 = _assistant_record(
        message_id="msg_2", usage={"input_tokens": 20, "output_tokens": 8}
    )
    source.feed(_jsonl(record2))
    await tailer._drain()

    assert runtime.update_session_fields.call_count == 2
    second_used = runtime.update_session_fields.call_args.kwargs[
        "context_usage"
    ].used_tokens
    assert second_used > first_used


class _ScriptedSource:
    """Returns a queued list of reads, then unobserved."""

    def __init__(self, reads: list[TranscriptRead]) -> None:
        self._reads = list(reads)

    def read_from(
        self, offset: int, *, metadata_only: bool = False, force: bool = False
    ) -> TranscriptRead:
        if self._reads:
            return self._reads.pop(0)
        return TranscriptRead(observed=False)


@pytest.mark.asyncio
async def test_partial_record_carries_across_reads() -> None:
    session = _make_session()
    runtime = _make_runtime(session)
    plugin = MagicMock()
    plugin._pending_questions = {}
    full = _jsonl(_assistant_record())
    mid = len(full) // 2
    source = _ScriptedSource(
        [
            TranscriptRead(observed=True, data=full[:mid], size=mid, identity=(1, 1)),
            TranscriptRead(
                observed=True, data=full[mid:], size=len(full), identity=(1, 1)
            ),
        ]
    )
    tailer = TranscriptTailer(
        session_id="sess-1", source=source, runtime=runtime, plugin=plugin
    )
    await tailer._drain()  # first half: incomplete record, no emit
    assert runtime.update_session_fields.call_count == 0
    await tailer._drain()  # second half completes the record
    assert runtime.update_session_fields.call_count == 1


@pytest.mark.asyncio
async def test_start_at_end_skips_history_then_emits_append() -> None:
    session = _make_session()
    runtime = _make_runtime(session)
    plugin = MagicMock()
    plugin._pending_questions = {}
    history = _jsonl(_assistant_record(message_id="old"))
    append = _jsonl(_assistant_record(message_id="new"))
    source = _ScriptedSource(
        [
            # prime: start-at-end fetches metadata only; body (if any) discarded
            TranscriptRead(
                observed=True, data=history, size=len(history), identity=(1, 1)
            ),
            TranscriptRead(
                observed=True,
                data=append,
                size=len(history) + len(append),
                identity=(1, 1),
            ),
        ]
    )
    tailer = TranscriptTailer(
        session_id="sess-1",
        source=source,
        runtime=runtime,
        plugin=plugin,
        start_at_end=True,
    )
    await tailer._drain()  # prime: no historical replay
    assert runtime.update_session_fields.call_count == 0
    await tailer._drain()  # append emitted once
    assert runtime.update_session_fields.call_count == 1


@pytest.mark.asyncio
async def test_truncation_records_note_and_skips_replay() -> None:
    session = _make_session()
    runtime = _make_runtime(session)
    plugin = MagicMock()
    plugin._pending_questions = {}
    first = _jsonl(_assistant_record(message_id="a"))
    source = _ScriptedSource(
        [
            TranscriptRead(observed=True, data=first, size=len(first), identity=(1, 1)),
            # file shrank below the cursor: truncation
            TranscriptRead(observed=True, data=b"", size=3, identity=(1, 1)),
        ]
    )
    tailer = TranscriptTailer(
        session_id="sess-1", source=source, runtime=runtime, plugin=plugin
    )
    await tailer._drain()
    assert runtime.update_session_fields.call_count == 1
    await tailer._drain()
    runtime._record_system_event.assert_awaited_once()
    # no re-parse: still exactly one context-usage publish
    assert runtime.update_session_fields.call_count == 1
    assert tailer._fetch_offset == 3


@pytest.mark.asyncio
async def test_replacement_identity_change_skips_replay() -> None:
    session = _make_session()
    runtime = _make_runtime(session)
    plugin = MagicMock()
    plugin._pending_questions = {}
    first = _jsonl(_assistant_record(message_id="a"))
    source = _ScriptedSource(
        [
            TranscriptRead(observed=True, data=first, size=len(first), identity=(1, 1)),
            # new inode → replacement; a fresh full transcript must not replay
            TranscriptRead(
                observed=True,
                data=_jsonl(_assistant_record(message_id="b")),
                size=999,
                identity=(2, 2),
            ),
        ]
    )
    tailer = TranscriptTailer(
        session_id="sess-1", source=source, runtime=runtime, plugin=plugin
    )
    await tailer._drain()
    assert runtime.update_session_fields.call_count == 1
    await tailer._drain()
    runtime._record_system_event.assert_awaited_once()
    assert runtime.update_session_fields.call_count == 1
    assert tailer._fetch_offset == 999
