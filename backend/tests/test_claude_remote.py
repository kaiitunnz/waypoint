from waypoint.backends.claude_code.remote import (
    build_remote_claude_launch_factory,
    build_remote_thread_enumeration_args,
)
from waypoint.launch_targets import SshLaunchTargetConfig


def test_remote_claude_launch_factory_uses_stdio_permission_protocol(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        remote_env={"OPENAI_API_KEY": "sk-test"},
        plugin_configs={"claude_code": {"remote_bin": "/opt/claude/bin/claude"}},
    )

    factory = build_remote_claude_launch_factory(config)
    launch = factory(
        "claude-sess",
        "~/workspace",
        "claude-uuid",
        True,
        "plan",
        None,
        None,
        [],
        None,
    )

    assert launch.cwd is None
    assert launch.env is None
    # SSH-layer liveness probes (~3 min detection floor) sit immediately after
    # the binary, before user ssh_args, so first-value-wins keeps them
    # authoritative against any user override. No reverse tunnel: tool approval
    # now rides the session's stdio stream, not an HTTP hook.
    assert launch.args[:8] == [
        "/usr/bin/ssh",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=6",
        "dev@example.com",
    ]
    assert "-R" not in launch.args
    assert "ExitOnForwardFailure=yes" not in launch.args
    remote_command = launch.args[-1]
    # No PreToolUse hook bootstrap is shipped anymore.
    assert "claude_pretool_hook.py" not in remote_command
    assert "claude_settings.json" not in remote_command
    assert "WAYPOINT_HOOK" not in remote_command
    assert "WAYPOINT_DIR" not in remote_command
    assert "--settings" not in remote_command
    # Approval rides the stdio control protocol; workflows are enabled.
    assert "--permission-prompt-tool stdio" in remote_command
    assert "CLAUDE_CODE_WORKFLOWS=1" in remote_command
    assert "OPENAI_API_KEY=sk-test" in remote_command
    assert "/opt/claude/bin/claude -p" in remote_command
    assert "--resume claude-uuid" in remote_command
    assert "--permission-mode plan" in remote_command
    # Without an explicit model the remote command must not carry --model.
    assert "--model" not in remote_command


def test_remote_claude_launch_factory_appends_model_flag(monkeypatch) -> None:
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        plugin_configs={"claude_code": {"remote_bin": "/opt/claude/bin/claude"}},
    )

    factory = build_remote_claude_launch_factory(config)
    launch = factory(
        "claude-sess",
        "~/workspace",
        "claude-uuid",
        False,
        "default",
        "opus",
        None,
        [],
        None,
    )
    assert "--model opus" in launch.args[-1]


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
