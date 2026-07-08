"""Resolution and redaction of account/config-dir profiles.

Profiles are **agent-owned**: only backends whose config subclass carries an
``account_profiles`` field (``claude_code``, ``codex``) host them — transports
(``claude_tty``, ``tmux``) and backends without a config-dir env var
(``opencode``) never do, so profiles surface under agent ids only and consumers
map transport → agent.

Global profiles come from ``Settings.plugin_config(backend)``. A launch target
may override an existing profile field-by-field by id (only the fields it sets
win; unset fields fall back to the global) and may introduce target-only ids.

Public payloads expose only ``{id, label, config_dir_key}`` — never the
``config_dir`` path, ``expected_account_key``, or ``transcript_policy``.
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from waypoint.backends.base import ConfigDirReadinessReporting
from waypoint.backends.plugin_config import AccountProfileConfig
from waypoint.backends.registry import get_registry
from waypoint.schemas import AccountProbeResult, ProfileCheck

if TYPE_CHECKING:
    from waypoint.launch_targets import SshLaunchTargetConfig
    from waypoint.runtime import SessionRuntime
    from waypoint.settings import Settings


def backend_hosts_account_profiles(settings: "Settings", backend: str) -> bool:
    """Whether ``backend``'s config model carries an ``account_profiles`` field.

    True only for the agent backends that own a config-dir env var
    (``claude_code``, ``codex``); the base config model omits the field so every
    other backend rejects an ``account_profiles`` block at parse time.
    """
    return "account_profiles" in type(settings.plugin_config(backend)).model_fields


def _profiles_of(config: Any) -> dict[str, AccountProfileConfig]:
    # ``account_profiles`` lives only on the agent config subclasses; the
    # getattr default covers configs that don't host it.
    return dict(getattr(config, "account_profiles", {}))


def _merge_profile(
    base: AccountProfileConfig, override: AccountProfileConfig
) -> AccountProfileConfig:
    """Overlay only the fields the target explicitly set onto the global base.

    Using ``model_fields_set`` (not the resolved values) so a target that omits
    ``transcript_policy`` inherits the global one rather than clobbering it with
    the field default. Re-validates so a merge can't produce an invalid state.
    """
    data = base.model_dump()
    data.update(
        {field: getattr(override, field) for field in override.model_fields_set}
    )
    return AccountProfileConfig.model_validate(data)


def resolve_account_profiles(
    settings: "Settings",
    backend: str,
    launch_target: "SshLaunchTargetConfig | None" = None,
) -> dict[str, AccountProfileConfig]:
    """Global profiles for ``backend``, with target overrides merged in by id."""
    if not backend_hosts_account_profiles(settings, backend):
        return {}
    profiles = _profiles_of(settings.plugin_config(backend))
    if launch_target is not None:
        for pid, override in _profiles_of(launch_target.plugin_config(backend)).items():
            base = profiles.get(pid)
            profiles[pid] = (
                _merge_profile(base, override) if base is not None else override
            )
    return profiles


def redacted_profile_metadata(
    settings: "Settings",
    backend: str,
    launch_target: "SshLaunchTargetConfig | None" = None,
) -> list[dict[str, str]]:
    """Public ``{id, label, config_dir_key}`` metadata for a backend's profiles.

    Returns an empty list for backends that don't host profiles. Never leaks the
    config-dir path, expected account key, or transcript policy.
    """
    config_dir_key = get_registry().get(backend).capabilities.config_dir_env_var
    if config_dir_key is None:
        return []
    profiles = resolve_account_profiles(settings, backend, launch_target)
    return [
        {"id": pid, "label": profile.label, "config_dir_key": config_dir_key}
        for pid, profile in profiles.items()
    ]


def _redact_path(path: str, show_paths: bool) -> str:
    return path if show_paths else "<hidden; pass --show-paths>"


def account_profile_static_checks(
    settings: "Settings",
    backend: str,
    profile_id: str,
    profile: AccountProfileConfig,
    *,
    local: bool,
    show_paths: bool = False,
) -> list[ProfileCheck]:
    """The server-free portion of an ``accounts doctor`` checklist for a profile.

    Pure filesystem + registry lookups — no runtime, no live probe — so both the
    ``accounts doctor`` endpoint and the root ``waypoint doctor`` (which runs
    without the server) share it. The live ``account_matches_expected`` check is
    appended separately by the runtime path, which owns the probe.

    ``local`` is ``False`` for a remote launch target, where the config dir can't
    be stat'd here; filesystem checks are then reported as skipped rather than
    failed (remote diagnostics are a later phase). Config-dir paths surface in
    details only when ``show_paths`` is set.
    """
    plugin = get_registry().get(backend)
    caps = plugin.capabilities
    config_dir = profile.config_dir
    checks: list[ProfileCheck] = []

    checks.append(
        ProfileCheck(
            name="supported",
            ok=bool(caps.config_dir_env_var),
            detail=(
                f"hosts account profiles via {caps.config_dir_env_var}; live "
                "switching also needs a restart-capable transport"
                if caps.config_dir_env_var
                else "backend has no config-dir env var"
            ),
        )
    )

    if not local:
        for name in ("config_dir_exists", "ready", "transcript_setup"):
            checks.append(
                ProfileCheck(
                    name=name,
                    ok=True,
                    detail="skipped: unsupported on a remote launch target",
                )
            )
        return checks

    expanded = Path(config_dir).expanduser()
    checks.append(
        ProfileCheck(
            name="config_dir_exists",
            ok=expanded.is_dir(),
            detail=(
                f"config dir {_redact_path(str(expanded), show_paths)} "
                + ("exists" if expanded.is_dir() else "is missing")
            ),
        )
    )

    if isinstance(plugin, ConfigDirReadinessReporting):
        readiness = plugin.config_dir_readiness(config_dir)
        checks.append(
            ProfileCheck(
                name="ready",
                ok=readiness.ready,
                detail=readiness.reason if not readiness.ready else "ready",
            )
        )
    else:
        checks.append(
            ProfileCheck(
                name="ready",
                ok=True,
                detail="n/a: agent reports no readiness signal",
            )
        )

    checks.append(
        _transcript_setup_check(
            caps.native_thread_store, profile, show_paths=show_paths
        )
    )
    return checks


def _transcript_setup_check(
    native_thread_store: str | None,
    profile: AccountProfileConfig,
    *,
    show_paths: bool,
) -> ProfileCheck:
    name = "transcript_setup"
    if profile.transcript_policy != "symlink_shared":
        return ProfileCheck(
            name=name,
            ok=True,
            detail=f"n/a: transcript_policy is {profile.transcript_policy!r}",
        )
    shared = profile.shared_transcript_dir
    if not shared:
        return ProfileCheck(
            name=name, ok=False, detail="symlink_shared requires shared_transcript_dir"
        )
    shared_path = Path(shared).expanduser()
    if not shared_path.is_dir():
        return ProfileCheck(
            name=name,
            ok=False,
            detail=(
                f"shared_transcript_dir {_redact_path(str(shared_path), show_paths)} "
                "is missing; run 'waypoint accounts setup-transcripts'"
            ),
        )
    if native_thread_store is None:
        return ProfileCheck(
            name=name, ok=False, detail="backend has no native transcript store"
        )
    store = Path(profile.config_dir).expanduser() / native_thread_store
    if not store.is_symlink():
        return ProfileCheck(
            name=name,
            ok=False,
            detail=(
                f"{_redact_path(str(store), show_paths)} is not a symlink; run "
                "'waypoint accounts setup-transcripts' to set it up"
            ),
        )
    if store.resolve() != shared_path.resolve():
        return ProfileCheck(
            name=name,
            ok=False,
            detail=(
                f"{_redact_path(str(store), show_paths)} links to "
                f"{_redact_path(os.readlink(store), show_paths)}, not "
                f"{_redact_path(str(shared_path), show_paths)}"
            ),
        )
    return ProfileCheck(
        name=name,
        ok=True,
        detail=f"linked to {_redact_path(str(shared_path), show_paths)}",
    )


async def probe_account(
    runtime: "SessionRuntime",
    backend: str,
    launch_env: dict[str, str],
    *,
    launch_target: "SshLaunchTargetConfig | None" = None,
    cwd: str = ".",
) -> AccountProbeResult | None:
    """Identify the account a ``backend`` authenticates as under ``launch_env``.

    Composes the account rate-limit probe (run with the target ``launch_env`` so
    it authenticates as that config dir's account, ``force`` to bypass any TTL
    cache) with the plugin's ``rate_limit_account`` mapping. Returns ``None``
    when the backend can't probe or can't produce a stable account key — the
    runtime treats that as "cannot verify" and refuses a switch. Dispatches
    through the registry; no per-backend branching.
    """
    plugin = get_registry().get(backend)
    probe = getattr(plugin, "probe_account_rate_limit", None)
    account_of = getattr(plugin, "rate_limit_account", None)
    if probe is None or account_of is None:
        return None
    snapshot = await probe(
        runtime, launch_target, cwd=cwd, launch_env=launch_env, force=True
    )
    if snapshot is None:
        return None
    account = account_of(snapshot)
    if account is None:
        return None
    key, label = account
    return AccountProbeResult(account_key=key, account_label=label)
