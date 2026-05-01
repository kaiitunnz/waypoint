from waypoint.backends.codex.remote import build_remote_codex_client_factory
from waypoint.server_config import SshLaunchTargetConfig


def test_remote_client_factory_uses_default_cwd_when_not_provided(
    monkeypatch,
) -> None:
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        default_cwd="~/workspace",
    )

    monkeypatch.setattr("waypoint.server_config.shutil.which", lambda _: "/usr/bin/ssh")
    client = build_remote_codex_client_factory(config)("~/workspace", lambda *_: {})

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
        config_overrides=['model="gpt-5"'],
    )

    client = build_remote_codex_client_factory(config)(
        "/srv/work/project-a", lambda *_: {}
    )

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
