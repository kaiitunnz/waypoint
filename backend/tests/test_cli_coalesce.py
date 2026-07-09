import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from waypoint.cli import WaypointClient, app
from waypoint.settings import Settings


def test_sessions_output_raw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()

    def _config(p: Path) -> Path:
        cf = p / "waypoint.toml"
        cf.write_text('core:\n  url: "http://t"')
        return cf

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/events":
            return httpx.Response(
                200,
                json={
                    "events": [
                        {
                            "kind": "agent_output",
                            "text": "answ",
                            "sequence": 1,
                            "metadata": {"item_id": "1"},
                        },
                        {
                            "kind": "agent_output",
                            "text": "er",
                            "sequence": 2,
                            "metadata": {"item_id": "1"},
                        },
                    ],
                    "has_more": False,
                },
            )
        return httpx.Response(404)

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app, ["--config", str(_config(tmp_path)), "sessions", "output", "s1", "--raw"]
    )
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert len(data["events"]) == 2
    assert data["events"][0]["text"] == "answ"
    assert data["events"][1]["text"] == "er"


def test_sessions_events_coalesce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()

    def _config(p: Path) -> Path:
        cf = p / "waypoint.toml"
        cf.write_text('core:\n  url: "http://t"')
        return cf

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/events":
            return httpx.Response(
                200,
                json={
                    "events": [
                        {
                            "kind": "agent_output",
                            "text": "answ",
                            "sequence": 1,
                            "metadata": {"item_id": "1"},
                        },
                        {
                            "kind": "agent_output",
                            "text": "er",
                            "sequence": 2,
                            "metadata": {"item_id": "1"},
                        },
                    ],
                    "has_more": False,
                },
            )
        return httpx.Response(404)

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "events", "s1", "--coalesce"],
    )
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert len(data["events"]) == 1
    assert data["events"][0]["text"] == "answer"


def test_sessions_events_compact_coalesces_and_lifts_minimal_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    state: dict[str, object] = {}

    def _config(p: Path) -> Path:
        cf = p / "waypoint.toml"
        cf.write_text('core:\n  url: "http://t"')
        return cf

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/events":
            state["events_params"] = dict(request.url.params)
            return httpx.Response(
                200,
                json={
                    "events": [
                        {
                            "kind": "agent_output",
                            "text": "answ",
                            "sequence": 1,
                            "metadata": {
                                "item_id": "msg-1",
                                "status": "running",
                                "payload": {"large": True},
                            },
                        },
                        {
                            "kind": "agent_output",
                            "text": "er",
                            "sequence": 2,
                            "metadata": {
                                "item_id": "msg-1",
                                "status": "idle",
                                "payload": {"large": True},
                            },
                        },
                        {
                            "kind": "tool_call",
                            "text": "",
                            "sequence": 3,
                            "metadata": {
                                "item_id": "tool-1",
                                "item_type": "commandExecution",
                                "tool_name": "Bash",
                                "status": "running",
                                "payload": {"command": "secret-ish"},
                            },
                        },
                        {
                            "kind": "approval_request",
                            "text": "Approve command?",
                            "sequence": 4,
                            "metadata": {
                                "approval_id": "approval-1",
                                "status": "waiting_input",
                                "payload": {"details": "verbose"},
                            },
                        },
                    ],
                    "has_more": True,
                    "latest_todo": {
                        "kind": "system_note",
                        "text": "todo",
                        "sequence": 99,
                    },
                },
            )
        return httpx.Response(404)

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "events",
            "s1",
            "--before-sequence",
            "10",
            "--compact",
        ],
    )
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert data == {
        "events": [
            {
                "seq": 2,
                "kind": "agent_output",
                "text": "answer",
                "item_id": "msg-1",
                "status": "idle",
            },
            {
                "seq": 3,
                "kind": "tool_call",
                "text": "",
                "item_id": "tool-1",
                "item_type": "commandExecution",
                "tool": "Bash",
                "status": "running",
            },
            {
                "seq": 4,
                "kind": "approval_request",
                "text": "Approve command?",
                "status": "waiting_input",
                "approval_id": "approval-1",
            },
        ],
        "has_more": True,
    }
    assert state["events_params"] == {"before_sequence": "10"}


def test_sessions_events_compact_rejects_follow(tmp_path: Path) -> None:
    runner = CliRunner()
    cf = tmp_path / "waypoint.toml"
    cf.write_text('core:\n  url: "http://t"')

    result = runner.invoke(
        app,
        [
            "--config",
            str(cf),
            "sessions",
            "events",
            "s1",
            "--compact",
            "--follow",
        ],
    )

    assert result.exit_code == 1
    assert "--compact is not supported with --follow" in result.output
