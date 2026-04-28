from pathlib import Path

from waypoint.server_config import RemoteCodexSshConfig, build_remote_codex_client_factory, load_server_config


def test_load_server_config_parses_remote_codex_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "waypoint.yaml"
    config_path.write_text(
        "\n".join(
            [
                "codex_remote:",
                "  enabled: true",
                "  ssh_destination: dev@example.com",
                "  ssh_args:",
                "    - -p",
                "    - '2222'",
                "  codex_bin: /opt/codex/bin/codex",
                "  config_overrides:",
                "    - model_reasoning_effort=\"high\"",
                "  remote_env:",
                "    OPENAI_API_KEY: sk-test",
                "  cwd_mappings:",
                "    - local_prefix: /Users/alice/work",
                "      remote_prefix: /srv/work",
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_server_config(config_path)

    assert loaded.codex_remote is not None
    assert loaded.codex_remote.enabled is True
    assert loaded.codex_remote.ssh_destination == "dev@example.com"
    assert loaded.codex_remote.ssh_args == ["-p", "2222"]
    assert loaded.codex_remote.remote_env["OPENAI_API_KEY"] == "sk-test"
    assert loaded.codex_remote.cwd_mappings[0].remote_prefix == "/srv/work"


def test_resolve_remote_cwd_prefers_longest_mapping() -> None:
    config = RemoteCodexSshConfig(
        enabled=True,
        ssh_destination="dev@example.com",
        cwd_mappings=[
            {"local_prefix": "/Users/alice", "remote_prefix": "/home/alice"},
            {"local_prefix": "/Users/alice/work", "remote_prefix": "/srv/work"},
        ],
    )

    remote_cwd = config.resolve_remote_cwd("/Users/alice/work/project-a")

    assert remote_cwd == "/srv/work/project-a"


def test_remote_client_factory_uses_ssh_launch_args(monkeypatch) -> None:
    monkeypatch.setattr("waypoint.server_config.shutil.which", lambda _: "/usr/bin/ssh")
    config = RemoteCodexSshConfig(
        enabled=True,
        ssh_destination="dev@example.com",
        ssh_args=["-p", "2222"],
        remote_env={"OPENAI_API_KEY": "sk-test"},
        config_overrides=["model=\"gpt-5\""],
        cwd_mappings=[{"local_prefix": "/Users/alice/work", "remote_prefix": "/srv/work"}],
    )

    client = build_remote_codex_client_factory(config)("/Users/alice/work/project-a", lambda *_: {})

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
