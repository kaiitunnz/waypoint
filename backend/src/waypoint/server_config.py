import json
import re
import secrets
import shlex
import shutil
from pathlib import Path

from codex_app_server.client import AppServerClient, AppServerConfig
from pydantic import BaseModel, Field, field_validator

from waypoint.claude_cli import ClaudeLaunchSpec
from waypoint.schemas import Backend

SAFE_TILDE_HEAD = re.compile(r"~[A-Za-z0-9._-]*$")

# Placeholder that gets substituted (via sed) on the remote with the absolute
# path of the hook script after `$HOME` is resolved.
HOOK_PATH_PLACEHOLDER = "__WAYPOINT_HOOK_PATH__"


def _default_supported_backends() -> list[Backend]:
    return [Backend.CODEX, Backend.CLAUDE_CODE]


class SshLaunchTargetConfig(BaseModel):
    id: str
    name: str
    enabled: bool = True
    ssh_destination: str
    ssh_bin: str = "ssh"
    ssh_args: list[str] = Field(default_factory=list)
    codex_bin: str = "codex"
    claude_bin: str = "claude"
    default_remote_cwd: str = "~"
    # Remote login shell wrapper used to run codex/claude commands so user
    # rcfiles (PATH, env vars, helpers) get sourced. `-i` (interactive) is
    # needed so that `.bashrc` runs past the standard `case $- in *i*) ;; *)
    # return;; esac` guard most distros ship — `-l` alone (login) only sources
    # `.bash_profile`, and `BASH_ENV` is also blocked by that guard. Set to an
    # empty string to skip wrapping and run the command via sshd's default
    # shell. NB: any `.bashrc` line that writes to stdout would corrupt
    # codex/claude's stream protocols, so keep rcfile output on stderr only.
    remote_shell: str = "bash -ilc"
    supported_backends: list[Backend] = Field(
        default_factory=_default_supported_backends
    )
    config_overrides: list[str] = Field(default_factory=list)
    remote_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("supported_backends")
    @classmethod
    def validate_supported_backends(cls, value: list[Backend]) -> list[Backend]:
        if not value:
            return _default_supported_backends()
        deduped: list[Backend] = []
        for backend in value:
            if backend not in deduped:
                deduped.append(backend)
        return deduped

    def supports(self, backend: Backend) -> bool:
        return backend in self.supported_backends

    def resolve_default_backend(self, fallback: Backend) -> Backend:
        if fallback in self.supported_backends:
            return fallback
        return self.supported_backends[0]

    def build_codex_launch_args(self, remote_cwd: str) -> tuple[str, ...]:
        codex_args = [self.codex_bin]
        for override in self.config_overrides:
            codex_args.extend(["--config", override])
        codex_args.extend(["app-server", "--listen", "stdio://"])
        return self.build_remote_exec_args(codex_args, remote_cwd)

    def build_remote_exec_args(
        self, command: list[str], remote_cwd: str
    ) -> tuple[str, ...]:
        ssh_bin = _resolve_local_binary(self.ssh_bin)
        remote_parts = [f"cd {_quote_remote_path(remote_cwd)}", "&&", "exec"]
        if self.remote_env:
            remote_parts.append("env")
            for key, value in sorted(self.remote_env.items()):
                remote_parts.append(shlex.quote(f"{key}={value}"))
        remote_parts.append(shlex.join(command))
        remote_command = self.wrap_remote_command(" ".join(remote_parts))
        return (ssh_bin, *self.ssh_args, self.ssh_destination, remote_command)

    def wrap_remote_command(self, command: str) -> str:
        """Wrap a remote command in the configured login-shell so rcfiles run."""
        shell = self.remote_shell.strip()
        if not shell:
            return command
        return f"{shell} {shlex.quote(command)}"

    def remote_command_for_backend(
        self, backend: Backend, args: list[str], remote_cwd: str
    ) -> tuple[str, ...]:
        executable = (
            self.claude_bin if backend == Backend.CLAUDE_CODE else self.codex_bin
        )
        return self.build_remote_exec_args([executable, *args], remote_cwd)


def build_remote_codex_client_factory(target: SshLaunchTargetConfig):
    def factory(cwd: str, remote_cwd: str | None, approval_handler):
        launch_cwd = remote_cwd or target.default_remote_cwd
        return AppServerClient(
            config=AppServerConfig(
                launch_args_override=target.build_codex_launch_args(launch_cwd),
                client_name="waypoint",
                client_title="Waypoint",
            ),
            approval_handler=approval_handler,
        )

    return factory


def build_remote_claude_launch_factory(
    target: SshLaunchTargetConfig,
    hook_script_path: Path,
    hook_secret: str,
    local_backend_port: int,
    permission_mode: str = "default",
):
    hook_script = hook_script_path.read_text(encoding="utf-8")

    def factory(
        session_id: str, cwd: str, claude_session_id: str, resume: bool
    ) -> ClaudeLaunchSpec:
        remote_cwd = cwd or target.default_remote_cwd
        reverse_port = _random_reverse_tunnel_port()
        # Claude reads `--settings` and the hook `command` field as literal
        # filesystem paths (no tilde / shell expansion), so we build paths off
        # `$HOME` via a shell variable that bash expands before running claude.
        settings_payload = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "^(?:Bash|Edit|Write|MultiEdit|NotebookEdit|Task|WebFetch|WebSearch)$",
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
        claude_args = [
            target.claude_bin,
            "-p",
            "--input-format=stream-json",
            "--output-format=stream-json",
            "--include-hook-events",
            "--verbose",
            "--permission-mode",
            permission_mode,
        ]
        if resume:
            claude_args.extend(["--resume", claude_session_id])
        else:
            claude_args.extend(["--session-id", claude_session_id])
        remote_command = _build_remote_claude_command(
            target=target,
            remote_cwd=remote_cwd,
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


def _quote_remote_path(path: str) -> str:
    """Quote a path for the remote shell while preserving tilde expansion.

    `shlex.quote` wraps the whole string in single quotes, which suppresses
    the remote shell's tilde substitution. For paths starting with `~` (e.g.
    `~`, `~/foo`, or `~user/bar`) leave the tilde-prefix unquoted so the
    remote shell can expand it, and quote only the suffix.
    """
    if not path or not path.startswith("~"):
        return shlex.quote(path)
    head, sep, rest = path.partition("/")
    if not SAFE_TILDE_HEAD.fullmatch(head):
        return shlex.quote(path)
    if not sep:
        return head
    if not rest:
        return f"{head}/"
    return f"{head}/{shlex.quote(rest)}"


def _resolve_local_binary(binary: str) -> str:
    if "/" in binary:
        candidate = Path(binary).expanduser()
        if candidate.exists():
            return str(candidate)
        raise FileNotFoundError(f"binary not found: {candidate}")
    resolved = shutil.which(binary)
    if resolved is None:
        raise FileNotFoundError(f"binary not found on PATH: {binary}")
    return resolved


def _build_remote_claude_command(
    *,
    target: SshLaunchTargetConfig,
    remote_cwd: str,
    hook_script: str,
    settings_payload: str,
    claude_args: list[str],
    hook_secret: str,
    hook_url: str,
    session_id: str,
) -> str:
    launch_line = [
        f"cd {_quote_remote_path(remote_cwd)}",
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
