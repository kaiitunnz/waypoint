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
