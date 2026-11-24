import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

from waypointctl.config import StackConfig
from waypointctl.paths import log_file_for
from waypointctl.process import running_pid
from waypointctl.services import (
    BackendService,
    CaffeinateService,
    FrontendService,
    LogFn,
    ManagedService,
    ServiceResult,
    ServiceStatus,
)
from waypointctl.workspace import other_variant


class WaypointStack:
    def __init__(self, config: StackConfig) -> None:
        self.config = config
        self.backend = BackendService(config)
        self.frontend = FrontendService(
            config,
            name="frontend",
            variant=config.frontend_variant,
            port=config.frontend_port,
        )
        self.frontend_preview = FrontendService(
            config,
            name="frontend-preview",
            variant=other_variant(config.frontend_variant),
            port=config.frontend_preview_port,
            optional=True,
        )
        self.caffeinate = CaffeinateService(config)

    @property
    def preview_active(self) -> bool:
        """Whether the preview is configured, or still running from an earlier config.

        An unconfigured but live preview stays selectable so `stop` and
        `restart` never leave it orphaned.
        """
        return (
            self.frontend_preview.configured
            or running_pid(self.frontend_preview.pid_path) is not None
        )

    def start(self, log: LogFn, target: str = "all") -> ServiceResult:
        if message := self.check_target(target, "start"):
            return ServiceResult(ok=False, message=message)
        services = self.select(target) or ()
        for svc in services:
            svc.started_marker.unlink(missing_ok=True)

        results = self._parallel(services, "start", log)
        failed = tuple(
            svc for svc, r in zip(services, results, strict=True) if not r.ok
        )
        if any(not svc.optional for svc in failed):
            self._stop_started(services, log)
            return _aggregate(results)

        # Only optional services failed (the preview): drop them and keep the
        # rest of the stack serving, but still report the failure.
        self._stop_started(failed, log)
        self.caffeinate.start(log)
        self._emit_status(log)
        return _aggregate(results)

    def stop(self, log: LogFn, target: str = "all") -> ServiceResult:
        services = self.select(target)
        if services is None:
            return ServiceResult(ok=False, message=f"unknown service: {target}")
        # Caffeinate spans the whole stack — only stop it on `stop all`.
        targets: tuple[ManagedService, ...] = (
            (self.caffeinate, *services) if target in {"all", ""} else services
        )
        results = self._parallel(targets, "stop", log)
        return _aggregate(results)

    def restart(self, target: str, log: LogFn) -> ServiceResult:
        if message := self.check_target(target, "restart"):
            return ServiceResult(ok=False, message=message)
        self.stop(log, target)
        return self.start(log, target)

    def check_target(self, target: str, action: str) -> str | None:
        """Why `action` on `target` can't run, if it can't.

        `stop` is exempt from the preview-configured check so a preview left
        running after its port was removed from the config can still be stopped.
        """
        if self.select(target) is None:
            return f"unknown service: {target}"
        if (
            action != "stop"
            and target == "frontend-preview"
            and not self.frontend_preview.configured
        ):
            return (
                "frontend-preview is not configured; "
                "set WAYPOINT_STACK_FRONTEND_PREVIEW_PORT"
            )
        # A lone preview start must be refused before `restart` stops the preview.
        if action != "stop" and target == "frontend-preview":
            return self.frontend_preview.conflict()
        return None

    def select(self, target: str) -> tuple[ManagedService, ...] | None:
        """Services a target names; `frontend` covers the preview alongside it."""
        frontends: tuple[ManagedService, ...] = (
            (self.frontend, self.frontend_preview)
            if self.preview_active
            else (self.frontend,)
        )
        if target == "backend":
            return (self.backend,)
        if target == "frontend":
            return frontends
        if target == "frontend-preview":
            return (self.frontend_preview,)
        if target in {"all", ""}:
            return (self.backend, *frontends)
        return None

    def status(self, log: LogFn) -> ServiceResult:
        self._emit_status(log)
        return ServiceResult(ok=True)

    def logs_argv(self, target: str) -> list[str]:
        services = self.select(target)
        if services is None:
            raise ValueError(f"unknown service: {target}")
        return [
            "tail",
            "-n",
            "50",
            "-f",
            *(str(log_file_for(svc.name)) for svc in services),
        ]

    def _emit_status(self, log: LogFn) -> None:
        for svc in self.select("all") or ():
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
        if status.variant is not None:
            parts.append(f"variant={status.variant}")
        return " ".join(parts)
    if status.state == "unmanaged" and status.port is not None:
        return f"{status.name}: unmanaged port={status.port} in-use"
    return f"{status.name}: stopped"
