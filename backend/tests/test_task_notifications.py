from typing import Any

from waypoint.backends.task_notifications import (
    TASK_NOTIFICATION_INLINE_LIMIT,
    TASK_NOTIFICATION_ITEM_TYPE,
    TASK_NOTIFICATION_METHOD,
    TaskNotification,
    task_notification_event,
)
from waypoint.schemas import SessionStatus

PAYLOAD_KEYS = {
    "version",
    "id",
    "task_id",
    "tool_use_id",
    "kind",
    "status",
    "summary",
    "event",
    "note",
    "result_preview",
    "result_truncated",
    "event_truncated",
    "note_truncated",
    "output_available",
    "output_unavailable_reason",
}


def _notification(**overrides: Any) -> TaskNotification:
    fields: dict[str, Any] = {
        "id": "n1",
        "kind": "agent",
        "status": "completed",
        "summary": 'Agent "reviewer" finished',
    }
    fields.update(overrides)
    return TaskNotification(**fields)


def test_short_notification_is_inline_with_the_v1_payload() -> None:
    text, metadata = task_notification_event(
        _notification(result="ok", usage={"tool_uses": 2}), no_spill_reason=None
    )

    assert text == 'Agent "reviewer" finished'
    assert metadata["method"] == TASK_NOTIFICATION_METHOD
    assert metadata["item_type"] == TASK_NOTIFICATION_ITEM_TYPE
    assert metadata["status"] == SessionStatus.RUNNING
    payload = metadata["task_notification"]
    assert set(payload) == PAYLOAD_KEYS | {"usage"}
    assert payload["version"] == 1
    assert payload["result_preview"] == "ok"
    assert payload["output_available"] is False
    assert payload["output_unavailable_reason"] is None
    assert payload["usage"] == {"tool_uses": 2}
    assert not {"capture_inline_blobs", "capture_host_text"} & set(metadata)


def test_text_joins_summary_and_event_and_falls_back() -> None:
    text, _ = task_notification_event(_notification(event="tick"), no_spill_reason=None)
    assert text == 'Agent "reviewer" finished — tick'
    text, _ = task_notification_event(_notification(summary=None), no_spill_reason=None)
    assert text == "Task notification"


def test_notification_reason_applies_only_when_nothing_else_explains() -> None:
    _, metadata = task_notification_event(
        _notification(output_unavailable_reason="report unavailable"),
        no_spill_reason=None,
    )
    assert (
        metadata["task_notification"]["output_unavailable_reason"]
        == "report unavailable"
    )

    _, metadata = task_notification_event(
        _notification(
            result="r" * (TASK_NOTIFICATION_INLINE_LIMIT + 1),
            output_unavailable_reason="report unavailable",
        ),
        no_spill_reason="not kept",
    )
    payload = metadata["task_notification"]
    assert payload["output_unavailable_reason"] == "not kept"
    assert "capture_inline_blobs" not in metadata
