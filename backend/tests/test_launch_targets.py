import os
from pathlib import Path

from waypoint.launch_targets import SshLaunchTargetConfig, quote_remote_path
from waypoint.settings import load_settings


def test_quote_remote_path_preserves_leading_tilde() -> None:
    assert quote_remote_path("~") == "~"
    assert quote_remote_path("~/") == "~/"
    assert quote_remote_path("~/workspace") == "~/workspace"
    assert quote_remote_path("~/My Projects") == "~/'My Projects'"
    assert quote_remote_path("~user/work") == "~user/work"
    assert quote_remote_path("/srv/work") == "/srv/work"
    assert quote_remote_path("/srv/My Work") == "'/srv/My Work'"
    assert quote_remote_path("~;touch /tmp/pwned") == "'~;touch /tmp/pwned'"
    assert quote_remote_path("~$(id)/work") == "'~$(id)/work'"
    assert quote_remote_path("~user;uname -a/work") == "'~user;uname -a/work'"


def test_load_settings_parses_yaml_defaults_and_ssh_targets(
    monkeypatch, tmp_path: Path
) -> None:
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
                "    remote_bins:",
                "      codex: /opt/codex/bin/codex",
                "    default_cwd: ~/workspace",
                "    supported_backends:",
                "      - codex",
                "    config_overrides:",
                '      - model_reasoning_effort="high"',
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
    assert loaded.ssh_targets[0].default_cwd == "~/workspace"
    assert loaded.ssh_targets[0].remote_env["OPENAI_API_KEY"] == "sk-test"
    assert loaded.ssh_targets[0].supported_backends == ["codex"]
    assert loaded.ssh_targets[0].remote_bins == {"codex": "/opt/codex/bin/codex"}


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


def test_build_remote_exec_args_wraps_with_login_shell_by_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    config = SshLaunchTargetConfig(
        id="devbox", name="Devbox", ssh_destination="dev@example.com"
    )

    args = config.build_remote_exec_args(["claude", "--resume"], "~/workspace")

    assert args[2].startswith("bash -ilc ")
    assert "cd ~/workspace" in args[2]
    assert "claude --resume" in args[2]


def test_build_remote_exec_args_skips_wrapping_when_shell_blank(monkeypatch) -> None:
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        remote_shell="",
    )

    args = config.build_remote_exec_args(["codex"], "~/workspace")

    assert args[2].startswith("cd ~/workspace")


def test_build_remote_exec_args_omits_cd_when_cwd_is_none(monkeypatch) -> None:
    """Callers whose only filesystem dependency is an absolute path must
    be able to opt out of the cwd prefix so a stale ``default_cwd`` on
    the target can't fail their command."""
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    config = SshLaunchTargetConfig(
        id="devbox", name="Devbox", ssh_destination="dev@example.com"
    )

    args = config.build_remote_exec_args(["bash", "-s"])

    remote_command = args[-1]
    assert "cd " not in remote_command
    assert "exec bash -s" in remote_command


def test_remote_bin_for_falls_back_to_default(monkeypatch) -> None:
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        remote_bins={"claude_code": "/opt/claude/bin/claude"},
    )

    assert config.remote_bin_for("claude_code", "claude") == "/opt/claude/bin/claude"
    assert config.remote_bin_for("codex", "codex") == "codex"
    assert config.remote_bin_for("opencode", None) is None
