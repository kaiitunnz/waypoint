"""Resolve a remote Claude session's terminal appearance over SSH.

Runs the canonical stdlib-only classifier (``terminal_theme.py``) on the launch
target through the same ``python3 -`` mechanism the rate-limit probe uses, under
the session's launch environment so a work-profile session reads the *remote*
profile's theme rather than the Waypoint server's. Every failure mode
(unreachable host, missing interpreter, timeout, non-zero exit, malformed output)
degrades to ``UNKNOWN`` and logs only a category — never the captured stderr,
which could carry a config path.
"""

import asyncio
import importlib.resources
import json
import logging
from typing import Any

from waypoint.backends.claude_code.terminal_theme import UNKNOWN
from waypoint.launch_targets import SshLaunchTargetConfig

log = logging.getLogger("waypoint.claude_code.terminal_theme")

_PROBE_TIMEOUT_SECONDS = 3.0

# Env var the remote classifier reads for the session cwd (see terminal_theme).
# Passed through the launch env rather than a `cd` so a stale remote project dir
# cannot fail the probe.
_CWD_ENV_VAR = "WAYPOINT_TERMINAL_THEME_CWD"

_SCRIPT_BYTES: bytes | None = None


def _script_bytes() -> bytes:
    global _SCRIPT_BYTES
    if _SCRIPT_BYTES is None:
        _SCRIPT_BYTES = (
            importlib.resources.files("waypoint.backends.claude_code")
            .joinpath("terminal_theme.py")
            .read_bytes()
        )
    return _SCRIPT_BYTES


async def probe_terminal_appearance_remote(
    launch_target: SshLaunchTargetConfig,
    cwd: str | None,
    *,
    launch_env: dict[str, str] | None = None,
    timeout_seconds: float = _PROBE_TIMEOUT_SECONDS,
) -> str:
    """Return ``"light"`` / ``"dark"`` / ``"unknown"`` for a remote session.

    Never raises; any failure resolves to ``"unknown"``.
    """
    extra_env = dict(launch_env or {})
    if cwd:
        extra_env[_CWD_ENV_VAR] = cwd
    argv = launch_target.build_remote_exec_args(["python3", "-"], extra_env=extra_env)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        log.warning("remote terminal-theme probe failed to spawn ssh")
        return UNKNOWN
    try:
        stdout, _stderr = await asyncio.wait_for(
            proc.communicate(_script_bytes()), timeout=timeout_seconds
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("remote terminal-theme probe timed out")
        return UNKNOWN
    if proc.returncode != 0:
        # Deliberately omit stderr: a traceback can carry a config path.
        log.warning(
            f"remote terminal-theme probe exited non-zero (rc={proc.returncode})"
        )
        return UNKNOWN
    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        log.warning("remote terminal-theme probe produced no output")
        return UNKNOWN
    try:
        decoded: Any = json.loads(text.splitlines()[-1])
    except json.JSONDecodeError:
        log.warning("remote terminal-theme probe produced non-JSON output")
        return UNKNOWN
    appearance = decoded.get("appearance") if isinstance(decoded, dict) else None
    if appearance in ("light", "dark", "unknown"):
        return appearance
    return UNKNOWN
