import sys
from typing import Any

import pytest

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
