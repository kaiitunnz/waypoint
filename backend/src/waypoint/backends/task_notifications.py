"""Backend-neutral task-notification contract.

A task notification reports a background task's lifecycle — a subagent
finishing, a monitor firing, a background command exiting — as a standalone
``SYSTEM_NOTE`` event the frontend renders as a task card. Any agent plugin may
emit one; each owns how it learns of the task and builds a
:class:`TaskNotification`, and :func:`task_notification_event` turns that into
the event's ``(text, metadata)``.
"""

from dataclasses import dataclass, field
from typing import Any

from waypoint.schemas import AttachmentOrigin, SessionStatus

TASK_NOTIFICATION_METHOD = "task_notification"
# Stored events from before the contract was backend-neutral; the frontend
# still renders them.
LEGACY_TASK_NOTIFICATION_METHODS = frozenset({"claude.task_notification"})
TASK_NOTIFICATION_ITEM_TYPE = "task_notification"
TASK_NOTIFICATION_VERSION = 1
# Largest body field (result/event/note) kept verbatim in event metadata, which
# every client receives whether or not the card is ever expanded. A larger body
# spills to a session attachment and is read back on demand.
TASK_NOTIFICATION_INLINE_LIMIT = 4 * 1024
# A summary is a headline, not a body: bound it hard, and never spill it.
TASK_NOTIFICATION_SUMMARY_LIMIT = 4 * 1024
# Why a history import never has the full text of an over-long body.
NOT_CAPTURED_ON_IMPORT = "full output not captured on import"
# Why the operator's ``task_output_capture_enabled`` switch kept it out.
CAPTURE_DISABLED = "output capture is disabled"


@dataclass(frozen=True)
class TaskNotification:
    id: str
    kind: str  # agent | monitor | background_command | unknown
    status: str | None
    summary: str | None
    task_id: str | None = None
    tool_use_id: str | None = None
    result: str | None = None
    event: str | None = None
    note: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    # Why the full output is unavailable when nothing else explains it.
    output_unavailable_reason: str | None = None


def _truncate_utf8(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def bounded(
    text: str | None, limit: int = TASK_NOTIFICATION_INLINE_LIMIT
) -> tuple[str | None, bool]:
    """Cap ``text`` to ``limit`` bytes, reporting whether anything was cut."""
    if text is None or len(text.encode("utf-8")) <= limit:
        return text, False
    return _truncate_utf8(text, limit), True


def _compact_text(summary: str | None, event: str | None) -> str:
    # Takes the bounded values: this becomes ``EventRecord.text``.
    parts = [part for part in (summary, event) if part]
    return " — ".join(parts) if parts else "Task notification"


def task_notification_event(
    notification: TaskNotification,
    *,
    allow_spill: bool,
    no_spill_reason: str,
    output_path: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build the ``(text, metadata)`` for a task-notification event.

    With ``allow_spill``, an over-long body spills its full text on
    ``capture_inline_blobs``; without it, the card says ``no_spill_reason``.
    ``output_path`` names a host file holding the task's full output: with
    ``allow_spill`` it rides ``capture_host_text``, and since that file already
    keeps the event stream, an over-long ``event`` is not spilled again.
    """
    result_preview, result_truncated = bounded(notification.result)
    event_text, event_truncated = bounded(notification.event)
    note_text, note_truncated = bounded(notification.note)
    summary_text, _ = bounded(notification.summary, TASK_NOTIFICATION_SUMMARY_LIMIT)

    spills: list[dict[str, Any]] = []
    if allow_spill:
        for name, text, truncated in (
            ("result", notification.result, result_truncated),
            ("event", notification.event, event_truncated and output_path is None),
            ("note", notification.note, note_truncated),
        ):
            if truncated and text is not None:
                spills.append(
                    {
                        "filename": f"task-{notification.id}-{name}.txt",
                        "text": text,
                        "mime": "text/plain; charset=utf-8",
                    }
                )

    output_available = False
    output_unavailable_reason: str | None = None
    capture_path: str | None = None
    if output_path is not None:
        if allow_spill:
            capture_path = output_path
            output_available = True
        else:
            output_unavailable_reason = no_spill_reason
    elif spills:
        output_available = True
    elif not allow_spill and (result_truncated or event_truncated or note_truncated):
        output_unavailable_reason = no_spill_reason
    if not output_available and output_unavailable_reason is None:
        output_unavailable_reason = notification.output_unavailable_reason

    payload: dict[str, Any] = {
        "version": TASK_NOTIFICATION_VERSION,
        "id": notification.id,
        "task_id": notification.task_id,
        "tool_use_id": notification.tool_use_id,
        "kind": notification.kind,
        "status": notification.status,
        "summary": summary_text,
        "event": event_text,
        "note": note_text,
        "result_preview": result_preview,
        "result_truncated": result_truncated,
        "event_truncated": event_truncated,
        "note_truncated": note_truncated,
        "output_available": output_available,
        "output_unavailable_reason": output_unavailable_reason,
    }
    if notification.usage:
        payload["usage"] = notification.usage

    metadata: dict[str, Any] = {
        "method": TASK_NOTIFICATION_METHOD,
        "item_type": TASK_NOTIFICATION_ITEM_TYPE,
        "task_notification": payload,
        "status": SessionStatus.RUNNING,
    }
    if capture_path is not None:
        # Captured as *text*: a report small enough to render whole rides in
        # the event itself, so the card needs no attachment and no fetch. Only
        # an oversized one becomes a pinned attachment with a bounded preview.
        metadata["capture_host_text"] = [capture_path]
    if spills:
        metadata["capture_inline_blobs"] = spills
    if capture_path is not None or spills:
        metadata["capture_origin"] = AttachmentOrigin.TASK_OUTPUT
    return _compact_text(summary_text, event_text), metadata
