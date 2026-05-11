import argparse
import json
import os
import signal
import socketserver
from pathlib import Path
from typing import cast

from waypointctl.paths import (
    resolve_waypoint_home,
    state_log_dir,
    state_run_dir,
    waypoint_pid_path,
    waypoint_socket_path,
)
from waypointctl.process import write_pid_file
from waypointctl.protocol import DaemonRequest, DaemonResponse
from waypointctl.supervisor import WaypointSupervisor


class WaypointDaemonServer(socketserver.UnixStreamServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, socket_path: Path, home: Path) -> None:
        self.home = home
        self.supervisor = WaypointSupervisor(home)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)
        super().__init__(str(socket_path), WaypointDaemonHandler)


class WaypointDaemonHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline().decode("utf-8").strip()
        if not raw:
            return
        try:
            payload = json.loads(raw)
            request = DaemonRequest(
                command=str(payload["command"]),
                args=[str(item) for item in payload.get("args", [])],
            )
        except Exception as exc:  # noqa: BLE001
            self._write_response(DaemonResponse(ok=False, returncode=1, error=str(exc)))
            return

        try:
            response = self._dispatch(request)
        except Exception as exc:  # noqa: BLE001
            response = DaemonResponse(ok=False, returncode=1, error=str(exc))
        self._write_response(response)

    def _dispatch(self, request: DaemonRequest) -> DaemonResponse:
        if request.command == "ping":
            return DaemonResponse(ok=True)

        supervisor = cast(WaypointDaemonServer, self.server).supervisor
        if request.command not in {"start", "stop", "restart", "status"}:
            return DaemonResponse(
                ok=False,
                returncode=2,
                error=f"unknown command: {request.command}",
            )

        completed = supervisor.run(request.command, request.args)
        return DaemonResponse(
            ok=completed.returncode == 0,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            error=None if completed.returncode == 0 else "command failed",
        )

    def _write_response(self, response: DaemonResponse) -> None:
        self.wfile.write(json.dumps(response.to_payload()).encode("utf-8") + b"\n")
        self.wfile.flush()


def serve(home: Path) -> None:
    state_run_dir().mkdir(parents=True, exist_ok=True)
    state_log_dir().mkdir(parents=True, exist_ok=True)
    pid_path = waypoint_pid_path()
    socket_path = waypoint_socket_path()

    server = WaypointDaemonServer(socket_path, home)
    write_pid_file(pid_path, os.getpid())

    def _shutdown(_signum: int, _frame: object) -> None:
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        socket_path.unlink(missing_ok=True)
        pid_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(prog="waypointd")
    parser.add_argument("--home", default=None)
    args = parser.parse_args()
    home = resolve_waypoint_home(args.home)
    serve(home)


if __name__ == "__main__":
    main()
