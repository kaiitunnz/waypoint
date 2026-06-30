"""Password-authenticated SSH ControlMaster lifecycle.

The whole stack streams each agent's protocol over a non-interactive ``ssh``
subprocess, so ``ssh`` itself can never prompt for a password. Instead, a
password-auth launch target (``ssh_auth == "password"``) reuses one multiplexed
ControlMaster connection: this manager opens that master exactly once using the
UI-prompted password, fed to ``ssh`` via OpenSSH's ``SSH_ASKPASS`` mechanism
(``sshpass`` is intentionally not required). Every other SSH call on the target
already splices ``*target.ssh_args`` — which carry the same ``ControlPath`` — so
they transparently reuse the authenticated socket with no password.

The password lives only in the child process environment for the duration of
the seeding ``ssh`` invocation; it is never persisted, logged, or stored on the
manager.
"""

import asyncio
import logging
import os
from pathlib import Path

from pydantic import BaseModel

from waypoint.launch_targets import SshLaunchTargetConfig, _resolve_local_binary
from waypoint.ssh_askpass import PASSWORD_ENV_VAR

log = logging.getLogger(__name__)

_ASKPASS_HELPER_PATH = Path(__file__).with_name("ssh_askpass.py")

# Options layered on top of ``target.ssh_args`` only when seeding the master.
# ``ControlMaster=yes`` overrides the ``auto`` already in ssh_args (OpenSSH
# last-value-wins) so this invocation owns the socket; ``ControlPersist=yes``
# keeps it alive after the backgrounded ``-f -N`` process exits.
#
# Auth methods are intentionally NOT forced. The prompted password is supplied
# via SSH_ASKPASS, and each hop negotiates whatever it accepts: a ProxyJump can
# use a password (askpass-fed) while the destination uses a key, or vice versa.
# Pinning ``PreferredAuthentications=password`` here would whitelist away
# publickey and break any key-authenticated hop in the chain. ``BatchMode=no``
# keeps interactive auth (and thus askpass) enabled; ``NumberOfPasswordPrompts=1``
# makes a wrong password fail fast instead of retrying.
_SEED_OPTIONS: tuple[str, ...] = (
    "-o",
    "ControlMaster=yes",
    "-o",
    "ControlPersist=yes",
    "-o",
    "BatchMode=no",
    "-o",
    "ConnectTimeout=15",
    "-o",
    "NumberOfPasswordPrompts=1",
)


class SshMasterStatus(BaseModel):
    target_id: str
    auth: str
    connected: bool
    detail: str | None = None


class SshMasterManager:
    """Opens, checks, and tears down ControlMaster sockets for password targets."""

    def __init__(self) -> None:
        self._connected: dict[str, bool] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._ensure_helper_executable()

    @staticmethod
    def _ensure_helper_executable() -> None:
        if not _ASKPASS_HELPER_PATH.exists():
            raise RuntimeError(f"ssh askpass helper missing: {_ASKPASS_HELPER_PATH}")
        if not os.access(_ASKPASS_HELPER_PATH, os.X_OK):
            # A wheel install or lost git exec bit can strip the bit; restore it
            # best-effort and fail loud if even that is impossible.
            try:
                _ASKPASS_HELPER_PATH.chmod(0o755)
            except OSError as exc:
                raise RuntimeError(
                    f"ssh askpass helper is not executable and could not be made "
                    f"executable: {_ASKPASS_HELPER_PATH} ({exc})"
                ) from exc

    def _lock(self, target_id: str) -> asyncio.Lock:
        lock = self._locks.get(target_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[target_id] = lock
        return lock

    def is_connected_cached(self, target: SshLaunchTargetConfig) -> bool:
        """Last-known master state without spawning ``ssh``. Safe for hot paths
        like ``/api/me`` that must not block on a per-target ``-O check``."""
        return self._connected.get(target.id, False)

    async def is_connected(self, target: SshLaunchTargetConfig) -> bool:
        """Live ``ssh -O check`` against the master socket; updates the cache."""
        argv = (
            _resolve_local_binary(target.ssh_bin),
            *target.ssh_args,
            "-O",
            "check",
            target.ssh_destination,
        )
        connected = await self._run_quiet(argv)
        self._connected[target.id] = connected
        return connected

    async def connect(
        self, target: SshLaunchTargetConfig, password: str
    ) -> SshMasterStatus:
        async with self._lock(target.id):
            if await self.is_connected(target):
                return self._status(target, True)
            argv = (
                _resolve_local_binary(target.ssh_bin),
                *target.ssh_args,
                *_SEED_OPTIONS,
                "-f",
                "-N",
                target.ssh_destination,
            )
            env = {
                **os.environ,
                "SSH_ASKPASS": str(_ASKPASS_HELPER_PATH),
                "SSH_ASKPASS_REQUIRE": "force",
                "DISPLAY": os.environ.get("DISPLAY", ":0"),
                PASSWORD_ENV_VAR: password,
            }
            proc = await asyncio.create_subprocess_exec(
                *argv,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            _, stderr = await proc.communicate()
            detail = stderr.decode("utf-8", errors="ignore").strip() or None
            if proc.returncode != 0:
                log.warning(
                    "ssh master seed failed for %s (rc=%s): %s",
                    target.id,
                    proc.returncode,
                    detail,
                )
                self._connected[target.id] = False
                return self._status(target, False, detail)
            connected = await self.is_connected(target)
            return self._status(
                target,
                connected,
                None if connected else "master socket did not come up",
            )

    async def disconnect(self, target: SshLaunchTargetConfig) -> None:
        argv = (
            _resolve_local_binary(target.ssh_bin),
            *target.ssh_args,
            "-O",
            "exit",
            target.ssh_destination,
        )
        await self._run_quiet(argv)
        self._connected[target.id] = False

    async def disconnect_all(self, targets: list[SshLaunchTargetConfig]) -> None:
        for target in targets:
            if target.requires_password and self._connected.get(target.id):
                await self.disconnect(target)

    @staticmethod
    async def _run_quiet(argv: tuple[str, ...]) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            return False
        await proc.wait()
        return proc.returncode == 0

    @staticmethod
    def _status(
        target: SshLaunchTargetConfig, connected: bool, detail: str | None = None
    ) -> SshMasterStatus:
        return SshMasterStatus(
            target_id=target.id,
            auth=target.ssh_auth,
            connected=connected,
            detail=detail,
        )
