import os
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


def running_pid(path: Path) -> int | None:
    remove_if_stale(path)
    pid = read_pid_file(path)
    if pid is None or not is_pid_running(pid):
        return None
    return pid
