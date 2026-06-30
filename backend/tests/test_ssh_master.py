"""Unit tests for the password-auth ControlMaster manager.

These never spawn a real ``ssh``; ``asyncio.create_subprocess_exec`` is
monkeypatched so the argv and environment the manager builds can be asserted
directly.
"""

import os
from pathlib import Path
from typing import Any

from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.ssh_askpass import PASSWORD_ENV_VAR
from waypoint.ssh_master import _ASKPASS_HELPER_PATH, SshMasterManager

_MULTIPLEX_ARGS = [
    "-o",
    "ControlMaster=auto",
    "-o",
    "ControlPath=~/.ssh/cm-%C",
    "-o",
    "ControlPersist=600s",
]


def _password_target() -> SshLaunchTargetConfig:
    return SshLaunchTargetConfig(
        id="pw",
        name="pw",
        ssh_destination="host",
        ssh_auth="password",
        ssh_args=_MULTIPLEX_ARGS,
    )


class _FakeProc:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""

    async def wait(self) -> int:
        return self.returncode


class _Recorder:
    """Stateful ``create_subprocess_exec`` stand-in.

    The master is reported absent on the first ``-O check`` and present after
    the seed runs, mirroring a successful connect.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._seeded = False

    async def __call__(self, *argv: str, **kwargs: Any) -> _FakeProc:
        self.calls.append({"argv": argv, "env": kwargs.get("env")})
        if "-O" in argv and "check" in argv:
            return _FakeProc(0 if self._seeded else 1)
        if "ControlMaster=yes" in argv:
            self._seeded = True
            return _FakeProc(0)
        return _FakeProc(0)


def _patch(monkeypatch, recorder: _Recorder) -> None:
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    monkeypatch.setattr("waypoint.ssh_master.asyncio.create_subprocess_exec", recorder)


async def test_connect_builds_seed_argv_and_env(monkeypatch) -> None:
    recorder = _Recorder()
    _patch(monkeypatch, recorder)
    target = _password_target()

    status = await SshMasterManager().connect(target, "s3cret")

    assert status.connected is True
    seed = next(c for c in recorder.calls if "ControlMaster=yes" in c["argv"])
    argv = seed["argv"]
    assert argv[0] == "/usr/bin/ssh"
    for token in _MULTIPLEX_ARGS:
        assert token in argv
    assert "-N" in argv and "-f" in argv
    assert argv[-1] == "host"
    # Auth methods must NOT be whitelisted, or a key-authenticated hop (e.g. a
    # key dest behind a password ProxyJump) would be unable to offer its key.
    assert not any("PreferredAuthentications" in token for token in argv)
    assert not any("PubkeyAuthentication=no" in token for token in argv)
    env = seed["env"]
    assert env["SSH_ASKPASS"] == str(_ASKPASS_HELPER_PATH)
    assert env["SSH_ASKPASS_REQUIRE"] == "force"
    assert env[PASSWORD_ENV_VAR] == "s3cret"
    # The password must never leak into the parent process environment.
    assert PASSWORD_ENV_VAR not in os.environ


async def test_connect_idempotent_when_already_live(monkeypatch) -> None:
    recorder = _Recorder()
    recorder._seeded = True  # master already up
    _patch(monkeypatch, recorder)

    status = await SshMasterManager().connect(_password_target(), "pw")

    assert status.connected is True
    assert not any("ControlMaster=yes" in c["argv"] for c in recorder.calls)


async def test_disconnect_issues_control_exit(monkeypatch) -> None:
    recorder = _Recorder()
    _patch(monkeypatch, recorder)

    await SshMasterManager().disconnect(_password_target())

    exit_call = recorder.calls[-1]["argv"]
    assert "-O" in exit_call and "exit" in exit_call


def test_askpass_helper_is_executable() -> None:
    assert Path(_ASKPASS_HELPER_PATH).exists()
    assert os.access(_ASKPASS_HELPER_PATH, os.X_OK)
