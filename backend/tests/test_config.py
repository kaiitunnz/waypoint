from pathlib import Path

import pytest
from pydantic import ValidationError

from waypoint import settings as settings_module
from waypoint.backends.claude_code.plugin import ClaudeCodePluginConfig
from waypoint.settings import DEFAULT_CONFIG_PATH, load_settings


def test_default_config_path_points_at_backend_waypoint_yaml() -> None:
    assert DEFAULT_CONFIG_PATH.name == "waypoint.yaml"
    assert DEFAULT_CONFIG_PATH.parent == settings_module.BACKEND_ROOT


def test_load_settings_silently_uses_default_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "absent.yaml"
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", missing)
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
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    monkeypatch.delenv("WAYPOINT_HOST", raising=False)
    monkeypatch.delenv("WAYPOINT_PORT", raising=False)
    settings = load_settings()
    assert settings.config_path == config_file
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000
    assert settings.default_backend == "claude_code"
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
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    settings = load_settings()
    assert settings.chat_page_messages == 30


def test_chat_page_messages_rejects_out_of_range_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text("chat_page_messages: 0\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    with pytest.raises(ValidationError):
        load_settings()


def test_load_settings_parses_plugin_configs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text(
        "\n".join(
            [
                "plugin_configs:",
                "  codex:",
                "    default_model: gpt-5",
                "    default_effort: high",
                "  claude_code:",
                "    default_model: opus",
                "    models:",
                "      - id: opus",
                "        label: Opus 4.7",
                "        is_default: true",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    settings = load_settings()
    codex_config = settings.plugin_config("codex")
    assert codex_config.default_model == "gpt-5"
    assert codex_config.default_effort == "high"
    claude_config = settings.plugin_config("claude_code")
    assert isinstance(claude_config, ClaudeCodePluginConfig)
    assert claude_config.default_model == "opus"
    assert [model.id for model in claude_config.models] == ["opus"]


def test_load_settings_rejects_unknown_plugin_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text(
        "plugin_configs:\n  not_a_plugin:\n    default_model: gpt-5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    with pytest.raises(ValidationError):
        load_settings()
