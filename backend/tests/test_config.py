import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from waypoint import settings as settings_module
from waypoint.backends.claude_code.plugin import ClaudeCodePluginConfig
from waypoint.launch_env import validate_launch_env
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


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://100.64.0.1:3000",
        "http://mymachine:3000",
        "http://my-machine-1:3000",
        "http://mymachine.tailnet.ts.net:3000",
        "http://mymachine.github.beta.tailscale.net:3000",
        "https://mymachine:3000",
    ],
)
def test_default_cors_regex_allows_local_and_tailscale_origins(origin: str) -> None:
    assert settings_module.DEFAULT_CORS_ORIGIN_REGEX is not None
    assert re.fullmatch(settings_module.DEFAULT_CORS_ORIGIN_REGEX, origin)


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com:3000",
        "http://not_tailscale:3000",
        "http://bad-.tailnet.ts.net:3000",
        "ftp://mymachine:3000",
    ],
)
def test_default_cors_regex_rejects_public_or_invalid_origins(origin: str) -> None:
    assert settings_module.DEFAULT_CORS_ORIGIN_REGEX is not None
    assert not re.fullmatch(settings_module.DEFAULT_CORS_ORIGIN_REGEX, origin)


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
                "    default_model_id: gpt-5",
                "    default_effort: high",
                "    local_bin: /opt/codex/bin/codex",
                "  claude_code:",
                "    default_model_id: opus",
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
    assert codex_config.default_model_id == "gpt-5"
    assert codex_config.default_effort == "high"
    assert codex_config.local_bin == "/opt/codex/bin/codex"
    claude_config = settings.plugin_config("claude_code")
    assert isinstance(claude_config, ClaudeCodePluginConfig)
    assert claude_config.default_model_id == "opus"
    assert claude_config.local_bin is None
    assert [model.id for model in claude_config.models] == ["opus"]


def test_load_settings_parses_launch_env_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text(
        "\n".join(
            [
                "plugin_configs:",
                "  codex:",
                "    env:",
                "      OPENAI_API_KEY: local-secret",
                "ssh_targets:",
                "  - id: devbox",
                "    name: Devbox",
                "    ssh_destination: dev@example.com",
                "    plugin_configs:",
                "      codex:",
                "        env:",
                "          OPENAI_API_KEY: remote-secret",
                "          FEATURE_FLAG: enabled",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    settings = load_settings()
    assert settings.plugin_config("codex").env == {"OPENAI_API_KEY": "local-secret"}
    target = settings.ssh_targets[0]
    assert target.plugin_config("codex").env == {
        "OPENAI_API_KEY": "remote-secret",
        "FEATURE_FLAG": "enabled",
    }


def test_launch_env_rejects_nul_in_values() -> None:
    with pytest.raises(ValueError, match="cannot contain NUL"):
        validate_launch_env({"TOKEN": "abc\x00def"})


def test_assistant_defaults_to_none_when_block_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text("default_backend: codex\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    settings = load_settings()
    assert settings.assistant is None
    assert settings.assistant_backend() is None


def test_assistant_block_enables_and_resolves_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text(
        "\n".join(
            [
                "default_backend: codex",
                "assistant:",
                "  model: opus",
                "  effort: high",
                "  permission_mode: bypassPermissions",
                "  backend: claude_code",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    settings = load_settings()
    assert settings.assistant is not None
    assert settings.assistant.enabled is True
    assert settings.assistant_backend() == "claude_code"


def test_assistant_backend_falls_back_to_default_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text(
        "default_backend: claude_code\nassistant:\n  model: opus\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    settings = load_settings()
    assert settings.assistant_backend() == "claude_code"


def test_assistant_disabled_block_resolves_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text(
        "default_backend: codex\nassistant:\n  enabled: false\n  backend: claude_code\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    settings = load_settings()
    assert settings.assistant is not None
    assert settings.assistant.enabled is False
    assert settings.assistant_backend() is None


def test_assistant_rejects_unknown_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text(
        "assistant:\n  backend: not_a_backend\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    with pytest.raises(ValidationError):
        load_settings()


def test_assistant_rejects_unknown_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text(
        "assistant:\n  bogus: 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    with pytest.raises(ValidationError):
        load_settings()


def test_load_settings_rejects_unknown_plugin_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "waypoint.yaml"
    config_file.write_text(
        "plugin_configs:\n  not_a_plugin:\n    default_model_id: gpt-5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", config_file)
    monkeypatch.delenv("WAYPOINT_CONFIG_PATH", raising=False)
    with pytest.raises(ValidationError):
        load_settings()
