from typing import Any

from waypoint.backends.task_notifications import (
    TASK_NOTIFICATION_INLINE_LIMIT,
    TASK_NOTIFICATION_ITEM_TYPE,
    TASK_NOTIFICATION_METHOD,
    TASK_NOTIFICATION_SUMMARY_LIMIT,
    TaskNotification,
    task_notification_event,
)
from waypoint.schemas import AttachmentOrigin, SessionStatus

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
        _notification(result="ok", usage={"tool_uses": 2}),
        allow_spill=True,
        no_spill_reason="unused",
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
    text, _ = task_notification_event(
        _notification(event="tick"), allow_spill=True, no_spill_reason=""
    )
    assert text == 'Agent "reviewer" finished — tick'
    text, _ = task_notification_event(
        _notification(summary=None), allow_spill=True, no_spill_reason=""
    )
    assert text == "Task notification"


def test_long_body_spills_when_allowed() -> None:
    result = "é" * TASK_NOTIFICATION_INLINE_LIMIT
    _, metadata = task_notification_event(
        _notification(result=result), allow_spill=True, no_spill_reason="unused"
    )

    payload = metadata["task_notification"]
    assert payload["result_truncated"] is True
    assert len(payload["result_preview"].encode()) <= TASK_NOTIFICATION_INLINE_LIMIT
    assert payload["output_available"] is True
    [spill] = metadata["capture_inline_blobs"]
    assert spill["filename"] == "task-n1-result.txt"
    assert spill["text"] == result
    assert metadata["capture_origin"] == AttachmentOrigin.TASK_OUTPUT


def test_long_body_without_spill_says_why() -> None:
    _, metadata = task_notification_event(
        _notification(note="n" * (TASK_NOTIFICATION_INLINE_LIMIT + 1)),
        allow_spill=False,
        no_spill_reason="not kept",
    )

    payload = metadata["task_notification"]
    assert payload["note_truncated"] is True
    assert payload["output_available"] is False
    assert payload["output_unavailable_reason"] == "not kept"
    assert "capture_inline_blobs" not in metadata


def test_summary_is_bounded_and_never_spilled() -> None:
    _, metadata = task_notification_event(
        _notification(summary="s" * (TASK_NOTIFICATION_SUMMARY_LIMIT + 5)),
        allow_spill=True,
        no_spill_reason="",
    )
    payload = metadata["task_notification"]
    assert len(payload["summary"]) == TASK_NOTIFICATION_SUMMARY_LIMIT
    assert "capture_inline_blobs" not in metadata


def test_output_path_is_captured_and_replaces_the_event_spill() -> None:
    _, metadata = task_notification_event(
        _notification(event="e" * (TASK_NOTIFICATION_INLINE_LIMIT + 1)),
        allow_spill=True,
        no_spill_reason="",
        output_path="/tmp/task.out",
    )

    assert metadata["capture_host_text"] == ["/tmp/task.out"]
    assert "capture_inline_blobs" not in metadata
    assert metadata["task_notification"]["output_available"] is True


def test_output_path_without_spill_says_why() -> None:
    _, metadata = task_notification_event(
        _notification(),
        allow_spill=False,
        no_spill_reason="capture off",
        output_path="/tmp/task.out",
    )

    assert "capture_host_text" not in metadata
    payload = metadata["task_notification"]
    assert payload["output_available"] is False
    assert payload["output_unavailable_reason"] == "capture off"


def test_notification_reason_applies_only_when_nothing_else_explains() -> None:
    _, metadata = task_notification_event(
        _notification(output_unavailable_reason="report unavailable"),
        allow_spill=True,
        no_spill_reason="",
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
        allow_spill=False,
        no_spill_reason="not kept",
    )
    assert metadata["task_notification"]["output_unavailable_reason"] == "not kept"
