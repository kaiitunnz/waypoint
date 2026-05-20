import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

from waypointctl.config import StackConfig
from waypointctl.paths import log_file_for
from waypointctl.services import (
    BackendService,
    CaffeinateService,
    FrontendService,
    LogFn,
    ManagedService,
    ServiceResult,
    ServiceStatus,
)


class WaypointStack:
    def __init__(self, config: StackConfig) -> None:
        self.config = config
        self.backend = BackendService(config)
        self.frontend = FrontendService(config)
        self.caffeinate = CaffeinateService(config)

    def start(self, log: LogFn, target: str = "all") -> ServiceResult:
        services = self._select(target)
        if services is None:
            return ServiceResult(ok=False, message=f"unknown service: {target}")
        for svc in services:
            svc.started_marker.unlink(missing_ok=True)

        results = self._parallel(services, "start", log)
        if any(not r.ok for r in results):
            self._stop_started(services, log)
            return _aggregate(results)

        self.caffeinate.start(log)
        self._emit_status(log)
        return ServiceResult(ok=True)

    def stop(self, log: LogFn, target: str = "all") -> ServiceResult:
        services = self._select(target)
        if services is None:
            return ServiceResult(ok=False, message=f"unknown service: {target}")
        # Caffeinate spans the whole stack — only stop it on `stop all`.
        targets: tuple[ManagedService, ...] = (
            (self.caffeinate, *services) if target in {"all", ""} else services
        )
        results = self._parallel(targets, "stop", log)
        return _aggregate(results)

    def restart(self, target: str, log: LogFn) -> ServiceResult:
        if self._select(target) is None:
            return ServiceResult(ok=False, message=f"unknown service: {target}")
        self.stop(log, target)
        return self.start(log, target)

    def _select(self, target: str) -> tuple[ManagedService, ...] | None:
        if target == "backend":
            return (self.backend,)
        if target == "frontend":
            return (self.frontend,)
        if target in {"all", ""}:
            return (self.backend, self.frontend)
        return None

    def status(self, log: LogFn) -> ServiceResult:
        self._emit_status(log)
        return ServiceResult(ok=True)

    def logs_argv(self, target: str) -> list[str]:
        if target == "backend":
            return ["tail", "-n", "50", "-f", str(log_file_for("backend"))]
        if target == "frontend":
            return ["tail", "-n", "50", "-f", str(log_file_for("frontend"))]
        if target in {"all", ""}:
            return [
                "tail",
                "-n",
                "50",
                "-f",
                str(log_file_for("backend")),
                str(log_file_for("frontend")),
            ]
        raise ValueError(f"unknown service: {target}")

    def _emit_status(self, log: LogFn) -> None:
        for svc in (self.backend, self.frontend):
            log("stdout", _format_status(svc.status()))
        cf_status = self.caffeinate.status()
        if cf_status.state == "running":
            log("stdout", _format_status(cf_status))

    def _stop_started(self, services: tuple[ManagedService, ...], log: LogFn) -> None:
        for svc in services:
            if svc.started_marker.exists():
                svc.stop(log)

    def _parallel(
        self,
        services: tuple[ManagedService, ...],
        method: str,
        log: LogFn,
    ) -> tuple[ServiceResult, ...]:
        log_lock = threading.Lock()

        def synced_log(stream: str, line: str) -> None:
            with log_lock:
                log(stream, line)

        with ThreadPoolExecutor(max_workers=len(services)) as pool:
            futures = [
                pool.submit(getattr(svc, method), synced_log) for svc in services
            ]
            return tuple(f.result() for f in futures)


def _aggregate(results: Iterable[ServiceResult]) -> ServiceResult:
    failures = [r for r in results if not r.ok]
    if not failures:
        return ServiceResult(ok=True)
    message = "; ".join(r.message or "failed" for r in failures)
    return ServiceResult(ok=False, message=message)


def _format_status(status: ServiceStatus) -> str:
    if status.state == "running":
        parts = [f"{status.name}: running"]
        if status.pid is not None:
            parts.append(f"pid={status.pid}")
        if status.port is not None:
            parts.append(f"port={status.port}")
        if status.health is not None:
            parts.append(f"health={status.health}")
        return " ".join(parts)
    if status.state == "unmanaged" and status.port is not None:
        return f"{status.name}: unmanaged port={status.port} in-use"
    return f"{status.name}: stopped"
