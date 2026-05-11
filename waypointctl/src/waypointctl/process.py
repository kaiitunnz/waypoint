from __future__ import annotations

import os
import signal
import time
from pathlib import Path


def is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_pid_file(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not raw.isdigit():
        return None
    return int(raw)


def write_pid_file(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def remove_if_stale(path: Path) -> None:
    pid = read_pid_file(path)
    if pid is None:
        return
    if not is_pid_running(pid):
        path.unlink(missing_ok=True)


def terminate_pid(pid: int, *, timeout_seconds: float = 10.0) -> None:
    if not is_pid_running(pid):
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and is_pid_running(pid):
        time.sleep(0.1)
    if is_pid_running(pid):
        os.kill(pid, 9)
