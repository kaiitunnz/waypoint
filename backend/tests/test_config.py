from pathlib import Path

import pytest

from waypoint import config as config_module
from waypoint.config import DEFAULT_CONFIG_PATH, load_settings
from waypoint.schemas import Backend


def test_default_config_path_points_at_backend_waypoint_yaml() -> None:
    assert DEFAULT_CONFIG_PATH.name == "waypoint.yaml"
    assert DEFAULT_CONFIG_PATH.parent == config_module.BACKEND_ROOT


def test_load_settings_silently_uses_default_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "absent.yaml"
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", missing)
    for var in ("WAYPOINT_CONFIG_PATH", "WAYPOINT_HOST", "WAYPOINT_PORT"):
        monkeypatch.delenv(var, raising=False)
    settings = load_settings()
    assert settings.config_path is None
    assert settings.host == "127.0.0.1"


def test_load_settings_loads_default_when_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text(
        "host: 0.0.0.0\nport: 9000\ndefault_backend: claude_code\ndefault_cwd: /tmp/project\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    monkeypatch.delenv("WAYPOINT_HOST", raising=False)
    monkeypatch.delenv("WAYPOINT_PORT", raising=False)
    settings = load_settings()
    assert settings.config_path == config_file
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000
    assert settings.default_backend == Backend.CLAUDE_CODE
    assert settings.default_cwd == "/tmp/project"


def test_load_settings_errors_when_explicit_path_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    missing = tmp_path / "explicit.yaml"
    with pytest.raises(FileNotFoundError):
        load_settings(config_path_override=missing)
