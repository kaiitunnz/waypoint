from pathlib import Path

from waypoint.backends.claude_code.remote import (
    build_remote_claude_launch_factory,
    build_remote_thread_enumeration_args,
)
from waypoint.launch_targets import SshLaunchTargetConfig


def test_remote_claude_launch_factory_builds_reverse_tunnel_and_hook_bootstrap(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.remote.secrets.randbelow", lambda _: 1234
    )
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        remote_env={"OPENAI_API_KEY": "sk-test"},
        plugin_configs={"claude_code": {"remote_bin": "/opt/claude/bin/claude"}},
    )
    hook_script = tmp_path / "hook.py"
    hook_script.write_text("#!/usr/bin/env python3\nprint('hook')\n", encoding="utf-8")

    factory = build_remote_claude_launch_factory(
        config,
        hook_script_path=hook_script,
        hook_secret="secret-123",
        local_backend_port=8787,
    )
    launch = factory(
        "claude-sess",
        "~/workspace",
        "claude-uuid",
        True,
        "plan",
        None,
        None,
    )

    assert launch.cwd is None
    assert launch.env is None
    assert launch.args[:6] == [
        "/usr/bin/ssh",
        "-o",
        "ExitOnForwardFailure=yes",
        "-R",
        "21234:127.0.0.1:8787",
        "dev@example.com",
    ]
    remote_command = launch.args[6]
    assert 'WAYPOINT_DIR="$HOME/.waypoint/claude/claude-sess"' in remote_command
    assert 'mkdir -p "$WAYPOINT_DIR"' in remote_command
    assert "claude_pretool_hook.py" in remote_command
    assert "claude_settings.json" in remote_command
    # The hook command path placeholder must be substituted via sed so claude
    # gets an absolute path (it does not expand `~` itself).
    assert "__WAYPOINT_HOOK_PATH__" in remote_command
    assert (
        'sed -i.bak "s|__WAYPOINT_HOOK_PATH__|$WAYPOINT_DIR/claude_pretool_hook.py|g"'
        in remote_command
    )
    assert '--settings "$WAYPOINT_DIR/claude_settings.json"' in remote_command
    assert "WAYPOINT_HOOK_URL=http://127.0.0.1:21234" in remote_command
    assert "WAYPOINT_HOOK_SECRET=secret-123" in remote_command
    assert "WAYPOINT_SESSION_ID=claude-sess" in remote_command
    assert "OPENAI_API_KEY=sk-test" in remote_command
    assert "/opt/claude/bin/claude -p" in remote_command
    assert "--resume claude-uuid" in remote_command
    assert "--permission-mode plan" in remote_command
    # Without an explicit model the remote command must not carry --model.
    assert "--model" not in remote_command


def test_remote_claude_launch_factory_appends_model_flag(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.remote.secrets.randbelow", lambda _: 1234
    )
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        plugin_configs={"claude_code": {"remote_bin": "/opt/claude/bin/claude"}},
    )
    hook_script = tmp_path / "hook.py"
    hook_script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    factory = build_remote_claude_launch_factory(
        config,
        hook_script_path=hook_script,
        hook_secret="secret-123",
        local_backend_port=8787,
    )
    launch = factory(
        "claude-sess",
        "~/workspace",
        "claude-uuid",
        False,
        "default",
        "opus",
        None,
    )
    assert "--model opus" in launch.args[6]


def test_build_remote_thread_enumeration_args_wraps_in_bash_and_passes_env(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        ssh_args=["-p", "2222"],
        default_cwd="~/workspace",
    )

    args = build_remote_thread_enumeration_args(
        config, env={"WAYPOINT_THREAD_ID": "abc-123"}
    )

    assert args[:4] == ("/usr/bin/ssh", "-p", "2222", "dev@example.com")
    remote_command = args[4]
    # Wrapped via bash -ilc so rcfiles run.
    assert remote_command.startswith("bash -ilc ")
    # The enumerator only reads $CLAUDE_CONFIG_DIR/projects (absolute),
    # so it must NOT cd into default_cwd — a stale/deleted directory on
    # the target should not be able to fail listing.
    assert "cd " not in remote_command
    # Helper script is fed via stdin (`bash -s`), so argv carries no body.
    assert "bash -s" in remote_command
    # Env override passed through to the remote shell's `env` invocation.
    assert "WAYPOINT_THREAD_ID=abc-123" in remote_command


def test_build_remote_thread_enumeration_args_ignores_default_cwd(
    monkeypatch,
) -> None:
    """Even when default_cwd is set on the target, the enumeration argv
    must not include a `cd` step. Regression for the case where a user
    deletes/renames default_cwd on the remote: listing must still work."""
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        default_cwd="/srv/long-gone-project",
    )

    args = build_remote_thread_enumeration_args(config)
    remote_command = args[2]
    assert "/srv/long-gone-project" not in remote_command
    assert "cd " not in remote_command


def test_build_remote_thread_enumeration_args_without_env(monkeypatch) -> None:
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        default_cwd="~/workspace",
    )

    args = build_remote_thread_enumeration_args(config)
    assert args[:2] == ("/usr/bin/ssh", "dev@example.com")
    remote_command = args[2]
    assert "bash -s" in remote_command
    assert "WAYPOINT_THREAD_ID" not in remote_command
