import dataclasses
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
    variant_file_for,
)
from waypointctl.process import running_pid, write_pid_file
from waypointctl.procgroup import spawn_detached, terminate_group
from waypointctl.workspace import APP_DIRS, FrontendVariant, as_variant

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
    variant: FrontendVariant | None = None


class ManagedService:
    name: str = ""
    # Reported on failure but never stops the rest of the stack.
    optional: bool = False

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

    def _write_marker(self) -> None:
        self.started_marker.parent.mkdir(parents=True, exist_ok=True)
        self.started_marker.write_text("")


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


class FrontendService(ManagedService):
    def __init__(
        self,
        config: StackConfig,
        *,
        name: str,
        variant: FrontendVariant,
        port: int | None,
        optional: bool = False,
    ) -> None:
        super().__init__(config)
        self.name = name
        self.variant = variant
        self.port = port
        self.optional = optional

    @property
    def configured(self) -> bool:
        return self.port is not None

    @property
    def running(self) -> bool:
        return running_pid(self.pid_path) is not None

    @property
    def variant_path(self) -> Path:
        return variant_file_for(self.name)

    def status(self) -> ServiceStatus:
        if self.port is not None:
            status = _http_service_status(
                self.name,
                self.pid_path,
                port=self.port,
                health_url=f"http://127.0.0.1:{self.port}",
            )
        elif pid := running_pid(self.pid_path):
            status = ServiceStatus(name=self.name, state="running", pid=pid)
        else:
            status = ServiceStatus(name=self.name, state="stopped")
        if status.state != "running":
            return status
        return dataclasses.replace(status, variant=_read_variant(self.variant_path))

    def stop(self, log: LogFn) -> ServiceResult:
        result = super().stop(log)
        self.variant_path.unlink(missing_ok=True)
        return result

    # Both frontends share one root node_modules and each build rewrites its own
    # .next, so neither may happen under a live sibling.
    def conflict(self) -> str | None:
        sibling = "frontend-preview" if self.name == "frontend" else "frontend"
        if running_pid(pid_file_for(sibling)) is None:
            return None
        if frontend_install.needs_install(self.config.home):
            return (
                f"frontend dependencies changed while {sibling} is running; "
                "run `waypointctl restart frontend` to reinstall"
            )
        if _read_variant(variant_file_for(sibling)) == self.variant:
            return (
                f"{sibling} is already serving {self.variant}; "
                "run `waypointctl restart frontend` to apply the new selection"
            )
        return None

    def start(self, log: LogFn) -> ServiceResult:
        if self.port is None:
            return ServiceResult(ok=True)

        if pid := running_pid(self.pid_path):
            log("stdout", f"{self.name} already running (pid {pid})")
            return ServiceResult(ok=True)

        if port_in_use(self.port):
            return ServiceResult(
                ok=False,
                message=f"{self.name} port {self.port} is already in use",
            )

        _ensure_state_dirs()
        self._write_marker()
        self.log_path.write_text("")

        if failure := self._ensure_dependencies(log):
            return failure

        app_dir = self.config.home / APP_DIRS[self.variant]
        env = dict(
            self.config.child_env,
            PORT=str(self.port),
            NEXT_PUBLIC_BACKEND_PORT=str(self.config.backend_port),
        )
        if self.config.frontend_dev:
            log(
                "stdout",
                f"starting {self.name} in development mode on 0.0.0.0:{self.port}",
            )
            argv = ["npm", "run", "dev"]
        else:
            if failure := self._ensure_build(app_dir, env, log):
                return failure
            log("stdout", f"starting {self.name} on 0.0.0.0:{self.port}")
            argv = ["npm", "run", "start"]
        pid = spawn_detached(argv, cwd=app_dir, env=env, log_path=self.log_path)

        write_pid_file(self.pid_path, pid)
        self.variant_path.write_text(f"{self.variant}\n", encoding="utf-8")

        if not wait_for_http(
            f"http://127.0.0.1:{self.port}",
            timeout_seconds=self.config.start_timeout,
            pid=pid,
        ):
            log("stderr", f"{self.name} failed to become healthy")
            _emit_recent_log(self.log_path, log)
            return ServiceResult(
                ok=False, message=f"{self.name} failed to become healthy"
            )

        return ServiceResult(ok=True)

    def _ensure_dependencies(self, log: LogFn) -> ServiceResult | None:
        with frontend_install.install_lock:
            if conflict := self.conflict():
                log("stderr", conflict)
                return ServiceResult(ok=False, message=conflict)
            if not frontend_install.needs_install(self.config.home):
                log("stdout", "frontend dependencies up to date")
                return None
            log("stdout", "installing frontend dependencies")
            install_rc = frontend_install.run_install(
                self.config.home, self.log_path, self.config.child_env
            )
        if install_rc != 0:
            log("stderr", "frontend dependency install failed")
            _emit_recent_log(self.log_path, log)
            return ServiceResult(
                ok=False,
                message=f"frontend dependency install exited with {install_rc}",
            )
        return None

    def _ensure_build(
        self, app_dir: Path, env: dict[str, str], log: LogFn
    ) -> ServiceResult | None:
        if frontend_build.is_fresh(
            self.config.home,
            self.variant,
            backend_port=self.config.backend_port,
            force=self.config.force_frontend_build,
        ):
            log("stdout", f"{self.name} build up to date, skipping rebuild")
            return None
        log("stdout", f"building {self.name}")
        with self.log_path.open("a", encoding="utf-8") as log_file:
            proc = subprocess.run(
                ["npm", "run", "build"],
                cwd=app_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if proc.returncode != 0:
            log("stderr", f"{self.name} build failed")
            _emit_recent_log(self.log_path, log)
            return ServiceResult(
                ok=False, message=f"{self.name} build exited with {proc.returncode}"
            )
        frontend_build.record_build(
            self.config.home, self.variant, backend_port=self.config.backend_port
        )
        return None


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


def _read_variant(path: Path) -> FrontendVariant | None:
    try:
        return as_variant(path.read_text(encoding="utf-8"))
    except OSError:
        return None


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
