import os
from pathlib import Path

from waypoint.config import load_settings
from waypoint.server_config import RemoteCodexSshConfig, build_remote_codex_client_factory


def test_load_settings_parses_yaml_defaults_and_remote_codex(monkeypatch, tmp_path: Path) -> None:
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
                "codex_remote:",
                "  enabled: true",
                "  ssh_destination: dev@example.com",
                "  ssh_args:",
                "    - -p",
                "    - '2222'",
                "  codex_bin: /opt/codex/bin/codex",
                "  default_remote_cwd: ~/workspace",
                "  config_overrides:",
                "    - model_reasoning_effort=\"high\"",
                "  remote_env:",
                "    OPENAI_API_KEY: sk-test",
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_settings(config_path)

    assert loaded.host == "0.0.0.0"
    assert loaded.port == 9999
    assert loaded.password == "from-yaml"
    assert loaded.codex_remote is not None
    assert loaded.codex_remote.enabled is True
    assert loaded.codex_remote.ssh_destination == "dev@example.com"
    assert loaded.codex_remote.ssh_args == ["-p", "2222"]
    assert loaded.codex_remote.default_remote_cwd == "~/workspace"
    assert loaded.codex_remote.remote_env["OPENAI_API_KEY"] == "sk-test"


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
    config = RemoteCodexSshConfig(
        enabled=True,
        ssh_destination="dev@example.com",
        default_remote_cwd="~/workspace",
    )

    monkeypatch.setattr("waypoint.server_config.shutil.which", lambda _: "/usr/bin/ssh")
    client = build_remote_codex_client_factory(config)("/Users/alice/work/project-a", None, lambda *_: {})

    assert client.config.launch_args_override is not None
    assert "cd '~/workspace'" in client.config.launch_args_override[2]


def test_remote_client_factory_uses_ssh_launch_args(monkeypatch) -> None:
    monkeypatch.setattr("waypoint.server_config.shutil.which", lambda _: "/usr/bin/ssh")
    config = RemoteCodexSshConfig(
        enabled=True,
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
