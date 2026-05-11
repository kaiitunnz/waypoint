import argparse
import json
import os
import signal
import socketserver
import threading
from pathlib import Path
from typing import cast

from waypointctl.config import load_stack_config
from waypointctl.paths import (
    resolve_waypoint_home,
    state_log_dir,
    state_run_dir,
    waypoint_pid_path,
    waypoint_socket_path,
)
from waypointctl.process import write_pid_file
from waypointctl.protocol import DaemonLog, DaemonRequest, DaemonResult
from waypointctl.stack import WaypointStack


class WaypointDaemonServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, socket_path: Path, home: Path) -> None:
        self.home = home
        self.stack = WaypointStack(load_stack_config(home))
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
            self._send_result(DaemonResult(ok=False, returncode=1, error=str(exc)))
            return

        try:
            self._dispatch(request)
        except Exception as exc:  # noqa: BLE001
            self._send_result(DaemonResult(ok=False, returncode=1, error=str(exc)))

    def _dispatch(self, request: DaemonRequest) -> None:
        if request.command == "ping":
            self._send_result(DaemonResult(ok=True))
            return

        stack = cast(WaypointDaemonServer, self.server).stack
        write_lock = threading.Lock()

        def log(stream: str, line: str) -> None:
            with write_lock:
                self._send_log(DaemonLog(stream=stream, line=line))

        if request.command == "start":
            result = stack.start(log)
        elif request.command == "stop":
            result = stack.stop(log)
        elif request.command == "restart":
            target = request.args[0] if request.args else "all"
            result = stack.restart(target, log)
        elif request.command == "status":
            result = stack.status(log)
        else:
            self._send_result(
                DaemonResult(
                    ok=False,
                    returncode=2,
                    error=f"unknown command: {request.command}",
                )
            )
            return

        self._send_result(
            DaemonResult(
                ok=result.ok,
                returncode=0 if result.ok else 1,
                error=result.message or None,
            )
        )

    def _send_log(self, frame: DaemonLog) -> None:
        self._write_frame(frame.to_payload())

    def _send_result(self, frame: DaemonResult) -> None:
        self._write_frame(frame.to_payload())

    def _write_frame(self, payload: dict[str, object]) -> None:
        try:
            self.wfile.write((json.dumps(payload) + "\n").encode("utf-8"))
            self.wfile.flush()
        except BrokenPipeError:
            pass


def serve(home: Path) -> None:
    state_run_dir().mkdir(parents=True, exist_ok=True)
    state_log_dir().mkdir(parents=True, exist_ok=True)
    pid_path = waypoint_pid_path()
    socket_path = waypoint_socket_path()

    server = WaypointDaemonServer(socket_path, home)
    write_pid_file(pid_path, os.getpid())

    shutdown_event = threading.Event()

    def _shutdown(_signum: int, _frame: object) -> None:
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    server_thread = threading.Thread(
        target=server.serve_forever, args=(0.5,), daemon=True
    )
    server_thread.start()
    try:
        shutdown_event.wait()
    finally:
        server.shutdown()
        server_thread.join(timeout=2.0)
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
