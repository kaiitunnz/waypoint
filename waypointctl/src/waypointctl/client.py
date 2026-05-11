import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from waypointctl.paths import waypoint_log_path, waypoint_pid_path, waypoint_socket_path
from waypointctl.process import is_pid_running, read_pid_file, remove_if_stale
from waypointctl.protocol import DaemonRequest, DaemonResult

DAEMON_START_TIMEOUT_SECONDS = 15.0
DAEMON_POLL_INTERVAL_SECONDS = 0.2

LogFn = Callable[[str, str], None]


class DaemonUnavailableError(RuntimeError):
    pass


class DaemonClient:
    def __init__(self, home: Path) -> None:
        self.home = home

    def request(
        self,
        command: str,
        args: list[str],
        log: LogFn | None = None,
    ) -> DaemonResult:
        on_log = log or (lambda _stream, _line: None)
        payload = json.dumps(DaemonRequest(command=command, args=args).to_payload())

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(DAEMON_START_TIMEOUT_SECONDS)
            sock.connect(str(waypoint_socket_path()))
            sock.sendall(payload.encode("utf-8") + b"\n")
            sock.shutdown(socket.SHUT_WR)
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


def daemon_available() -> bool:
    socket_path = waypoint_socket_path()
    if not socket_path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect(str(socket_path))
            sock.sendall(b'{"command":"ping","args":[]}\n')
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
    if daemon_available():
        return DaemonClient(home)

    _clear_stale_state()
    start_daemon(home)

    deadline = time.monotonic() + DAEMON_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if daemon_available():
            return DaemonClient(home)
        time.sleep(DAEMON_POLL_INTERVAL_SECONDS)

    raise DaemonUnavailableError("waypointd did not become ready in time")


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
