from pathlib import Path

import pytest
from typer.testing import CliRunner

from waypoint.cli import app

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
        "reset",
        "session",
        "sessions",
        "board",
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
    ):
        assert name in result.stdout


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
