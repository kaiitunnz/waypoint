"""Claude-specific helpers for launching the CLI and enumerating remote
transcripts over SSH.

Lives next to ``adapter.py`` and ``threads_remote.py`` so the
PreToolUse hook bootstrap (which has to ship a Python script + a JSON
settings blob to the remote in addition to the bare ``claude``
invocation) doesn't leak into ``launch_targets.py``.
"""

import json
import secrets
import shlex
from pathlib import Path

from waypoint.backends.claude_code.adapter import ClaudeLaunchSpec, LaunchFactory
from waypoint.backends.claude_code.runtime_hook import GATED_TOOLS_REGEX
from waypoint.launch_targets import (
    SshLaunchTargetConfig,
    _resolve_local_binary,
    quote_remote_path,
)

CLAUDE_PLUGIN_ID = "claude_code"
CLAUDE_DEFAULT_BIN = "claude"

# Placeholder that gets substituted (via sed) on the remote with the
# absolute path of the hook script after `$HOME` is resolved.
HOOK_PATH_PLACEHOLDER = "__WAYPOINT_HOOK_PATH__"


def build_remote_claude_launch_factory(
    target: SshLaunchTargetConfig,
    *,
    hook_script_path: Path,
    hook_secret: str,
    local_backend_port: int,
) -> LaunchFactory:
    hook_script = hook_script_path.read_text(encoding="utf-8")

    def factory(
        session_id: str,
        cwd: str,
        claude_session_id: str,
        resume: bool,
        cli_mode: str,
        model: str | None = None,
        effort: str | None = None,
        custom_args: list[str] | None = None,
    ) -> ClaudeLaunchSpec:
        cwd = cwd or target.default_cwd
        reverse_port = _random_reverse_tunnel_port()
        # Claude reads `--settings` and the hook `command` field as
        # literal filesystem paths (no tilde / shell expansion), so we
        # build paths off `$HOME` via a shell variable that bash expands
        # before running claude.
        settings_payload = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": GATED_TOOLS_REGEX,
                        "hooks": [
                            {
                                "type": "command",
                                "command": HOOK_PATH_PLACEHOLDER,
                            }
                        ],
                    }
                ]
            }
        }
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
            "--permission-mode",
            cli_mode,
        ]
        if model:
            claude_args.extend(["--model", model])
        if effort:
            claude_args.extend(["--effort", effort])
        if resume:
            claude_args.extend(["--resume", claude_session_id])
        else:
            claude_args.extend(["--session-id", claude_session_id])
        if custom_args:
            claude_args.extend(custom_args)
        remote_command = _build_remote_claude_command(
            target=target,
            cwd=cwd,
            hook_script=hook_script,
            settings_payload=json.dumps(settings_payload, indent=2),
            claude_args=claude_args,
            hook_secret=hook_secret,
            hook_url=f"http://127.0.0.1:{reverse_port}",
            session_id=session_id,
        )
        args = [
            _resolve_local_binary(target.ssh_bin),
            *target.ssh_args,
            "-o",
            "ExitOnForwardFailure=yes",
            "-R",
            f"{reverse_port}:127.0.0.1:{local_backend_port}",
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
    hook_script: str,
    settings_payload: str,
    claude_args: list[str],
    hook_secret: str,
    hook_url: str,
    session_id: str,
) -> str:
    launch_line = [
        f"cd {quote_remote_path(cwd)}",
        "&&",
        "exec",
        "env",
    ]
    combined_env = {
        **target.remote_env,
        "WAYPOINT_HOOK_URL": hook_url,
        "WAYPOINT_HOOK_SECRET": hook_secret,
        "WAYPOINT_SESSION_ID": session_id,
    }
    for key, value in sorted(combined_env.items()):
        launch_line.append(shlex.quote(f"{key}={value}"))
    # Claude doesn't expand `~` in --settings, so pass an absolute path that
    # bash interpolates from the WAYPOINT_DIR variable set earlier in the
    # script.
    launch_line.append(shlex.join(claude_args))
    launch_line.append('--settings "$WAYPOINT_DIR/claude_settings.json"')
    waypoint_dir_assign = f'WAYPOINT_DIR="$HOME/.waypoint/claude/{session_id}"'
    remote_parts = [
        waypoint_dir_assign,
        'mkdir -p "$WAYPOINT_DIR"',
        _render_remote_file_write(
            '"$WAYPOINT_DIR/claude_pretool_hook.py"', hook_script
        ),
        'chmod 755 "$WAYPOINT_DIR/claude_pretool_hook.py"',
        _render_remote_file_write(
            '"$WAYPOINT_DIR/claude_settings.json"', settings_payload
        ),
        # Substitute the hook path placeholder with an absolute path now that
        # the file is on disk and $HOME is known. `-i.bak` works on both BSD
        # and GNU sed.
        'sed -i.bak "s|'
        + HOOK_PATH_PLACEHOLDER
        + '|$WAYPOINT_DIR/claude_pretool_hook.py|g" '
        '"$WAYPOINT_DIR/claude_settings.json"',
        " ".join(launch_line),
    ]
    return target.wrap_remote_command("\n".join(remote_parts))


def _render_remote_file_write(quoted_path: str, content: str) -> str:
    """Emit a heredoc that writes `content` to `quoted_path` (already
    shell-quoted) without any expansion."""
    delimiter = f"__WAYPOINT_{secrets.token_hex(8)}__"
    return f"cat > {quoted_path} <<'{delimiter}'\n" f"{content}\n" f"{delimiter}"


def _random_reverse_tunnel_port() -> int:
    return 20000 + secrets.randbelow(30000)
