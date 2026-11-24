from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from waypoint.backends.approvals import (
    approval_request_id,
    is_approval_resolution,
    open_approval_requests,
)
from waypoint.schemas import (
    EventKind,
    EventRecord,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage


def event(
    kind: EventKind = EventKind.APPROVAL_REQUEST,
    text: str = "Approve",
    metadata: dict[str, Any] | None = None,
    sequence: int = 1,
) -> EventRecord:
    return EventRecord(
        session_id="s1",
        ts=datetime.now(UTC),
        kind=kind,
        text=text,
        metadata=metadata or {},
        sequence=sequence,
    )


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            {
                "interaction": {"request_id": "from-interaction"},
                "approval_id": "top-level",
                "approval": {"approval_id": "nested"},
            },
            "from-interaction",
        ),
        ({"approval_id": "top-level", "approval": {"approval_id": "n"}}, "top-level"),
        ({"approval": {"approval_id": "nested"}}, "nested"),
        ({"interaction": {"request_id": ""}, "approval_id": "top-level"}, "top-level"),
        ({"interaction": "bogus", "approval": ["bogus"]}, None),
        ({}, None),
    ],
)
def test_approval_request_id_prefers_the_interaction_envelope(
    metadata: dict[str, Any], expected: str | None
) -> None:
    assert approval_request_id(event(metadata=metadata)) == expected


def test_a_resolution_note_dequeues_a_request_whose_id_is_nested() -> None:
    # OpenCode's request keeps its id in ``approval`` / ``interaction`` only.
    opencode = event(
        metadata={
            "approval": {"approval_id": "perm-1"},
            "interaction": {"request_id": "perm-1"},
        }
    )
    other = event(metadata={"approval_id": "other"}, sequence=2)
    answered = event(
        EventKind.SYSTEM_NOTE,
        "Approval response sent: approve",
        {"approval_id": "perm-1"},
        sequence=3,
    )

    assert open_approval_requests([opencode, other, answered]) == [other]


def test_is_approval_resolution_matches_text_case_insensitively() -> None:
    assert is_approval_resolution(
        event(EventKind.SYSTEM_NOTE, "approval TIMED OUT after 5m")
    )
    assert is_approval_resolution(
        event(EventKind.SYSTEM_NOTE, "x", {"method": "approval.invalidated"})
    )
    assert not is_approval_resolution(event(EventKind.SYSTEM_NOTE, "Unrelated"))
    assert not is_approval_resolution(
        event(EventKind.AGENT_OUTPUT, "Approval response sent")
    )


def test_stored_approval_events_hold_exactly_the_requests_and_resolutions(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    now = datetime.now(UTC)
    storage.create_session(
        SessionRecord(
            id="s1",
            backend="claude_code",
            source=SessionSource.MANAGED,
            transport="claude_cli",
            title="t",
            cwd="/tmp",
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path="/tmp/r",
            structured_log_path="/tmp/e",
        )
    )
    rows: list[tuple[EventKind, str, dict[str, Any]]] = [
        (EventKind.APPROVAL_REQUEST, "Approve", {"approval_id": "a"}),
        (EventKind.SYSTEM_NOTE, "Approval response sent: approve", {}),
        (EventKind.SYSTEM_NOTE, "approval timed out", {}),
        (EventKind.SYSTEM_NOTE, "Approval TIMED OUT", {}),
        (EventKind.SYSTEM_NOTE, "cleared", {"method": "approval.invalidated"}),
        (EventKind.SYSTEM_NOTE, "Unrelated note", {"method": "other"}),
        (EventKind.SYSTEM_NOTE, "Plan declined", {"plan_decision": "decline"}),
        (EventKind.AGENT_OUTPUT, "Approval response sent", {}),
        (EventKind.APPROVAL_REQUEST, "Approve again", {"approval_id": "b"}),
    ]
    appended = [
        storage.append_event(
            event(kind, text, metadata, sequence=storage.next_sequence("s1"))
        )
        for kind, text, metadata in rows
    ]

    stored = storage.list_approval_events("s1")

    assert stored == [
        e
        for e in appended
        if e.kind is EventKind.APPROVAL_REQUEST or is_approval_resolution(e)
    ]
    assert len(stored) == 6
