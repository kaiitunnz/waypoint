"""The OpenCode adapter against a 2.x server."""

import asyncio
import signal
from pathlib import Path
from typing import Any, cast

import pytest

from opencode_stubs import (
    WORKDIR,
    Emitted,
    FakeOpenCode,
    driving,
    event,
    model_entry,
)
from waypoint.attachments import ResolvedAttachment
from waypoint.backends.opencode import adapter as adapter_module
from waypoint.backends.opencode.adapter import (
    MigrationGate,
    OpenCodeAdapter,
    OpenCodeError,
)
from waypoint.backends.opencode.client import LocalOpenCodeClient
from waypoint.backends.opencode.server_auth import ServerAuth
from waypoint.backends.opencode.sessions import SessionState
from waypoint.questions import QuestionLiveness
from waypoint.schemas import (
    AttachmentSpec,
    EventKind,
    SessionStatus,
    TokenUsageRecord,
)

ASK = [{"action": "*", "resource": "*", "effect": "ask"}]
TOKENS = {
    "input": 100,
    "output": 20,
    "reasoning": 5,
    "cache": {"read": 40, "write": 3},
}


def _fake() -> FakeOpenCode:
    fake = FakeOpenCode()
    fake.add_session("ses_root", permissions=list(ASK))
    return fake


def _register(adapter: OpenCodeAdapter, **fields: Any) -> SessionState:
    state = SessionState("local", WORKDIR, "ses_root", **fields)
    adapter._register_session(state)
    return state


async def _deliver(adapter: OpenCodeAdapter, *events: dict[str, Any]) -> None:
    for item in events:
        await adapter._handle_event(item)


def _shell_call(session_id: str = "ses_root", call_id: str = "call_1") -> list[Any]:
    return [
        event("session.tool.input.started", session_id, id=call_id, name="shell"),
        event("session.tool.called", session_id, id=call_id, input={"command": "ls"}),
    ]


def _permission(
    request_id: str = "per_1",
    session_id: str = "ses_root",
    *,
    action: str = "shell",
    save: tuple[str, ...] = ("ls *",),
    call_id: str = "call_1",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": request_id,
        "sessionID": session_id,
        "action": action,
        "resources": ["ls"],
        "save": list(save),
        "source": {"type": "tool", "messageID": "msg_1", "id": call_id},
        **extra,
    }


def _asked(fake: FakeOpenCode, request: dict[str, Any]) -> dict[str, Any]:
    fake.permissions.append(request)
    return {"type": "permission.asked", "data": request}


def _form(form_id: str = "frm_1", session_id: str = "ses_root") -> dict[str, Any]:
    return {
        "id": form_id,
        "sessionID": session_id,
        "title": "Questions",
        "fields": [
            {
                "key": "q0",
                "type": "string",
                "description": "Pick one",
                "options": [{"value": "red", "label": "Red"}],
            }
        ],
    }


def _form_created(fake: FakeOpenCode, form: dict[str, Any]) -> dict[str, Any]:
    fake.forms.append(form)
    return {"type": "form.created", "data": {"form": form}}


def _attachment(tmp_path: Path) -> ResolvedAttachment:
    path = tmp_path / "note.txt"
    path.write_text("teal")
    spec = AttachmentSpec(
        id="att_1", filename="note.txt", mime="text/plain", size=4, kind="file"
    )
    return ResolvedAttachment(spec=spec, path=path)


# ── Sessions and turns ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_new_session_takes_its_location_rules_agent_and_model() -> None:
    fake = FakeOpenCode()
    async with driving(fake) as (adapter, emitted):
        remote = await adapter.start_session(
            "local",
            WORKDIR,
            title="T",
            model="opencode/big-pickle",
            effort="high",
            agent="plan",
            permissions=ASK,
        )

    assert fake.calls("POST", "/api/session") == [
        {
            "location": {"directory": WORKDIR},
            "title": "T",
            "agent": "plan",
            "permissions": ASK,
            "model": {"providerID": "opencode", "id": "big-pickle", "variant": "high"},
        }
    ]
    assert emitted.texts() == [f"OpenCode session started ({remote})"]
    assert emitted.statuses == [SessionStatus.IDLE]


@pytest.mark.asyncio
async def test_a_variant_the_model_lacks_is_not_selected() -> None:
    fake = FakeOpenCode()
    async with driving(fake) as (adapter, _emitted):
        await adapter.start_session(
            "local", WORKDIR, model="opencode/big-pickle", effort="max"
        )

    [body] = fake.calls("POST", "/api/session")
    assert body["model"] == {"providerID": "opencode", "id": "big-pickle"}
    assert "permissions" not in body and "agent" not in body


@pytest.mark.asyncio
async def test_an_effort_without_a_model_goes_to_the_default_model() -> None:
    fake = FakeOpenCode()
    async with driving(fake) as (adapter, _emitted):
        remote = await adapter.start_session("local", WORKDIR, effort="low")

    assert fake.calls("POST", f"/api/session/{remote}/model") == [
        {"model": {"providerID": "opencode", "id": "big-pickle", "variant": "low"}}
    ]


@pytest.mark.asyncio
async def test_prompts_commands_and_skills_carry_their_files(tmp_path: Path) -> None:
    fake = _fake()
    data_url = "data:text/plain;base64,dGVhbA=="
    async with driving(fake) as (adapter, _emitted):
        _register(adapter)
        await adapter.send_input("local", "hello", [_attachment(tmp_path)])
        await adapter.send_input("local", "/wp go", skills=["wp"])
        await adapter.execute_command("local", "init", "now", [_attachment(tmp_path)])
        await adapter.compact_session("local")

    assert fake.calls("POST", "/api/session/ses_root/prompt") == [
        {"text": "hello", "files": [{"uri": data_url, "name": "note.txt"}]},
        {"text": "/wp go", "skills": [{"id": "wp"}]},
    ]
    assert fake.calls("POST", "/api/session/ses_root/command") == [
        {
            "name": "init",
            "text": "now",
            "files": [{"uri": data_url, "name": "note.txt"}],
        }
    ]
    assert fake.calls("POST", "/api/session/ses_root/compact") == [{}]


@pytest.mark.asyncio
async def test_a_turn_streams_text_tools_usage_and_ends_idle() -> None:
    fake = _fake()
    fake.diffs = [
        {
            "file": "a.txt",
            "patch": "--- a/a.txt\n+++ b/a.txt\n@@ -0,0 +1 @@\n+teal\n",
            "additions": 1,
            "deletions": 0,
            "status": "added",
        }
    ]
    usage: list[TokenUsageRecord] = []
    context: list[dict[str, Any]] = []

    async def on_usage(_sid: str, record: TokenUsageRecord, _publish: bool) -> None:
        usage.append(record)

    async def on_update(_sid: str, update: dict[str, Any], _publish: bool) -> None:
        context.append(update["context_usage"])

    async with driving(fake, on_token_usage=on_usage, on_session_update=on_update) as (
        adapter,
        emitted,
    ):
        _register(adapter)
        await adapter.list_models()
        message = {"assistantMessageID": "msg_1"}
        await _deliver(
            adapter,
            event("session.execution.started"),
            event(
                "session.step.started",
                model={"providerID": "opencode", "id": "big-pickle"},
                **message,
            ),
            event("session.reasoning.delta", ordinal=0, delta="hm", **message),
            event("session.reasoning.ended", ordinal=0, text="hm", **message),
            event("session.text.delta", ordinal=0, delta="Do", **message),
            event("session.text.delta", ordinal=0, delta="ne", **message),
            event("session.text.ended", ordinal=0, text="Done", **message),
            event("session.text.ended", ordinal=1, text="All at once", **message),
            *_shell_call(),
            event(
                "session.tool.success",
                id="call_1",
                content=[
                    {"type": "text", "text": "a.txt\n"},
                    {"type": "text", "text": "Exit code: 0"},
                ],
            ),
            event("session.step.ended", tokens=TOKENS, files=["a.txt"], **message),
            event("session.execution.succeeded"),
        )

    assert emitted.texts()[1:5] == ["hm", "Do", "ne", "All at once"]
    items = [
        metadata.get("item_id") for _, metadata in emitted.of(EventKind.AGENT_OUTPUT)
    ]
    assert items == [
        "msg_1:reasoning:0",
        "msg_1:text:0",
        "msg_1:text:0",
        "msg_1:text:1",
    ]
    [(_, call)] = emitted.of(EventKind.TOOL_CALL)
    assert (call["tool_name"], call["tool_input"]) == ("bash", {"command": "ls"})
    [(result, _)] = emitted.of(EventKind.TOOL_RESULT)
    assert result == "Result for bash:\na.txt\nExit code: 0"

    # The diff comes before the note that releases queued messages, and says
    # nothing about the status.
    assert emitted.texts()[-2:] == ["Changes: +1 -0", "Session idle"]
    assert emitted.statuses[-2:] == [None, SessionStatus.IDLE]
    assert emitted.events[-2][2]["diff_preview"]["phase"] == "aggregate"
    assert set(emitted.statuses[:-2]) == {SessionStatus.RUNNING}

    [record] = usage
    assert record.record_id == "ses_root:msg_1"
    assert record.model == "opencode/big-pickle"
    # Waypoint's output total includes reasoning.
    assert record.totals["output_tokens"] == 25
    [snapshot] = context
    assert snapshot["used_tokens"] == 143
    assert snapshot["context_window_tokens"] == 200_000


@pytest.mark.asyncio
async def test_a_failed_turn_reports_its_error() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, emitted):
        _register(adapter)
        await _deliver(
            adapter,
            event("session.execution.started"),
            event("session.execution.failed", error={"message": "no route"}),
        )

    assert emitted.texts()[-1] == "Error: no route"
    assert emitted.statuses[-1] == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_events_of_other_or_closing_sessions_are_dropped() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, emitted):
        state = _register(adapter)
        await _deliver(adapter, event("session.execution.started", "ses_elsewhere"))
        state.closing = True
        await _deliver(adapter, event("session.execution.started"))

    assert emitted.events == []


# ── Permissions ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_permission_request_shows_its_tool_call_and_takes_a_reply() -> None:
    fake = _fake()
    patch = {"file": "a.txt", "patch": "@@ -0,0 +1 @@\n+teal\n", "status": "added"}
    async with driving(fake) as (adapter, emitted):
        _register(adapter)
        await _deliver(
            adapter,
            event("session.execution.started"),
            *_shell_call(),
            _asked(fake, _permission(metadata={"files": [patch]})),
        )
        [(text, card)] = emitted.of(EventKind.APPROVAL_REQUEST)
        assert text == "bash: shell (ls)"
        assert card["approval_id"] == "per_1"
        assert card["interaction"]["request_id"] == "per_1"
        assert card["tool_input"] == {"command": "ls"}
        assert card["diff_preview"]["phase"] == "proposed"
        assert emitted.statuses[-1] == SessionStatus.WAITING_INPUT
        assert adapter.pending_approval_ids("local") == ("per_1",)

        assert await adapter.respond_to_permission(
            "local", "approve", approval_id="per_1"
        )
        assert not adapter.has_pending_approval("local")
        await _deliver(
            adapter, event("permission.replied", requestID="per_1", reply="once")
        )

    assert fake.calls("POST", "/api/session/ses_root/permission/per_1/reply") == [
        {"decision": "once"}
    ]
    assert emitted.texts()[-1] == "Permission once"
    assert emitted.statuses[-1] == SessionStatus.RUNNING
    assert "approval.invalidated" not in emitted.methods()


@pytest.mark.asyncio
async def test_a_reply_read_before_the_post_returns_is_waypoints_own() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, emitted):
        state = _register(adapter)
        await _deliver(adapter, _asked(fake, _permission()))
        state.replying.add("per_1")
        await _deliver(
            adapter, event("permission.replied", requestID="per_1", reply="reject")
        )

    assert emitted.texts()[-1] == "Permission reject"
    assert "approval.invalidated" not in emitted.methods()


@pytest.mark.asyncio
async def test_approval_for_the_session_grants_it_and_never_replies_always() -> None:
    fake = _fake()
    fake.add_session("ses_child", parentID="ses_root", permissions=list(ASK))
    grant = {"action": "shell", "resource": "ls *", "effect": "allow"}
    async with driving(fake) as (adapter, _emitted):
        _register(adapter)
        await _deliver(
            adapter,
            {
                "type": "session.created",
                "data": {"sessionID": "ses_child", "parentID": "ses_root"},
            },
            _asked(fake, _permission(session_id="ses_child")),
        )
        assert await adapter.respond_to_permission("local", "acceptForSession")

    assert fake.sessions["ses_child"]["permissions"] == [*ASK, grant]
    assert fake.sessions["ses_root"]["permissions"] == [*ASK, grant]
    assert fake.calls("POST", "/api/session/ses_child/permission/per_1/reply") == [
        {"decision": "once"}
    ]
    assert fake.calls("GET", "/api/permission/saved") == []


@pytest.mark.asyncio
async def test_grants_made_at_once_all_land() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, _emitted):
        _register(adapter)
        await _deliver(
            adapter,
            _asked(fake, _permission("per_1", save=("ls *",))),
            _asked(fake, _permission("per_2", save=("cat *",), call_id="call_2")),
        )
        # Each grant rewrites the whole ruleset.
        await asyncio.gather(
            adapter.respond_to_permission("local", "always", approval_id="per_1"),
            adapter.respond_to_permission("local", "always", approval_id="per_2"),
        )

    granted = {rule["resource"] for rule in fake.sessions["ses_root"]["permissions"]}
    assert granted == {"*", "ls *", "cat *"}


@pytest.mark.asyncio
async def test_a_grant_in_plan_mode_reaches_later_subagents() -> None:
    fake = _fake()
    fake.sessions["ses_root"]["permissions"] = []
    grant = {"action": "shell", "resource": "ls *", "effect": "allow"}

    def created(child: str) -> dict[str, Any]:
        return {
            "type": "session.created",
            "data": {"sessionID": child, "parentID": "ses_root"},
        }

    async with driving(fake) as (adapter, _emitted):
        _register(adapter)
        # Plan mode: no rules on the root, ask for its subagents.
        assert await adapter.set_session_permission("local", [], ASK)
        fake.add_session("ses_a", parentID="ses_root", permissions=[])
        await _deliver(adapter, created("ses_a"))
        await asyncio.gather(*adapter._tasks)
        await _deliver(adapter, _asked(fake, _permission(session_id="ses_a")))
        assert await adapter.respond_to_permission("local", "acceptForSession")

        # The plan agent's own rules are not widened by a subagent's approval.
        assert fake.sessions["ses_root"]["permissions"] == []

        # The server gives a new subagent a copy of the root's rules.
        root = list(fake.sessions["ses_root"]["permissions"])
        fake.add_session("ses_b", parentID="ses_root", permissions=root)
        await _deliver(adapter, created("ses_b"))
        await asyncio.gather(*adapter._tasks)

    assert fake.sessions["ses_b"]["permissions"] == [*ASK, grant]


@pytest.mark.asyncio
async def test_a_grant_racing_a_switch_to_plan_leaves_the_plan_agent_alone() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, _emitted):
        state = _register(adapter)
        fake.add_session("ses_a", parentID="ses_root", permissions=list(ASK))
        state.children.add("ses_a")
        adapter._routes["ses_a"] = "local"
        await _deliver(adapter, _asked(fake, _permission(session_id="ses_a")))
        entering = asyncio.create_task(adapter.set_session_permission("local", [], ASK))
        while not adapter._ruleset_lock.locked():
            await asyncio.sleep(0)
        granting = asyncio.create_task(
            adapter.respond_to_permission("local", "acceptForSession")
        )
        assert await entering
        assert await granting
        await asyncio.gather(*adapter._tasks)

    assert fake.sessions["ses_root"]["permissions"] == []


@pytest.mark.asyncio
async def test_a_subagent_seen_during_a_grant_gets_it() -> None:
    fake = _fake()
    fake.sessions["ses_root"]["permissions"] = []
    async with driving(fake) as (adapter, _emitted):
        state = _register(adapter)
        assert await adapter.set_session_permission("local", [], ASK)
        fake.add_session("ses_a", parentID="ses_root", permissions=list(ASK))
        state.children.add("ses_a")
        adapter._routes["ses_a"] = "local"
        await _deliver(adapter, _asked(fake, _permission(session_id="ses_a")))
        fake.add_session("ses_b", parentID="ses_root", permissions=[])
        granting = asyncio.create_task(
            adapter.respond_to_permission("local", "acceptForSession")
        )
        while not adapter._ruleset_lock.locked():
            await asyncio.sleep(0)
        await _deliver(
            adapter,
            {
                "type": "session.created",
                "data": {"sessionID": "ses_b", "parentID": "ses_root"},
            },
        )
        await granting
        await asyncio.gather(*adapter._tasks)

    grant = {"action": "shell", "resource": "ls *", "effect": "allow"}
    assert fake.sessions["ses_b"]["permissions"] == [*ASK, grant]


@pytest.mark.asyncio
async def test_a_failed_grant_leaves_the_request_unanswered() -> None:
    fake = _fake()
    fake.failures[("PATCH", "/api/session/ses_root")] = 500
    async with driving(fake) as (adapter, _emitted):
        _register(adapter)
        await _deliver(adapter, _asked(fake, _permission()))
        with pytest.raises(OpenCodeError, match="for the session"):
            await adapter.respond_to_permission("local", "acceptForSession")

        assert adapter.pending_approval_ids("local") == ("per_1",)
    assert not any(path.endswith("/reply") for _, path in fake.writes())


@pytest.mark.asyncio
async def test_a_request_with_nothing_to_save_is_just_allowed_once() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, _emitted):
        _register(adapter)
        await _deliver(adapter, _asked(fake, _permission(save=())))
        assert await adapter.respond_to_permission("local", "acceptForSession")

    assert fake.writes() == [("POST", "/api/session/ses_root/permission/per_1/reply")]


@pytest.mark.asyncio
async def test_a_rejection_closes_the_other_requests_it_took_down() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, emitted):
        _register(adapter)
        await _deliver(
            adapter,
            _asked(fake, _permission("per_1")),
            _asked(fake, _permission("per_2", call_id="call_2")),
        )
        await adapter.respond_to_permission(
            "local", "decline", "no thanks", approval_id="per_1"
        )
        await _deliver(
            adapter,
            event("permission.replied", requestID="per_1", reply="reject"),
            event("permission.replied", requestID="per_2", reply="reject"),
        )
        assert not adapter.has_pending_approval("local")

    assert fake.calls("POST", "/api/session/ses_root/permission/per_1/reply") == [
        {"decision": "reject", "message": "no thanks"}
    ]
    assert emitted.events[-1][2]["method"] == "approval.invalidated"
    assert emitted.events[-1][2]["approval_id"] == "per_2"


@pytest.mark.asyncio
async def test_a_reply_the_server_no_longer_takes_closes_the_card() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, emitted):
        _register(adapter)
        await _deliver(adapter, _asked(fake, _permission()))
        fake.permissions.clear()

        assert not await adapter.respond_to_permission("local", "approve")
        assert not adapter.has_pending_approval("local")

    assert emitted.methods()[-1] == "approval.invalidated"


@pytest.mark.asyncio
async def test_an_unknown_decision_is_refused() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, _emitted):
        _register(adapter)
        await _deliver(adapter, _asked(fake, _permission()))
        with pytest.raises(OpenCodeError, match="unsupported"):
            await adapter.respond_to_permission("local", "perhaps")


@pytest.mark.asyncio
async def test_interrupting_stops_subagents_first_and_stays_interrupted() -> None:
    fake = _fake()
    fake.add_session("ses_child", parentID="ses_root")
    async with driving(fake) as (adapter, emitted):
        _register(adapter)
        await _deliver(
            adapter,
            event("session.execution.started"),
            {
                "type": "session.created",
                "data": {"sessionID": "ses_child", "parentID": "ses_root"},
            },
            event("session.execution.started", "ses_child"),
            _asked(fake, _permission()),
        )
        # An interrupt drops pending requests without saying so.
        fake.permissions.clear()
        await adapter.interrupt("local")
        await _deliver(
            adapter,
            event("session.execution.interrupted", "ses_child", reason="user"),
            event("session.execution.interrupted", reason="user"),
        )

    assert [path for _, path in fake.writes()] == [
        "/api/session/ses_child/interrupt",
        "/api/session/ses_root/interrupt",
    ]
    invalidated = emitted.methods().index("approval.invalidated")
    # No status from here on reads as idle, which would release queued messages.
    assert SessionStatus.IDLE not in emitted.statuses[invalidated:]
    assert emitted.statuses[-1] == SessionStatus.INTERRUPTED


@pytest.mark.asyncio
async def test_interrupting_a_subagent_the_turn_left_running_stays_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module, "SUBAGENT_SETTLE_SECONDS", 0.0)
    fake = _fake()
    fake.add_session("ses_child", parentID="ses_root")
    async with driving(fake) as (adapter, emitted):
        _register(adapter)
        await _deliver(
            adapter,
            {
                "type": "session.created",
                "data": {"sessionID": "ses_child", "parentID": "ses_root"},
            },
            event("session.execution.started", "ses_child"),
        )
        # The root has no execution of its own to report as interrupted.
        await adapter.interrupt("local")
        await _deliver(
            adapter,
            event("session.execution.interrupted", "ses_child", reason="user"),
        )
        await asyncio.gather(*adapter._tasks)
        assert adapter.session_status("local") == SessionStatus.INTERRUPTED

        # The parent then takes the cancelled result: still the interrupted turn.
        await _deliver(
            adapter,
            event("session.execution.started"),
            event("session.execution.succeeded"),
        )
        assert SessionStatus.IDLE not in emitted.statuses
        assert emitted.statuses[-1] == SessionStatus.INTERRUPTED

        await adapter.send_input("local", "go on")
        await _deliver(
            adapter,
            event("session.execution.started"),
            event("session.execution.succeeded"),
        )

    assert emitted.statuses[-1] == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_a_root_interrupted_before_its_subagent_ends_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module, "SUBAGENT_SETTLE_SECONDS", 0.0)
    fake = _fake()
    fake.add_session("ses_child", parentID="ses_root")
    async with driving(fake) as (adapter, emitted):
        _register(adapter, children={"ses_child"})
        adapter._routes["ses_child"] = "local"
        await _deliver(
            adapter,
            event("session.execution.started"),
            event("session.execution.started", "ses_child"),
        )
        await adapter.interrupt("local")
        await _deliver(
            adapter,
            event("session.execution.interrupted", reason="user"),
            event("session.execution.interrupted", "ses_child", reason="user"),
        )
        await asyncio.gather(*adapter._tasks)

    assert emitted.texts()[-1] == "Turn interrupted"
    assert emitted.statuses[-1] == SessionStatus.INTERRUPTED


@pytest.mark.asyncio
async def test_a_superseded_execution_does_not_end_the_turn() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, emitted):
        _register(adapter)
        await _deliver(
            adapter,
            event("session.execution.started"),
            event("session.execution.interrupted", reason="superseded"),
        )

        assert adapter.session_status("local") == SessionStatus.RUNNING
    assert emitted.texts() == ["Session busy"]


@pytest.mark.asyncio
async def test_a_request_being_replied_to_is_not_dropped_as_unlisted() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, emitted):
        state = _register(adapter)
        await _deliver(adapter, _asked(fake, _permission()))
        # The server has taken the reply; the post has not returned.
        state.replying.add("per_1")
        fake.permissions.clear()
        await _deliver(adapter, event("session.execution.succeeded"))

        assert adapter.pending_approval_ids("local") == ("per_1",)
    assert "approval.invalidated" not in emitted.methods()


@pytest.mark.asyncio
async def test_an_execution_that_ends_reconciles_what_is_pending() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, emitted):
        _register(adapter)
        await _deliver(
            adapter,
            _asked(fake, _permission()),
            _form_created(fake, _form()),
        )
        fake.failures[("GET", "/api/session/ses_root/form")] = 500
        await _deliver(adapter, event("session.execution.succeeded"))
        # A failed listing is not an empty one.
        assert adapter.pending_approval_ids("local") == ("per_1",)

        fake.failures.clear()
        fake.permissions.clear()
        await _deliver(adapter, event("session.execution.succeeded"))

        assert not adapter.has_pending_approval("local")
        assert adapter.current_question_id("local") == "frm_1"
    assert emitted.methods()[-1] == "approval.invalidated"


# ── Subagents ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subagents_share_asks_and_status_but_not_the_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module, "SUBAGENT_SETTLE_SECONDS", 0.0)
    fake = _fake()
    context: list[Any] = []

    async def on_update(_sid: str, update: dict[str, Any], _publish: bool) -> None:
        context.append(update)

    async with driving(fake, on_session_update=on_update) as (adapter, emitted):
        _register(adapter)
        await adapter.list_models()
        await _deliver(
            adapter,
            event("session.execution.started"),
            event(
                "session.tool.progress",
                id="call_0",
                metadata={"sessionID": "ses_child"},
            ),
            event("session.execution.started", "ses_child"),
        )
        before = len(emitted.events)
        await _deliver(
            adapter,
            event(
                "session.step.started",
                "ses_child",
                assistantMessageID="msg_c",
                model={"providerID": "opencode", "id": "big-pickle"},
            ),
            event(
                "session.text.delta",
                "ses_child",
                assistantMessageID="msg_c",
                ordinal=0,
                delta="hidden",
            ),
            *_shell_call("ses_child"),
            event(
                "session.step.ended",
                "ses_child",
                assistantMessageID="msg_c",
                tokens=TOKENS,
            ),
        )
        assert len(emitted.events) == before
        assert context == []

        await _deliver(adapter, _asked(fake, _permission(session_id="ses_child")))
        [(text, card)] = emitted.of(EventKind.APPROVAL_REQUEST)
        assert text.startswith("Subagent · bash")
        assert card["tool_input"] == {"command": "ls"}
        await adapter.respond_to_permission("local", "approve")

        # The root ends while its subagent runs on.
        await _deliver(adapter, event("session.execution.succeeded"))
        assert adapter.session_status("local") == SessionStatus.RUNNING
        assert "Session idle" not in emitted.texts()

        await _deliver(adapter, event("session.execution.succeeded", "ses_child"))
        await asyncio.sleep(0.05)

    assert emitted.texts()[-1] == "Session idle"
    assert emitted.statuses[-1] == SessionStatus.IDLE
    assert ("POST", "/api/session/ses_child/permission/per_1/reply") in fake.writes()


@pytest.mark.asyncio
async def test_a_parent_that_takes_the_result_reports_idle_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module, "SUBAGENT_SETTLE_SECONDS", 0.05)
    fake = _fake()
    async with driving(fake) as (adapter, emitted):
        _register(adapter)
        await _deliver(
            adapter,
            event(
                "session.tool.progress",
                id="call_0",
                metadata={"sessionID": "ses_child"},
            ),
            event("session.execution.started", "ses_child"),
            event("session.execution.succeeded", "ses_child"),
            event("session.execution.started"),
            event("session.execution.succeeded"),
        )
        await asyncio.gather(*adapter._tasks)

    assert emitted.texts().count("Session idle") == 1


# ── Questions ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_form_is_a_question_that_takes_an_answer() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, emitted):
        _register(adapter)
        await _deliver(
            adapter,
            event("session.execution.started"),
            event("session.tool.input.started", id="call_q", name="question"),
            event("session.tool.called", id="call_q", input={"questions": []}),
            _form_created(fake, _form()),
        )
        [(text, card)] = emitted.of(EventKind.TOOL_CALL)
        assert (text, card["tool_name"]) == ("Need your input", "AskUserQuestion")
        assert card["tool_use_id"] == "frm_1"
        assert adapter.question_liveness("local", ["frm_1", "frm_0"]) == {
            "frm_1": QuestionLiveness.ACTIONABLE,
            "frm_0": QuestionLiveness.CLOSED,
        }

        assert await adapter.answer_question("local", "frm_1", "Red")
        await _deliver(
            adapter,
            event("form.replied", id="frm_1"),
            event("session.tool.success", id="call_q", content=[]),
        )

    assert fake.calls("POST", "/api/session/ses_root/form/frm_1/reply") == [
        {"answer": {"q0": "red"}}
    ]
    assert emitted.events[-1][2]["tool_use_id"] == "frm_1"
    assert emitted.of(EventKind.TOOL_RESULT) == []


@pytest.mark.parametrize(
    ("status", "outcome"), [(None, "ok"), (404, "missing"), (500, "error")]
)
@pytest.mark.asyncio
async def test_declining_a_question(status: int | None, outcome: str) -> None:
    fake = _fake()
    if status:
        fake.failures[("DELETE", "/api/session/ses_root/form/frm_1")] = status
    async with driving(fake) as (adapter, _emitted):
        _register(adapter)
        await _deliver(adapter, _form_created(fake, _form()))

        assert await adapter.reject_question("local", "frm_1") == outcome
        pending = adapter.current_question_id("local")

    assert pending == ("frm_1" if outcome == "error" else None)


@pytest.mark.asyncio
async def test_an_answer_the_server_no_longer_takes_closes_the_question() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, emitted):
        _register(adapter)
        await _deliver(adapter, _form_created(fake, _form()))
        fake.forms.clear()

        assert not await adapter.answer_question("local", "frm_1", "Red")
        assert adapter.current_question_id("local") is None

    assert emitted.events[-1][2]["tool_use_id"] == "frm_1"


@pytest.mark.asyncio
async def test_unlisted_questions_stay_unknown_until_a_listing_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake()
    fake.forms.append(_form("frm_open"))
    fake.failures[("GET", "/api/session/ses_root/form")] = 500
    changed: list[str] = []
    async with driving(fake, on_questions_changed=changed.append) as (adapter, _):
        state = _register(adapter, forms_seeded=False)
        await adapter._seed_asks(state)
        assert adapter.question_liveness("local", ["frm_open"]) == {
            "frm_open": QuestionLiveness.UNAVAILABLE
        }
        assert adapter._question_seed_tasks == {}

        fake.failures.clear()
        monkeypatch.setattr(adapter_module, "QUESTION_SEED_RETRY_SECONDS", 0.0)
        adapter.question_liveness("local", ["frm_open"])
        await asyncio.gather(*adapter._question_seed_tasks.values())

        assert changed == ["local"]
        assert adapter.question_liveness("local", ["frm_open", "frm_gone"]) == {
            "frm_open": QuestionLiveness.ACTIONABLE,
            "frm_gone": QuestionLiveness.CLOSED,
        }


# ── Restore ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restore_adopts_subagents_running_state_and_pending_asks() -> None:
    fake = _fake()
    fake.sessions["ses_root"].update(
        model={"providerID": "opencode", "id": "big-pickle", "variant": "default"},
        agent="build",
        permissions=[],
    )
    fake.add_session("ses_child", parentID="ses_root")
    fake.add_session("ses_grandchild", parentID="ses_child")
    # A subagent waiting on a request is listed as running.
    fake.active = {"ses_child", "ses_grandchild"}
    fake.permissions = [
        _permission("per_new", "ses_child"),
        _permission("per_shown"),
        _permission("per_other", "ses_elsewhere"),
    ]
    fake.forms = [_form("frm_new"), _form("frm_shown")]
    async with driving(fake) as (adapter, emitted):
        await adapter.restore_session(
            "local",
            WORKDIR,
            "ses_root",
            permissions=ASK,
            model="opencode/big-pickle",
            agent=None,
            open_approvals=["per_shown", "per_gone"],
            open_questions=["frm_shown"],
        )
        state = adapter._sessions["local"]

        assert state.children == {"ses_child", "ses_grandchild"}
        assert state.running == {"ses_child", "ses_grandchild"}
        assert adapter.session_status("local") == SessionStatus.WAITING_INPUT
        assert adapter.pending_approval_ids("local") == ("per_new", "per_shown")

    # What the transcript lacks is shown, and what the server lacks is closed.
    assert [m["approval_id"] for _, m in emitted.of(EventKind.APPROVAL_REQUEST)] == [
        "per_new"
    ]
    assert [m["tool_use_id"] for _, m in emitted.of(EventKind.TOOL_CALL)] == ["frm_new"]
    assert emitted.events[-1][2]["approval_id"] == "per_gone"
    # The same model is not selected again; the rules are written back, to the
    # subagents too.
    assert fake.writes() == [
        ("PATCH", "/api/session/ses_root"),
        ("PATCH", "/api/session/ses_child"),
        ("PATCH", "/api/session/ses_grandchild"),
    ]
    assert fake.sessions["ses_grandchild"]["permissions"] == ASK


@pytest.mark.asyncio
async def test_restore_keeps_open_cards_when_it_cannot_list_asks() -> None:
    fake = _fake()
    fake.failures[("GET", "/api/session/ses_root/permission")] = 500
    async with driving(fake) as (adapter, emitted):
        await adapter.restore_session(
            "local", WORKDIR, "ses_root", permissions=ASK, open_approvals=["per_1"]
        )

    assert "approval.invalidated" not in emitted.methods()


@pytest.mark.asyncio
async def test_restore_applies_what_waypoint_recorded() -> None:
    fake = _fake()
    fake.sessions["ses_root"].update(
        model={"providerID": "opencode", "id": "other"}, agent="build"
    )
    async with driving(fake) as (adapter, _emitted):
        await adapter.restore_session(
            "local",
            WORKDIR,
            "ses_root",
            permissions=[],
            model="opencode/big-pickle",
            agent="plan",
            effort="high",
        )

    assert fake.calls("POST", "/api/session/ses_root/model") == [
        {"model": {"providerID": "opencode", "id": "big-pickle", "variant": "high"}}
    ]
    assert fake.calls("POST", "/api/session/ses_root/agent") == [{"agent": "plan"}]


@pytest.mark.asyncio
async def test_a_selection_the_server_refuses_keeps_the_session() -> None:
    fake = _fake()
    fake.failures[("POST", "/api/session/ses_root/model")] = 400
    async with driving(fake) as (adapter, emitted):
        await adapter.restore_session(
            "local", WORKDIR, "ses_root", permissions=[], model="opencode/big-pickle"
        )
        assert adapter.session_status("local") == SessionStatus.IDLE

    assert emitted.texts()[-1].startswith("Could not restore the session's model")


@pytest.mark.parametrize(
    "broken", [("GET", "/api/session/ses_root"), ("PATCH", "/api/session/ses_root")]
)
@pytest.mark.asyncio
async def test_restore_fails_without_the_session_or_its_rules(
    broken: tuple[str, str],
) -> None:
    fake = _fake()
    fake.failures[broken] = 500
    async with driving(fake) as (adapter, _emitted):
        with pytest.raises(OpenCodeError):
            await adapter.restore_session("local", WORKDIR, "ses_root", permissions=ASK)

        assert adapter._sessions == {} and adapter._routes == {}


@pytest.mark.asyncio
async def test_asks_a_restore_could_not_list_are_repaired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module, "QUESTION_SEED_RETRY_SECONDS", 0.01)
    fake = _fake()
    fake.active = {"ses_root"}
    fake.permissions = [_permission("per_live"), _permission("per_new")]
    fake.failures[("GET", "/api/session/ses_root/permission")] = 500
    async with driving(fake) as (adapter, emitted):
        await adapter.restore_session(
            "local",
            WORKDIR,
            "ses_root",
            permissions=ASK,
            open_approvals=["per_live", "per_gone"],
        )
        assert emitted.events == []

        fake.failures.clear()
        await asyncio.gather(*adapter._tasks)

        assert adapter.pending_approval_ids("local") == ("per_live", "per_new")
    # The card the transcript has is not repeated; the stale one is closed.
    assert [m["approval_id"] for _, m in emitted.of(EventKind.APPROVAL_REQUEST)] == [
        "per_new"
    ]
    assert emitted.events[-1][2]["approval_id"] == "per_gone"


@pytest.mark.asyncio
async def test_a_late_listing_reports_a_wait_it_has_no_card_to_show(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module, "QUESTION_SEED_RETRY_SECONDS", 0.01)
    fake = _fake()
    fake.active = {"ses_root"}
    fake.permissions = [_permission()]
    fake.failures[("GET", "/api/session/ses_root/permission")] = 500
    async with driving(fake) as (adapter, emitted):
        await adapter.restore_session(
            "local", WORKDIR, "ses_root", permissions=ASK, open_approvals=["per_1"]
        )
        fake.failures.clear()
        await asyncio.gather(*adapter._tasks)

    assert emitted.texts() == ["Waiting for your input"]
    assert emitted.statuses == [SessionStatus.WAITING_INPUT]


@pytest.mark.asyncio
async def test_a_cancelled_restore_leaves_no_session_behind() -> None:
    fake = _fake()
    listing = asyncio.Event()
    async with driving(fake) as (adapter, _emitted):

        async def stuck(state: SessionState) -> None:
            listing.set()
            await asyncio.Event().wait()

        adapter._adopt_tree = stuck  # type: ignore[method-assign]
        restoring = asyncio.create_task(
            adapter.restore_session("local", WORKDIR, "ses_root", permissions=ASK)
        )
        await listing.wait()
        restoring.cancel()
        with pytest.raises(asyncio.CancelledError):
            await restoring

        assert adapter._sessions == {} and adapter._routes == {}


@pytest.mark.asyncio
async def test_a_mode_change_writes_the_subagents_rules_too() -> None:
    fake = _fake()
    fake.add_session("ses_child", permissions=[])
    deny = [{"action": "*", "resource": "*", "effect": "deny"}]
    async with driving(fake) as (adapter, emitted):
        # A subagent that has been deleted since is skipped.
        _register(adapter, children={"ses_child", "ses_deleted"})
        assert await adapter.set_session_permission("local", [], deny)
        assert fake.sessions["ses_root"]["permissions"] == []
        assert fake.sessions["ses_child"]["permissions"] == deny
        assert emitted.events == []
        # No rules at all clears the subagents' too.
        assert await adapter.set_session_permission("local", [], [])
        assert fake.sessions["ses_child"]["permissions"] == []

        # A subagent that refuses its rules does not undo the session's.
        fake.failures[("PATCH", "/api/session/ses_child")] = 500
        assert await adapter.set_session_permission("local", ASK)
        assert fake.sessions["ses_root"]["permissions"] == ASK
    assert emitted.texts()[-1].startswith(
        "Could not apply the permission mode to subagent ses_child"
    )


@pytest.mark.asyncio
async def test_a_subagent_started_after_a_switch_gets_the_subagent_rules() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, _emitted):
        _register(adapter)
        assert await adapter.set_session_permission("local", [], ASK)
        fake.add_session("ses_child", parentID="ses_root", permissions=[])
        await _deliver(
            adapter,
            {
                "type": "session.created",
                "data": {"sessionID": "ses_child", "parentID": "ses_root"},
            },
        )
        await asyncio.gather(*adapter._tasks)
        assert fake.sessions["ses_child"]["permissions"] == ASK

        # Rules a subagent copies from the session are not written again.
        assert await adapter.set_session_permission("local", ASK, ASK)
        fake.add_session("ses_other", parentID="ses_root", permissions=ASK)
        writes = len(fake.writes())
        await _deliver(
            adapter,
            {
                "type": "session.created",
                "data": {"sessionID": "ses_other", "parentID": "ses_root"},
            },
        )
        await asyncio.gather(*adapter._tasks)
        assert len(fake.writes()) == writes


@pytest.mark.parametrize("ask_id", ["per_1", "frm_1"])
@pytest.mark.asyncio
async def test_an_ask_answered_during_the_listing_is_not_brought_back(
    ask_id: str,
) -> None:
    fake = _fake()
    fake.permissions = [_permission()]
    fake.forms = [_form()]
    async with driving(fake) as (adapter, _emitted):
        state = _register(adapter)
        listing = adapter._list_asks

        async def answered_meanwhile(owners: Any) -> Any:
            result = await listing(owners)
            state.forget_ask(ask_id)
            return result

        adapter._list_asks = answered_meanwhile  # type: ignore[method-assign,assignment]
        await adapter._seed_asks(state)

        assert {*state.permissions, *state.forms} == {"per_1", "frm_1"} - {ask_id}


def test_executions_reported_during_the_listing_win_over_it() -> None:
    state = SessionState("local", WORKDIR, "ses_root", children={"ses_child"})
    mark = state.mark()
    state.execution_ended("ses_root")
    state.execution_started("ses_child")

    state.seed_running({"ses_root"}, mark)

    assert state.running == {"ses_child"}


# ── Model, agent, permissions ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_and_effort_are_selected_on_the_session() -> None:
    fake = _fake()
    fake.models = [model_entry(), model_entry("plain", variants=())]
    fake.default_model = model_entry()
    async with driving(fake) as (adapter, _emitted):
        _register(adapter, model="opencode/big-pickle")
        await adapter.set_effort("local", "high")
        await adapter.set_model("local", "opencode/plain", "high")
        await adapter.set_model("local", None, "high")
        # An effort the model lacks is refused, and the selection stands.
        with pytest.raises(OpenCodeError, match="no reasoning effort 'max'"):
            await adapter.set_effort("local", "max")
        await adapter.set_effort("local", None)

    assert [
        body["model"] for body in fake.calls("POST", "/api/session/ses_root/model")
    ] == [
        {"providerID": "opencode", "id": "big-pickle", "variant": "high"},
        {"providerID": "opencode", "id": "plain"},
        {"providerID": "opencode", "id": "big-pickle", "variant": "high"},
        {"providerID": "opencode", "id": "big-pickle"},
    ]


@pytest.mark.asyncio
async def test_only_a_switch_waypoint_did_not_make_is_reported() -> None:
    fake = _fake()
    switches: list[tuple[str, str | None, str | None]] = []
    async with driving(fake, on_agent_changed=lambda *args: switches.append(args)) as (
        adapter,
        _emitted,
    ):
        _register(adapter)
        await adapter.set_agent("local", "plan")
        await _deliver(
            adapter,
            event("session.agent.selected", agent="plan"),
            event("session.agent.selected", agent="build"),
        )

    assert switches == [("local", "plan", "build")]


# ── Catalogs and threads ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_catalogs_wait_for_the_server_to_load_them() -> None:
    fake = _fake()
    fake.unsettled = 1
    fake.skills = [{"id": "wp-skill", "name": "WP", "description": "d"}]
    async with driving(fake) as (adapter, _emitted):
        commands = await adapter.list_commands()
        fake.unsettled = 1
        models = await adapter.list_models()

    assert commands == [
        {"name": "init", "description": None, "source": "command"},
        {"name": "wp-skill", "description": "d", "source": "skill"},
    ]
    assert [model.id for model in models] == ["opencode/big-pickle"]
    assert fake.calls("GET", "/api/model")[0] == {"location[directory]": WORKDIR}


@pytest.mark.asyncio
async def test_threads_and_history_follow_every_page() -> None:
    fake = FakeOpenCode()
    fake.page_size = 1
    fake.add_session("ses_1")
    fake.add_session("ses_2")
    fake.add_session("ses_sub", parentID="ses_1")
    fake.messages["ses_1"] = [{"id": "msg_1"}, {"id": "msg_2"}]
    async with driving(fake) as (adapter, _emitted):
        sessions = await adapter.list_sessions()
        messages = await adapter.get_session_messages("ses_1")
        with pytest.raises(OpenCodeError):
            await adapter.get_session_messages("ses_missing")
        assert await adapter.get_session("ses_missing") is None
        assert await adapter.delete_session("ses_2")
        assert not await adapter.delete_session("ses_2")

    assert [item["id"] for item in sessions] == ["ses_1", "ses_2"]
    assert [item["id"] for item in messages] == ["msg_1", "msg_2"]


@pytest.mark.asyncio
async def test_fork_writes_the_sources_rules() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, _emitted):
        forked = await adapter.fork_session("local2", WORKDIR, "ses_root", ASK)

    assert forked == "ses_root_fork"
    assert fake.calls("PATCH", "/api/session/ses_root_fork") == [{"permissions": ASK}]


# ── Server lifecycle ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("states", "failure"),
    [
        (
            [{"status": "required"}, {"status": "running"}, {"status": "completed"}],
            None,
        ),
        ([{"status": "error", "error": "disk full"}], "migration failed: disk full"),
    ],
)
@pytest.mark.asyncio
async def test_start_waits_for_the_history_migration(
    states: list[dict[str, Any]], failure: str | None
) -> None:
    fake = FakeOpenCode()
    fake.migration = list(states)
    async with driving(fake) as (adapter, _emitted):
        if failure:
            with pytest.raises(OpenCodeError, match=failure):
                await adapter._await_migration()
        else:
            await adapter._await_migration()

    assert len(fake.calls("GET", "/api/experimental/migration/v1")) == len(states)


@pytest.mark.asyncio
async def test_a_server_without_the_migration_route_is_ready() -> None:
    fake = FakeOpenCode()
    fake.failures[("GET", "/api/experimental/migration/v1")] = 404
    async with driving(fake) as (adapter, _emitted):
        await adapter._await_migration()


@pytest.mark.asyncio
async def test_starts_are_serialized_until_one_has_migrated() -> None:
    gate = MigrationGate()
    inside: list[str] = []
    overlap: list[int] = []

    async def start(name: str) -> None:
        async with gate.hold():
            inside.append(name)
            overlap.append(len(inside))
            await asyncio.sleep(0.01)
            gate.migrated = True
            inside.remove(name)

    await asyncio.gather(start("a"), start("b"), start("c"))
    # The first ran alone; the rest, queued behind it, ran together.
    assert overlap == [1, 1, 2]


@pytest.mark.asyncio
async def test_the_event_stream_is_open_before_start_returns() -> None:
    fake = _fake()
    async with fake.serving() as url:
        emitted = Emitted()
        adapter = OpenCodeAdapter(emit_event=emitted)
        adapter._client = LocalOpenCodeClient(url, ServerAuth("secret"))
        try:
            await asyncio.wait_for(adapter._connect_events(), timeout=5)
            assert adapter._connected.is_set()
        finally:
            await adapter.shutdown()


@pytest.mark.asyncio
async def test_a_stream_that_never_opens_fails_the_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module, "_MAX_SSE_FAILURES", 1)
    fake = _fake()
    fake.failures[("GET", "/api/event")] = 500
    died: list[list[str]] = []
    async with fake.serving() as url:
        adapter = OpenCodeAdapter(emit_event=Emitted(), on_server_died=died.append)
        adapter._client = LocalOpenCodeClient(url, ServerAuth("secret"))
        try:
            with pytest.raises(OpenCodeError, match="event stream"):
                await asyncio.wait_for(adapter._connect_events(), timeout=5)
        finally:
            await adapter.shutdown()
    assert died == []


@pytest.mark.asyncio
async def test_losing_the_stream_closes_asks_and_reports_the_sessions() -> None:
    fake = _fake()
    died: list[list[str]] = []
    async with fake.serving() as url:
        emitted = Emitted()
        adapter = OpenCodeAdapter(emit_event=emitted, on_server_died=died.append)
        adapter._client = LocalOpenCodeClient(url, ServerAuth("secret"))
        try:
            await adapter._connect_events()
            adapter._started = True
            _register(adapter)
            fake.events.put_nowait(_asked(fake, _permission()))
            fake.events.put_nowait(None)
            async with asyncio.timeout(5):
                while not died:
                    await asyncio.sleep(0.01)
        finally:
            await adapter.shutdown()

    assert died == [["local"]]
    assert emitted.methods() == [
        "permission.asked",
        "approval.invalidated",
        "server.disconnected",
    ]
    assert emitted.statuses[-2:] == [SessionStatus.ERROR, SessionStatus.ERROR]


@pytest.mark.asyncio
async def test_terminate_forgets_the_session() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, emitted):
        state = _register(adapter)
        state.children.add("ses_child")
        adapter._routes["ses_child"] = "local"

        assert await adapter.terminate_session("local")
        assert not await adapter.terminate_session("local")
        assert adapter._routes == {}

    assert emitted.statuses == [SessionStatus.EXITED]


@pytest.mark.asyncio
async def test_an_ending_handled_across_a_close_does_not_revive_it() -> None:
    fake = _fake()
    async with driving(fake) as (adapter, emitted):
        state = _register(adapter, changed_files=True)
        listing = adapter._emit_changes

        async def closed_meanwhile(target: SessionState) -> None:
            await adapter.terminate_session("local")
            await listing(target)

        adapter._emit_changes = closed_meanwhile  # type: ignore[method-assign,assignment]
        state.execution_started("ses_root")
        state.changed_files = True
        await _deliver(adapter, event("session.execution.succeeded"))

    assert emitted.statuses == [SessionStatus.EXITED]


@pytest.mark.asyncio
async def test_cancelled_start_discards_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = OpenCodeAdapter(emit_event=Emitted())
    discarded: list[str] = []

    class _Client:
        async def close(self) -> None:
            discarded.append("client")

    async def hang_while_starting() -> None:
        adapter._client = cast(Any, _Client())
        await asyncio.Event().wait()

    monkeypatch.setattr(adapter, "_start_local", hang_while_starting)
    monkeypatch.setattr(
        adapter, "_terminate_server_process", lambda: discarded.append("process")
    )

    start = asyncio.create_task(adapter.start())
    await asyncio.sleep(0.01)
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start

    assert discarded == ["process", "client"]
    assert adapter._client is None
    assert not adapter._started


@pytest.mark.asyncio
async def test_discarding_a_server_that_ignores_sigterm_kills_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = OpenCodeAdapter(emit_event=Emitted())
    exited = asyncio.Event()
    signals: list[int] = []

    class _Process:
        pid = 4242
        returncode: int | None = None

        async def wait(self) -> int:
            await exited.wait()
            return -signal.SIGKILL

    def killpg(_pid: int, sent: int) -> None:
        signals.append(sent)
        if sent == signal.SIGKILL:
            exited.set()

    monkeypatch.setattr("waypoint.backends.opencode.adapter.os.killpg", killpg)
    adapter._server_process = cast(Any, _Process())

    await adapter._discard_server()

    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert adapter._server_process is None
