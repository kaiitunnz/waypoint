import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import click
import httpx
import pytest
import typer
from typer.testing import CliRunner

from waypoint.cli import (
    _backend_choices,
    _json_safe,
    _settings_from_arg,
    app,
    exit_code_for_wait,
    parse_wait_until,
)
from waypoint.client import WaypointClient
from waypoint.schemas import (
    EventKind,
    EventRecord,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.telemetry.ingest import TelemetryIngester

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The test host may itself run Waypoint, whose WAYPOINT_* env vars would
    # otherwise override the per-test config file (notably data_dir).
    for var in (
        "WAYPOINT_DATA_DIR",
        "WAYPOINT_CONFIG_PATH",
        "WAYPOINT_HOST",
        "WAYPOINT_PORT",
        "WAYPOINT_PASSWORD",
        "WAYPOINT_CORS_ORIGINS",
        "WAYPOINT_CORS_ORIGIN_REGEX",
    ):
        monkeypatch.delenv(var, raising=False)


def _config(tmp_path: Path) -> Path:
    cfg = tmp_path / "waypoint.yaml"
    cfg.write_text(
        f"default_backend: codex\ndata_dir: {tmp_path / 'data'}\n", encoding="utf-8"
    )
    return cfg


def test_help_lists_command_groups() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in (
        "serve",
        "doctor",
        "backends",
        "models",
        "reset",
        "session",
        "sessions",
        "board",
        "schedule",
    ):
        assert name in result.stdout


def test_board_help_lists_commands() -> None:
    result = runner.invoke(app, ["board", "--help"])
    assert result.exit_code == 0
    for name in (
        "post",
        "read",
        "channels",
        "clear",
        "delete",
        "delete-entry",
        "edit-entry",
        "set-meta",
    ):
        assert name in result.stdout


def test_backends_help_lists_threads() -> None:
    result = runner.invoke(app, ["backends", "--help"])
    assert result.exit_code == 0
    assert "threads" in result.stdout


def test_help_command_dumps_full_surface() -> None:
    # No server or token configured — recursive help is pure introspection.
    result = runner.invoke(app, ["help"])
    assert result.exit_code == 0
    for command in ("sessions start", "board set-meta", "usage"):
        assert command in result.stdout
    assert "--backend" in result.stdout


def test_help_json_is_structured() -> None:
    result = runner.invoke(app, ["help", "--json"])
    assert result.exit_code == 0
    commands = json.loads(result.stdout)
    by_path = {entry["command"]: entry for entry in commands}
    start = by_path["sessions start"]
    backend = next(o for o in start["options"] if "--backend" in o["flags"])
    # --backend is optional: a --preset (or the default preset) may supply it,
    # with required-field validation happening after preset resolution.
    assert backend["required"] is False
    assert backend["type"] == "text"
    assert any("--launch-env" in o["flags"] for o in start["options"])
    assert any("--preset" in o["flags"] for o in start["options"])
    schedule_create = by_path["schedule create"]
    assert any("--launch-env" in o["flags"] for o in schedule_create["options"])


def _walk_leaf_paths(group: click.Group, prefix: str) -> list[str]:
    paths: list[str] = []
    for name, cmd in group.commands.items():
        if cmd.hidden:
            continue
        path = f"{prefix} {name}".strip()
        if isinstance(getattr(cmd, "commands", None), dict):
            if getattr(cmd, "invoke_without_command", False):
                paths.append(path)
            paths.extend(_walk_leaf_paths(cast(click.Group, cmd), path))
        else:
            paths.append(path)
    return paths


def test_help_covers_every_leaf_command() -> None:
    # Regression guard: every leaf in the tree must appear in the dump so the
    # surface can't silently drift away from the generated help.
    result = runner.invoke(app, ["help", "--json"])
    assert result.exit_code == 0
    dumped = {entry["command"] for entry in json.loads(result.stdout)}
    root = cast(click.Group, typer.main.get_command(app))
    expected = set(_walk_leaf_paths(root, ""))
    assert expected <= dumped


def test_help_surfaces_runnable_groups() -> None:
    # `backends` runs without a subcommand (lists backend capabilities), so it
    # must appear as its own command, not only its `threads` child.
    result = runner.invoke(app, ["help", "--json"])
    assert result.exit_code == 0
    by_path = {entry["command"]: entry for entry in json.loads(result.stdout)}
    assert "backends" in by_path
    assert by_path["backends"]["help"]


def test_json_safe_stringifies_non_primitive_defaults() -> None:
    # Primitives pass through untouched so the JSON dump stays faithful.
    for value in (None, True, 3, 1.5, "x"):
        assert _json_safe(value) == value
    # Non-serializable defaults (enums, paths, frozensets) coerce to str so a
    # future option can never break `help --json`.
    assert _json_safe(frozenset({"a"})) == str(frozenset({"a"}))
    assert _json_safe(Path("/tmp/x")) == "/tmp/x"


def test_board_post_rejects_malformed_meta(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "board",
            "post",
            "topic:x",
            "hello",
            "--meta",
            "novalue",
        ],
    )
    assert result.exit_code != 0
    assert "key=value" in result.output


def test_board_edit_entry_rejects_malformed_meta(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "board",
            "edit-entry",
            "topic:x",
            "7",
            "new text",
            "--meta",
            "novalue",
        ],
    )
    assert result.exit_code != 0
    assert "key=value" in result.output


def test_backend_choices_excludes_only_the_fallback_wrapper() -> None:
    """Launch ``--backend`` lists agents plus claude_tty; only the tmux
    managed-launch fallback wrapper is excluded (it is routed to via the
    registry, never selected directly)."""
    choices = set(_backend_choices())
    assert {"claude_code", "codex", "opencode", "claude_tty"}.issubset(choices)
    assert "tmux" not in choices


def test_session_start_rejects_fallback_wrapper_as_backend(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "session",
            "start",
            "--backend",
            "tmux",
            "--cwd",
            "/tmp",
        ],
    )
    assert result.exit_code != 0
    assert "unknown backend" in result.output


def test_models_rejects_unknown_backend(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--config", str(_config(tmp_path)), "models", "bogus"])
    assert result.exit_code != 0
    assert "unknown backend" in result.output


def test_models_sweep_skips_fallback_and_isolates_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/backends":
            return httpx.Response(
                200,
                json={
                    "backends": [
                        {
                            "id": "claude_code",
                            "capabilities": {"is_fallback_for_managed_launch": False},
                        },
                        {
                            "id": "codex",
                            "capabilities": {"is_fallback_for_managed_launch": False},
                        },
                        {
                            "id": "tmux",
                            "capabilities": {"is_fallback_for_managed_launch": True},
                        },
                    ]
                },
            )
        if path.endswith("/models"):
            backend = path[len("/api/backends/") : -len("/models")]
            requested.append(backend)
            if backend == "codex":
                return httpx.Response(502, json={"detail": "codex discovery failed"})
            return httpx.Response(
                200, json={"backend": backend, "models": [{"id": "x"}]}
            )
        return httpx.Response(404, json={"detail": f"unexpected {path}"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(app, ["--config", str(_config(tmp_path)), "models"])
    assert result.exit_code == 0
    by_id = {entry["backend"]: entry for entry in json.loads(result.stdout)["backends"]}
    # The fallback backend is filtered out before any model request.
    assert set(by_id) == {"claude_code", "codex"}
    assert "tmux" not in requested
    # A live-discovery failure becomes an error entry, not a failed sweep.
    assert by_id["claude_code"]["models"] == [{"id": "x"}]
    assert "error" in by_id["codex"]


def test_backends_threads_emits_threads_and_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/backends/claude_code/threads":
            state["threads_params"] = dict(request.url.params)
            return httpx.Response(
                200,
                json={
                    "threads": [
                        {"id": "thread-1", "title": "Alpha", "cwd": "/tmp/repo"}
                    ]
                },
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "backends",
            "threads",
            "claude_code",
            "--launch-target-id",
            "ssh-box",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "threads": [{"id": "thread-1", "title": "Alpha", "cwd": "/tmp/repo"}]
    }
    assert state["threads_params"] == {"launch_target_id": "ssh-box"}


def test_backends_threads_surfaces_server_capability_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/backends/tmux/threads":
            return httpx.Response(
                400,
                json={"detail": "thread discovery is not supported for tmux"},
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "backends", "threads", "tmux"],
    )
    assert result.exit_code != 0
    assert "thread discovery is not supported for tmux" in result.output


def test_sessions_list_spawned_by_passes_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "GET":
            state["params"] = dict(request.url.params)
            return httpx.Response(
                200, json={"sessions": [{"id": "child-1", "spawner_session_id": "p1"}]}
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "list", "--spawned-by", "p1"],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["sessions"] == [
        {"id": "child-1", "spawner_session_id": "p1"}
    ]
    assert state["params"] == {"spawned_by": "p1"}


def test_sessions_list_mine_reads_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_SESSION_ID", "my-session")
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "GET":
            state["params"] = dict(request.url.params)
            return httpx.Response(200, json={"sessions": []})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "list", "--mine"],
    )
    assert result.exit_code == 0
    assert state["params"] == {"spawned_by": "my-session"}


def test_sessions_list_mine_errors_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WAYPOINT_SESSION_ID", raising=False)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "list", "--mine"],
    )
    assert result.exit_code != 0
    assert "WAYPOINT_SESSION_ID" in result.output


def test_sessions_list_recursive_passes_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "GET":
            state["params"] = dict(request.url.params)
            return httpx.Response(200, json={"sessions": []})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "list",
            "--spawned-by",
            "p1",
            "--recursive",
        ],
    )
    assert result.exit_code == 0, result.output
    assert state["params"] == {"spawned_by": "p1", "recursive": "true"}


def test_sessions_list_recursive_requires_scope(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "list", "--recursive"],
    )
    assert result.exit_code != 0
    assert "recursive" in result.output.lower()


def test_sessions_list_idle_for_filters_client_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "sessions": [
                        {"id": "fresh", "last_event_at": now.isoformat()},
                        {
                            "id": "stale",
                            "last_event_at": (now - timedelta(hours=2)).isoformat(),
                        },
                    ]
                },
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "list", "--idle-for", "1h"],
    )
    assert result.exit_code == 0, result.output
    ids = [s["id"] for s in json.loads(result.stdout)["sessions"]]
    assert ids == ["stale"]


def test_sessions_tree_renders_nested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "sessions": [
                        {"id": "root", "title": "R", "status": "idle"},
                        {"id": "a", "spawner_session_id": "root"},
                        {"id": "b", "spawner_session_id": "a"},
                    ]
                },
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "tree", "root"],
    )
    assert result.exit_code == 0, result.output
    tree = json.loads(result.stdout)["tree"]
    assert tree["id"] == "root"
    assert tree["children"][0]["id"] == "a"
    assert tree["children"][0]["children"][0]["id"] == "b"


def test_sessions_tree_unknown_id_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "GET":
            return httpx.Response(200, json={"sessions": []})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "tree", "ghost"],
    )
    assert result.exit_code != 0
    assert "ghost" in result.output


def test_sessions_reap_deletes_matched_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "sessions": [
                        {"id": "c1", "spawner_session_id": "p1"},
                        {"id": "c2", "spawner_session_id": "p1"},
                    ]
                },
            )
        if request.url.path.startswith("/api/sessions/") and request.method == "DELETE":
            sid = request.url.path.rsplit("/", 1)[-1]
            deleted.append(sid)
            return httpx.Response(200, json={"deleted": sid})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "reap", "--spawned-by", "p1"],
    )
    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    assert set(summary["reaped"]) == {"c1", "c2"}
    assert summary["failed"] == []
    assert set(deleted) == {"c1", "c2"}


def test_sessions_reap_reports_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "GET":
            return httpx.Response(200, json={"sessions": [{"id": "c1"}, {"id": "c2"}]})
        if request.url.path == "/api/sessions/c1" and request.method == "DELETE":
            return httpx.Response(200, json={"deleted": "c1"})
        if request.url.path == "/api/sessions/c2" and request.method == "DELETE":
            return httpx.Response(500, json={"detail": "internal error"})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "reap", "--all"],
    )
    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    assert summary["reaped"] == ["c1"]
    assert summary["failed"] == ["c2"]


def test_sessions_reap_requires_scope(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "reap"],
    )
    assert result.exit_code != 0
    assert "--all" in result.output or "scope" in result.output


def test_sessions_launch_tag_sends_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "POST":
            state["create"] = json.loads(request.content)
            return httpx.Response(200, json={"session": {"id": "new"}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "start",
            "--backend",
            "codex",
            "--cwd",
            "/tmp",
            "--tag",
            "role=backend-lead",
            "--tag",
            "overflow",
        ],
    )
    assert result.exit_code == 0, result.output
    assert state["create"]["tags"] == {"role": "backend-lead", "overflow": ""}


def test_sessions_tag_command_sends_set_and_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/tags" and request.method == "PATCH":
            state["body"] = json.loads(request.content)
            return httpx.Response(200, json={"session": {"id": "s1", "tags": {}}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "tag",
            "s1",
            "--set",
            "role=qa",
            "--unset",
            "old",
        ],
    )
    assert result.exit_code == 0, result.output
    assert state["body"] == {"set": {"role": "qa"}, "unset": ["old"]}


def test_sessions_tag_command_requires_change(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "tag", "s1"],
    )
    assert result.exit_code != 0


def test_sessions_list_tag_passes_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "GET":
            state["params"] = list(request.url.params.multi_items())
            return httpx.Response(200, json={"sessions": []})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "list",
            "--tag",
            "role=qa",
            "--tag",
            "overflow",
        ],
    )
    assert result.exit_code == 0, result.output
    assert state["params"] == [("tag", "role=qa"), ("tag", "overflow")]


def test_sessions_reap_tag_and_exclude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deleted: list[str] = []
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "GET":
            state["params"] = list(request.url.params.multi_items())
            return httpx.Response(
                200, json={"sessions": [{"id": "keep"}, {"id": "drop"}]}
            )
        if request.url.path.startswith("/api/sessions/") and request.method == "DELETE":
            sid = request.url.path.rsplit("/", 1)[-1]
            deleted.append(sid)
            return httpx.Response(200, json={"deleted": sid})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "reap",
            "--tag",
            "overflow",
            "--exclude",
            "keep",
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["reaped"] == ["drop"]
    assert summary["skipped"] == ["keep"]
    assert deleted == ["drop"]
    assert ("tag", "overflow") in state["params"]


def test_sessions_import_reads_json_file_and_emits_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload_path = tmp_path / "thread.json"
    payload_path.write_text(
        '{"thread_id": "thread-1", "cwd": "/tmp/repo"}', encoding="utf-8"
    )
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/backends/claude_code/sessions/import":
            state["import_body"] = json.loads(request.content)
            return httpx.Response(200, json={"session": {"id": "imported-1"}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "import",
            "claude_code",
            "--json",
            str(payload_path),
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"session": {"id": "imported-1"}}
    assert state["import_body"] == {"thread_id": "thread-1", "cwd": "/tmp/repo"}


def test_sessions_import_launch_env_overrides_json_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload_path = tmp_path / "thread.json"
    payload_path.write_text(
        json.dumps(
            {
                "thread_id": "thread-1",
                "launch_env": {"FROM_JSON": "old"},
            }
        ),
        encoding="utf-8",
    )
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/backends/claude_code/sessions/import":
            state["import_body"] = json.loads(request.content)
            return httpx.Response(200, json={"session": {"id": "imported-env"}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "import",
            "claude_code",
            "--json",
            str(payload_path),
            "--launch-env",
            "FROM_FLAG=new",
            "--launch-env",
            "HAS_EQUALS=a=b",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"session": {"id": "imported-env"}}
    assert state["import_body"] == {
        "thread_id": "thread-1",
        "launch_env": {"FROM_FLAG": "new", "HAS_EQUALS": "a=b"},
    }


def test_sessions_import_reads_stdin_when_dash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/backends/claude_code/sessions/import":
            state["import_body"] = json.loads(request.content)
            return httpx.Response(200, json={"session": {"id": "imported-stdin"}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "import",
            "claude_code",
            "--json",
            "-",
        ],
        input='{"thread_id": "stdin-1"}',
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"session": {"id": "imported-stdin"}}
    assert state["import_body"] == {"thread_id": "stdin-1"}


def test_sessions_import_rejects_malformed_json(tmp_path: Path) -> None:
    payload_path = tmp_path / "thread.json"
    payload_path.write_text("not json", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "import",
            "claude_code",
            "--json",
            str(payload_path),
        ],
    )
    assert result.exit_code != 0
    assert "not valid JSON" in result.output


def test_sessions_import_rejects_non_object_json(tmp_path: Path) -> None:
    payload_path = tmp_path / "thread.json"
    payload_path.write_text('["thread-1"]', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "import",
            "claude_code",
            "--json",
            str(payload_path),
        ],
    )
    assert result.exit_code != 0
    assert "JSON object" in result.output


def test_sessions_output_filters_events_and_passes_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/events":
            state["events_params"] = dict(request.url.params)
            return httpx.Response(
                200,
                json={
                    "events": [
                        {"kind": "status_update", "text": "busy", "sequence": 1},
                        {"kind": "user_input", "text": "question", "sequence": 2},
                        {"kind": "tool_call", "text": "ignored", "sequence": 3},
                        {
                            "kind": "agent_output",
                            "text": "answ",
                            "sequence": 4,
                            "metadata": {"item_id": "1"},
                        },
                        {
                            "kind": "agent_output",
                            "text": "er",
                            "sequence": 5,
                            "metadata": {"item_id": "1"},
                        },
                        {"kind": "system_note", "text": "ignored", "sequence": 6},
                    ],
                    "has_more": True,
                },
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "output",
            "s1",
            "--messages",
            "7",
        ],
    )
    assert result.exit_code == 0

    def dict_without_none(d):
        return {k: v for k, v in d.items() if v is not None}

    actual = json.loads(result.stdout)
    actual["events"] = [dict_without_none(e) for e in actual["events"]]

    assert actual == {
        "events": [
            {"kind": "user_input", "text": "question", "sequence": 2},
            {
                "kind": "agent_output",
                "text": "answer",
                "sequence": 5,
                "metadata": {"item_id": "1"},
            },
        ]
    }
    assert state["events_params"] == {"messages": "7"}


def test_sessions_output_text_prints_only_agent_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/events":
            return httpx.Response(
                200,
                json={
                    "events": [
                        {"kind": "user_input", "text": "question", "sequence": 1},
                        {
                            "kind": "agent_output",
                            "text": "hello",
                            "sequence": 2,
                            "metadata": {"item_id": "1"},
                        },
                        {"kind": "tool_result", "text": "ignored", "sequence": 3},
                        {
                            "kind": "agent_output",
                            "text": " world",
                            "sequence": 4,
                            "metadata": {"item_id": "2"},
                        },
                    ],
                    "has_more": False,
                },
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "output",
            "s1",
            "--text",
        ],
    )
    assert result.exit_code == 0
    assert result.stdout == "hello\n\n world"


def test_sessions_output_compact_returns_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/events":
            return httpx.Response(
                200,
                json={
                    "events": [
                        {"kind": "status_update", "text": "busy", "sequence": 1},
                        {"kind": "user_input", "text": "question", "sequence": 2},
                        {
                            "kind": "agent_output",
                            "text": "answ",
                            "sequence": 3,
                            "metadata": {
                                "item_id": "msg-1",
                                "payload": {"verbose": True},
                            },
                        },
                        {
                            "kind": "agent_output",
                            "text": "er",
                            "sequence": 4,
                            "metadata": {
                                "item_id": "msg-1",
                                "payload": {"verbose": True},
                            },
                        },
                        {"kind": "tool_result", "text": "ignored", "sequence": 5},
                    ],
                    "has_more": True,
                },
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "output",
            "s1",
            "--compact",
        ],
    )
    assert result.exit_code == 0

    assert json.loads(result.stdout) == {
        "messages": [
            {"seq": 2, "role": "user", "text": "question"},
            {
                "seq": 4,
                "role": "assistant",
                "text": "answer",
                "item_id": "msg-1",
            },
        ],
        "has_more": True,
    }


def test_sessions_output_compact_rejects_text(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "output",
            "s1",
            "--compact",
            "--text",
        ],
    )

    assert result.exit_code == 1
    assert "--compact cannot be combined with --text" in result.output


def test_sessions_output_compact_rejects_raw(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "output",
            "s1",
            "--compact",
            "--raw",
        ],
    )

    assert result.exit_code == 1
    assert "--compact cannot be combined with --raw" in result.output


def test_answer_question_rejects_malformed_answers_json(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "answer-question",
            "s1",
            "--answer",
            "ok",
            "--answers-json",
            "not json",
        ],
    )
    assert result.exit_code != 0
    assert "not valid JSON" in result.output


def test_answer_question_rejects_non_array_answers_json(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "answer-question",
            "s1",
            "--answer",
            "ok",
            "--answers-json",
            '{"question": "q"}',
        ],
    )
    assert result.exit_code != 0
    assert "array of objects" in result.output


def test_config_is_a_top_level_option(tmp_path: Path) -> None:
    # `--config` lives on the root app, before the command.
    result = runner.invoke(app, ["--config", str(_config(tmp_path)), "reset"])
    assert result.exit_code == 0
    assert "Nothing to reset" in result.stdout


def test_reset_requires_yes_to_delete(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "waypoint.db").write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["--config", str(cfg), "reset"])
    assert result.exit_code == 0
    assert "Would remove" in result.stdout
    assert (tmp_path / "data" / "waypoint.db").exists()


def test_unknown_backend_is_rejected(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "start",
            "--backend",
            "bogus",
            "--cwd",
            "/tmp",
        ],
    )
    assert result.exit_code != 0
    assert "unknown backend" in result.output


def test_doctor_reports_config_path(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--config", str(_config(tmp_path)), "doctor"])
    assert result.exit_code == 0
    assert "config_path" in result.stdout


def test_schedule_help_lists_commands() -> None:
    result = runner.invoke(app, ["schedule", "--help"])
    assert result.exit_code == 0
    for name in ("list", "create", "delete", "clear-history"):
        assert name in result.stdout


def test_schedule_create_rejects_unknown_backend(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "schedule",
            "create",
            "--backend",
            "bogus",
            "--cwd",
            "/tmp",
        ],
    )
    assert result.exit_code != 0
    assert "unknown backend" in result.output


def test_schedule_create_emits_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/schedules" and request.method == "POST":
            payload = json.loads(request.content)
            state["payload"] = payload
            return httpx.Response(200, json={"schedule": {"id": "sc1", **payload}})
        return httpx.Response(404, json={"detail": "unexpected"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "schedule",
            "create",
            "--backend",
            "claude_code",
            "--cwd",
            "/tmp",
            "--prompt",
            "hello",
            "--launch-env",
            "FOO=bar",
            "--launch-env",
            "HAS_EQUALS=a=b",
            "--delay-seconds",
            "60",
        ],
    )
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["schedule"]["id"] == "sc1"
    assert out["schedule"]["initial_prompt"] == "hello"
    assert out["schedule"]["delay_seconds"] == 60
    payload = state["payload"]
    assert isinstance(payload, dict)
    assert payload["launch_env"] == {"FOO": "bar", "HAS_EQUALS": "a=b"}


def test_parse_wait_until_defaults_and_validates() -> None:
    assert parse_wait_until(None) == frozenset(
        {"idle", "waiting_input", "exited", "error"}
    )
    assert parse_wait_until("exited, error") == frozenset({"exited", "error"})
    with pytest.raises(typer.BadParameter):
        parse_wait_until("bogus,idle")
    with pytest.raises(typer.BadParameter):
        parse_wait_until(",")


def test_exit_code_for_wait_maps_terminal_status() -> None:
    assert exit_code_for_wait(None) == 124  # timeout
    assert exit_code_for_wait("error") == 1
    assert exit_code_for_wait("idle") == 0
    assert exit_code_for_wait("waiting_input") == 0
    assert exit_code_for_wait("exited") == 0


def _session_state(status: str) -> dict[str, Any]:
    return {
        "type": "session_state",
        "payload": {"session": {"id": "s1", "status": status}},
    }


def _patch_stream(
    monkeypatch: pytest.MonkeyPatch, envelopes: list[dict[str, Any]]
) -> None:
    async def stream(self: WaypointClient, session_id: str) -> AsyncIterator[dict]:
        for envelope in envelopes:
            yield envelope

    monkeypatch.setattr(WaypointClient, "stream_session_envelopes", stream)


def test_wait_emits_final_session_and_exits_zero_on_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")
    _patch_stream(monkeypatch, [_session_state("running"), _session_state("idle")])
    result = runner.invoke(
        app, ["--config", str(_config(tmp_path)), "sessions", "wait", "s1"]
    )
    assert result.exit_code == 0
    # Nothing intermediate: a single final session blob.
    assert json.loads(result.stdout)["session"]["status"] == "idle"


def test_wait_exits_one_on_error_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")
    _patch_stream(monkeypatch, [_session_state("error")])
    result = runner.invoke(
        app, ["--config", str(_config(tmp_path)), "sessions", "wait", "s1"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["session"]["status"] == "error"


def test_wait_falls_back_to_polling_when_ws_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")

    async def failing_stream(
        self: WaypointClient, session_id: str
    ) -> AsyncIterator[dict]:
        raise OSError("connection refused")
        yield {}  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(WaypointClient, "stream_session_envelopes", failing_stream)
    monkeypatch.setattr(
        WaypointClient,
        "get_session",
        lambda self, session_id: {"id": session_id, "status": "exited"},
    )
    result = runner.invoke(
        app, ["--config", str(_config(tmp_path)), "sessions", "wait", "s1"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["session"]["status"] == "exited"


def test_wait_times_out_with_code_124(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")
    # A stream that never reaches the until-set; the timeout must win.
    _patch_stream(monkeypatch, [_session_state("running")])
    monkeypatch.setattr(
        WaypointClient,
        "get_session",
        lambda self, session_id: {"id": session_id, "status": "running"},
    )
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "wait",
            "s1",
            "--timeout",
            "0.05",
        ],
    )
    assert result.exit_code == 124
    assert json.loads(result.stdout)["session"]["status"] == "running"


def test_events_follow_streams_ndjson_until_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")
    _patch_stream(
        monkeypatch,
        [
            {"type": "event", "payload": {"event": {"sequence": 1}}},
            _session_state("running"),
            {"type": "event", "payload": {"event": {"sequence": 2}}},
            _session_state("exited"),
            {"type": "event", "payload": {"event": {"sequence": 3}}},
        ],
    )
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "events", "s1", "--follow"],
    )
    assert result.exit_code == 0
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    # Only event envelopes are emitted, one compact JSON object per line, and
    # the stream stops at the terminal `exited` state (event 3 never prints).
    assert len(lines) == 2
    assert all(
        line == json.dumps(json.loads(line), separators=(",", ":")) for line in lines
    )
    assert [json.loads(line)["payload"]["event"]["sequence"] for line in lines] == [
        1,
        2,
    ]


def _board_set_meta_handler(
    request: httpx.Request, state: dict[str, Any]
) -> httpx.Response:
    path = request.url.path
    if path == "/api/board/job:x" and request.method == "GET":
        key_filter = request.url.params.get("key")
        entries = [
            {"id": 42, "channel": "job:x", "key": "task:1", "text": "original text"}
        ]
        if key_filter:
            entries = [e for e in entries if e.get("key") == key_filter]
        return httpx.Response(200, json={"channel": "job:x", "entries": entries})
    if path == "/api/board/job:x/entries/42" and request.method == "PATCH":
        payload = json.loads(request.content)
        state["patch_body"] = payload
        return httpx.Response(
            200,
            json={
                "entry": {
                    "id": 42,
                    "channel": "job:x",
                    "text": "original text",
                    **payload,
                }
            },
        )
    return httpx.Response(404, json={"detail": f"unexpected {path}"})


def test_board_set_meta_by_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state: dict[str, Any] = {}

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(
            transport=httpx.MockTransport(lambda r: _board_set_meta_handler(r, state)),
            base_url="http://t",
        )
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "board",
            "set-meta",
            "job:x",
            "--key",
            "task:1",
            "--meta",
            "status=done",
        ],
    )
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    # Text is preserved; only metadata changed.
    assert out["entry"]["text"] == "original text"
    assert out["entry"]["metadata"] == {"status": "done"}
    assert "text" not in state["patch_body"]


def test_board_set_meta_by_entry_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, Any] = {}

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(
            transport=httpx.MockTransport(lambda r: _board_set_meta_handler(r, state)),
            base_url="http://t",
        )
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "board",
            "set-meta",
            "job:x",
            "--entry-id",
            "42",
            "--meta",
            "status=blocked",
        ],
    )
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["entry"]["text"] == "original text"
    assert out["entry"]["metadata"] == {"status": "blocked"}
    assert "text" not in state["patch_body"]


def test_sessions_start_worktree_creates_worktree_and_sets_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    root = str(tmp_path / "myrepo")
    worktree_dir = str(tmp_path / "myrepo-feat-foo")

    import subprocess

    def fake_run(
        cmd: list[str], *, check: bool = False, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=root + "\n", stderr="")
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="main\n", stderr="")
        if cmd[:3] == ["git", "worktree", "add"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr("waypoint.cli.subprocess.run", fake_run)

    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "POST":
            body = json.loads(request.content)
            state["create_body"] = body
            return httpx.Response(200, json={"session": {"id": "new", **body}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "start",
            "--backend",
            "codex",
            "--cwd",
            root,
            "--worktree",
            "feat/foo",
        ],
    )
    assert result.exit_code == 0, result.output
    wt_call = next(c for c in calls if c[:3] == ["git", "worktree", "add"])
    assert wt_call[3] == worktree_dir
    assert wt_call[4:6] == ["-b", "feat/foo"]
    body = state["create_body"]
    assert isinstance(body, dict)
    assert body["worktree_path"] == worktree_dir
    assert body["cwd"] == worktree_dir


def test_sessions_start_launch_env_sends_request_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "POST":
            body = json.loads(request.content)
            state["create_body"] = body
            return httpx.Response(200, json={"session": {"id": "new", **body}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "start",
            "--backend",
            "codex",
            "--cwd",
            "/tmp/repo",
            "--launch-env",
            "FOO=bar",
            "--launch-env",
            "HAS_EQUALS=a=b",
        ],
    )
    assert result.exit_code == 0, result.output
    body = state["create_body"]
    assert isinstance(body, dict)
    assert body["launch_env"] == {"FOO": "bar", "HAS_EQUALS": "a=b"}


def test_sessions_start_rejects_malformed_launch_env(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "start",
            "--backend",
            "codex",
            "--cwd",
            "/tmp/repo",
            "--launch-env",
            "NOT_KEY_VALUE",
        ],
    )
    assert result.exit_code == 2


def test_sessions_start_worktree_git_failure_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    def fake_run(
        cmd: list[str], *, check: bool = False, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            raise subprocess.CalledProcessError(128, cmd, stderr="not a git repo")
        raise AssertionError(f"unexpected: {cmd}")

    monkeypatch.setattr("waypoint.cli.subprocess.run", fake_run)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "start",
            "--backend",
            "codex",
            "--cwd",
            "/tmp",
            "--worktree",
            "feat/bar",
        ],
    )
    assert result.exit_code == 1


# ── multi-id sessions wait ────────────────────────────────────────────────────


def _session_state_for(session_id: str, status: str) -> dict[str, Any]:
    """Session-state envelope with the given id (unlike _session_state which hardcodes s1)."""
    return {
        "type": "session_state",
        "payload": {"session": {"id": session_id, "status": status}},
    }


def test_wait_multi_all_emits_sessions_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")

    streams: dict[str, list[dict[str, Any]]] = {
        "s1": [_session_state_for("s1", "running"), _session_state_for("s1", "idle")],
        "s2": [_session_state_for("s2", "idle")],
    }

    async def stream(self: WaypointClient, session_id: str) -> AsyncIterator[dict]:
        for envelope in streams[session_id]:
            yield envelope

    monkeypatch.setattr(WaypointClient, "stream_session_envelopes", stream)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "wait", "s1", "s2"],
    )
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert "sessions" in out
    statuses = {s["id"]: s["status"] for s in out["sessions"]}
    assert statuses["s1"] == "idle"
    assert statuses["s2"] == "idle"


def test_wait_multi_any_returns_first_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")

    # s2 reaches idle immediately; s1 would block indefinitely without cancel.
    import asyncio

    async def stream(self: WaypointClient, session_id: str) -> AsyncIterator[dict]:
        if session_id == "s2":
            yield _session_state("idle")
        else:
            # Never yields a terminal status — would block without --any.
            await asyncio.sleep(60)
            yield _session_state("idle")

    monkeypatch.setattr(WaypointClient, "stream_session_envelopes", stream)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "wait",
            "s1",
            "s2",
            "--any",
        ],
    )
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    # --any returns a single-element sessions list (the first to finish).
    assert "sessions" in out
    assert len(out["sessions"]) == 1
    assert out["sessions"][0]["status"] == "idle"


def test_wait_multi_exits_one_when_any_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")

    streams: dict[str, list[dict[str, Any]]] = {
        "s1": [_session_state_for("s1", "idle")],
        "s2": [_session_state_for("s2", "error")],
    }

    async def stream(self: WaypointClient, session_id: str) -> AsyncIterator[dict]:
        for envelope in streams[session_id]:
            yield envelope

    monkeypatch.setattr(WaypointClient, "stream_session_envelopes", stream)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "wait", "s1", "s2"],
    )
    assert result.exit_code == 1


def test_wait_multi_exits_124_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")

    _patch_stream(monkeypatch, [_session_state("running")])
    monkeypatch.setattr(
        WaypointClient,
        "get_session",
        lambda self, session_id: {"id": session_id, "status": "running"},
    )
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "wait",
            "s1",
            "s2",
            "--timeout",
            "0.05",
        ],
    )
    assert result.exit_code == 124


# ── fleet events --follow ─────────────────────────────────────────────────────


def test_events_follow_single_session_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-id follow still works without a session_id prefix in output."""
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")
    _patch_stream(
        monkeypatch,
        [
            {
                "type": "event",
                "payload": {"event": {"sequence": 1, "kind": "agent_output"}},
            },
            _session_state("exited"),
        ],
    )
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "events", "s1", "--follow"],
    )
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    # No session_id prefix for single-session follow.
    envelope = json.loads(lines[0])
    assert "session_id" not in envelope
    assert envelope["type"] == "event"


def test_events_follow_multi_prefixes_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")

    streams: dict[str, list[dict[str, Any]]] = {
        "s1": [
            {
                "type": "event",
                "payload": {"event": {"sequence": 1, "kind": "agent_output"}},
            },
            _session_state_for("s1", "exited"),
        ],
        "s2": [
            {
                "type": "event",
                "payload": {"event": {"sequence": 2, "kind": "user_input"}},
            },
            _session_state_for("s2", "exited"),
        ],
    }

    async def stream(self: WaypointClient, session_id: str) -> AsyncIterator[dict]:
        for envelope in streams[session_id]:
            yield envelope

    monkeypatch.setattr(WaypointClient, "stream_session_envelopes", stream)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "events",
            "s1",
            "s2",
            "--follow",
        ],
    )
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2
    session_ids_seen = {json.loads(ln)["session_id"] for ln in lines}
    assert session_ids_seen == {"s1", "s2"}


def test_events_follow_filter_type_excludes_non_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")
    _patch_stream(
        monkeypatch,
        [
            {
                "type": "event",
                "payload": {"event": {"sequence": 1, "kind": "agent_output"}},
            },
            {
                "type": "event",
                "payload": {"event": {"sequence": 2, "kind": "approval_request"}},
            },
            _session_state("exited"),
        ],
    )
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "events",
            "s1",
            "--follow",
            "--filter",
            "approval_request",
        ],
    )
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["payload"]["event"]["kind"] == "approval_request"


def test_events_follow_spawned_by_resolves_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")

    all_sessions = [
        {"id": "child1", "spawner_session_id": "parent"},
        {"id": "child2", "spawner_session_id": "parent"},
        {"id": "other", "spawner_session_id": "other-parent"},
    ]
    seen_ids: list[str] = []

    async def stream(self: WaypointClient, session_id: str) -> AsyncIterator[dict]:
        seen_ids.append(session_id)
        yield _session_state("exited")

    def fake_list_sessions(self: WaypointClient) -> list[dict]:
        return all_sessions

    monkeypatch.setattr(WaypointClient, "stream_session_envelopes", stream)
    monkeypatch.setattr(WaypointClient, "list_sessions", fake_list_sessions)

    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "events",
            "--follow",
            "--spawned-by",
            "parent",
        ],
    )
    assert result.exit_code == 0
    assert set(seen_ids) == {"child1", "child2"}


def test_events_follow_mine_resolves_spawned_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")
    monkeypatch.setenv("WAYPOINT_SESSION_ID", "me")

    all_sessions = [
        {"id": "my-child", "spawner_session_id": "me"},
        {"id": "other", "spawner_session_id": "someone-else"},
    ]
    seen_ids: list[str] = []

    async def stream(self: WaypointClient, session_id: str) -> AsyncIterator[dict]:
        seen_ids.append(session_id)
        yield _session_state("exited")

    def fake_list_sessions(self: WaypointClient) -> list[dict]:
        return all_sessions

    monkeypatch.setattr(WaypointClient, "stream_session_envelopes", stream)
    monkeypatch.setattr(WaypointClient, "list_sessions", fake_list_sessions)

    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "events",
            "--follow",
            "--mine",
        ],
    )
    assert result.exit_code == 0
    assert seen_ids == ["my-child"]


# ── sessions send timeout semantics ──────────────────────────────────────────


def test_sessions_send_reports_delivered_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/input":
            raise httpx.ReadTimeout("timeout", request=request)
        if request.url.path == "/api/sessions/s1":
            return httpx.Response(
                200, json={"session": {"id": "s1", "status": "running"}}
            )
        return httpx.Response(404, json={"detail": "unexpected"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "send", "s1", "hi"],
    )
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["session"]["send"] == "delivered"


def test_sessions_send_reports_unknown_on_timeout_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/input":
            raise httpx.ReadTimeout("timeout", request=request)
        if request.url.path == "/api/sessions/s1":
            return httpx.Response(200, json={"session": {"id": "s1", "status": "idle"}})
        return httpx.Response(404, json={"detail": "unexpected"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "send", "s1", "hi"],
    )
    # unknown send → exits 1 to signal uncertainty
    assert result.exit_code == 1
    out = json.loads(result.stdout)
    assert out["session"]["send"] == "unknown"


# ── sessions upload / sessions send --attachment-id ───────────────────────────


def test_sessions_upload_emits_attachment_specs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")
    f1 = tmp_path / "img.png"
    f2 = tmp_path / "doc.txt"
    f1.write_bytes(b"\x89PNG")
    f2.write_text("hello")
    uploads: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.url.path == "/api/sessions/s1/attachments"
            and request.method == "POST"
        ):
            n = len(uploads) + 1
            att_id = f"att{n:032x}"
            uploads.append(att_id)
            return httpx.Response(
                200,
                json={
                    "id": att_id,
                    "filename": f"file{n}",
                    "mime": "application/octet-stream",
                    "size": 4,
                    "kind": "file",
                },
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "upload",
            "s1",
            str(f1),
            str(f2),
        ],
    )
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert "attachments" in out
    assert len(out["attachments"]) == 2
    assert [a["id"] for a in out["attachments"]] == uploads


def test_sessions_upload_pin_sends_form_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")
    f = tmp_path / "a.txt"
    f.write_text("x")
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.url.path == "/api/sessions/s1/attachments"
            and request.method == "POST"
        ):
            state["pinned"] = b'name="pin"' in request.content
            return httpx.Response(
                200,
                json={
                    "id": "att" + "0" * 29,
                    "filename": "a.txt",
                    "mime": "text/plain",
                    "size": 1,
                    "kind": "file",
                },
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "upload",
            "s1",
            str(f),
            "--pin",
        ],
    )
    assert result.exit_code == 0, result.output
    assert state["pinned"] is True


def test_sessions_attachments_list_get_delete_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/sessions/s1/attachments" and request.method == "GET":
            return httpx.Response(200, json=[{"id": "a" * 32, "filename": "shot.png"}])
        if path.startswith("/api/sessions/s1/attachments/"):
            tail = path[len("/api/sessions/s1/attachments/") :]
            if tail.endswith("/pin"):
                state["pin"] = (request.method, tail[: -len("/pin")])
                return httpx.Response(204)
            if request.method == "GET":
                return httpx.Response(
                    200,
                    content=b"blob-bytes",
                    headers={"content-disposition": 'inline; filename="shot.png"'},
                )
            if request.method == "DELETE":
                state["deleted"] = tail
                return httpx.Response(204)
        return httpx.Response(404, json={"detail": f"unexpected {path}"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    cfg = str(_config(tmp_path))

    listed = runner.invoke(
        app, ["--config", cfg, "sessions", "attachments", "list", "s1"]
    )
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)["attachments"][0]["filename"] == "shot.png"

    out_path = tmp_path / "downloaded.png"
    got = runner.invoke(
        app,
        [
            "--config",
            cfg,
            "sessions",
            "attachments",
            "get",
            "s1",
            "a" * 32,
            "--out",
            str(out_path),
        ],
    )
    assert got.exit_code == 0, got.output
    assert out_path.read_bytes() == b"blob-bytes"

    deleted = runner.invoke(
        app, ["--config", cfg, "sessions", "attachments", "delete", "s1", "b" * 32]
    )
    assert deleted.exit_code == 0, deleted.output
    assert state["deleted"] == "b" * 32

    pinned = runner.invoke(
        app, ["--config", cfg, "sessions", "attachments", "pin", "s1", "c" * 32]
    )
    assert pinned.exit_code == 0, pinned.output
    assert state["pin"] == ("POST", "c" * 32)

    unpinned = runner.invoke(
        app, ["--config", cfg, "sessions", "attachments", "unpin", "s1", "c" * 32]
    )
    assert unpinned.exit_code == 0, unpinned.output
    assert state["pin"] == ("DELETE", "c" * 32)


def test_sessions_attachments_get_out_directory_uses_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"blob",
            headers={"content-disposition": 'inline; filename="shot.png"'},
        )

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    out_dir = tmp_path / "downloads"
    out_dir.mkdir()
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "attachments",
            "get",
            "s1",
            "a" * 32,
            "--out",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    # A directory --out lands the blob under its original filename.
    assert (out_dir / "shot.png").read_bytes() == b"blob"


def test_sessions_send_attachment_id_passes_ids_to_send_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/input":
            state["input_body"] = json.loads(request.content)
            return httpx.Response(200, json={"session": {"id": "s1"}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "send",
            "s1",
            "hello",
            "--attachment-id",
            "id-aaa",
            "--attachment-id",
            "id-bbb",
        ],
    )
    assert result.exit_code == 0, result.output
    assert state["input_body"]["attachments"] == ["id-aaa", "id-bbb"]


def test_sessions_send_attach_and_attachment_id_combined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", "t")
    f = tmp_path / "a.txt"
    f.write_text("x")
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.url.path == "/api/sessions/s1/attachments"
            and request.method == "POST"
        ):
            return httpx.Response(
                200,
                json={
                    "id": "uploaded-id",
                    "filename": "a.txt",
                    "mime": "text/plain",
                    "size": 1,
                    "kind": "file",
                },
            )
        if request.url.path == "/api/sessions/s1/input":
            state["input_body"] = json.loads(request.content)
            return httpx.Response(200, json={"session": {"id": "s1"}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

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
            "send",
            "s1",
            "hello",
            "--attach",
            str(f),
            "--attachment-id",
            "preexisting-id",
        ],
    )
    assert result.exit_code == 0, result.output
    # uploaded files come first, then explicit IDs
    assert state["input_body"]["attachments"] == ["uploaded-id", "preexisting-id"]


# ── usage command ─────────────────────────────────────────────────────────────


def test_usage_emits_dashboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/usage" and request.method == "GET":
            return httpx.Response(200, json={"buckets": [], "total_cost_usd": 0.5})
        return httpx.Response(404, json={"detail": "unexpected"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(app, ["--config", str(_config(tmp_path)), "usage"])
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["buckets"] == []
    assert out["total_cost_usd"] == 0.5


def test_usage_refresh_posts_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/usage/refresh" and request.method == "POST":
            called.append("refresh")
            return httpx.Response(
                200, json={"buckets": [{"id": "b1"}], "total_cost_usd": 2.0}
            )
        return httpx.Response(404, json={"detail": "unexpected"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app, ["--config", str(_config(tmp_path)), "usage", "--refresh"]
    )
    assert result.exit_code == 0
    assert called == ["refresh"]
    out = json.loads(result.stdout)
    assert out["total_cost_usd"] == 2.0


def test_help_includes_usage_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "usage" in result.stdout


# ── board read / board log CLI tests ─────────────────────────────────────────


def _board_read_handler(entries: list[dict]) -> "httpx.MockTransport":
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/board/") and request.method == "GET":
            return httpx.Response(
                200,
                json={"channel": "job:test", "entries": entries},
            )
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"token": "t", "expires_at": "x"})
        return httpx.Response(404, json={"detail": "unexpected"})

    return httpx.MockTransport(handler)


_MIXED_ENTRIES: list[dict[str, Any]] = [
    {
        "id": 1,
        "channel": "job:test",
        "key": "plan",
        "text": "plan text",
        "metadata": {"state": "done"},
        "created_at": "2024-01-01T00:00:00+00:00",
        "author_session_id": "lead",
        "author_label": "Lead Session",
    },
    {
        "id": 2,
        "channel": "job:test",
        "key": None,
        "text": "task 1 done",
        "metadata": {},
        "created_at": "2024-01-02T00:00:00+00:00",
        "author_session_id": "worker-1",
        "author_label": "Worker One",
    },
    {
        "id": 3,
        "channel": "job:test",
        "key": None,
        "text": "task 2 done",
        "metadata": {},
        "created_at": "2024-01-03T00:00:00+00:00",
        "author_session_id": "worker-2",
        "author_label": "Worker Two",
    },
]


def test_board_read_json_splits_cells_and_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(
            transport=_board_read_handler(_MIXED_ENTRIES),
            base_url="http://t",
        )
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "board", "read", "job:test", "--json"],
    )
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["channel"] == "job:test"
    assert len(out["cells"]) == 1
    assert out["cells"][0]["key"] == "plan"
    assert len(out["log"]) == 2
    assert all(e["key"] is None for e in out["log"])


def test_board_read_default_render_shows_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(
            transport=_board_read_handler(_MIXED_ENTRIES),
            base_url="http://t",
        )
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "board", "read", "job:test"],
    )
    assert result.exit_code == 0
    assert "=== Cells" in result.output
    assert "=== Log" in result.output
    assert "plan" in result.output
    assert "task 1 done" in result.output
    assert "task 2 done" in result.output


def test_board_read_key_no_match_writes_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(
            transport=_board_read_handler([]),
            base_url="http://t",
        )
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "board",
            "read",
            "job:test",
            "--key",
            "missing",
        ],
    )
    assert result.exit_code == 0
    assert "no cell 'missing' matched" in result.output


def test_board_log_filters_by_author(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(
            transport=_board_read_handler(_MIXED_ENTRIES),
            base_url="http://t",
        )
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "board",
            "log",
            "job:test",
            "--author",
            "worker-1",
            "--json",
        ],
    )
    assert result.exit_code == 0
    posts = json.loads(result.stdout)
    assert len(posts) == 1
    assert posts[0]["author_session_id"] == "worker-1"


def test_board_log_filters_by_grep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(
            transport=_board_read_handler(_MIXED_ENTRIES),
            base_url="http://t",
        )
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "board",
            "log",
            "job:test",
            "--grep",
            "task 2",
            "--json",
        ],
    )
    assert result.exit_code == 0
    posts = json.loads(result.stdout)
    assert len(posts) == 1
    assert posts[0]["text"] == "task 2 done"


def test_board_log_empty_author_filter_writes_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(
            transport=_board_read_handler(_MIXED_ENTRIES),
            base_url="http://t",
        )
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "board",
            "log",
            "job:test",
            "--author",
            "nobody",
        ],
    )
    assert result.exit_code == 0
    assert "no posts by 'nobody' matched" in result.output


def test_board_clear_keep_last_passes_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"token": "t", "expires_at": "x"})
        if request.url.path == "/api/board/job:test/clear" and request.method == "POST":
            state["params"] = dict(request.url.params)
            return httpx.Response(200, json={"channel": "job:test", "cleared": 2})
        return httpx.Response(404, json={"detail": "unexpected"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "board",
            "clear",
            "job:test",
            "--keep-last",
            "5",
        ],
    )
    assert result.exit_code == 0
    assert state["params"].get("keep_last") == "5"


def test_board_help_lists_log_command() -> None:
    result = runner.invoke(app, ["board", "--help"])
    assert result.exit_code == 0
    assert "log" in result.stdout


def _permission_mode_handler(
    posted: list[dict[str, Any]],
    *,
    backend: str,
    supports_inline: bool,
    modes: list[str],
) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/sessions/s1":
            return httpx.Response(
                200, json={"session": {"id": "s1", "backend": backend}}
            )
        if path == "/api/backends":
            return httpx.Response(
                200,
                json={
                    "backends": [
                        {
                            "id": backend,
                            "capabilities": {
                                "supports_set_permission_mode_inline": supports_inline,
                                "permission_modes": [{"id": m} for m in modes],
                            },
                        }
                    ]
                },
            )
        if path == "/api/sessions/s1/mode":
            posted.append(json.loads(request.content))
            return httpx.Response(
                200, json={"session": {"id": "s1", "permission_mode": "auto"}}
            )
        return httpx.Response(404, json={"detail": f"unexpected {path}"})

    return handler


def _fake_client_factory(handler: Any) -> Any:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    return fake_client


def test_set_permission_mode_posts_valid_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[dict[str, Any]] = []
    handler = _permission_mode_handler(
        posted, backend="claude_code", supports_inline=True, modes=["default", "auto"]
    )
    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "set-permission-mode",
            "s1",
            "auto",
        ],
    )
    assert result.exit_code == 0
    assert posted == [{"mode": "auto"}]
    assert json.loads(result.stdout)["session"]["permission_mode"] == "auto"


def test_set_permission_mode_rejects_unknown_mode_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[dict[str, Any]] = []
    handler = _permission_mode_handler(
        posted, backend="claude_code", supports_inline=True, modes=["default", "auto"]
    )
    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "sessions", "mode", "s1", "bogus"],
    )
    assert result.exit_code != 0
    assert "unknown permission mode" in result.output
    # Validation is local — the server is never asked to set a bad mode.
    assert posted == []


def test_set_permission_mode_rejects_unsupported_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[dict[str, Any]] = []
    handler = _permission_mode_handler(
        posted, backend="tmux", supports_inline=False, modes=[]
    )
    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "set-permission-mode",
            "s1",
            "auto",
        ],
    )
    assert result.exit_code != 0
    assert "does not support" in result.output
    assert posted == []


def test_start_warns_on_unknown_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/backends/codex/models":
            return httpx.Response(
                200,
                json={
                    "backend": "codex",
                    "models": [{"id": "gpt-5"}],
                    "supports_free_text": True,
                },
            )
        if path == "/api/sessions" and request.method == "POST":
            return httpx.Response(200, json={"session": {"id": "new"}})
        return httpx.Response(404, json={"detail": f"unexpected {path}"})

    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "start",
            "--backend",
            "codex",
            "--cwd",
            "/tmp",
            "--model",
            "gpt-5-codex",
        ],
    )
    # Warning, not rejection: the session is still created.
    assert result.exit_code == 0
    assert "is not among" in result.output


# ── schedule message CLI tests ─────────────────────────────────────────────────


def test_schedule_message_help_lists_commands(tmp_path: Path) -> None:
    result = runner.invoke(app, ["schedule", "message", "--help"])
    assert result.exit_code == 0
    for name in ("list", "create", "delete", "clear-history"):
        assert name in result.stdout


def _message_schedule_handler(
    request: httpx.Request,
) -> httpx.Response:
    path = request.url.path
    if path == "/api/message-schedules" and request.method == "GET":
        return httpx.Response(
            200,
            json={
                "message_schedules": [
                    {"id": "ms1", "session_id": "s1", "text": "hello"}
                ]
            },
        )
    if path == "/api/sessions/s1/message-schedules" and request.method == "POST":
        payload = json.loads(request.content)
        return httpx.Response(200, json={"message_schedule": {"id": "ms1", **payload}})
    if path.startswith("/api/message-schedules/") and request.method == "DELETE":
        schedule_id = path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"message_schedule": {"id": schedule_id}})
    if path == "/api/message-schedules/clear-history" and request.method == "POST":
        return httpx.Response(200, json={"removed": 2})
    return httpx.Response(404, json={"detail": f"unexpected {path}"})


def test_schedule_message_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(
            transport=httpx.MockTransport(_message_schedule_handler),
            base_url="http://t",
        )
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "schedule", "message", "list"],
    )
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["message_schedules"] == [
        {"id": "ms1", "session_id": "s1", "text": "hello"}
    ]


def test_schedule_message_list_with_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/message-schedules" and request.method == "GET":
            state["params"] = dict(request.url.params)
            return httpx.Response(
                200, json={"message_schedules": [{"id": "ms1", "session_id": "s1"}]}
            )
        return httpx.Response(404, json={"detail": "unexpected"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "schedule",
            "message",
            "list",
            "--session-id",
            "s1",
        ],
    )
    assert result.exit_code == 0
    assert state["params"]["session_id"] == "s1"


def test_schedule_message_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(
            transport=httpx.MockTransport(_message_schedule_handler),
            base_url="http://t",
        )
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "schedule",
            "message",
            "create",
            "s1",
            "hello world",
            "--delay-seconds",
            "30",
        ],
    )
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["message_schedule"]["id"] == "ms1"
    assert out["message_schedule"]["text"] == "hello world"
    assert out["message_schedule"]["delay_seconds"] == 30


def test_schedule_message_create_with_scheduled_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(
            transport=httpx.MockTransport(_message_schedule_handler),
            base_url="http://t",
        )
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "schedule",
            "message",
            "create",
            "s1",
            "hello",
            "--scheduled-at",
            "2026-07-01T12:00:00Z",
        ],
    )
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["message_schedule"]["scheduled_at"] == "2026-07-01T12:00:00Z"


def test_schedule_message_create_no_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.url.path == "/api/sessions/s1/message-schedules"
            and request.method == "POST"
        ):
            state["body"] = json.loads(request.content)
            return httpx.Response(
                200, json={"message_schedule": {"id": "ms1", **state["body"]}}
            )
        return httpx.Response(404, json={"detail": "unexpected"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "schedule",
            "message",
            "create",
            "s1",
            "hello",
            "--no-submit",
        ],
    )
    assert result.exit_code == 0
    assert state["body"]["submit"] is False


def test_schedule_message_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(
            transport=httpx.MockTransport(_message_schedule_handler),
            base_url="http://t",
        )
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "schedule",
            "message",
            "delete",
            "ms1",
        ],
    )
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["message_schedule"] == {"id": "ms1"}


def test_schedule_message_clear_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(
            transport=httpx.MockTransport(_message_schedule_handler),
            base_url="http://t",
        )
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "schedule",
            "message",
            "clear-history",
        ],
    )
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out == {"removed": 2}


def test_schedule_message_clear_history_with_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.url.path == "/api/message-schedules/clear-history"
            and request.method == "POST"
        ):
            state["params"] = dict(request.url.params)
            return httpx.Response(200, json={"removed": 1})
        return httpx.Response(404, json={"detail": "unexpected"})

    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "schedule",
            "message",
            "clear-history",
            "--session-id",
            "s1",
        ],
    )
    assert result.exit_code == 0
    assert state["params"]["session_id"] == "s1"


# ── account-profile launch surfaces ───────────────────────────────────────────


def test_sessions_start_account_profile_sends_request_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "POST":
            body = json.loads(request.content)
            state["create_body"] = body
            return httpx.Response(200, json={"session": {"id": "new", **body}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "start",
            "--backend",
            "codex",
            "--cwd",
            "/tmp/repo",
            "--account-profile",
            "work",
        ],
    )
    assert result.exit_code == 0, result.output
    body = state["create_body"]
    assert isinstance(body, dict)
    assert body["account_profile_id"] == "work"


def test_sessions_start_omits_account_profile_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "POST":
            body = json.loads(request.content)
            state["create_body"] = body
            return httpx.Response(200, json={"session": {"id": "new", **body}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "start",
            "--backend",
            "codex",
            "--cwd",
            "/tmp/repo",
        ],
    )
    assert result.exit_code == 0, result.output
    body = state["create_body"]
    assert isinstance(body, dict)
    # Omitted, not sent as null, so the preset/server default still applies.
    assert "account_profile_id" not in body


def test_schedule_create_account_profile_sends_request_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/schedules" and request.method == "POST":
            body = json.loads(request.content)
            state["schedule_body"] = body
            return httpx.Response(200, json={"schedule": {"id": "sc1", **body}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "schedule",
            "create",
            "--backend",
            "codex",
            "--cwd",
            "/tmp/repo",
            "--delay-seconds",
            "60",
            "--account-profile",
            "work",
        ],
    )
    assert result.exit_code == 0, result.output
    body = state["schedule_body"]
    assert isinstance(body, dict)
    assert body["account_profile_id"] == "work"


def test_presets_create_account_profile_in_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session-presets" and request.method == "POST":
            body = json.loads(request.content)
            state["preset_body"] = body
            return httpx.Response(200, json={"preset": {"id": "p1", **body}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "presets",
            "create",
            "--name",
            "work-preset",
            "--backend",
            "codex",
            "--account-profile",
            "work",
        ],
    )
    assert result.exit_code == 0, result.output
    body = state["preset_body"]
    assert isinstance(body, dict)
    assert body["spec"]["account_profile_id"] == "work"


def test_sessions_import_account_profile_in_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/backends/codex/sessions/import":
            body = json.loads(request.content)
            state["import_body"] = body
            return httpx.Response(200, json={"session": {"id": "new", **body}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "import",
            "codex",
            "--thread-id",
            "11111111-1111-1111-1111-111111111111",
            "--account-profile",
            "work",
        ],
    )
    assert result.exit_code == 0, result.output
    body = state["import_body"]
    assert isinstance(body, dict)
    assert body["account_profile_id"] == "work"


def test_sessions_set_account_patches_launch_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.url.path == "/api/sessions/s1/launch-settings"
            and request.method == "PATCH"
        ):
            body = json.loads(request.content)
            state["patch_body"] = body
            return httpx.Response(
                200,
                json={"session": {"id": "s1", "account_profile_id": "work"}},
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "set-account",
            "s1",
            "work",
        ],
    )
    assert result.exit_code == 0, result.output
    assert state["patch_body"] == {"restart": True, "account_profile_id": "work"}
    assert json.loads(result.stdout)["session"]["account_profile_id"] == "work"


def test_sessions_set_account_no_restart_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/launch-settings":
            state["patch_body"] = json.loads(request.content)
            return httpx.Response(200, json={"session": {"id": "s1"}})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "set-account",
            "s1",
            "work",
            "--no-restart",
        ],
    )
    assert result.exit_code == 0, result.output
    body = state["patch_body"]
    assert isinstance(body, dict)
    assert body["restart"] is False


def test_sessions_launch_settings_emits_get(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.url.path == "/api/sessions/s1/launch-settings"
            and request.method == "GET"
        ):
            return httpx.Response(
                200,
                json={
                    "backend": "codex",
                    "transport": "codex_app_server",
                    "account_profile_id": "work",
                    "launch_env_keys": ["CODEX_HOME"],
                },
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "sessions",
            "launch-settings",
            "s1",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["account_profile_id"] == "work"
    assert payload["launch_env_keys"] == ["CODEX_HOME"]


def test_accounts_list_from_backends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/backends":
            return httpx.Response(
                200,
                json={
                    "backends": [
                        {
                            "id": "codex",
                            "account_profiles": [
                                {
                                    "id": "work",
                                    "label": "Work",
                                    "config_dir_key": "CODEX_HOME",
                                }
                            ],
                        },
                        {"id": "opencode", "account_profiles": []},
                    ]
                },
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "accounts", "list"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    # Backends without profiles are dropped.
    assert payload == {
        "accounts": [
            {
                "backend": "codex",
                "profiles": [
                    {"id": "work", "label": "Work", "config_dir_key": "CODEX_HOME"}
                ],
            }
        ]
    }


def test_accounts_list_filter_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/backends":
            return httpx.Response(
                200,
                json={
                    "backends": [
                        {
                            "id": "codex",
                            "account_profiles": [
                                {
                                    "id": "work",
                                    "label": "W",
                                    "config_dir_key": "CODEX_HOME",
                                }
                            ],
                        },
                        {
                            "id": "claude_code",
                            "account_profiles": [
                                {
                                    "id": "personal",
                                    "label": "P",
                                    "config_dir_key": "CLAUDE_CONFIG_DIR",
                                }
                            ],
                        },
                    ]
                },
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        ["--config", str(_config(tmp_path)), "accounts", "list", "--backend", "codex"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [a["backend"] for a in payload["accounts"]] == ["codex"]


def test_accounts_list_launch_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/me":
            return httpx.Response(
                200,
                json={
                    "launch_targets": [
                        {
                            "id": "ssh-box",
                            "account_profiles_by_backend": {
                                "codex": [
                                    {
                                        "id": "work",
                                        "label": "Work",
                                        "config_dir_key": "CODEX_HOME",
                                    }
                                ]
                            },
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "accounts",
            "list",
            "--launch-target-id",
            "ssh-box",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["accounts"] == [
        {
            "backend": "codex",
            "profiles": [
                {"id": "work", "label": "Work", "config_dir_key": "CODEX_HOME"}
            ],
        }
    ]


def test_accounts_list_unknown_launch_target_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/me":
            return httpx.Response(200, json={"launch_targets": []})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    monkeypatch.setattr("waypoint.cli.WaypointClient", _fake_client_factory(handler))
    result = runner.invoke(
        app,
        [
            "--config",
            str(_config(tmp_path)),
            "accounts",
            "list",
            "--launch-target-id",
            "nope",
        ],
    )
    assert result.exit_code != 0
    assert "unknown launch target" in result.output


# ── maintenance rebuild-telemetry ────────────────────────────────────────────


def _seed_telemetry_source(db_path: Path) -> None:
    """Seed one session + a user-input event so a backfill derives real facts."""
    storage = Storage(db_path)
    try:
        now = datetime.now(UTC)
        storage.create_session(
            SessionRecord(
                id="s1",
                backend="codex",
                source=SessionSource.MANAGED,
                transport="tmux",
                title="t",
                cwd="/home/user/projects/waypoint",
                repo_name="/home/user/projects/waypoint",
                status=SessionStatus.IDLE,
                created_at=now,
                updated_at=now,
                last_event_at=now,
                raw_log_path="/tmp/raw.log",
                structured_log_path="/tmp/events.jsonl",
                resolved_model="gpt-5-codex",
                spawner_session_id=None,
                tags={},
            )
        )
        storage.append_event(
            EventRecord(
                session_id="s1",
                ts=now,
                kind=EventKind.USER_INPUT,
                text="hello",
                sequence=1,
            )
        )
    finally:
        storage.close()


def _mark_backfilled(db_path: Path) -> None:
    storage = Storage(db_path)
    try:
        asyncio.run(TelemetryIngester(storage).backfill())
    finally:
        storage.close()


def _fact_count(db_path: Path) -> int:
    storage = Storage(db_path)
    try:
        return storage.connection.execute(
            "SELECT COUNT(*) AS n FROM telemetry_facts"
        ).fetchone()["n"]
    finally:
        storage.close()


@pytest.fixture(autouse=True)
def _stub_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default to "no live backend" so tests exercise the command body; the two
    # refusal tests override these explicitly.
    monkeypatch.setattr("waypoint.cli._backend_reachable", lambda _s: False)
    monkeypatch.setattr("waypoint.cli._database_write_locked", lambda _s: False)


def test_rebuild_telemetry_initial_import_needs_no_force(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    db_path = _settings_from_arg(str(cfg)).database_path
    _seed_telemetry_source(db_path)

    result = runner.invoke(
        app, ["--config", str(cfg), "maintenance", "rebuild-telemetry"]
    )

    assert result.exit_code == 0, result.output
    assert _fact_count(db_path) > 0
    summary = json.loads(result.output)
    assert summary["telemetry_facts"] > 0
    assert summary["backfill_through"] is not None


def test_rebuild_telemetry_refuses_when_done_without_force(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    db_path = _settings_from_arg(str(cfg)).database_path
    _seed_telemetry_source(db_path)
    _mark_backfilled(db_path)

    result = runner.invoke(
        app, ["--config", str(cfg), "maintenance", "rebuild-telemetry"]
    )

    assert result.exit_code == 1
    assert "--force" in result.output


def test_rebuild_telemetry_force_yes_re_derives(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    db_path = _settings_from_arg(str(cfg)).database_path
    _seed_telemetry_source(db_path)
    _mark_backfilled(db_path)

    storage = Storage(db_path)
    try:
        storage.connection.execute("DELETE FROM telemetry_facts")
        storage.connection.commit()
    finally:
        storage.close()
    assert _fact_count(db_path) == 0

    result = runner.invoke(
        app,
        ["--config", str(cfg), "maintenance", "rebuild-telemetry", "--force", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert _fact_count(db_path) > 0


def test_rebuild_telemetry_refuses_when_backend_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path)
    db_path = _settings_from_arg(str(cfg)).database_path
    _seed_telemetry_source(db_path)
    monkeypatch.setattr("waypoint.cli._backend_reachable", lambda _s: True)

    result = runner.invoke(
        app,
        ["--config", str(cfg), "maintenance", "rebuild-telemetry", "--force", "--yes"],
    )

    assert result.exit_code == 1
    assert "Stop it first" in result.output


def test_rebuild_telemetry_refuses_when_database_write_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path)
    db_path = _settings_from_arg(str(cfg)).database_path
    _seed_telemetry_source(db_path)
    # Real lock probe against a sibling connection holding an open write txn.
    monkeypatch.undo()
    monkeypatch.setattr("waypoint.cli._backend_reachable", lambda _s: False)

    holder = sqlite3.connect(str(db_path))
    try:
        holder.execute("BEGIN IMMEDIATE")
        result = runner.invoke(
            app,
            [
                "--config",
                str(cfg),
                "maintenance",
                "rebuild-telemetry",
                "--force",
                "--yes",
            ],
        )
    finally:
        holder.rollback()
        holder.close()

    assert result.exit_code == 1
    assert "Stop it first" in result.output


def test_maintenance_stats_reports_backfill_state(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    db_path = _settings_from_arg(str(cfg)).database_path
    _seed_telemetry_source(db_path)

    before = json.loads(
        runner.invoke(app, ["--config", str(cfg), "maintenance", "stats"]).output
    )
    assert before["telemetry_backfill"] == {"done": False, "through": None}

    _mark_backfilled(db_path)

    after = json.loads(
        runner.invoke(app, ["--config", str(cfg), "maintenance", "stats"]).output
    )
    assert after["telemetry_backfill"]["done"] is True
    assert after["telemetry_backfill"]["through"] is not None
