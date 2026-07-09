"""Session-less discovery env resolution (``SessionRuntime.discovery_env``).

Discovery (models, threads, import, delete) has no ``launch_env`` to carry a
profile, so it resolves one from a ``(backend, launch_target, profile)`` triple
through the same overlay the launch path uses — so discovery and launch can
never disagree on the config dir.
"""

import os

import pytest
from fastapi import HTTPException

from waypoint.runtime import SessionRuntime
from waypoint.settings import Settings
from waypoint.storage import Storage


def _runtime(tmp_path) -> SessionRuntime:
    settings = Settings(
        data_dir=tmp_path / "data",
        plugin_configs={
            "codex": {
                "account_profiles": {
                    "personal": {
                        "label": "Personal",
                        "config_dir": "~/.codex-personal",
                    },
                    "abs": {"label": "Abs", "config_dir": "/srv/codex-abs"},
                }
            }
        },
    )
    settings.ensure_dirs()
    return SessionRuntime(settings, Storage(settings.database_path))


@pytest.mark.asyncio
async def test_discovery_env_none_profile_is_default_store(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    env = await runtime.discovery_env("codex", None, None)
    # Back-compat: no profile ⇒ the default lookup env, which never pins a
    # profile-scoped CODEX_HOME (only what the ambient process/env already set).
    assert env.get("CODEX_HOME") == os.environ.get("CODEX_HOME")


@pytest.mark.asyncio
async def test_discovery_env_overlays_profile_config_dir(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    env = await runtime.discovery_env("codex", None, "personal")
    assert env["CODEX_HOME"] == os.path.expanduser("~/.codex-personal")

    abs_env = await runtime.discovery_env("codex", None, "abs")
    assert abs_env["CODEX_HOME"] == "/srv/codex-abs"


@pytest.mark.asyncio
async def test_discovery_env_matches_launch_overlay(tmp_path) -> None:
    """Discovery and launch must resolve the same config dir for the same inputs."""
    runtime = _runtime(tmp_path)
    discovery = await runtime.discovery_env("codex", None, "personal")
    launch = runtime.account_lookup_env(
        "codex", runtime._profile_launch_env("codex", "personal", None)
    )
    assert discovery == launch


@pytest.mark.asyncio
async def test_discovery_env_unknown_profile_400(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    with pytest.raises(HTTPException) as exc:
        await runtime.discovery_env("codex", None, "nope")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_discovery_env_backend_without_config_dir_env_var_400(tmp_path) -> None:
    """OpenCode has no config-dir env var, so a profile can't scope it — 400,
    matching the launch path (never a silent accept)."""
    runtime = _runtime(tmp_path)
    with pytest.raises(HTTPException) as exc:
        await runtime.discovery_env("opencode", None, "personal")
    assert exc.value.status_code == 400
