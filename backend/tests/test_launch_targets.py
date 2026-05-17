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
                "    plugin_configs:",
                "      codex:",
                "        remote_bin: /opt/codex/bin/codex",
                "        config_overrides: ['model_reasoning_effort=\"high\"']",
                "    default_cwd: ~/workspace",
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
    target = loaded.ssh_targets[0]
    assert target.id == "devbox"
    assert target.name == "Devbox"
    assert target.ssh_destination == "dev@example.com"
    assert target.ssh_args == ["-p", "2222"]
    assert target.default_cwd == "~/workspace"
    assert target.remote_env["OPENAI_API_KEY"] == "sk-test"
    assert target.supported_plugins() == ["codex"]
    codex_config = target.plugin_config("codex")
    assert codex_config.remote_bin == "/opt/codex/bin/codex"
    assert codex_config.config_overrides == ['model_reasoning_effort="high"']  # type: ignore[attr-defined]


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


def test_build_remote_exec_args_allocates_tty_when_requested(monkeypatch) -> None:
    """tmux-wrapped CLIs need a remote PTY: without it, ``claude`` flips
    to ``--print`` mode and errors on the missing stdin, and ``bash -ilc``
    warns about no job control. ``-tt`` forces allocation even though
    SSH's own stdin (a tmux pane pipe) isn't a terminal."""
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        ssh_args=["-o", "ConnectTimeout=5"],
    )

    args = config.build_remote_exec_args(
        ["claude", "--session-id", "abc"], "~/workspace", allocate_tty=True
    )

    assert args == (
        "/usr/bin/ssh",
        "-o",
        "ConnectTimeout=5",
        "-tt",
        "dev@example.com",
        args[-1],
    )

    no_tty = config.build_remote_exec_args(["claude"], "~/workspace")
    assert "-tt" not in no_tty


def test_build_remote_exec_args_merges_extra_env_with_target_env(monkeypatch) -> None:
    """Plugin-contributed env (e.g. claude's CLAUDE_CODE_NO_FLICKER) merges
    with the target's user-configured ``remote_env``; on key collision the
    target wins so explicit yaml config still overrides plugin defaults."""
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        remote_env={"FOO": "from-target", "TARGET_ONLY": "1"},
    )

    args = config.build_remote_exec_args(
        ["claude"],
        "~/workspace",
        extra_env={"FOO": "from-plugin", "PLUGIN_ONLY": "1"},
    )

    remote_command = args[-1]
    assert "FOO=from-target" in remote_command
    assert "FOO=from-plugin" not in remote_command
    assert "TARGET_ONLY=1" in remote_command
    assert "PLUGIN_ONLY=1" in remote_command


def test_remote_bin_for_falls_back_to_default(monkeypatch) -> None:
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        plugin_configs={"claude_code": {"remote_bin": "/opt/claude/bin/claude"}},
    )

    assert config.remote_bin_for("claude_code", "claude") == "/opt/claude/bin/claude"
    assert config.remote_bin_for("codex", "codex") == "codex"


def test_supported_plugins_default_includes_all_non_fallback(monkeypatch) -> None:
    """Empty plugin_configs ⇒ supports every registered non-fallback
    plugin (today: codex + claude_code, but not the tmux wrapper).
    Adding a structured backend would lift it into this list
    automatically."""
    config = SshLaunchTargetConfig(
        id="devbox", name="Devbox", ssh_destination="dev@example.com"
    )
    plugins = config.supported_plugins()
    assert "codex" in plugins
    assert "claude_code" in plugins
    assert "tmux" not in plugins  # fallback wrapper
    assert config.supports("codex") and config.supports("claude_code")
    assert not config.supports("tmux")


def test_supported_plugins_explicit_list_narrows(monkeypatch) -> None:
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        plugin_configs={"codex": {}},
    )
    assert config.supported_plugins() == ["codex"]
    assert config.supports("codex")
    assert not config.supports("claude_code")


def test_resolve_default_backend_prefers_fallback_when_supported() -> None:
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        plugin_configs={"codex": {}, "claude_code": {}},
    )
    assert config.resolve_default_backend("codex") == "codex"
    assert config.resolve_default_backend("claude_code") == "claude_code"
    # Unsupported fallback drops to first explicit entry
    assert config.resolve_default_backend("opencode") == "codex"


def test_plugin_configs_validates_against_plugin_schema() -> None:
    """Codex's launch_target_schema accepts ``config_overrides``;
    claude_code's base schema doesn't, and ``extra='forbid'`` rejects
    typos so misconfigurations fail loudly at load time."""
    import pytest as pt
    from pydantic import ValidationError

    SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        plugin_configs={"codex": {"config_overrides": ['x="y"']}},
    )
    with pt.raises(ValidationError):
        SshLaunchTargetConfig(
            id="devbox",
            name="Devbox",
            ssh_destination="dev@example.com",
            # config_overrides is codex-only — claude_code rejects it.
            plugin_configs={"claude_code": {"config_overrides": ["nope"]}},
        )
