import platform
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from waypointctl import frontend_build, frontend_install
from waypointctl.config import StackConfig
from waypointctl.health import http_ok, wait_for_http
from waypointctl.net import port_in_use
from waypointctl.paths import (
    log_file_for,
    pid_file_for,
    started_marker_for,
    state_log_dir,
    state_run_dir,
)
from waypointctl.process import running_pid, write_pid_file
from waypointctl.procgroup import spawn_detached, terminate_group

LogFn = Callable[[str, str], None]

ServiceState = Literal["running", "stopped", "unmanaged"]
HealthState = Literal["healthy", "unhealthy"]


@dataclass(slots=True, frozen=True)
class ServiceResult:
    ok: bool
    message: str = ""


@dataclass(slots=True, frozen=True)
class ServiceStatus:
    name: str
    state: ServiceState
    pid: int | None = None
    port: int | None = None
    health: HealthState | None = None


class ManagedService:
    name: str = ""

    def __init__(self, config: StackConfig) -> None:
        self.config = config

    @property
    def pid_path(self) -> Path:
        return pid_file_for(self.name)

    @property
    def log_path(self) -> Path:
        return log_file_for(self.name)

    @property
    def started_marker(self) -> Path:
        return started_marker_for(self.name)

    def status(self) -> ServiceStatus:
        raise NotImplementedError

    def start(self, log: LogFn) -> ServiceResult:
        raise NotImplementedError

    def stop(self, log: LogFn) -> ServiceResult:
        return _stop_managed_service(self.name, self.pid_path, log)


class BackendService(ManagedService):
    name = "backend"

    def status(self) -> ServiceStatus:
        return _http_service_status(
            self.name,
            self.pid_path,
            port=self.config.backend_port,
            health_url=f"http://127.0.0.1:{self.config.backend_port}/health",
        )

    def start(self, log: LogFn) -> ServiceResult:
        if pid := running_pid(self.pid_path):
            log("stdout", f"backend already running (pid {pid})")
            return ServiceResult(ok=True)

        if port_in_use(self.config.backend_port):
            return ServiceResult(
                ok=False,
                message=f"backend port {self.config.backend_port} is already in use",
            )

        _ensure_state_dirs()
        self._write_marker()
        self.log_path.write_text("")
        log(
            "stdout",
            f"starting backend on {self.config.backend_host}:{self.config.backend_port}",
        )

        env = dict(self.config.child_env)
        env.update(
            {
                "WAYPOINT_CONFIG_PATH": str(self.config.backend_config),
                "WAYPOINT_HOST": self.config.backend_host,
                "WAYPOINT_PORT": str(self.config.backend_port),
                "WAYPOINT_DATA_DIR": str(self.config.backend_data_dir),
                "UV_CACHE_DIR": str(self.config.uv_cache_dir),
            }
        )
        pid = spawn_detached(
            ["uv", "run", "waypoint", "serve"],
            cwd=self.config.home / "backend",
            env=env,
            log_path=self.log_path,
        )
        write_pid_file(self.pid_path, pid)

        if not wait_for_http(
            f"http://127.0.0.1:{self.config.backend_port}/health",
            timeout_seconds=self.config.start_timeout,
            pid=pid,
        ):
            log("stderr", "backend failed to become healthy")
            _emit_recent_log(self.log_path, log)
            return ServiceResult(ok=False, message="backend failed to become healthy")

        return ServiceResult(ok=True)

    def _write_marker(self) -> None:
        self.started_marker.parent.mkdir(parents=True, exist_ok=True)
        self.started_marker.write_text("")


class FrontendService(ManagedService):
    name = "frontend"

    def status(self) -> ServiceStatus:
        return _http_service_status(
            self.name,
            self.pid_path,
            port=self.config.frontend_port,
            health_url=f"http://127.0.0.1:{self.config.frontend_port}",
        )

    def start(self, log: LogFn) -> ServiceResult:
        if pid := running_pid(self.pid_path):
            log("stdout", f"frontend already running (pid {pid})")
            return ServiceResult(ok=True)

        if port_in_use(self.config.frontend_port):
            return ServiceResult(
                ok=False,
                message=f"frontend port {self.config.frontend_port} is already in use",
            )

        _ensure_state_dirs()
        self._write_marker()
        self.log_path.write_text("")

        frontend_dir = self.config.home / "frontend"
        port_env = str(self.config.frontend_port)
        backend_port_env = str(self.config.backend_port)

        if frontend_install.needs_install(self.config.home):
            log("stdout", "installing frontend dependencies")
            install_rc = frontend_install.run_install(frontend_dir, self.log_path)
            if install_rc != 0:
                log("stderr", "frontend dependency install failed")
                _emit_recent_log(self.log_path, log)
                return ServiceResult(
                    ok=False,
                    message=f"frontend dependency install exited with {install_rc}",
                )

        if self.config.frontend_dev:
            log(
                "stdout",
                f"starting frontend in development mode on 0.0.0.0:{port_env}",
            )
            env = dict(
                self.config.child_env,
                PORT=port_env,
                NEXT_PUBLIC_BACKEND_PORT=backend_port_env,
            )
            pid = spawn_detached(
                ["npm", "run", "dev"],
                cwd=frontend_dir,
                env=env,
                log_path=self.log_path,
            )
        else:
            if frontend_build.is_fresh(
                self.config.home,
                backend_port=self.config.backend_port,
                force=self.config.force_frontend_build,
            ):
                log("stdout", "frontend build up to date, skipping rebuild")
            else:
                log("stdout", "building frontend")
                build_rc = self._run_build(frontend_dir, port_env, backend_port_env)
                if build_rc != 0:
                    log("stderr", "frontend build failed")
                    _emit_recent_log(self.log_path, log)
                    return ServiceResult(
                        ok=False, message=f"frontend build exited with {build_rc}"
                    )
                frontend_build.record_build(
                    self.config.home, backend_port=self.config.backend_port
                )

            log("stdout", f"starting frontend on 0.0.0.0:{port_env}")
            env = dict(
                self.config.child_env,
                PORT=port_env,
                NEXT_PUBLIC_BACKEND_PORT=backend_port_env,
            )
            pid = spawn_detached(
                ["npm", "run", "start"],
                cwd=frontend_dir,
                env=env,
                log_path=self.log_path,
            )

        write_pid_file(self.pid_path, pid)

        if not wait_for_http(
            f"http://127.0.0.1:{self.config.frontend_port}",
            timeout_seconds=self.config.start_timeout,
            pid=pid,
        ):
            log("stderr", "frontend failed to become healthy")
            _emit_recent_log(self.log_path, log)
            return ServiceResult(ok=False, message="frontend failed to become healthy")

        return ServiceResult(ok=True)

    def _run_build(
        self, frontend_dir: Path, port_env: str, backend_port_env: str
    ) -> int:
        env = dict(
            self.config.child_env,
            PORT=port_env,
            NEXT_PUBLIC_BACKEND_PORT=backend_port_env,
        )
        with self.log_path.open("a", encoding="utf-8") as log_file:
            proc = subprocess.run(
                ["npm", "run", "build"],
                cwd=frontend_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                check=False,
            )
        return proc.returncode

    def _write_marker(self) -> None:
        self.started_marker.parent.mkdir(parents=True, exist_ok=True)
        self.started_marker.write_text("")


class CaffeinateService(ManagedService):
    name = "caffeinate"

    @property
    def available(self) -> bool:
        return (
            platform.system() == "Darwin"
            and self.config.caffeinate
            and shutil.which("caffeinate") is not None
        )

    def status(self) -> ServiceStatus:
        if platform.system() != "Darwin":
            return ServiceStatus(name=self.name, state="stopped")
        pid = running_pid(self.pid_path)
        if pid is None:
            return ServiceStatus(name=self.name, state="stopped")
        return ServiceStatus(name=self.name, state="running", pid=pid)

    def start(self, log: LogFn) -> ServiceResult:
        if not self.available:
            return ServiceResult(ok=True)
        if running_pid(self.pid_path):
            return ServiceResult(ok=True)

        _ensure_state_dirs()
        log(
            "stdout",
            "engaging caffeinate to keep the system awake while the stack runs",
        )
        pid = spawn_detached(
            ["caffeinate", "-i", "-s"],
            cwd=self.config.home,
            env=dict(self.config.child_env),
            log_path=self.log_path,
        )
        write_pid_file(self.pid_path, pid)
        return ServiceResult(ok=True)


def _stop_managed_service(name: str, pid_path: Path, log: LogFn) -> ServiceResult:
    pid = running_pid(pid_path)
    if pid is None:
        log("stdout", f"{name} already stopped")
        pid_path.unlink(missing_ok=True)
        return ServiceResult(ok=True)

    log("stdout", f"stopping {name} (pid {pid})")
    terminate_group(pid)
    pid_path.unlink(missing_ok=True)
    started_marker_for(name).unlink(missing_ok=True)
    return ServiceResult(ok=True)


def _http_service_status(
    name: str, pid_path: Path, *, port: int, health_url: str
) -> ServiceStatus:
    pid = running_pid(pid_path)
    if pid is None:
        if port_in_use(port):
            return ServiceStatus(name=name, state="unmanaged", port=port)
        return ServiceStatus(name=name, state="stopped")
    health: HealthState = "healthy" if http_ok(health_url) else "unhealthy"
    return ServiceStatus(name=name, state="running", pid=pid, port=port, health=health)


def _emit_recent_log(log_path: Path, log: LogFn, lines: int = 40) -> None:
    if not log_path.exists():
        return
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    tail = text.splitlines()[-lines:]
    log("stderr", f"--- {log_path.name} ---")
    for line in tail:
        log("stderr", line)


def _ensure_state_dirs() -> None:
    state_run_dir().mkdir(parents=True, exist_ok=True)
    state_log_dir().mkdir(parents=True, exist_ok=True)
