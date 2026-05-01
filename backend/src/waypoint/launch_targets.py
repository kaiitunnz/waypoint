"""Plugin-agnostic SSH launch-target configuration and primitives.

Backend-specific remote-launch builders (codex App Server client, claude
CLI launch + PreToolUse hook bootstrap, claude thread enumeration) live
next to their plugin in ``backends/<id>/remote.py``. This module owns
the model and the generic SSH argv primitives only.

Per-target per-plugin configuration (which plugins this target
supports, the remote binary path for each, any plugin-specific knobs
like Codex's ``--config`` overrides) lives in a single
``plugin_configs`` mapping keyed by plugin id, mirroring
``Settings.plugin_configs``. Each entry is validated against the
plugin's ``launch_target_schema`` so adding a per-target knob to a
new plugin doesn't require editing this module.
"""

import re
import shlex
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from waypoint.backends.plugin_config import PluginLaunchTargetConfig
from waypoint.backends.registry import get_registry
from waypoint.schemas import BackendId

SAFE_TILDE_HEAD = re.compile(r"~[A-Za-z0-9._-]*$")


def _default_supported_backends() -> list[str]:
    """Plugin ids implicitly supported when ``plugin_configs`` is empty.

    Managed-launch fallback wrappers (capabilities flag
    ``is_fallback_for_managed_launch``) never own a managed SSH
    launch themselves — they exist to wrap a real backend's CLI in a
    tmux pane. Excluding them keeps the picker focused on real coding
    agents and lets new structured backends show up here
    automatically.
    """
    return [
        plugin.id
        for plugin in get_registry().all()
        if not plugin.capabilities.is_fallback_for_managed_launch
    ]


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
    remote_env: dict[str, str] = Field(default_factory=dict)
    # Per-plugin per-target config blocks keyed by plugin id. Presence
    # of a key means "this target supports the plugin"; an empty
    # mapping means "supports every non-fallback registered plugin
    # with defaults" so a minimal target spec (just ``ssh_destination``)
    # Just Works. Each raw YAML block is dispatched at validation time
    # to the plugin's ``launch_target_schema`` so subclass fields (e.g.
    # codex's ``config_overrides``) survive ``extra="forbid"``.
    plugin_configs: dict[BackendId, PluginLaunchTargetConfig] = Field(
        default_factory=dict
    )

    @field_validator("plugin_configs", mode="before")
    @classmethod
    def _dispatch_plugin_configs(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        registry = get_registry()
        dispatched: dict[str, PluginLaunchTargetConfig] = {}
        for plugin_id, raw in value.items():
            if not isinstance(plugin_id, str) or not registry.has_backend(plugin_id):
                raise ValueError(f"unknown backend: {plugin_id!r}")
            schema = registry.get(plugin_id).launch_target_schema
            dispatched[plugin_id] = (
                raw if isinstance(raw, schema) else schema.model_validate(raw or {})
            )
        return dispatched

    def supported_plugins(self) -> list[str]:
        """Plugin ids this target accepts managed launches for.

        Empty ``plugin_configs`` falls back to every non-fallback
        registered plugin (the zero-config default). Otherwise the
        explicit list of keys.
        """
        if not self.plugin_configs:
            return _default_supported_backends()
        return list(self.plugin_configs)

    def supports(self, plugin_id: str) -> bool:
        return plugin_id in self.supported_plugins()

    def resolve_default_backend(self, fallback: str) -> str:
        supported = self.supported_plugins()
        if fallback in supported:
            return fallback
        return supported[0] if supported else fallback

    def plugin_config(self, plugin_id: str) -> PluginLaunchTargetConfig:
        """Return the validated per-plugin config for this target.

        Falls back to a default-constructed instance of the plugin's
        ``launch_target_schema`` when the user hasn't supplied a
        block in ``waypoint.yaml`` (i.e. the plugin is implicitly
        supported via the empty-``plugin_configs`` default).
        """
        cfg = self.plugin_configs.get(plugin_id)
        if cfg is not None:
            return cfg
        return get_registry().get(plugin_id).launch_target_schema()

    def remote_bin_for(self, plugin_id: str, default: str | None = None) -> str | None:
        """Convenience for the common case: get the remote binary path
        for ``plugin_id`` on this target, falling back to ``default``
        (typically the plugin's ``capabilities.cli_binary``).
        """
        return self.plugin_config(plugin_id).remote_bin or default

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
