import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from waypointctl.paths import waypoint_log_path, waypoint_pid_path, waypoint_socket_path
from waypointctl.process import (
    is_pid_running,
    read_pid_file,
    remove_if_stale,
    write_pid_file,
)
from waypointctl.protocol import DaemonRequest, DaemonResponse

DAEMON_START_TIMEOUT_SECONDS = 15.0
DAEMON_POLL_INTERVAL_SECONDS = 0.2


class DaemonUnavailableError(RuntimeError):
    pass


class DaemonClient:
    def __init__(self, home: Path) -> None:
        self.home = home

    def request(self, command: str, args: list[str]) -> DaemonResponse:
        payload = json.dumps(DaemonRequest(command=command, args=args).to_payload())
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(DAEMON_START_TIMEOUT_SECONDS)
            sock.connect(str(waypoint_socket_path()))
            sock.sendall(payload.encode("utf-8") + b"\n")
            sock.shutdown(socket.SHUT_WR)
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                chunks.append(chunk)
        raw = b"".join(chunks).decode("utf-8")
        if not raw.strip():
            raise DaemonUnavailableError("daemon returned an empty response")
        return DaemonResponse.from_payload(json.loads(raw))


def ensure_daemon(home: Path) -> DaemonClient:
    socket_path = waypoint_socket_path()
    pid_path = waypoint_pid_path()
    remove_if_stale(pid_path)
    if socket_path.exists():
        client = DaemonClient(home)
        try:
            response = client.request("ping", [])
        except OSError:
            socket_path.unlink(missing_ok=True)
        else:
            if response.ok:
                return client

    pid = read_pid_file(pid_path)
    if pid is not None and not is_pid_running(pid):
        pid_path.unlink(missing_ok=True)
        socket_path.unlink(missing_ok=True)

    start_daemon(home)
    client = DaemonClient(home)
    deadline = time.monotonic() + DAEMON_START_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = client.request("ping", [])
        except (OSError, json.JSONDecodeError, DaemonUnavailableError) as exc:
            last_error = exc
            time.sleep(DAEMON_POLL_INTERVAL_SECONDS)
            continue
        if response.ok:
            return client
        last_error = DaemonUnavailableError(response.error or "daemon ping failed")
        time.sleep(DAEMON_POLL_INTERVAL_SECONDS)

    raise DaemonUnavailableError(f"waypointd did not become ready: {last_error}")


def start_daemon(home: Path) -> None:
    pid_path = waypoint_pid_path()
    log_path = waypoint_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["WAYPOINT_HOME"] = str(home)
    script = [
        sys.executable,
        "-m",
        "waypointctl.daemon",
        "--home",
        str(home),
    ]
    with log_path.open("a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            script,
            cwd=home,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    write_pid_file(pid_path, proc.pid)
    if proc.poll() is not None:
        pid_path.unlink(missing_ok=True)
        raise DaemonUnavailableError(
            f"waypointd exited immediately with code {proc.returncode}"
        )
