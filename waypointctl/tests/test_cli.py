import json
from typing import cast

import click
import typer
from typer.testing import CliRunner

from waypointctl.cli import app

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
    assert skill_dir["type"] == "str"


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
