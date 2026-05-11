import os
import signal
import subprocess
import time
from pathlib import Path

from waypointctl.process import is_pid_running


def spawn_detached(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return proc.pid


def terminate_group(
    pid: int,
    *,
    timeout_seconds: float = 10.0,
    poll_interval_seconds: float = 0.1,
) -> bool:
    if _is_dead(pid):
        return True

    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return True

    _signal_group(pgid, signal.SIGTERM)
    if _wait_dead(pid, timeout_seconds, poll_interval_seconds):
        return True

    _signal_group(pgid, signal.SIGKILL)
    return _wait_dead(pid, 2.0, poll_interval_seconds)


def _wait_dead(pid: int, timeout: float, poll: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_dead(pid):
            return True
        time.sleep(poll)
    return _is_dead(pid)


def _is_dead(pid: int) -> bool:
    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return not is_pid_running(pid)
    if reaped == pid:
        return True
    return False


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass
