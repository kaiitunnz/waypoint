"""Historical transcript -> EventRecord conversion for thread-history import.

Feeds the ``reader`` callable ``runtime.seed_thread_history`` expects for both
the claude_code (direct SDK) and claude_tty transports, which persist and
resume the same on-disk ``~/.claude/projects/<enc-cwd>/<uuid>.jsonl``
transcript. Reuses the pure block helpers from ``normalize.py`` so seeded
events carry metadata shaped like the live normalizers' output
(``item_id``/``tool_use_id``/``tool_name``) — what ``parseEvent`` /
``TranscriptCard`` on the frontend key off of to render and pair
tool_call/tool_result cards.

Unlike the live normalizers (``adapter.py``'s ``_handle_user`` and
``claude_tty/normalize.py``'s ``_process_user``), which intentionally skip
plain user text because it is already recorded at the input boundary
(``runtime._record_user_event``), historical replay has no such live input
boundary: user turns are reconstructed here or they vanish from the imported
transcript entirely.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from waypoint.backends.claude_code.normalize import (
    is_injected_user_turn,
    iter_content_blocks,
    stringify_tool_result,
)
from waypoint.backends.claude_code.threads import (
    parse_iso_timestamp,
    read_local_claude_transcript,
)
from waypoint.schemas import EventKind, EventRecord, SessionStatus


async def read_local_claude_history(
    session_id: str, thread_id: str
) -> list[EventRecord]:
    """Read and convert a local Claude transcript for thread-history import."""
    records = await asyncio.to_thread(read_local_claude_transcript, thread_id)
    return convert_transcript_records(session_id, records)


def convert_transcript_records(
    session_id: str, records: list[dict[str, Any]]
) -> list[EventRecord]:
    """Convert ordered raw transcript records into ordered EventRecords.

    Only ``assistant`` text/tool_use and ``user`` text/tool_result records
    produce events; other record types (system notes, compact boundaries,
    summaries) carry no chat content worth replaying and are dropped.
    """
    events: list[EventRecord] = []
    last_ts = datetime.now(UTC)
    for record in records:
        ts = _record_timestamp(record) or last_ts
        last_ts = ts
        rec_type = record.get("type")
        if rec_type == "assistant":
            events.extend(_convert_assistant(session_id, record, ts))
        elif rec_type == "user":
            events.extend(_convert_user(session_id, record, ts))
    return events


def _convert_assistant(
    session_id: str, record: dict[str, Any], ts: datetime
) -> list[EventRecord]:
    message: dict[str, Any] = record.get("message") or {}
    message_id = str(message.get("id") or "")
    events: list[EventRecord] = []
    for block in iter_content_blocks(message.get("content")):
        block_type = block.get("type")
        if block_type == "text":
            text = str(block.get("text") or "")
            if not text:
                continue
            events.append(
                _event(
                    session_id,
                    ts,
                    EventKind.AGENT_OUTPUT,
                    text,
                    {
                        "method": "assistant.text",
                        "item_id": message_id,
                        "payload": block,
                    },
                )
            )
        elif block_type == "tool_use":
            tool_use_id = str(block.get("id") or "")
            tool_name = str(block.get("name") or "tool")
            input_text = json.dumps(block.get("input") or {}, indent=2)
            events.append(
                _event(
                    session_id,
                    ts,
                    EventKind.TOOL_CALL,
                    f"{tool_name}\n{input_text}",
                    {
                        "method": "assistant.tool_use",
                        "item_id": tool_use_id,
                        "tool_name": tool_name,
                        "tool_use_id": tool_use_id,
                        "payload": block,
                    },
                )
            )
        # "thinking" and other block types render no card live either.
    return events


def _convert_user(
    session_id: str, record: dict[str, Any], ts: datetime
) -> list[EventRecord]:
    message: dict[str, Any] = record.get("message") or {}
    content = message.get("content")
    if is_injected_user_turn(content):
        return []
    blocks = iter_content_blocks(content)
    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    if tool_result_blocks:
        # A tool_result record never mixes in genuine human text (the CLI
        # only echoes the tool verdict here), matching the live normalizers'
        # assumption in ``_handle_user``/``_process_user``.
        return [
            _event(
                session_id,
                ts,
                EventKind.TOOL_RESULT,
                stringify_tool_result(block.get("content")),
                {
                    "method": "user.tool_result",
                    "item_id": str(block.get("tool_use_id") or ""),
                    "tool_use_id": str(block.get("tool_use_id") or ""),
                    "is_error": bool(block.get("is_error")),
                    "payload": block,
                },
            )
            for block in tool_result_blocks
        ]
    text = "\n".join(
        str(block.get("text") or "")
        for block in blocks
        if block.get("type") == "text" and block.get("text")
    ).strip()
    if not text:
        return []
    return [
        _event(
            session_id,
            ts,
            EventKind.USER_INPUT,
            text,
            {"method": "user.text", "submit": True, "imported": True},
        )
    ]


def _event(
    session_id: str,
    ts: datetime,
    kind: EventKind,
    text: str,
    metadata: dict[str, Any],
) -> EventRecord:
    return EventRecord(
        session_id=session_id,
        ts=ts,
        kind=kind,
        text=text,
        metadata={**metadata, "status": SessionStatus.RUNNING},
        sequence=0,  # storage.seed_events reassigns sequences from list order.
    )


def _record_timestamp(record: dict[str, Any]) -> datetime | None:
    raw = record.get("timestamp")
    return parse_iso_timestamp(raw) if isinstance(raw, str) else None
