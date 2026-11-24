"""The pending-asks endpoint and its frame on the global sessions stream."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from waypoint.api import create_app
from waypoint.questions import QuestionDecline, QuestionLiveness
from waypoint.schemas import (
    EventKind,
    EventRecord,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.transports.base import TransportAdapter


class QuestionPlugin:
    question_parks_turn = True

    def question_liveness(
        self, runtime: Any, session: SessionRecord, tool_use_ids: list[str]
    ) -> dict[str, QuestionLiveness]:
        return dict.fromkeys(tool_use_ids, QuestionLiveness.ACTIONABLE)

    async def decline_question(
        self, runtime: Any, session: SessionRecord, tool_use_id: str
    ) -> QuestionDecline:
        return QuestionDecline.TURN_RESUMES


class LiveApprovals(TransportAdapter):
    is_structured = True

    def __init__(self) -> None:
        self.ids: tuple[str, ...] = ()

    async def send_input(self, session, text, attachments=None) -> None: ...

    async def interrupt(self, session) -> None: ...

    async def terminate(self, session) -> None: ...

    async def respond_to_approval(
        self, session, decision, text, approval_id=None
    ) -> bool:
        return True

    def has_pending_approval(self, session: SessionRecord) -> bool:
        return bool(self.ids)

    def pending_approval_ids(self, session: SessionRecord) -> tuple[str, ...]:
        return self.ids


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, str]:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    context = app.state.context
    runtime = context.runtime
    monkeypatch.setattr(
        runtime.registry, "plugin_for", lambda session: QuestionPlugin()
    )
    transport = LiveApprovals()
    monkeypatch.setattr(runtime, "transport_for", lambda session: transport)
    runtime.live_approvals = transport
    now = datetime.now(UTC)
    runtime.storage.create_session(
        SessionRecord(
            id="s1",
            backend="claude_code",
            source=SessionSource.MANAGED,
            transport="claude_cli",
            title="work",
            cwd="/tmp",
            status=SessionStatus.WAITING_INPUT,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(tmp_path / "raw.log"),
            structured_log_path=str(tmp_path / "events.jsonl"),
        )
    )
    runtime.storage.append_event(
        EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.TOOL_CALL,
            text="AskUserQuestion",
            metadata={
                "tool_name": "AskUserQuestion",
                "tool_use_id": "q1",
                "payload": {
                    "input": {
                        "questions": [
                            {"question": "Ship?", "options": [{"label": "yes"}]}
                        ]
                    }
                },
            },
            sequence=1,
        )
    )
    return app, context, context.tokens.issue().token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_pending_asks_requires_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _, _ = _build(tmp_path, monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/pending-asks")

    assert response.status_code == 401


async def test_pending_asks_lists_the_open_asks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _, token = _build(tmp_path, monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/pending-asks", headers=_auth(token))

    body = response.json()
    assert response.status_code == 200
    assert set(body) == {"epoch", "revision", "items"}
    [item] = body["items"]
    assert item["id"] == "question:s1:q1"
    assert item["question"]["questions"][0]["options"] == [
        {"label": "yes", "description": None, "recommended": False}
    ]
    assert item["approval"] is None and item["plan"] is None


def test_stream_sends_the_feed_after_the_session_list_then_live_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, context, token = _build(tmp_path, monkeypatch)
    runtime = context.runtime
    with TestClient(app) as client:
        assert client.portal is not None
        with client.websocket_connect(f"/ws/sessions?token={token}") as ws:
            assert ws.receive_json()["type"] == "session_list_update"
            first = ws.receive_json()
            assert first["type"] == "pending_asks"
            assert [i["id"] for i in first["payload"]["items"]] == ["question:s1:q1"]

            runtime.live_approvals.ids = ("a1",)
            client.portal.call(
                runtime._emit_adapter_event,
                "s1",
                EventKind.APPROVAL_REQUEST,
                "Approve Bash command:\nls",
                {
                    "approval_id": "a1",
                    "tool_name": "Bash",
                    "tool_input": {"command": "ls"},
                    "interaction": {"kind": "approval", "request_id": "a1"},
                },
                SessionStatus.WAITING_INPUT,
            )
            update = _next_of_type(ws, "pending_asks")

    assert update["payload"]["epoch"] == first["payload"]["epoch"]
    assert update["payload"]["revision"] > first["payload"]["revision"]
    assert [i["id"] for i in update["payload"]["items"]] == [
        "question:s1:q1",
        "approval:s1:a1",
    ]


def _next_of_type(ws: Any, frame_type: str) -> dict[str, Any]:
    for _ in range(50):
        frame = ws.receive_json()
        if frame["type"] == frame_type:
            return frame
    raise AssertionError(f"no {frame_type} frame")
