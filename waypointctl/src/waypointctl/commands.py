"""The one stack-control vocabulary shared by every front door.

The CLI (`_run_in_process`), the daemon socket dispatch, and the HTTP remote
control all route through here so they can't drift apart. Human-facing log
streaming stays with the callers; this module is the action + structured-state
core.
"""

from collections.abc import Callable

from waypointctl.paths import log_file_for
from waypointctl.services import ManagedService, ServiceResult
from waypointctl.stack import WaypointStack

LogFn = Callable[[str, str], None]

ACTIONS = ("start", "stop", "restart")
LOG_TARGETS = ("backend", "frontend")
_MAX_LOG_LINES = 1000
_TAIL_BYTES = 256 * 1024


def run_action(
    stack: WaypointStack, action: str, target: str, log: LogFn
) -> ServiceResult:
    if action == "start":
        return stack.start(log, target)
    if action == "stop":
        return stack.stop(log, target)
    if action == "restart":
        return stack.restart(target, log)
    return ServiceResult(ok=False, message=f"unknown action: {action}")


def status_payload(stack: WaypointStack) -> list[dict[str, object]]:
    services = [_status_dict(stack.backend), _status_dict(stack.frontend)]
    caffeinate = stack.caffeinate.status()
    if caffeinate.state == "running":
        services.append(
            {
                "name": caffeinate.name,
                "state": caffeinate.state,
                "pid": caffeinate.pid,
                "port": caffeinate.port,
                "health": caffeinate.health,
            }
        )
    return services


def _status_dict(service: ManagedService) -> dict[str, object]:
    status = service.status()
    return {
        "name": status.name,
        "state": status.state,
        "pid": status.pid,
        "port": status.port,
        "health": status.health,
    }


def tail_log(target: str, lines: int) -> list[str]:
    if target not in LOG_TARGETS:
        raise ValueError(f"unknown log target: {target}")
    path = log_file_for(target)
    if not path.exists():
        return []
    count = max(1, min(lines, _MAX_LOG_LINES))
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - _TAIL_BYTES))
        data = handle.read()
    text = data.decode("utf-8", errors="replace")
    return text.splitlines()[-count:]
