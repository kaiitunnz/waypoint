"""CLI tests for the Waypoint Manager surface: they verify argument parsing and
client routing (which HTTP call, which body) rather than server logic, which the
route-level test_manager_api.py and the pure test_manager.py already cover."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from waypoint.cli import app as cli_app
from waypoint.client import WaypointClient
from waypoint.settings import Settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "WAYPOINT_DATA_DIR",
        "WAYPOINT_CONFIG_PATH",
        "WAYPOINT_HOST",
        "WAYPOINT_PORT",
        "WAYPOINT_PASSWORD",
        "WAYPOINT_TOKEN",
        "WAYPOINT_SESSION_ID",
    ):
        monkeypatch.delenv(var, raising=False)


def _cli_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "waypoint.yaml"
    cfg.write_text(
        f"default_backend: codex\ndata_dir: {tmp_path / 'data'}\n", encoding="utf-8"
    )
    return cfg


def _mock_cli(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)


# ── manager ─────────────────────────────────────────────────────────────────


def test_cli_manager_ticket_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/manager/tickets"
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"ticket": {"id": "ticket-1", "state": "intake"}}
        )

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "ticket",
            "add",
            "My ticket",
            "--priority",
            "p1",
            "--scale",
            "substantial",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["ticket"]["id"] == "ticket-1"
    assert captured["body"] == {
        "title": "My ticket",
        "priority": "p1",
        "scale": "substantial",
    }


def test_cli_manager_next(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/manager/next"
        return httpx.Response(
            200,
            json={
                "slots": {"total": 2, "used": 0, "free": 2},
                "tickets": [
                    {
                        "ticket_id": "ticket-1",
                        "priority": "p2",
                        "state": "intake",
                        "legal_transitions": ["triaged"],
                    }
                ],
                "recommended": {
                    "ticket_id": "ticket-1",
                    "from_state": "intake",
                    "to_state": "triaged",
                    "event": "triage",
                    "reason": "new ticket awaiting triage",
                },
            },
        )

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app,
        ["--config", str(_cli_config(tmp_path)), "manager", "next", "--json"],
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["recommended"]["ticket_id"] == "ticket-1"
    assert body["tickets"][0]["legal_transitions"] == ["triaged"]


def test_cli_manager_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/manager/state"
        return httpx.Response(
            200,
            json={
                "config": {"trunk": "main"},
                "slots": {"total": 1, "used": 1, "free": 0},
                "tickets": [{"id": "ticket-1", "priority": "p2", "state": "building"}],
            },
        )

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app,
        ["--config", str(_cli_config(tmp_path)), "manager", "state", "--json"],
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["slots"]["free"] == 0
    assert body["tickets"][0]["id"] == "ticket-1"


def test_cli_manager_ticket_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/manager/tickets/ticket-1/transition"
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"ticket": {"id": "ticket-1", "state": "triaged"}}
        )

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "ticket",
            "transition",
            "ticket-1",
            "--to",
            "triaged",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["ticket"]["state"] == "triaged"
    assert captured["body"] == {"to": "triaged"}


# ── sessions wake ─────────────────────────────────────────────────────────


def test_cli_sessions_wake_on_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/sessions/codex-1/wake-subscriptions"
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"subscription": {"id": "wake-1"}})

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "sessions",
            "wake-on-board",
            "codex-1",
            "--channels",
            "ticket-*",
            "--wake-on-inbox",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["subscription"]["id"] == "wake-1"
    assert captured["body"] == {
        "channel_globs": ["ticket-*"],
        "kinds": [],
        "wake_on_inbox": True,
    }


def test_cli_sessions_wake_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/sessions/codex-1/wake-subscriptions/wake-1"
        return httpx.Response(200, json={"deleted": True})

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "sessions",
            "wake-off",
            "codex-1",
            "--id",
            "wake-1",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout) == {"deleted": True}


# ── board wait ──────────────────────────────────────────────────────────────


def test_cli_board_wait_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/board":
            return httpx.Response(200, json={"channels": [{"channel": "ticket-1"}]})
        if request.url.path == "/api/board/ticket-1":
            return httpx.Response(200, json={"entries": [{"id": 5, "text": "x"}]})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "board",
            "wait",
            "--channels",
            "ticket-*",
            "--since",
            "0",
        ],
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["outcome"] == "changed"
    assert body["channel"] == "ticket-1"
    assert body["entries"] == [{"id": 5, "text": "x"}]


def test_cli_board_wait_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json={"channels": []})

    _mock_cli(monkeypatch, handler)
    # No board change: the WS stream is unavailable and polling never matches, so
    # the short timeout wins and the envelope reports the timeout outcome.
    monkeypatch.setattr(WaypointClient, "list_board_channels", lambda self: [])

    async def failing_stream(self: WaypointClient) -> AsyncIterator[dict[str, Any]]:
        raise OSError("no ws")
        yield {}  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(WaypointClient, "stream_global_envelopes", failing_stream)
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "board",
            "wait",
            "--channels",
            "ticket-*",
            "--since",
            "0",
            "--timeout",
            "0.05",
        ],
    )
    assert result.exit_code == 124, result.stdout
    assert json.loads(result.stdout)["outcome"] == "timeout"


# ── manager render ──────────────────────────────────────────────────────────


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "waypoint-manager.yaml"
    path.write_text(
        "project: waypoint\n"
        "trunk: main\n"
        "board:\n"
        "  tickets_channel: tickets\n"
        "  org_channel: org\n"
        "  ticket_channel_prefix: ticket-\n",
        encoding="utf-8",
    )
    return path


def _template(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "tmpl.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_cli_manager_render_manifest_and_set(tmp_path: Path) -> None:
    # No --ticket, so no server call: manifest + --set resolve everything.
    tmpl = _template(
        tmp_path,
        "Ticket {{ticket_id}} for {{project}} on {{trunk}} / {{tickets_channel}}"
        " in {{spec_dir}}: {{note}}",
    )
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "render",
            str(tmpl),
            "--manifest",
            str(_manifest(tmp_path)),
            "--set",
            "ticket_id=ticket-9",
            "--set",
            "note=hello",
        ],
    )
    assert result.exit_code == 0, result.stdout
    # {{spec_dir}} defaults to .waypoint/specs when the manifest omits it.
    assert result.stdout == (
        "Ticket ticket-9 for waypoint on main / tickets in .waypoint/specs: hello"
    )


def test_cli_manager_render_spec_dir_override(tmp_path: Path) -> None:
    manifest = tmp_path / "waypoint-manager.yaml"
    manifest.write_text("spec_dir: custom/specs\n", encoding="utf-8")
    tmpl = _template(tmp_path, "spec at {{spec_dir}}")
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "render",
            str(tmpl),
            "--manifest",
            str(manifest),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert result.stdout == "spec at custom/specs"


def test_cli_manager_render_strict_fails_on_unknown(tmp_path: Path) -> None:
    tmpl = _template(tmp_path, "Hi {{mystery}}")
    result = runner.invoke(
        cli_app,
        ["--config", str(_cli_config(tmp_path)), "manager", "render", str(tmpl)],
    )
    assert result.exit_code != 0
    assert "unresolved placeholders: mystery" in result.output


def test_cli_manager_render_allow_unresolved(tmp_path: Path) -> None:
    tmpl = _template(tmp_path, "Hi {{mystery}}")
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "render",
            str(tmpl),
            "--allow-unresolved",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert result.stdout == "Hi {{mystery}}"


def test_cli_manager_render_ticket_pulls_record_and_board_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/manager/tickets/42":
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "id": "42",
                        "title": "Fix bug",
                        "priority": "p1",
                        "scale": "trivial",
                        "footprint": [],
                        "spec_ref": "docs/x.md",
                        "branch": "ticket/42",
                        "pr_url": None,
                    }
                },
            )
        assert request.url.path == "/api/board/tickets"
        assert request.url.params.get("key") == "ticket:42"
        return httpx.Response(
            200,
            json={
                "entries": [
                    {
                        "text": "the reported bug",
                        "metadata": {"input_type": "bug-report"},
                    }
                ]
            },
        )

    _mock_cli(monkeypatch, handler)
    tmpl = _template(
        tmp_path,
        "{{ticket_title}} [{{input_type}}] on {{ticket_channel}}: {{ticket_body}} (spec {{spec_ref}})",
    )
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "render",
            str(tmpl),
            "--manifest",
            str(_manifest(tmp_path)),
            "--ticket",
            "42",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert result.stdout == (
        "Fix bug [bug-report] on ticket-42: the reported bug (spec docs/x.md)"
    )


def test_cli_manager_render_set_overrides_board_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/manager/tickets/42":
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "id": "42",
                        "title": "t",
                        "priority": "p2",
                        "scale": None,
                        "footprint": [],
                        "spec_ref": None,
                        "branch": None,
                        "pr_url": None,
                    }
                },
            )
        return httpx.Response(
            200, json={"entries": [{"text": "board body", "metadata": {}}]}
        )

    _mock_cli(monkeypatch, handler)
    tmpl = _template(tmp_path, "body={{ticket_body}}")
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "render",
            str(tmpl),
            "--manifest",
            str(_manifest(tmp_path)),
            "--ticket",
            "42",
            "--set",
            "ticket_body=override wins",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert result.stdout == "body=override wins"


# ── manager deinit / ticket delete ──────────────────────────────────────────


def test_cli_manager_deinit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/manager"
        captured["hit"] = True
        return httpx.Response(200, json={"deinitialized": True, "tickets_deleted": 3})

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app,
        ["--config", str(_cli_config(tmp_path)), "manager", "deinit", "--yes"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured.get("hit")
    assert json.loads(result.stdout)["tickets_deleted"] == 3


def test_cli_manager_deinit_aborts_without_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hit = {"server": False}

    def handler(request: httpx.Request) -> httpx.Response:
        hit["server"] = True
        return httpx.Response(200, json={})

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app,
        ["--config", str(_cli_config(tmp_path)), "manager", "deinit"],
        input="n\n",
    )
    assert result.exit_code != 0  # aborted at the confirmation prompt
    assert hit["server"] is False  # the server is never hit on abort


def test_cli_manager_ticket_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/manager/tickets/ticket-1"
        return httpx.Response(200, json={"deleted": True})

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "ticket",
            "delete",
            "ticket-1",
        ],
    )
    assert result.exit_code == 0, result.stdout


def test_cli_manager_init_sends_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/manager/init"
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"config": {}})

    _mock_cli(monkeypatch, handler)
    manifest = tmp_path / "m.yaml"
    manifest.write_text("trunk: main\n", encoding="utf-8")
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "init",
            "--manifest",
            str(manifest),
            "--owner",
            "mgr-7",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["config"]["owner_session_id"] == "mgr-7"
