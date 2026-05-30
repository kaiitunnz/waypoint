"""Claude-specific helpers for launching the CLI and enumerating remote
transcripts over SSH.

Lives next to ``adapter.py`` and ``threads_remote.py`` so the SSH launch
plumbing doesn't leak into ``launch_targets.py``. Tool approval rides the
``can_use_tool`` control protocol over the CLI's stdio stream — which SSH
already tunnels — so a remote launch is just ``ssh <dest> 'cd <cwd> && exec
claude …'`` with no reverse tunnel or shipped hook script.
"""

import shlex

from waypoint.backends.claude_code.adapter import ClaudeLaunchSpec, LaunchFactory
from waypoint.launch_targets import (
    SshLaunchTargetConfig,
    _resolve_local_binary,
    quote_remote_path,
)

CLAUDE_PLUGIN_ID = "claude_code"
CLAUDE_DEFAULT_BIN = "claude"

# SSH-layer liveness probes for the session's stdio stream (which now carries
# the control protocol, including tool-approval round-trips).
# ``ServerAliveInterval=30`` + ``ServerAliveCountMax=6`` collapses a wedged
# connection into a clean ssh exit within ~3 minutes; the runtime then drives
# normal session teardown and any pending approvals are answered as
# "session terminated". Inserted immediately after the ssh binary so
# user-supplied ``ssh_args`` cannot accidentally relax them (ssh
# first-value-wins).
SSH_KEEPALIVE_ARGS: tuple[str, ...] = (
    "-o",
    "ConnectTimeout=15",
    "-o",
    "ServerAliveInterval=30",
    "-o",
    "ServerAliveCountMax=6",
)


def build_remote_claude_launch_factory(
    target: SshLaunchTargetConfig,
) -> LaunchFactory:
    def factory(
        session_id: str,
        cwd: str,
        claude_session_id: str,
        resume: bool,
        cli_mode: str,
        model: str | None = None,
        effort: str | None = None,
        custom_args: list[str] | None = None,
        fork_from_claude_session_id: str | None = None,
    ) -> ClaudeLaunchSpec:
        cwd = cwd or target.default_cwd
        claude_bin = (
            target.remote_bin_for(CLAUDE_PLUGIN_ID, CLAUDE_DEFAULT_BIN)
            or CLAUDE_DEFAULT_BIN
        )
        claude_args = [
            claude_bin,
            "-p",
            "--input-format=stream-json",
            "--output-format=stream-json",
            "--include-hook-events",
            "--verbose",
            # Route tool permission prompts over the stdio control protocol;
            # SSH tunnels stdio, so the same channel works remotely.
            "--permission-prompt-tool",
            "stdio",
            "--permission-mode",
            cli_mode,
        ]
        if model:
            claude_args.extend(["--model", model])
        if effort:
            claude_args.extend(["--effort", effort])
        if fork_from_claude_session_id:
            claude_args.extend(
                [
                    "--resume",
                    fork_from_claude_session_id,
                    "--fork-session",
                    "--session-id",
                    claude_session_id,
                ]
            )
        elif resume:
            claude_args.extend(["--resume", claude_session_id])
        else:
            claude_args.extend(["--session-id", claude_session_id])
        if custom_args:
            claude_args.extend(custom_args)
        remote_command = _build_remote_claude_command(
            target=target,
            cwd=cwd,
            claude_args=claude_args,
        )
        args = [
            _resolve_local_binary(target.ssh_bin),
            *SSH_KEEPALIVE_ARGS,
            *target.ssh_args,
            target.ssh_destination,
            remote_command,
        ]
        return ClaudeLaunchSpec(args=args)

    return factory


def build_remote_thread_enumeration_args(
    target: SshLaunchTargetConfig,
    *,
    env: dict[str, str] | None = None,
) -> tuple[str, ...]:
    """SSH argv that runs the Claude thread enumerator on a remote host.

    The helper script body is fed via subprocess stdin (`bash -s`), not
    embedded in argv, so the SSH command stays small and quoting concerns
    vanish. ``target.build_remote_exec_args`` wraps via ``bash -ilc`` so
    user rcfiles run and ``$CLAUDE_CONFIG_DIR`` resolves correctly.

    No ``cd`` is prepended: the helper only reads
    ``$CLAUDE_CONFIG_DIR/projects/`` (an absolute path), so a stale or
    deleted ``default_cwd`` on the target must not be able to fail the
    list / import-by-UUID call.
    """
    cmd = ["env"]
    for key, value in sorted((env or {}).items()):
        cmd.append(f"{key}={value}")
    cmd.extend(["bash", "-s"])
    return target.build_remote_exec_args(cmd)


def _build_remote_claude_command(
    *,
    target: SshLaunchTargetConfig,
    cwd: str,
    claude_args: list[str],
) -> str:
    launch_line = [
        f"cd {quote_remote_path(cwd)}",
        "&&",
        "exec",
        "env",
    ]
    combined_env = {
        **target.remote_env,
        # Enable the dynamic-workflow feature so the Workflow tool is available
        # and routes its approval through `can_use_tool`.
        "CLAUDE_CODE_WORKFLOWS": "1",
    }
    for key, value in sorted(combined_env.items()):
        launch_line.append(shlex.quote(f"{key}={value}"))
    launch_line.append(shlex.join(claude_args))
    return target.wrap_remote_command(" ".join(launch_line))
