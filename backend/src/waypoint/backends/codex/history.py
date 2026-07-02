"""Historical Codex thread -> ``EventRecord`` conversion.

Feeds ``runtime.seed_thread_history`` when importing a Codex thread with
``import_history=True``. Converts each ``Turn``'s ``ThreadItem``s (from
``client.thread_read(thread_id, include_turns=True)``) into ``EventRecord``s,
reusing ``codex/normalize.py``'s per-item-type formatters so a synthesized
historical tool item carries the same metadata envelope (``item_id``,
``item_type``, ``tool_name``, ``payload.item``, diff-preview keys) that the
live adapter builds for ``item/started``/``item/completed`` notifications —
the frontend pairs tool cards solely on ``metadata.item_id``, and Codex items
carry no ``tool_use_id`` to fall back on.
"""

from datetime import UTC, datetime
from typing import Any

from openai_codex.generated.v2_all import Turn

from waypoint.backends.codex.normalize import (
    _format_item_completed,
    _format_item_started,
    diff_preview_for_notification,
    extract_tool_name,
    plan_metadata_for_item,
)
from waypoint.backends.diff_preview import preview_to_metadata
from waypoint.schemas import EventKind, EventRecord


def turns_to_events(turns: list[Turn], session_id: str) -> list[EventRecord]:
    """Replay a Codex thread's turns into ``EventRecord``s in sequence order."""
    events: list[EventRecord] = []
    for turn in turns:
        started_at = _turn_timestamp(turn.started_at, turn.completed_at)
        completed_at = _turn_timestamp(turn.completed_at, turn.started_at)
        for item in turn.items:
            events.extend(
                _item_to_events(item.root, session_id, started_at, completed_at)
            )
    return events


def _turn_timestamp(primary: int | None, fallback: int | None) -> datetime:
    epoch = primary if primary is not None else fallback
    return datetime.fromtimestamp(epoch or 0, UTC)


def _item_to_events(
    item: Any, session_id: str, started_at: datetime, completed_at: datetime
) -> list[EventRecord]:
    item_type = getattr(item, "type", None)
    if item_type == "userMessage":
        text = _user_message_text(item)
        if not text:
            return []
        return [_event(session_id, started_at, EventKind.USER_INPUT, text, metadata={})]

    item_dict = item.model_dump(mode="json", by_alias=True)
    call_kind, call_text, call_status = _format_item_started(item_dict)
    if call_kind is None or not call_text:
        return []

    item_id = item_dict.get("id")
    tool_name = extract_tool_name(item_type, item_dict)
    call_metadata = _envelope(
        "item/started", item_dict, item_id, item_type, tool_name, call_status
    )
    call_event = _event(session_id, started_at, call_kind, call_text, call_metadata)
    if call_kind != EventKind.TOOL_CALL:
        return [call_event]

    events = [call_event]
    result_kind, result_text, result_status = _format_item_completed(item_dict)
    if result_kind is not None and result_text:
        result_metadata = _envelope(
            "item/completed", item_dict, item_id, item_type, tool_name, result_status
        )
        events.append(
            _event(session_id, completed_at, result_kind, result_text, result_metadata)
        )
    return events


def _envelope(
    method: str,
    item_dict: dict[str, Any],
    item_id: Any,
    item_type: str | None,
    tool_name: str | None,
    status: Any,
) -> dict[str, Any]:
    payload = {"item": item_dict}
    metadata: dict[str, Any] = {
        "method": method,
        "payload": payload,
        "status": status,
    }
    if isinstance(item_id, str) and item_id:
        metadata["item_id"] = item_id
    if isinstance(item_type, str) and item_type:
        metadata["item_type"] = item_type
    if tool_name:
        metadata["tool_name"] = tool_name
    plan_envelope = plan_metadata_for_item(item_dict)
    if plan_envelope is not None:
        metadata["plan"] = plan_envelope
    diff_preview = diff_preview_for_notification(method, payload)
    metadata.update(preview_to_metadata(diff_preview))
    return metadata


def _user_message_text(item: Any) -> str:
    parts: list[str] = []
    for part in item.content:
        text = getattr(part.root, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)


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
        metadata=metadata,
        sequence=0,
    )
