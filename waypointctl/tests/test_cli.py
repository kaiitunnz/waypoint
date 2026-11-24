import json
import os
from pathlib import Path
from typing import cast

import click
import pytest
import typer
from typer.testing import CliRunner

from waypointctl import cli
from waypointctl.cli import app
from waypointctl.paths import pid_file_for, waypoint_pid_path

runner = CliRunner()


def test_help_command_dumps_full_surface() -> None:
    result = runner.invoke(app, ["help"])
    assert result.exit_code == 0
    for command in ("daemon start", "skills install", "tailscale up"):
        assert command in result.stdout
    assert "--skill-dir" in result.stdout


def test_help_json_is_structured() -> None:
    result = runner.invoke(app, ["help", "--json"])
    assert result.exit_code == 0
    commands = json.loads(result.stdout)
    by_path = {entry["command"]: entry for entry in commands}
    install = by_path["skills install"]
    skill_dir = next(o for o in install["options"] if "--skill-dir" in o["flags"])
    assert skill_dir["required"] is False
    assert skill_dir["type"] in {"str", "list[str]"}


def _walk_leaf_paths(group: click.Group, prefix: str) -> list[str]:
    paths: list[str] = []
    for name, cmd in group.commands.items():
        if cmd.hidden:
            continue
        path = f"{prefix} {name}".strip()
        if isinstance(getattr(cmd, "commands", None), dict):
            paths.extend(_walk_leaf_paths(cast(click.Group, cmd), path))
        else:
            paths.append(path)
    return paths


def test_help_covers_every_leaf_command() -> None:
    result = runner.invoke(app, ["help", "--json"])
    assert result.exit_code == 0
    dumped = {entry["command"] for entry in json.loads(result.stdout)}
    root = cast(click.Group, typer.main.get_command(app))
    expected = set(_walk_leaf_paths(root, ""))
    assert expected <= dumped


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state"
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(state))
    for name in (
        "WAYPOINTCTL_DAEMON",
        "WAYPOINT_STACK_FRONTEND",
        "WAYPOINT_STACK_FRONTEND_PREVIEW_PORT",
    ):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "repo"
    for sub in ("backend", "frontend", "scripts"):
        (home / sub).mkdir(parents=True)
    return home


def _record_pid(name: str, pid: int) -> None:
    path = pid_file_for(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n")


def _record_daemon_pid(pid: int) -> None:
    path = waypoint_pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n")


@pytest.mark.parametrize("target", ["frontend", "frontend-preview", "all"])
def test_restart_refuses_from_inside_the_preview_process_tree(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    monkeypatch.setenv("WAYPOINT_STACK_FRONTEND_PREVIEW_PORT", "3999")
    _record_pid("frontend-preview", os.getppid())

    result = runner.invoke(app, ["--home", str(cli_home), "restart", target])

    assert result.exit_code == 1
    assert "refusing to restart frontend-preview" in result.output


def test_restart_backend_ignores_the_preview_process_tree(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_STACK_FRONTEND_PREVIEW_PORT", "3999")
    _record_pid("frontend-preview", os.getppid())

    monkeypatch.setattr(cli, "_run_control_command", lambda *_a, **_kw: None)
    result = runner.invoke(app, ["--home", str(cli_home), "restart", "backend"])

    assert result.exit_code == 0


@pytest.mark.parametrize(
    "command", [["status"], ["daemon", "start"], ["daemon", "restart"]]
)
def test_invalid_configuration_is_rejected_before_a_daemon_is_spawned(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    monkeypatch.setenv("WAYPOINTCTL_DAEMON", "1")
    monkeypatch.setenv("WAYPOINT_STACK_FRONTEND", "v3")
    spawned: list[Path] = []
    monkeypatch.setattr(cli, "ensure_daemon", lambda home: spawned.append(home))

    result = runner.invoke(app, ["--home", str(cli_home), *command])

    assert result.exit_code == 2
    assert "invalid stack configuration" in result.output
    assert spawned == []


def test_daemon_restart_with_a_bad_configuration_leaves_the_daemon_alone(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_STACK_FRONTEND", "v3")
    _record_daemon_pid(os.getpid())
    signalled: list[int] = []
    monkeypatch.setattr(cli.os, "kill", lambda pid, _sig: signalled.append(pid))

    result = runner.invoke(app, ["--home", str(cli_home), "daemon", "restart"])

    assert result.exit_code == 2
    assert signalled == []


def test_invalid_stack_configuration_is_reported_in_one_line(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_STACK_FRONTEND", "v3")

    result = runner.invoke(app, ["--home", str(cli_home), "status"])

    assert result.exit_code == 2
    assert "invalid stack configuration" in result.output
    assert "WAYPOINT_STACK_FRONTEND" in result.output
    assert "Traceback" not in result.output
