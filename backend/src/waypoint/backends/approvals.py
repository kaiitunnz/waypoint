"""Shared approval-decision vocabulary.

The frontend and CLI send a decision string for a tool approval; several
distinct strings ("accept", "approve", "acceptForSession", …) all mean "let the
tool run". Each backend maps that to its own mechanism (claude_code → an "allow"
control response, claude_tty/tmux → the dialog's Yes keystroke). Centralising
the vocabulary keeps those backends from drifting apart — a backend that
recognises a narrower set silently turns an unrecognised approval into a
decline.
"""

import re

from waypoint.schemas import EventKind, EventRecord

APPROVE_DECISIONS = frozenset(
    {
        "approve",
        "accept",
        "yes",
        "y",
        "allow",
        "acceptforsession",
        "acceptalways",
    }
)


def is_approve_decision(decision: str) -> bool:
    """Return True when ``decision`` means the tool should be allowed to run."""
    return decision.strip().lower() in APPROVE_DECISIONS


_RESOLUTION_TEXT = re.compile(r"Approval response sent|Approval timed out", re.I)


def _is_resolution(event: EventRecord) -> bool:
    return event.kind is EventKind.SYSTEM_NOTE and (
        event.metadata.get("method") == "approval.invalidated"
        or bool(_RESOLUTION_TEXT.search(event.text))
    )


def open_approval_requests(events: list[EventRecord]) -> list[EventRecord]:
    """Approval requests the transcript still shows as pending, oldest first.

    Mirrors the frontend's approval pager: a resolution note dequeues its
    ``approval_id``, or the oldest request when it carries none.
    """
    queue: list[EventRecord] = []
    for event in events:
        if event.kind is EventKind.APPROVAL_REQUEST:
            queue.append(event)
        elif _is_resolution(event):
            approval_id = event.metadata.get("approval_id")
            if isinstance(approval_id, str):
                queue = [
                    item
                    for item in queue
                    if item.metadata.get("approval_id") != approval_id
                ]
            elif queue:
                queue.pop(0)
    return queue
