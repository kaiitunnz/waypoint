"""A plugin that fails to wire, or is not loaded, leaves its sessions unavailable."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from restore_harness import (
    add_assistant,
    add_session,
    assert_refused,
    assistant_settings,
    make_runtime,
    noop,
    notes,
    raising,
    set_column,
    started,
    stored,
    stub_restores,
)
from waypoint.api import create_app
from waypoint.backends import get_registry
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    EventKind,
    SessionAttachRequest,
    SessionCreateRequest,
    SessionInputRequest,
    SessionStatus,
)
from waypoint.settings import Settings

pytestmark = pytest.mark.usefixtures("quiet_warming")


def break_plugin(monkeypatch: pytest.MonkeyPatch, plugin_id: str, stage: str) -> None:
    monkeypatch.setattr(get_registry().get(plugin_id), stage, raising("bad wiring"))


def orphan(runtime: SessionRuntime, session_id: str) -> None:
    """Point the session at a transport no plugin provides."""
    set_column(runtime, session_id, "transport", "gone_transport")


@pytest.mark.parametrize("stage", ["setup", "transport_view"])
async def test_plugin_that_fails_to_wire_is_visibly_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stage: str,
) -> None:
    break_plugin(monkeypatch, "codex", stage)
    caplog.set_level(logging.ERROR, logger="waypoint.plugin_availability")
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")

    async with started(runtime):
        reason = f"backend plugin codex failed in {stage}: bad wiring"
        assert stored(runtime, "s1").status == SessionStatus.ERROR
        assert f"Session unavailable: {reason}" in notes(runtime, "s1")
        assert any(
            record.exc_info and f"codex failed in {stage}" in record.getMessage()
            for record in caplog.records
        )
        await assert_refused(
            runtime.handle_input("s1", SessionInputRequest(text="hi")), 503, reason
        )
        launch = SessionCreateRequest(
            backend="codex", transport="codex_app_server", cwd=str(tmp_path)
        )
        await assert_refused(runtime.create_session(launch), 503)
        await runtime.terminate("s1")
        assert stored(runtime, "s1").status == SessionStatus.EXITED
        await runtime.delete("s1")


def test_plugin_that_fails_to_register_routes_does_not_stop_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    break_plugin(monkeypatch, "codex", "register_routes")

    app = create_app(Settings(data_dir=tmp_path / "data"))

    assert app.state.context.runtime.availability.failures == {
        "codex": "failed in register_routes: bad wiring"
    }


async def test_session_with_no_plugin_does_not_block_startup(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="waypoint.plugin_availability")
    runtime = make_runtime(tmp_path, **assistant_settings())
    add_assistant(runtime)
    orphan(runtime, "assistant")

    async with started(runtime):
        assert stored(runtime, "assistant").status == SessionStatus.ERROR
        assert "no backend plugin provides codex over gone_transport" in notes(
            runtime, "assistant"
        )
        assert any(
            record.exc_info and "gone_transport" in record.getMessage()
            for record in caplog.records
        )
        summary = runtime.assistant_summary()
        assert summary is not None
        assert not summary.supports_reattach


async def test_session_with_no_plugin_answers_without_failing(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    orphan(runtime, "s1")

    async with started(runtime):
        with pytest.raises(HTTPException) as settings:
            runtime.get_launch_settings("s1")
        assert settings.value.status_code == 503
        await assert_refused(runtime.clone_session_launch("s1"), 503)
        assert await runtime.get_command_completions("s1") == ([], False)
        assert (await runtime.refresh_rate_limit_usage("s1")).id == "s1"


def test_routes_refuse_a_session_with_no_plugin(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    context = app.state.context
    add_session(context.runtime, "s1")
    orphan(context.runtime, "s1")
    token = context.tokens.issue().token

    with TestClient(app) as client:
        response = client.delete(
            "/api/sessions/s1/side-questions/q1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 503
        with client.websocket_connect(f"/ws/sessions/s1/terminal?token={token}") as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()
        assert closed.value.code == 4403


@pytest.mark.parametrize(
    ("terminate", "status_after"),
    [(False, SessionStatus.RUNNING), (True, SessionStatus.EXITED)],
)
async def test_session_status_once_its_plugin_is_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminate: bool,
    status_after: SessionStatus,
) -> None:
    stub_restores(monkeypatch, {"pane": noop})
    with monkeypatch.context() as broken:
        break_plugin(broken, "tmux", "setup")
        broken.setattr(get_registry().get("tmux"), "terminate_session", noop)
        runtime = make_runtime(tmp_path)
        add_session(runtime, "pane", transport="tmux", status=SessionStatus.RUNNING)
        async with started(runtime):
            assert stored(runtime, "pane").status == SessionStatus.ERROR
            if terminate:
                await runtime.terminate("pane")

    recovered = make_runtime(tmp_path)
    async with started(recovered):
        session = stored(recovered, "pane")
        assert session.status == status_after
        assert "unavailable_mark" not in session.transport_state


async def test_unavailable_session_is_noted_once_and_stays_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    break_plugin(monkeypatch, "codex", "setup")
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    runtime.storage.update_session("s1", read_at=datetime.now(UTC))
    async with started(runtime):
        assert not stored(runtime, "s1").unread

    again = make_runtime(tmp_path)
    async with started(again):
        assert notes(again, "s1").count("Session unavailable") == 1
        assert not stored(again, "s1").unread


async def test_failed_plugin_shows_on_a_session_awaiting_its_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    break_plugin(monkeypatch, "codex", "setup")
    target = SshLaunchTargetConfig(
        id="pw",
        name="pw",
        ssh_destination="host",
        ssh_auth="password",
        ssh_args=[
            "-oControlMaster=auto",
            "-oControlPath=/tmp/wp-%C",
            "-oControlPersist=60",
        ],
    )
    runtime = make_runtime(tmp_path, ssh_targets=[target])
    monkeypatch.setattr(runtime.ssh_master, "disconnect_all", noop)
    add_session(runtime, "s1")
    set_column(runtime, "s1", "launch_target_id", "pw")

    async with started(runtime):
        assert stored(runtime, "s1").status == SessionStatus.ERROR
        assert "Session unavailable" in notes(runtime, "s1")


async def test_nothing_lands_on_a_failed_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1", backend="claude_code", transport="claude_cli")

    def offered() -> set[str]:
        return {o.id for o in runtime.get_launch_settings("s1").transport_options}

    assert "tmux" in offered()
    runtime.availability.failures["tmux"] = "failed in setup: bad wiring"
    assert "tmux" not in offered()

    async def describe_target(_target: str) -> Any:
        return SimpleNamespace()

    monkeypatch.setattr(runtime.tmux, "describe_target", describe_target)
    for request in (
        runtime.import_thread("codex", {"thread_id": "t", "transport": "tmux"}),
        runtime.attach_tmux(
            SessionAttachRequest(tmux_target="main", backend_hint="codex")
        ),
    ):
        await assert_refused(request, 503, "backend plugin tmux")


async def test_questions_of_a_failed_plugin_stay_in_the_feed(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    await runtime._emit_adapter_event(
        "s1",
        EventKind.TOOL_CALL,
        "AskUserQuestion",
        {
            "tool_name": "AskUserQuestion",
            "tool_use_id": "q1",
            "payload": {
                "input": {
                    "questions": [{"question": "Which?", "options": [{"label": "A"}]}]
                }
            },
        },
        SessionStatus.WAITING_INPUT,
    )
    runtime.availability.failures["codex"] = "failed in setup: bad wiring"

    [ask] = (await runtime.asks.snapshot()).items

    assert (ask.session_id, getattr(ask, "availability", None)) == (
        "s1",
        "unavailable",
    )
