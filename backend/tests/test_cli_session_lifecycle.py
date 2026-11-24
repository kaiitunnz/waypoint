import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from waypoint.cli import app
from waypoint.client import WaypointClient
from waypoint.settings import Settings

runner = CliRunner()

type Route = tuple[str, str]


pytestmark = pytest.mark.usefixtures("isolate_waypoint_env")


def _server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[Route, tuple[int, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Point the CLI at a mock server; returns ``(cli_prefix, calls)``."""
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {
                "route": (request.method, request.url.path),
                "params": list(request.url.params.multi_items()),
                "body": json.loads(request.content) if request.content else None,
            }
        )
        key = (request.method, request.url.path)
        if key not in routes:
            return httpx.Response(404, json={"detail": f"unexpected {key}"})
        code, payload = routes[key]
        if payload is None:
            return httpx.Response(code)
        return httpx.Response(code, json=payload)

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    cfg = tmp_path / "waypoint.yaml"
    cfg.write_text(f"data_dir: {tmp_path / 'data'}\n", encoding="utf-8")
    return ["--config", str(cfg)], calls


def _routes(calls: list[dict[str, Any]]) -> list[Route]:
    return [call["route"] for call in calls]


@pytest.mark.parametrize("action", ["fork", "clone"])
def test_derive_sends_overrides_and_follow_up_as_spawner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    monkeypatch.setenv("WAYPOINT_SESSION_ID", "lead")
    prefix, calls = _server(
        tmp_path,
        monkeypatch,
        {
            ("POST", f"/api/sessions/src/{action}"): (200, {"session": {"id": "new"}}),
            ("POST", "/api/sessions/new/input"): (
                200,
                {"session": {"id": "new", "status": "running"}},
            ),
        },
    )
    result = runner.invoke(
        app,
        [
            *prefix,
            "sessions",
            action,
            "src",
            "carry on",
            "--title",
            "child",
            "--tag",
            "role=worker",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["body"] == {
        "spawner_session_id": "lead",
        "title": "child",
        "tags": {"role": "worker"},
    }
    assert calls[1]["body"]["text"] == "carry on"
    assert calls[1]["body"]["sender_session_id"] == "lead"
    assert json.loads(result.output) == {"session": {"id": "new", "status": "running"}}


def test_fork_without_message_or_spawner_sends_empty_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix, calls = _server(
        tmp_path,
        monkeypatch,
        {("POST", "/api/sessions/src/fork"): (200, {"session": {"id": "new"}})},
    )
    result = runner.invoke(app, [*prefix, "sessions", "fork", "src"])
    assert result.exit_code == 0, result.output
    assert _routes(calls) == [("POST", "/api/sessions/src/fork")]
    assert calls[0]["body"] == {}


def test_fork_follow_up_held_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_SESSION_ID", "lead")
    prefix, _ = _server(
        tmp_path,
        monkeypatch,
        {
            ("POST", "/api/sessions/src/fork"): (200, {"session": {"id": "new"}}),
            ("POST", "/api/sessions/new/input"): (
                200,
                {"session": {"id": "new"}, "held_message": {"id": "h1"}},
            ),
        },
    )
    result = runner.invoke(app, [*prefix, "sessions", "fork", "src", "hi"])
    assert result.exit_code == 0, result.output
    session = json.loads(result.output)["session"]
    assert session["send"] == "held"
    assert session["held_message_id"] == "h1"


def test_fork_follow_up_failure_keeps_new_session_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix, _ = _server(
        tmp_path,
        monkeypatch,
        {
            ("POST", "/api/sessions/src/fork"): (200, {"session": {"id": "new"}}),
            ("POST", "/api/sessions/new/input"): (409, {"detail": "starting"}),
        },
    )
    result = runner.invoke(app, [*prefix, "sessions", "fork", "src", "hi"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["session"] == {"id": "new", "send": "failed"}
    assert "409" in payload["error"]


@pytest.mark.parametrize("command", ["reattach", "resume"])
def test_revive_commands_post_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    prefix, calls = _server(
        tmp_path,
        monkeypatch,
        {("POST", f"/api/sessions/s1/{command}"): (200, {"session": {"id": "s1"}})},
    )
    result = runner.invoke(app, [*prefix, "sessions", command, "s1"])
    assert result.exit_code == 0, result.output
    assert _routes(calls) == [("POST", f"/api/sessions/s1/{command}")]


def test_history_passes_query_and_paging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = {"sessions": [{"id": "old"}], "has_more": True, "cursor": "c2", "total": 3}
    prefix, calls = _server(
        tmp_path, monkeypatch, {("GET", "/api/sessions/history"): (200, page)}
    )
    result = runner.invoke(
        app,
        [
            *prefix,
            "sessions",
            "history",
            "repo:waypoint",
            "--cursor",
            "c1",
            "--limit",
            "1",
            "--exclude-source",
            "assistant",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == page
    assert calls[0]["params"] == [
        ("q", "repo:waypoint"),
        ("limit", "1"),
        ("cursor", "c1"),
        ("exclude_source", "assistant"),
    ]


def test_approve_plan_sends_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix, calls = _server(
        tmp_path,
        monkeypatch,
        {("POST", "/api/sessions/s1/approve-plan"): (200, {"session": {"id": "s1"}})},
    )
    result = runner.invoke(
        app,
        [
            *prefix,
            "sessions",
            "approve-plan",
            "s1",
            "--plan-item-id",
            "p1",
            "--decision",
            "acceptForSession",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["body"] == {
        "plan_item_id": "p1",
        "decision": "acceptForSession",
        "text": None,
    }


def test_approve_plan_rejects_unknown_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix, calls = _server(tmp_path, monkeypatch, {})
    result = runner.invoke(
        app,
        [
            *prefix,
            "sessions",
            "approve-plan",
            "s1",
            "--plan-item-id",
            "p1",
            "--decision",
            "yes",
        ],
    )
    assert result.exit_code == 2
    assert calls == []


@pytest.mark.parametrize(
    ("args", "body"),
    [([], {"tool_use_id": None}), (["--tool-use-id", "tu9"], {"tool_use_id": "tu9"})],
)
def test_cancel_question_sends_optional_tool_use_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], body: Any
) -> None:
    prefix, calls = _server(
        tmp_path,
        monkeypatch,
        {
            ("POST", "/api/sessions/s1/cancel-question"): (
                200,
                {"session": {"id": "s1"}},
            )
        },
    )
    result = runner.invoke(app, [*prefix, "sessions", "cancel-question", "s1", *args])
    assert result.exit_code == 0, result.output
    assert calls[0]["body"] == body


def test_held_list_prints_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix, _ = _server(
        tmp_path,
        monkeypatch,
        {
            ("GET", "/api/sessions/s1/held-messages"): (
                200,
                {"session_id": "s1", "held_messages": [{"id": "h1"}]},
            )
        },
    )
    result = runner.invoke(app, [*prefix, "sessions", "held", "list", "s1"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"held_messages": [{"id": "h1"}]}


@pytest.mark.parametrize(
    ("args", "route", "output"),
    [
        (["release", "h1"], ("POST", "/api/held-messages/h1/release"), None),
        (
            ["release-all", "s1"],
            ("POST", "/api/sessions/s1/held-messages/release"),
            None,
        ),
        (["cancel", "h1"], ("DELETE", "/api/held-messages/h1"), {"cancelled": "h1"}),
        (
            ["cancel-all", "s1"],
            ("DELETE", "/api/sessions/s1/held-messages"),
            {"session_id": "s1", "cancelled": "all"},
        ),
    ],
)
def test_held_commands_hit_their_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    route: Route,
    output: dict[str, Any] | None,
) -> None:
    response = (204, None) if output else (200, {"session": {"id": "s1"}})
    prefix, calls = _server(tmp_path, monkeypatch, {route: response})
    result = runner.invoke(app, [*prefix, "sessions", "held", *args])
    assert result.exit_code == 0, result.output
    assert _routes(calls) == [route]
    if output:
        assert json.loads(result.output) == output
