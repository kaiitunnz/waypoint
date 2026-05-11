import http.client
import time
import urllib.error
import urllib.request

from waypointctl.process import is_pid_running


def wait_for_http(
    url: str,
    *,
    timeout_seconds: float,
    pid: int | None = None,
    poll_interval_seconds: float = 1.0,
    request_timeout_seconds: float = 2.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if pid is not None and not is_pid_running(pid):
            return False
        if http_ok(url, timeout_seconds=request_timeout_seconds):
            return True
        time.sleep(poll_interval_seconds)
    return False


def http_ok(url: str, *, timeout_seconds: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as resp:
            status = int(getattr(resp, "status", 0))
            return 200 <= status < 300
    except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError):
        return False
