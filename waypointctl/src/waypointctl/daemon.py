import argparse
import json
import os
import signal
import socket as socket_mod
import socketserver
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast

from waypointctl.config import apply_dotenv, load_stack_config
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

DEFERRED_COMMANDS: frozenset[str] = frozenset({"stop", "restart"})
WORKER_SHUTDOWN_GRACE_SECONDS = 30.0


class WaypointDaemonServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, socket_path: Path, home: Path) -> None:
        self.home = home
        self.stack = WaypointStack(load_stack_config(home))
        self._workers: list[threading.Thread] = []
        self._workers_lock = threading.Lock()
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)
        super().__init__(str(socket_path), WaypointDaemonHandler)

    def schedule_worker(self, target: Callable[[], None], name: str) -> None:
        # daemon=True: a stuck worker can't pin the interpreter past the
        # join_workers grace period in serve().
        thread = threading.Thread(
            target=self._run_worker, args=(target,), name=name, daemon=True
        )
        thread.start()
        with self._workers_lock:
            self._workers.append(thread)

    def _run_worker(self, target: Callable[[], None]) -> None:
        try:
            target()
        except Exception as exc:  # noqa: BLE001
            print(f"worker error: {exc}", file=sys.stderr, flush=True)

    def join_workers(self, timeout: float) -> None:
        with self._workers_lock:
            threads = list(self._workers)
        for thread in threads:
            thread.join(timeout=timeout)


class WaypointDaemonHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline().decode("utf-8").strip()
        if not raw:
            return
        try:
            payload = json.loads(raw)
            home_value = payload.get("home")
            request = DaemonRequest(
                command=str(payload["command"]),
                args=[str(item) for item in payload.get("args", [])],
                home=str(home_value) if home_value is not None else None,
                wait=bool(payload.get("wait", False)),
            )
        except Exception as exc:  # noqa: BLE001
            self._send_result(DaemonResult(ok=False, returncode=1, error=str(exc)))
            return

        try:
            self._dispatch(request)
        except Exception as exc:  # noqa: BLE001
            self._send_result(DaemonResult(ok=False, returncode=1, error=str(exc)))

    def _dispatch(self, request: DaemonRequest) -> None:
        server = cast(WaypointDaemonServer, self.server)
        if request.home is not None and request.home != str(server.home):
            self._send_result(
                DaemonResult(
                    ok=False,
                    returncode=1,
                    error=(
                        f"daemon serves home {server.home}, "
                        f"refusing request for home {request.home}"
                    ),
                )
            )
            return

        if request.command == "ping":
            self._send_result(DaemonResult(ok=True))
            return

        stack = server.stack

        if request.command in DEFERRED_COMMANDS and not request.wait:
            self._dispatch_deferred(server, stack, request)
            return

        write_lock = threading.Lock()

        def log(stream: str, line: str) -> None:
            with write_lock:
                self._send_log(DaemonLog(stream=stream, line=line))

        if request.command == "start":
            target = request.args[0] if request.args else "all"
            result = stack.start(log, target)
        elif request.command == "status":
            result = stack.status(log)
        elif request.command == "stop":
            target = request.args[0] if request.args else "all"
            result = stack.stop(log, target)
        elif request.command == "restart":
            target = request.args[0] if request.args else "all"
            result = stack.restart(target, log)
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

    def _dispatch_deferred(
        self,
        server: "WaypointDaemonServer",
        stack: WaypointStack,
        request: DaemonRequest,
    ) -> None:
        command = request.command

        def file_log(stream: str, line: str) -> None:
            tag = "stderr" if stream == "stderr" else "stdout"
            print(f"[{command}] {tag}: {line}", flush=True)

        if command == "restart":
            target = request.args[0] if request.args else "all"

            def worker() -> None:
                stack.restart(target, file_log)

        elif command == "stop":
            target = request.args[0] if request.args else "all"

            def worker() -> None:
                stack.stop(file_log, target)

        else:
            self._send_result(
                DaemonResult(
                    ok=False, returncode=2, error=f"unknown command: {command}"
                )
            )
            return

        self._send_result(DaemonResult(ok=True, returncode=0))
        # Flush + close the response side so the caller's connection is fully
        # released before we touch the stack. This is what lets a CLI invocation
        # inside the target's own process tree return to its caller before the
        # group is signalled.
        try:
            self.wfile.flush()
            self.connection.shutdown(socket_mod.SHUT_WR)
        except OSError:
            pass

        server.schedule_worker(worker, name=f"waypointd-{command}")

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
    apply_dotenv(home)
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
        server.join_workers(timeout=WORKER_SHUTDOWN_GRACE_SECONDS)
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
