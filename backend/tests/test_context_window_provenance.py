"""Claude context-window model-provenance across import, model change, and
transport switch.

Covers the pure helpers (effective-model resolution, snapshot rebase, transcript
seeding) plus the Emulated tailer's use of the durable model as the window
denominator. The runtime lifecycle call sites are exercised in
test_runtime_context_rebase.py.
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from waypoint.backends.claude_code.adapter import (
    rebase_claude_context_usage,
    seed_context_usage_from_transcript,
)
from waypoint.backends.claude_code.models import resolve_import_model_id
from waypoint.backends.claude_tty.tailer import TranscriptTailer
from waypoint.schemas import (
    SessionContextUsage,
    SessionRecord,
    SessionSource,
    SessionStatus,
)


def _usage_snapshot(window: int | None) -> SessionContextUsage:
    return SessionContextUsage(
        used_tokens=1234,
        context_window_tokens=window,
        updated_at=datetime.now(UTC),
        source="claude_code",
        breakdown={"input_tokens": 1000, "cache_read_tokens": 234},
    )


def _session(model: str | None, usage: SessionContextUsage | None) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id="sess-1",
        backend="claude_code",
        source=SessionSource.MANAGED,
        transport="claude_tty",
        title="t",
        cwd="/tmp",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
        model=model,
        context_usage=usage,
    )


# ── resolve_import_model_id ───────────────────────────────────────────────


def test_resolve_import_model_prefers_request() -> None:
    assert resolve_import_model_id("sonnet[1m]", "opus[1m]") == "sonnet[1m]"


def test_resolve_import_model_blank_falls_back_to_default() -> None:
    assert resolve_import_model_id("   ", "opus[1m]") == "opus[1m]"
    assert resolve_import_model_id(None, "opus[1m]") == "opus[1m]"


def test_resolve_import_model_none_when_no_default() -> None:
    assert resolve_import_model_id(None, None) is None


# ── rebase_claude_context_usage ───────────────────────────────────────────


def test_rebase_promotes_base_window_to_1m() -> None:
    snapshot = _usage_snapshot(200_000)
    session = _session("opus[1m]", snapshot)
    rebased = rebase_claude_context_usage(session)
    assert rebased is not None
    assert rebased.context_window_tokens == 1_000_000
    # Only the window changes; the ledger is untouched.
    assert rebased.used_tokens == 1234
    assert rebased.breakdown == snapshot.breakdown


def test_rebase_explicit_model_arg_wins_over_durable() -> None:
    session = _session("opus", _usage_snapshot(200_000))
    rebased = rebase_claude_context_usage(session, model="opus[1m]")
    assert rebased is not None
    assert rebased.context_window_tokens == 1_000_000


def test_rebase_unknown_selection_clears_window() -> None:
    # A legacy session with no durable model must not retain a stale 200K nor
    # fabricate one — the denominator is cleared.
    session = _session(None, _usage_snapshot(200_000))
    rebased = rebase_claude_context_usage(session)
    assert rebased is not None
    assert rebased.context_window_tokens is None


def test_rebase_no_snapshot_returns_none() -> None:
    session = _session("opus[1m]", None)
    assert rebase_claude_context_usage(session) is None


def test_rebase_same_window_returns_unchanged_snapshot() -> None:
    # Idempotency signal: an already-correct window round-trips the same object
    # so the runtime can skip the write/broadcast.
    snapshot = _usage_snapshot(1_000_000)
    session = _session("opus[1m]", snapshot)
    assert rebase_claude_context_usage(session) is snapshot


# ── seed_context_usage_from_transcript ────────────────────────────────────


def _assistant_line(usage: dict, *, sidechain: bool = False, model: str = "x") -> str:
    rec: dict = {
        "type": "assistant",
        "message": {"id": "m", "model": model, "usage": usage},
    }
    if sidechain:
        rec["isSidechain"] = True
    return json.dumps(rec)


def test_seed_reads_last_assistant_usage_with_durable_window(tmp_path) -> None:
    transcript = tmp_path / "thread.jsonl"
    transcript.write_text(
        "\n".join(
            [
                _assistant_line({"input_tokens": 10, "output_tokens": 2}),
                _assistant_line({"input_tokens": 500, "cache_read_input_tokens": 100}),
            ]
        ),
        encoding="utf-8",
    )
    snapshot = seed_context_usage_from_transcript(transcript, "opus[1m]")
    assert snapshot is not None
    assert snapshot.used_tokens == 600  # last turn: 500 + 100
    assert snapshot.context_window_tokens == 1_000_000


def test_seed_skips_sidechain_records(tmp_path) -> None:
    transcript = tmp_path / "thread.jsonl"
    transcript.write_text(
        "\n".join(
            [
                _assistant_line({"input_tokens": 500}),
                _assistant_line({"input_tokens": 999}, sidechain=True),
            ]
        ),
        encoding="utf-8",
    )
    snapshot = seed_context_usage_from_transcript(transcript, "opus")
    assert snapshot is not None
    # The subagent (sidechain) turn is skipped; the main turn wins.
    assert snapshot.used_tokens == 500


def test_seed_missing_file_returns_none(tmp_path) -> None:
    assert seed_context_usage_from_transcript(tmp_path / "nope.jsonl", "opus") is None


def test_seed_no_usage_returns_none(tmp_path) -> None:
    transcript = tmp_path / "thread.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}), encoding="utf-8"
    )
    assert seed_context_usage_from_transcript(transcript, "opus") is None


# ── Emulated tailer uses the durable model as the denominator ─────────────


class _FeedOnceSource:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._size = len(data)

    def read_from(self, offset, *, metadata_only=False, force=False):
        from waypoint.backends.claude_tty.byte_source import TranscriptRead

        data = b"" if (metadata_only or offset >= self._size) else self._data
        return TranscriptRead(
            observed=True, data=data, size=self._size, identity=(1, 1)
        )


@pytest.mark.asyncio
async def test_tailer_uses_durable_1m_model_over_transcript_resolved_id() -> None:
    # The transcript's resolved id is a base family (claude-opus-4-8 → 200K),
    # but the session's durable model is opus[1m]; the published window is 1M.
    session = _session("opus[1m]", None)
    runtime = MagicMock()
    runtime.storage.get_session.return_value = session
    runtime.update_session_fields = AsyncMock()
    runtime.publish_token_usage_record = AsyncMock()
    runtime._emit_adapter_event = AsyncMock()

    record = {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "model": "claude-opus-4-8",
            "content": [{"type": "text", "text": "hi"}],
            "usage": {"input_tokens": 42, "output_tokens": 1},
        },
    }
    data = (json.dumps(record) + "\n").encode()
    plugin = MagicMock()
    plugin._pending_questions = {}
    tailer = TranscriptTailer(
        session_id="sess-1",
        source=_FeedOnceSource(data),
        runtime=runtime,
        plugin=plugin,
    )
    await tailer._drain()

    runtime.update_session_fields.assert_called_once()
    snapshot = runtime.update_session_fields.call_args.kwargs["context_usage"]
    assert snapshot.context_window_tokens == 1_000_000
