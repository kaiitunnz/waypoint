"""`board ready`: the dep-satisfaction view over the task:/status: convention,
plus the CLI command."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from waypoint.cli import app, compute_ready_tasks
from waypoint.client import WaypointClient
from waypoint.settings import Settings

runner = CliRunner()


def _cell(key: str, text: str = "", **meta: str) -> dict[str, Any]:
    return {"key": key, "text": text, "metadata": dict(meta)}


def test_ready_when_all_deps_done() -> None:
    cells = [
        _cell("task:1", "scaffold"),
        _cell("status:1", state="done"),
        _cell("task:2", "build", deps="1"),
        _cell("status:2", state="todo"),
    ]
    ready = compute_ready_tasks(cells)
    assert [r["task"] for r in ready] == ["2"]
    assert ready[0]["deps"] == ["1"]


def test_not_ready_with_a_pending_dep() -> None:
    cells = [
        _cell("task:1"),
        _cell("status:1", state="doing"),
        _cell("task:2", deps="1"),
        _cell("status:2", state="todo"),
    ]
    assert compute_ready_tasks(cells) == []


def test_missing_dep_status_counts_as_not_done() -> None:
    # task:2 depends on 1, but there is no status:1 cell at all.
    cells = [_cell("task:2", deps="1"), _cell("status:2", state="todo")]
    assert compute_ready_tasks(cells) == []


def test_started_task_is_not_ready() -> None:
    # A task already doing/done is not "ready to start" even with deps clear.
    cells = [
        _cell("task:1", deps=""),
        _cell("status:1", state="doing"),
    ]
    assert compute_ready_tasks(cells) == []


def test_no_deps_and_todo_is_ready() -> None:
    cells = [_cell("task:1"), _cell("status:1", state="todo")]
    assert [r["task"] for r in compute_ready_tasks(cells)] == ["1"]


def test_multiple_comma_deps() -> None:
    cells = [
        _cell("task:1"),
        _cell("status:1", state="done"),
        _cell("task:2"),
        _cell("status:2", state="done"),
        _cell("task:3", deps="1, 2"),
        _cell("status:3", state="todo"),
    ]
    assert [r["task"] for r in compute_ready_tasks(cells)] == ["3"]


def test_one_cell_layout_is_tolerated() -> None:
    # deps and state both on the task cell (a post-metadata-patch one-cell shape).
    cells = [
        _cell("task:1", state="done"),
        _cell("task:2", deps="1", state="todo"),
    ]
    assert [r["task"] for r in compute_ready_tasks(cells)] == ["2"]


def test_non_conforming_channel_yields_empty() -> None:
    cells = [_cell("note:x", "hello"), _cell("phase", "build")]
    assert compute_ready_tasks(cells) == []


def test_mixed_numeric_and_named_task_keys_do_not_crash() -> None:
    # A non-numeric task key must not blow up the numeric sort, and a status
    # cell with no matching task cell is ignored.
    cells = [
        _cell("task:2"),
        _cell("status:2", state="todo"),
        _cell("task:foo"),
        _cell("status:foo", state="todo"),
        _cell("status:99", state="done"),  # orphan status, no task:99
    ]
    ready = {r["task"] for r in compute_ready_tasks(cells)}
    assert ready == {"2", "foo"}


def _config(tmp_path: Path) -> Path:
    settings = Settings(data_dir=tmp_path / "data")
    path = tmp_path / "waypoint.yaml"
    path.write_text(f"data_dir: {settings.data_dir}\n", encoding="utf-8")
    return path


def test_board_ready_command_emits_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/board/job:x" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "channel": "job:x",
                    "entries": [
                        {"id": 1, "key": "task:1", "text": "a", "metadata": {}},
                        {
                            "id": 2,
                            "key": "status:1",
                            "text": "",
                            "metadata": {"state": "done"},
                        },
                        {
                            "id": 3,
                            "key": "task:2",
                            "text": "b",
                            "metadata": {"deps": "1"},
                        },
                        {
                            "id": 4,
                            "key": "status:2",
                            "text": "",
                            "metadata": {"state": "todo"},
                        },
                    ],
                },
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app, ["--config", str(_config(tmp_path)), "board", "ready", "job:x"]
    )
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert out["channel"] == "job:x"
    assert [r["task"] for r in out["ready"]] == ["2"]
