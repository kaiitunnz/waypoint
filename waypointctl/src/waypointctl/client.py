import fcntl
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from waypointctl.paths import (
    state_run_dir,
    waypoint_log_path,
    waypoint_pid_path,
    waypoint_socket_path,
)
from waypointctl.process import is_pid_running, read_pid_file, remove_if_stale
from waypointctl.protocol import DaemonRequest, DaemonResult

DAEMON_CONNECT_TIMEOUT_SECONDS = 5.0
DAEMON_READY_TIMEOUT_SECONDS = 15.0
DAEMON_POLL_INTERVAL_SECONDS = 0.2

LogFn = Callable[[str, str], None]


class DaemonUnavailableError(RuntimeError):
    pass


class DaemonClient:
    def __init__(self, home: Path) -> None:
        self.home = home.expanduser().resolve()

    def request(
        self,
        command: str,
        args: list[str],
        log: LogFn | None = None,
    ) -> DaemonResult:
        on_log = log or (lambda _stream, _line: None)
        payload = json.dumps(
            DaemonRequest(command=command, args=args, home=str(self.home)).to_payload()
        )

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(DAEMON_CONNECT_TIMEOUT_SECONDS)
            sock.connect(str(waypoint_socket_path()))
            sock.sendall(payload.encode("utf-8") + b"\n")
            sock.shutdown(socket.SHUT_WR)
            # The daemon controls how long the work takes; some commands
            # (start with a slow health probe) emit no traffic for tens of
            # seconds. A short per-recv timeout would falsely time out here.
            sock.settimeout(None)
            reader = sock.makefile("r", encoding="utf-8")
            return _read_frames(reader, on_log)


def _read_frames(reader, on_log: LogFn) -> DaemonResult:  # type: ignore[no-untyped-def]
    result: DaemonResult | None = None
    for raw in reader:
        line = raw.strip()
        if not line:
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DaemonUnavailableError(f"invalid frame: {line!r}") from exc
        kind = frame.get("type")
        if kind == "log":
            on_log(
                str(frame.get("stream", "stdout")),
                str(frame.get("line", "")),
            )
        elif kind == "result":
            result = DaemonResult.from_payload(frame)
    if result is None:
        raise DaemonUnavailableError("daemon closed without a result frame")
    return result


def daemon_available(home: Path | None = None) -> bool:
    socket_path = waypoint_socket_path()
    if not socket_path.exists():
        return False
    payload: dict[str, object] = {"command": "ping", "args": []}
    if home is not None:
        payload["home"] = str(Path(home).expanduser().resolve())
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(DAEMON_CONNECT_TIMEOUT_SECONDS)
            sock.connect(str(socket_path))
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            sock.shutdown(socket.SHUT_WR)
            reader = sock.makefile("r", encoding="utf-8")
            for raw in reader:
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    return False
                if frame.get("type") == "result":
                    return bool(frame.get("ok"))
    except OSError:
        return False
    return False


def ensure_daemon(home: Path) -> DaemonClient:
    if daemon_available(home):
        return DaemonClient(home)

    state_run_dir().mkdir(parents=True, exist_ok=True)
    lock_path = state_run_dir() / "waypointd.lock"
    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            if daemon_available(home):
                return DaemonClient(home)
            if not _live_daemon_pid():
                _clear_stale_state()
                start_daemon(home)
            # If a live pid existed, a peer already started the daemon;
            # fall through to the readiness wait below.
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    deadline = time.monotonic() + DAEMON_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if daemon_available(home):
            return DaemonClient(home)
        time.sleep(DAEMON_POLL_INTERVAL_SECONDS)
    raise DaemonUnavailableError("waypointd did not become ready in time")


def _live_daemon_pid() -> int | None:
    pid = read_pid_file(waypoint_pid_path())
    if pid is None or not is_pid_running(pid):
        return None
    return pid


def _clear_stale_state() -> None:
    socket_path = waypoint_socket_path()
    pid_path = waypoint_pid_path()
    remove_if_stale(pid_path)
    pid = read_pid_file(pid_path)
    if pid is not None and not is_pid_running(pid):
        pid_path.unlink(missing_ok=True)
        socket_path.unlink(missing_ok=True)


def start_daemon(home: Path) -> None:
    log_path = waypoint_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["WAYPOINT_HOME"] = str(home)
    argv = [sys.executable, "-m", "waypointctl.daemon", "--home", str(home)]
    with log_path.open("a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            argv,
            cwd=home,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    if proc.poll() is not None:
        raise DaemonUnavailableError(
            f"waypointd exited immediately with code {proc.returncode}"
        )
