import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agent_cli_guard import AgentCliGuard
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.workspace_fs import (
    LocalWorkspaceFilesystem,
    RemoteWorkspaceFilesystem,
    WorkspaceFilesystem,
)
from waypoint.workspace_preview import DEFAULT_WORKSPACE_DENYLIST

# The test host may itself run Waypoint, whose WAYPOINT_* env vars would otherwise
# override a test's config file (notably data_dir) or stamp a spawner/sender.
_HOST_WAYPOINT_ENV = (
    "WAYPOINT_DATA_DIR",
    "WAYPOINT_CONFIG_PATH",
    "WAYPOINT_HOST",
    "WAYPOINT_PORT",
    "WAYPOINT_PASSWORD",
    "WAYPOINT_TOKEN",
    "WAYPOINT_SESSION_ID",
    "WAYPOINT_CORS_ORIGINS",
    "WAYPOINT_CORS_ORIGIN_REGEX",
)


@pytest.fixture(autouse=True)
def forbid_agent_cli_processes(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    guard = AgentCliGuard([Path(tempfile.gettempdir()), tmp_path_factory.getbasetemp()])
    guard.install(monkeypatch)
    yield
    if guard.spawned:
        pytest.fail(
            f"{request.node.nodeid} tried to start a real agent CLI: "
            f"{sorted(set(guard.spawned))}; stub the call at its plugin seam"
        )


@pytest.fixture(autouse=True)
def offline_account_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    # The runtime schedules a live verified-account probe on every
    # profile-scoped launch, import, reattach, and restore. Tests that cover
    # the probe patch this seam themselves.
    async def unverified(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr("waypoint.runtime.probe_account", unverified)


@pytest.fixture
def quiet_warming(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "waypoint.runtime.SessionRuntime._warm_command_completions",
        lambda *_, **__: None,
    )


@pytest.fixture
def isolate_waypoint_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _HOST_WAYPOINT_ENV:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def loopback_target(monkeypatch: pytest.MonkeyPatch) -> SshLaunchTargetConfig:
    """An SSH target that runs its remote commands as local subprocesses."""

    def _build(
        self: SshLaunchTargetConfig, command: list[str], *args: Any, **kwargs: Any
    ) -> tuple[str, ...]:
        assert command[0] == "python3"
        return (sys.executable, *command[1:])

    monkeypatch.setattr(SshLaunchTargetConfig, "build_remote_exec_args", _build)
    return SshLaunchTargetConfig(id="devbox", name="devbox", ssh_destination="devbox")


@pytest.fixture(params=["local", "remote"])
def workspace_fs(
    request: pytest.FixtureRequest, loopback_target: SshLaunchTargetConfig
) -> WorkspaceFilesystem:
    denylist = list(DEFAULT_WORKSPACE_DENYLIST)
    if request.param == "local":
        return LocalWorkspaceFilesystem(denylist, False)
    return RemoteWorkspaceFilesystem(loopback_target, denylist, False)
