import os
from pathlib import Path

from waypoint.config import load_settings
from waypoint.schemas import Backend
from waypoint.server_config import (
    SshLaunchTargetConfig,
    _quote_remote_path,
    build_remote_codex_client_factory,
)


def test_quote_remote_path_preserves_leading_tilde() -> None:
    assert _quote_remote_path("~") == "~"
    assert _quote_remote_path("~/") == "~/"
    assert _quote_remote_path("~/workspace") == "~/workspace"
    assert _quote_remote_path("~/My Projects") == "~/'My Projects'"
    assert _quote_remote_path("~user/work") == "~user/work"
    assert _quote_remote_path("/srv/work") == "/srv/work"
    assert _quote_remote_path("/srv/My Work") == "'/srv/My Work'"


def test_load_settings_parses_yaml_defaults_and_ssh_targets(monkeypatch, tmp_path: Path) -> None:
    for name in list(os.environ):
        if name.startswith("WAYPOINT_"):
            monkeypatch.delenv(name, raising=False)
    config_path = tmp_path / "waypoint.yaml"
    config_path.write_text(
        "\n".join(
            [
                "host: 0.0.0.0",
                "port: 9999",
                "password: from-yaml",
                "ssh_targets:",
                "  - id: devbox",
                "    name: Devbox",
                "    ssh_destination: dev@example.com",
                "    ssh_args:",
                "      - -p",
                "      - '2222'",
                "    codex_bin: /opt/codex/bin/codex",
                "    default_remote_cwd: ~/workspace",
                "    supported_backends:",
                "      - codex",
                "    config_overrides:",
                "      - model_reasoning_effort=\"high\"",
                "    remote_env:",
                "      OPENAI_API_KEY: sk-test",
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_settings(config_path)

    assert loaded.host == "0.0.0.0"
    assert loaded.port == 9999
    assert loaded.password == "from-yaml"
    assert len(loaded.ssh_targets) == 1
    assert loaded.ssh_targets[0].id == "devbox"
    assert loaded.ssh_targets[0].name == "Devbox"
    assert loaded.ssh_targets[0].ssh_destination == "dev@example.com"
    assert loaded.ssh_targets[0].ssh_args == ["-p", "2222"]
    assert loaded.ssh_targets[0].default_remote_cwd == "~/workspace"
    assert loaded.ssh_targets[0].remote_env["OPENAI_API_KEY"] == "sk-test"
    assert loaded.ssh_targets[0].supported_backends == [Backend.CODEX]


def test_load_settings_env_overrides_yaml(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "waypoint.yaml"
    config_path.write_text(
        "\n".join(
            [
                "host: yaml-host",
                "port: 9999",
                "password: from-yaml",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WAYPOINT_HOST", "127.0.0.1")
    monkeypatch.setenv("WAYPOINT_PORT", "8787")
    monkeypatch.setenv("WAYPOINT_PASSWORD", "from-env")

    loaded = load_settings(config_path)

    assert loaded.host == "127.0.0.1"
    assert loaded.port == 8787
    assert loaded.password == "from-env"


def test_remote_client_factory_uses_default_remote_cwd_when_not_provided(monkeypatch) -> None:
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        default_remote_cwd="~/workspace",
    )

    monkeypatch.setattr("waypoint.server_config.shutil.which", lambda _: "/usr/bin/ssh")
    client = build_remote_codex_client_factory(config)("/Users/alice/work/project-a", None, lambda *_: {})

    assert client.config.launch_args_override is not None
    # `~` must reach the remote shell unquoted so it can be expanded.
    assert "cd ~/workspace" in client.config.launch_args_override[2]


def test_remote_client_factory_uses_ssh_launch_args(monkeypatch) -> None:
    monkeypatch.setattr("waypoint.server_config.shutil.which", lambda _: "/usr/bin/ssh")
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        ssh_args=["-p", "2222"],
        remote_env={"OPENAI_API_KEY": "sk-test"},
        config_overrides=["model=\"gpt-5\""],
    )

    client = build_remote_codex_client_factory(config)("/Users/alice/work/project-a", "/srv/work/project-a", lambda *_: {})

    assert client.config.launch_args_override is not None
    assert client.config.cwd is None
    assert client.config.launch_args_override[:4] == (
        "/usr/bin/ssh",
        "-p",
        "2222",
        "dev@example.com",
    )
    remote_command = client.config.launch_args_override[4]
    assert "cd /srv/work/project-a" in remote_command
    assert "app-server --listen stdio://" in remote_command
    assert "OPENAI_API_KEY=sk-test" in remote_command


def test_ssh_target_remote_command_supports_claude(monkeypatch) -> None:
    monkeypatch.setattr("waypoint.server_config.shutil.which", lambda _: "/usr/bin/ssh")
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        claude_bin="/opt/claude/bin/claude",
    )

    command = config.remote_command_for_backend(Backend.CLAUDE_CODE, ["--resume"], "~/workspace")

    assert command[:2] == ("/usr/bin/ssh", "dev@example.com")
    assert "cd ~/workspace" in command[2]
    assert "/opt/claude/bin/claude --resume" in command[2]
