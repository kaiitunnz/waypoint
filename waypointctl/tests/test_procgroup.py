import os
import sys
import time
from pathlib import Path

import pytest

from waypointctl.process import is_pid_running
from waypointctl.procgroup import spawn_detached, terminate_group


def _wait_until(predicate, *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_spawn_detached_writes_log_and_uses_new_session(tmp_path: Path) -> None:
    log_path = tmp_path / "out.log"
    argv = [
        sys.executable,
        "-c",
        "import os, sys, time; print(os.getpgrp(), flush=True); time.sleep(30)",
    ]

    pid = spawn_detached(argv, cwd=tmp_path, env=os.environ.copy(), log_path=log_path)
    try:
        assert _wait_until(lambda: log_path.exists() and log_path.read_text().strip())
        printed_pgid = int(log_path.read_text().strip().splitlines()[0])
        assert printed_pgid == pid
        assert os.getpgid(pid) == pid
    finally:
        terminate_group(pid, timeout_seconds=2.0)


def test_terminate_group_kills_descendants(tmp_path: Path) -> None:
    log_path = tmp_path / "out.log"
    # Parent that spawns a child within the same process group.
    script = (
        "import os, subprocess, sys, time;"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        "print(child.pid, flush=True);"
        "time.sleep(60)"
    )

    pid = spawn_detached(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=os.environ.copy(),
        log_path=log_path,
    )
    try:
        assert _wait_until(lambda: log_path.exists() and log_path.read_text().strip())
        child_pid = int(log_path.read_text().strip().splitlines()[0])
        assert is_pid_running(child_pid)

        assert terminate_group(pid, timeout_seconds=2.0) is True
        assert _wait_until(lambda: not is_pid_running(pid))
        assert _wait_until(lambda: not is_pid_running(child_pid))
    finally:
        if is_pid_running(pid):
            terminate_group(pid, timeout_seconds=2.0)


def test_terminate_group_returns_true_when_pid_already_dead() -> None:
    assert terminate_group(999_999, timeout_seconds=0.5) is True


@pytest.mark.parametrize("sleep_seconds", [30])
def test_terminate_group_sigkill_on_stubborn_child(
    tmp_path: Path, sleep_seconds: int
) -> None:
    log_path = tmp_path / "out.log"
    # Ignore SIGTERM so terminate_group has to escalate to SIGKILL.
    script = (
        "import signal, time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        f"time.sleep({sleep_seconds})"
    )

    pid = spawn_detached(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=os.environ.copy(),
        log_path=log_path,
    )
    try:
        assert _wait_until(lambda: is_pid_running(pid))
        assert terminate_group(pid, timeout_seconds=0.5) is True
        assert _wait_until(lambda: not is_pid_running(pid), timeout=3.0)
    finally:
        if is_pid_running(pid):
            try:
                os.killpg(os.getpgid(pid), 9)
            except (ProcessLookupError, PermissionError):
                pass
