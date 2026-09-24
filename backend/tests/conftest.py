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


@pytest.fixture
def loopback_target(monkeypatch: pytest.MonkeyPatch) -> SshLaunchTargetConfig:
    """An SSH target whose remote commands run on this host, unwrapped.

    Remote workspace ops still pipe the real vendored module to a real
    ``python3 -`` subprocess, so only the SSH hop itself is skipped.
    """

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
