from pathlib import Path

import pytest
from pydantic import ValidationError

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


def test_load_settings_reads_chat_page_messages_from_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text("chat_page_messages: 30\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    settings = load_settings()
    assert settings.chat_page_messages == 30


def test_chat_page_messages_rejects_out_of_range_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text("chat_page_messages: 0\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    with pytest.raises(ValidationError):
        load_settings()


def test_legacy_events_page_size_migrates_to_chat_page_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text("events_page_size: 77\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    with caplog.at_level("WARNING", logger="waypoint.config"):
        settings = load_settings()
    # Migrated, clamped into [1, 200], and a deprecation warning was logged.
    assert settings.chat_page_messages == 77
    assert any("events_page_size" in record.message for record in caplog.records)


def test_legacy_events_page_size_clamps_to_new_field_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text("events_page_size: 999\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    settings = load_settings()
    # 999 was valid for the old (raw events, max 1000) field; clamp to
    # the new field's 200 cap rather than failing validation.
    assert settings.chat_page_messages == 200


def test_explicit_chat_page_messages_wins_over_legacy_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text(
        "events_page_size: 77\nchat_page_messages: 42\n", encoding="utf-8"
    )
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    settings = load_settings()
    assert settings.chat_page_messages == 42
