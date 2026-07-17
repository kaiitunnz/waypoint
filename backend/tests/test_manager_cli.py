"""CLI tests for the Waypoint Manager surface: they verify argument parsing and
client routing (which HTTP call, which body) rather than server logic, which the
route-level test_manager_api.py and the pure test_manager.py already cover."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import typer
from typer.testing import CliRunner

from waypoint.cli import _resolve_conditionals
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
                "tree": {"free": True, "held_by": None},
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


def test_cli_manager_reconcile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/manager/reconcile"
        return httpx.Response(
            200,
            json={
                "unregistered_intake": [
                    {"id": 7, "author_session_id": "human", "text": "fix X"}
                ],
                "dead_leads": [],
                "latency_timeouts": [],
            },
        )

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app,
        ["--config", str(_cli_config(tmp_path)), "manager", "reconcile", "--json"],
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["unregistered_intake"][0]["id"] == 7


def test_cli_manager_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/manager/state"
        return httpx.Response(
            200,
            json={
                "config": {"trunk": "main"},
                "tree": {"free": False, "held_by": "ticket-1"},
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
    assert body["tree"]["held_by"] == "ticket-1"
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


def _compiled_step(tmp_path: Path, role: str, step: str, body: str) -> str:
    """Write a compiled `<root>/<role>/<step>.md`; return the compiled root."""
    root = tmp_path / "compiled"
    (root / role).mkdir(parents=True, exist_ok=True)
    (root / role / f"{step}.md").write_text(body, encoding="utf-8")
    return str(root.resolve())


def _render_context(templates_dir: str) -> dict[str, Any]:
    return {
        "templates_dir": templates_dir,
        "tickets_channel": "tickets",
        "ticket_channel_prefix": "ticket-",
    }


def _state_response(render_context: dict[str, Any] | None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "config": {"trunk": "main", "render_context": render_context},
            "tree": {"free": True, "held_by": None},
            "tickets": [],
        },
    )


def test_cli_manager_render_per_ticket_and_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The compiled template carries the static values as literals (baked at init);
    # render fills only the per-ticket placeholders, here via --set.
    root = _compiled_step(
        tmp_path,
        "tech_lead",
        "brief",
        "Ticket {{ticket_id}} for waypoint on main / tickets: {{note}}",
    )
    rc = _render_context(root)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/manager/state"
        return _state_response(rc)

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "render",
            "--role",
            "tech_lead",
            "--step",
            "brief",
            "--set",
            "ticket_id=ticket-9",
            "--set",
            "note=hello",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert result.stdout == "Ticket ticket-9 for waypoint on main / tickets: hello"


def test_cli_manager_render_unknown_step_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc = _render_context(_compiled_step(tmp_path, "tech_lead", "brief", "x"))
    _mock_cli(monkeypatch, lambda request: _state_response(rc))
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "render",
            "--role",
            "prd_writer",
            "--step",
            "write",
        ],
    )
    assert result.exit_code != 0
    assert "no compiled template" in result.output


def test_cli_manager_render_requires_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_cli(monkeypatch, lambda request: _state_response(None))
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "render",
            "--role",
            "tech_lead",
            "--step",
            "brief",
        ],
    )
    assert result.exit_code != 0
    assert "no render context" in result.output


def test_cli_manager_render_strict_fails_on_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc = _render_context(
        _compiled_step(tmp_path, "tech_lead", "brief", "Hi {{mystery}}")
    )
    _mock_cli(monkeypatch, lambda request: _state_response(rc))
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "render",
            "--role",
            "tech_lead",
            "--step",
            "brief",
        ],
    )
    assert result.exit_code != 0
    assert "unresolved placeholders: mystery" in result.output


def test_cli_manager_render_allow_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc = _render_context(
        _compiled_step(tmp_path, "tech_lead", "brief", "Hi {{mystery}}")
    )
    _mock_cli(monkeypatch, lambda request: _state_response(rc))
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "render",
            "--role",
            "tech_lead",
            "--step",
            "brief",
            "--allow-unresolved",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert result.stdout == "Hi {{mystery}}"


def test_cli_manager_render_ticket_pulls_record_and_board_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _compiled_step(
        tmp_path,
        "tech_lead",
        "brief",
        "{{ticket_title}} [{{input_type}}] on {{ticket_channel}}: {{ticket_body}} (spec {{spec_ref}})",
    )
    rc = _render_context(root)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/manager/state":
            return _state_response(rc)
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
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "render",
            "--role",
            "tech_lead",
            "--step",
            "brief",
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
    rc = _render_context(
        _compiled_step(tmp_path, "tech_lead", "brief", "body={{ticket_body}}")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/manager/state":
            return _state_response(rc)
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
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "manager",
            "render",
            "--role",
            "tech_lead",
            "--step",
            "brief",
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


def test_cli_manager_init_compiles_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir = tmp_path / "raw" / "tech-lead"
    raw_dir.mkdir(parents=True)
    (raw_dir / "brief.md").write_text(
        "{{project}} on {{trunk}} / {{tickets_channel}}\n"
        "launch: {{tech_lead_launch}}\n"
        "scale: {{substantial_when}}\n"
        "escalate: {{always_escalate}}\n"
        "ci: {{require_ci_green}}\n"
        "ticket {{ticket_id}}\n",
        encoding="utf-8",
    )
    compiled = tmp_path / "compiled"
    manifest = tmp_path / "waypoint-manager.yaml"
    manifest.write_text(
        "project: waypoint\n"
        "trunk: main\n"
        f"templates_dir: {compiled}\n"
        "board:\n"
        "  tickets_channel: tickets\n"
        "  ticket_channel_prefix: ticket-\n"
        "scale:\n"
        "  substantial_when: needs a schema change\n"
        "escalation:\n"
        "  self_decide: [retryable-error]\n"
        "  always_escalate: [product-decision, scope-change]\n"
        "integration:\n"
        "  require_ci_green: false\n"
        "roles:\n"
        "  tech_lead:\n"
        '    launch: { backend: claude_code, model: "opus[1m]", '
        "permission_mode: auto }\n"
        f"    templates: {raw_dir}\n",
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/manager/init"
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"config": {}})

    _mock_cli(monkeypatch, handler)
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
            "mgr",
        ],
    )
    assert result.exit_code == 0, result.stdout

    body = (compiled / "tech_lead" / "brief.md").read_text(encoding="utf-8")
    assert "waypoint on main / tickets" in body  # channels/project baked
    assert 'launch: --backend claude_code --model "opus[1m]" ' in body  # model quoted
    assert "scale: needs a schema change" in body  # scale.substantial_when
    assert "escalate: product-decision, scope-change" in body  # list joined
    assert "ci: false" in body  # integration.require_ci_green baked
    assert "{{ticket_id}}" in body  # per-ticket left intact

    rc = captured["body"]["config"]["render_context"]
    assert rc["templates_dir"] == str(compiled.resolve())
    assert rc["tickets_channel"] == "tickets"
    assert rc["ticket_channel_prefix"] == "ticket-"


def test_cli_manager_init_resolves_relative_templates_under_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A relative `templates:` path resolves under the repo root (like templates_dir),
    # not the manifest's directory. The manifest sits in a subdir, so manifest-dir
    # resolution would look in the wrong place.
    monkeypatch.setattr("waypoint.cli._git_toplevel", lambda: str(tmp_path))
    raw_dir = tmp_path / "raw" / "tech-lead"
    raw_dir.mkdir(parents=True)
    (raw_dir / "brief.md").write_text(
        "{{project}} ticket {{ticket_id}}\n", encoding="utf-8"
    )
    manifest_dir = tmp_path / "cfg"
    manifest_dir.mkdir()
    manifest = manifest_dir / "waypoint-manager.yaml"
    manifest.write_text(
        "project: waypoint\n"
        "trunk: main\n"
        "templates_dir: compiled\n"
        "board:\n"
        "  tickets_channel: tickets\n"
        "  ticket_channel_prefix: ticket-\n"
        "roles:\n"
        "  tech_lead:\n"
        '    launch: { backend: claude_code, model: "opus[1m]", '
        "permission_mode: auto }\n"
        "    templates: raw/tech-lead\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"config": {}})

    _mock_cli(monkeypatch, handler)
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
            "mgr",
        ],
    )
    assert result.exit_code == 0, result.stdout
    body = (tmp_path / "compiled" / "tech_lead" / "brief.md").read_text(
        encoding="utf-8"
    )
    assert "waypoint ticket {{ticket_id}}" in body  # per-repo-root source compiled


def test_cli_manager_init_bakes_branch_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # branch_pattern bakes into the manager's compiled templates. Its single-brace
    # value ({type}/{slug}) is not a `{{…}}` placeholder and lands verbatim; absent,
    # it defaults to {type}/{slug}.
    monkeypatch.setattr("waypoint.cli._git_toplevel", lambda: str(tmp_path))
    raw_dir = tmp_path / "raw" / "manager"
    raw_dir.mkdir(parents=True)
    (raw_dir / "delegate.md").write_text(
        "branch: {{branch_pattern}}\n", encoding="utf-8"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"config": {}})

    _mock_cli(monkeypatch, handler)

    def _init(compiled: str, branch_pattern_line: str) -> str:
        manifest = tmp_path / f"{compiled}.yaml"
        manifest.write_text(
            "project: waypoint\n"
            "trunk: main\n"
            f"templates_dir: {compiled}\n"
            f"{branch_pattern_line}"
            "roles:\n"
            "  manager:\n"
            '    launch: { backend: claude_code, model: "opus[1m]", '
            "permission_mode: auto }\n"
            "    templates: raw/manager\n",
            encoding="utf-8",
        )
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
                "mgr",
            ],
        )
        assert result.exit_code == 0, result.stdout
        return (tmp_path / compiled / "manager" / "delegate.md").read_text(
            encoding="utf-8"
        )

    assert "branch: {type}/{slug}" in _init("default", "")
    assert "branch: {user}/{type}/{slug}" in _init(
        "custom", 'branch_pattern: "{user}/{type}/{slug}"\n'
    )


def _resolve(text: str, mode: str) -> str:
    return _resolve_conditionals(text, {"integration_mode": mode})


_COND_TEMPLATE = (
    "intro\n"
    "{{#if integration_mode == pr}}\n"
    "PR-ONLY\n"
    "```bash\n"
    "gh pr create\n"
    "```\n"
    "{{/if}}\n"
    "{{#if integration_mode == local}}\n"
    "LOCAL-ONLY\n"
    "```bash\n"
    "git merge --ff-only x\n"
    "```\n"
    "{{/if}}\n"
    "outro\n"
)


def test_resolve_conditionals_keeps_only_the_matching_block() -> None:
    pr = _resolve(_COND_TEMPLATE, "pr")
    assert "PR-ONLY" in pr and "gh pr create" in pr
    assert "LOCAL-ONLY" not in pr and "merge --ff-only" not in pr
    # No marker residue, and the kept fence stays intact and separated from prose.
    assert "{{#if" not in pr and "{{/if" not in pr
    assert "```\noutro\n" in pr

    local = _resolve(_COND_TEMPLATE, "local")
    assert "LOCAL-ONLY" in local and "merge --ff-only x" in local
    assert "PR-ONLY" not in local and "gh pr create" not in local
    assert "{{#if" not in local and "{{/if" not in local


def test_resolve_conditionals_rejects_malformed_markers() -> None:
    for bad in (
        "{{#if integration_mode == pr}}\nx\n",  # unclosed
        "{{/if}}\n",  # unmatched
        "a {{#if integration_mode == pr}}x{{/if}} b\n",  # marker not on its own line
        "{{#if unknown_key == pr}}\nx\n{{/if}}\n",  # unknown key
    ):
        with pytest.raises(typer.BadParameter):
            _resolve(bad, "pr")


def _init_with_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> str:
    raw_dir = tmp_path / "raw" / "tech-lead"
    raw_dir.mkdir(parents=True)
    (raw_dir / "brief.md").write_text(_COND_TEMPLATE, encoding="utf-8")
    compiled = tmp_path / "compiled"
    manifest = tmp_path / "waypoint-manager.yaml"
    manifest.write_text(
        "project: waypoint\n"
        "trunk: main\n"
        f"templates_dir: {compiled}\n"
        "board:\n"
        "  tickets_channel: tickets\n"
        "  ticket_channel_prefix: ticket-\n"
        "integration:\n"
        f"  mode: {mode}\n"
        "roles:\n"
        "  tech_lead:\n"
        '    launch: { backend: claude_code, model: "opus[1m]", '
        "permission_mode: auto }\n"
        f"    templates: {raw_dir}\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"config": {}})

    _mock_cli(monkeypatch, handler)
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
            "mgr",
        ],
    )
    if result.exit_code != 0:
        return f"__EXIT_{result.exit_code}__{result.stdout}"
    return (compiled / "tech_lead" / "brief.md").read_text(encoding="utf-8")


def test_cli_manager_init_compiles_pr_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _init_with_mode(tmp_path, monkeypatch, "pr")
    assert "PR-ONLY" in body and "gh pr create" in body
    assert "LOCAL-ONLY" not in body and "merge --ff-only" not in body
    assert "{{#if" not in body and "{{/if" not in body


def test_cli_manager_init_compiles_local_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _init_with_mode(tmp_path, monkeypatch, "local")
    assert "LOCAL-ONLY" in body and "merge --ff-only x" in body
    assert "PR-ONLY" not in body and "gh pr create" not in body
    assert "{{#if" not in body and "{{/if" not in body


def test_cli_manager_init_rejects_unknown_integration_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _init_with_mode(tmp_path, monkeypatch, "bogus")
    assert result.startswith("__EXIT_")
    assert "__EXIT_0__" not in result
