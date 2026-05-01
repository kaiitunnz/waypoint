"""Plugin-agnostic SSH launch-target configuration and primitives.

Backend-specific remote-launch builders (codex App Server client, claude
CLI launch + PreToolUse hook bootstrap, claude thread enumeration) live
next to their plugin in ``backends/<id>/remote.py``. This module owns
the model and the generic SSH argv primitives only.

Per-target binary overrides (the path to ``codex`` / ``claude`` on the
remote host) live in ``remote_bins`` keyed by plugin id, which keeps
the launch target plugin-agnostic — adding a new backend doesn't
require an edit here.
"""

import re
import shlex
import shutil
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from waypoint.backends.registry import get_registry
from waypoint.schemas import BackendId

SAFE_TILDE_HEAD = re.compile(r"~[A-Za-z0-9._-]*$")


def _default_supported_backends() -> list[str]:
    """Default to every registered, non-tmux backend.

    Tmux is the wrapper plugin — it never owns a managed SSH launch
    itself, so we exclude it from the default list. Adding a new
    structured backend (Opencode, etc.) shows up here automatically.
    """
    return [plugin.id for plugin in get_registry().all() if plugin.id != "tmux"]


class SshLaunchTargetConfig(BaseModel):
    id: str
    name: str
    enabled: bool = True
    ssh_destination: str
    ssh_bin: str = "ssh"
    ssh_args: list[str] = Field(default_factory=list)
    default_cwd: str = "~"
    # Remote login shell wrapper used to run codex/claude commands so user
    # rcfiles (PATH, env vars, helpers) get sourced. `-i` (interactive) is
    # needed so that `.bashrc` runs past the standard `case $- in *i*) ;; *)
    # return;; esac` guard most distros ship — `-l` alone (login) only sources
    # `.bash_profile`, and `BASH_ENV` is also blocked by that guard. Set to an
    # empty string to skip wrapping and run the command via sshd's default
    # shell. NB: any `.bashrc` line that writes to stdout would corrupt
    # codex/claude's stream protocols, so keep rcfile output on stderr only.
    remote_shell: str = "bash -ilc"
    supported_backends: list[BackendId] = Field(
        default_factory=_default_supported_backends
    )
    config_overrides: list[str] = Field(default_factory=list)
    remote_env: dict[str, str] = Field(default_factory=dict)
    # Per-plugin remote binary overrides keyed by plugin id; e.g.
    # ``{"claude_code": "/opt/claude/bin/claude", "codex": "codex"}``. A
    # missing entry falls back to the plugin's ``capabilities.cli_binary``.
    remote_bins: dict[BackendId, str] = Field(default_factory=dict)

    @field_validator("supported_backends")
    @classmethod
    def validate_supported_backends(cls, value: list[str]) -> list[str]:
        if not value:
            return _default_supported_backends()
        deduped: list[str] = []
        for backend in value:
            if backend not in deduped:
                deduped.append(backend)
        return deduped

    def supports(self, backend: str) -> bool:
        return backend in self.supported_backends

    def resolve_default_backend(self, fallback: str) -> str:
        if fallback in self.supported_backends:
            return fallback
        return self.supported_backends[0]

    def remote_bin_for(self, plugin_id: str, default: str | None = None) -> str | None:
        """Return the remote binary path to use for ``plugin_id``.

        Falls back to ``default`` (typically the plugin's
        ``capabilities.cli_binary``) when the user hasn't pinned one
        for this target.
        """
        return self.remote_bins.get(plugin_id) or default

    def build_remote_exec_args(
        self, command: list[str], cwd: str | None = None
    ) -> tuple[str, ...]:
        """Build the SSH argv for a remote command.

        ``cwd`` is optional: when omitted (or ``None``), no ``cd`` is
        prepended and the command runs in whatever directory the remote
        login shell lands in. This is the right choice for tooling whose
        only filesystem dependency is an absolute path (e.g. the Claude
        thread enumerator reads ``$CLAUDE_CONFIG_DIR/projects/``), so a
        stale or deleted ``default_cwd`` on the SSH target can't break
        the call.
        """
        ssh_bin = _resolve_local_binary(self.ssh_bin)
        remote_parts: list[str] = []
        if cwd:
            remote_parts.extend([f"cd {quote_remote_path(cwd)}", "&&"])
        remote_parts.append("exec")
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


def quote_remote_path(path: str) -> str:
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
