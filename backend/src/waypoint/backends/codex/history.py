"""Historical Codex thread -> ``EventRecord`` conversion.

Feeds ``runtime.seed_thread_history`` when importing a Codex thread with
``import_history=True``. Converts each ``Turn``'s ``ThreadItem``s (from
``client.thread_read(thread_id, include_turns=True)``) into ``EventRecord``s,
reusing ``codex/event_registry.py``'s per-item-type renderers so a synthesized
historical tool item carries the same metadata envelope (``item_id``,
``item_type``, ``tool_name``, ``payload.item``, diff-preview keys) that the
live adapter builds for ``item/started``/``item/completed`` notifications —
the frontend pairs tool cards solely on ``metadata.item_id``, and Codex items
carry no ``tool_use_id`` to fall back on.
"""

from datetime import UTC, datetime
from typing import Any

from openai_codex.generated.v2_all import Turn

from waypoint.backends.codex.event_registry import (
    extract_tool_name,
    persisted_item,
    reasoning_text,
    render_item_completed,
    render_item_started,
)
from waypoint.backends.codex.normalize import (
    diff_preview_for_notification,
    plan_metadata_for_item,
    set_completed_outcome,
)
from waypoint.backends.codex.questions import (
    ReplyEntry,
    apply_async_question,
    is_async_message,
    parse_reply,
)
from waypoint.backends.diff_preview import preview_to_metadata
from waypoint.questions import (
    ASK_QUESTION_ANSWER,
    ASK_QUESTION_CLOSED,
    ASK_USER_QUESTION_TOOL,
    QuestionCloseReason,
)
from waypoint.schemas import EventKind, EventRecord


def turns_to_events(turns: list[Turn], session_id: str) -> list[EventRecord]:
    """Replay a Codex thread's turns into ``EventRecord``s in sequence order.

    Async questions left unanswered before the final turn get a closure note;
    the final turn's stay open because it may have ended waiting on the answer.
    """
    if not turns:
        return []
    events: list[EventRecord] = []
    for turn in turns:
        earlier = len(events)
        started_at = _turn_timestamp(turn.started_at, turn.completed_at)
        completed_at = _turn_timestamp(turn.completed_at, turn.started_at)
        for item in turn.items:
            events.extend(
                _item_to_events(item.root, session_id, started_at, completed_at)
            )
    return events + _close_abandoned_questions(
        events, earlier, session_id, completed_at
    )


def _close_abandoned_questions(
    events: list[EventRecord], earlier: int, session_id: str, ts: datetime
) -> list[EventRecord]:
    """Closure notes for questions asked in ``events[:earlier]`` that no
    answer resolved."""
    answered = {
        event.metadata.get("tool_use_id")
        for event in events
        if event.metadata.get("kind") == ASK_QUESTION_ANSWER
    }
    return [
        _event(
            session_id,
            ts,
            EventKind.SYSTEM_NOTE,
            "Question closed: the agent moved on without an answer",
            {
                "kind": ASK_QUESTION_CLOSED,
                "tool_use_id": event.metadata["tool_use_id"],
                "reason": QuestionCloseReason.PROVIDER_CLOSED.value,
            },
        )
        for event in events[:earlier]
        if event.kind == EventKind.TOOL_CALL
        and event.metadata.get("tool_name") == ASK_USER_QUESTION_TOOL
        and event.metadata["tool_use_id"] not in answered
    ]


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
        replies = parse_reply(text)
        if replies is not None:
            return _reply_events(replies, session_id, started_at)
        return [_event(session_id, started_at, EventKind.USER_INPUT, text, metadata={})]

    item_dict = item.model_dump(mode="json", by_alias=True)
    if is_async_message(item_dict):
        return _async_message_events(item_dict, session_id, completed_at)
    if item_type == "reasoning" and not reasoning_text(item_dict):
        # Live sessions mark encrypted reasoning with a detail note; an import
        # has no use for it.
        return []

    item_id = item_dict.get("id")
    tool_name = extract_tool_name(item_type, item_dict)
    events: list[EventRecord] = []
    for method, rendered, ts in (
        ("item/started", render_item_started(item_dict), started_at),
        ("item/completed", render_item_completed(item_dict), completed_at),
    ):
        if rendered is None or not rendered.text:
            continue
        metadata = _envelope(
            method, item_dict, item_id, item_type, tool_name, rendered.status
        )
        metadata.update(rendered.metadata)
        events.append(_event(session_id, ts, rendered.kind, rendered.text, metadata))
    return events


def _async_message_events(
    item_dict: dict[str, Any], session_id: str, completed_at: datetime
) -> list[EventRecord]:
    rendered = render_item_completed(item_dict)
    if rendered is None or not rendered.text:
        return []
    kind, text, status = rendered.triple()
    item_id = item_dict.get("id")
    metadata = _envelope(
        "item/completed", item_dict, item_id, "agentMessage", None, status
    )
    apply_async_question(metadata, item_dict)
    return [_event(session_id, completed_at, kind, text, metadata)]


def _reply_events(
    replies: list[ReplyEntry], session_id: str, ts: datetime
) -> list[EventRecord]:
    """One answer event per question card, in the live answer-event shape."""
    by_card: dict[str, list[ReplyEntry]] = {}
    for reply in replies:
        by_card.setdefault(reply.tool_use_id, []).append(reply)
    events: list[EventRecord] = []
    for tool_use_id, entries in by_card.items():
        text = ", ".join(f'"{entry.question}"="{entry.answer}"' for entry in entries)
        events.append(
            _event(
                session_id,
                ts,
                EventKind.USER_INPUT,
                text,
                {
                    "kind": ASK_QUESTION_ANSWER,
                    "tool_use_id": tool_use_id,
                    "answers": [
                        {"question": entry.question, "answer": entry.answer}
                        for entry in entries
                    ],
                },
            )
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
    payload = {"item": persisted_item(item_dict)}
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
    if method == "item/completed":
        set_completed_outcome(metadata, item_type, item_dict)
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
