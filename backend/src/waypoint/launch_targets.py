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

import asyncio
import re
import shlex
import shutil
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

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
    # SSH authentication method. ``key`` (the default) delegates entirely to
    # the local ``ssh`` binary's key/agent discovery, exactly as before.
    # ``password`` opts the target into UI-prompted password auth: the password
    # is used once to seed a multiplexed ControlMaster connection (see
    # ``ssh_master.py``) and never stored. Password auth therefore requires the
    # ``ssh_args`` to enable connection multiplexing so every later call can
    # reuse the authenticated socket without a password.
    ssh_auth: Literal["key", "password"] = "key"
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

    @model_validator(mode="after")
    def _require_multiplexing_for_password(self) -> Self:
        """Password auth reuses one ControlMaster socket, so the target must
        configure connection multiplexing. Fail loud at config load (matching
        ``_dispatch_plugin_configs``) rather than discovering it at connect time.
        """
        if self.ssh_auth != "password":
            return self
        options = self._parsed_ssh_options()
        missing: list[str] = []
        if options.get("controlmaster", "").lower() not in {"auto", "yes"}:
            missing.append("ControlMaster=auto")
        if not options.get("controlpath"):
            missing.append("ControlPath=<path>")
        if not options.get("controlpersist"):
            missing.append("ControlPersist=<duration>")
        if missing:
            raise ValueError(
                f"ssh target {self.id!r} uses password auth and must configure "
                f"connection multiplexing; missing ssh_args: {', '.join(missing)}"
            )
        return self

    def _parsed_ssh_options(self) -> dict[str, str]:
        """Extract ``-o KEY=VALUE`` options from ``ssh_args`` keyed by lowercased
        name. Accepts both the split (``["-o", "ControlMaster=auto"]``) and
        glued (``["-oControlMaster=auto"]``) forms OpenSSH allows.
        """
        options: dict[str, str] = {}
        args = iter(self.ssh_args)
        for arg in args:
            spec: str | None = None
            if arg == "-o":
                spec = next(args, None)
            elif arg.startswith("-o") and len(arg) > 2:
                spec = arg[2:]
            if not spec or "=" not in spec:
                continue
            key, _, value = spec.partition("=")
            options[key.strip().lower()] = value.strip()
        return options

    @property
    def requires_password(self) -> bool:
        return self.ssh_auth == "password"

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
        self,
        command: list[str],
        cwd: str | None = None,
        *,
        allocate_tty: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> tuple[str, ...]:
        """Build the SSH argv for a remote command.

        ``cwd`` is optional: when omitted (or ``None``), no ``cd`` is
        prepended and the command runs in whatever directory the remote
        login shell lands in. This is the right choice for tooling whose
        only filesystem dependency is an absolute path (e.g. the Claude
        thread enumerator reads ``$CLAUDE_CONFIG_DIR/projects/``), so a
        stale or deleted ``default_cwd`` on the SSH target can't break
        the call.

        ``allocate_tty`` forces SSH to request a remote PTY (``-tt``).
        Required for interactive CLIs that detect TTY-ness on stdin —
        ``claude`` falls back to its non-interactive ``--print`` path
        when stdin is a pipe, immediately errors out, and ``bash -ilc``
        prints ``cannot set terminal process group`` warnings. Default
        off because most callers (the rate-limit ``python3 -`` probe,
        the codex App Server, opencode's HTTP launcher, the claude
        thread enumerator) need stdin as a real pipe.
        """
        ssh_bin = _resolve_local_binary(self.ssh_bin)
        # Target-wide remote_env is the base. Call-site extra_env can include
        # runtime-required values (e.g. WAYPOINT_SESSION_ID), so it must win
        # on collisions.
        merged_env = {**self.remote_env, **(extra_env or {})}
        remote_parts: list[str] = []
        if cwd:
            remote_parts.extend([f"cd {quote_remote_path(cwd)}", "&&"])
        remote_parts.append("exec")
        if merged_env:
            remote_parts.append("env")
            for key, value in sorted(merged_env.items()):
                remote_parts.append(shlex.quote(f"{key}={value}"))
        remote_parts.append(shlex.join(command))
        remote_command = self.wrap_remote_command(" ".join(remote_parts))
        # Double ``-tt`` forces PTY allocation even when SSH's own stdin
        # isn't a TTY (we're launched from inside a tmux pane via
        # ``new-session``, which gives ssh a pipe). A single ``-t`` would
        # warn ``Pseudo-terminal will not be allocated because stdin is
        # not a terminal.`` and silently fall back to no-TTY.
        tty_flag: tuple[str, ...] = ("-tt",) if allocate_tty else ()
        return (
            ssh_bin,
            *self.ssh_args,
            *tty_flag,
            self.ssh_destination,
            remote_command,
        )

    def wrap_remote_command(self, command: str) -> str:
        """Wrap a remote command in the configured login-shell so rcfiles run."""
        shell = self.remote_shell.strip()
        if not shell:
            return command
        return f"{shell} {shlex.quote(command)}"

    async def ssh_capture(self, remote_cmd: str) -> str:
        """``ssh <host> <cmd>`` — returns stdout (empty on non-zero exit).

        ``ConnectTimeout`` bounds the call so a probe against an unreachable
        host (or a password-auth target whose ControlMaster has dropped) fails
        within seconds instead of stalling the caller. The default is spliced
        *after* ``ssh_args`` so an explicit ``ConnectTimeout`` there wins
        (ssh first-value-wins); absent one, this 15s default applies.
        """
        proc = await asyncio.create_subprocess_exec(
            self.ssh_bin,
            *self.ssh_args,
            "-o",
            "ConnectTimeout=15",
            self.ssh_destination,
            remote_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return ""
        return stdout.decode("utf-8", errors="ignore")


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
