import json
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from waypointctl.config import StackConfig
from waypointctl.control import ControlServer
from waypointctl.services import ServiceResult, ServiceStatus
from waypointctl.stack import WaypointStack


class _StubService:
    def __init__(self, name: str, state: str = "running") -> None:
        self.name = name
        self.state = state
        self.calls: list[str] = []
        self.started_marker_value = False

    @property
    def started_marker(self):  # type: ignore[no-untyped-def]
        class _Marker:
            def __init__(self, parent: "_StubService") -> None:
                self.parent = parent

            def exists(self) -> bool:
                return self.parent.started_marker_value

            def unlink(self, missing_ok: bool = False) -> None:
                self.parent.started_marker_value = False

        return _Marker(self)

    def start(self, log):  # type: ignore[no-untyped-def]
        log("stdout", f"start {self.name}")
        self.calls.append("start")
        self.state = "running"
        self.started_marker_value = True
        return ServiceResult(ok=True)

    def stop(self, log):  # type: ignore[no-untyped-def]
        log("stdout", f"stop {self.name}")
        self.calls.append("stop")
        self.state = "stopped"
        return ServiceResult(ok=True)

    def status(self) -> ServiceStatus:
        if self.state == "running":
            return ServiceStatus(
                name=self.name, state="running", pid=123, port=8787, health="healthy"
            )
        return ServiceStatus(name=self.name, state="stopped")


def _build_stack(tmp_path: Path) -> WaypointStack:
    home = tmp_path / "repo"
    home.mkdir(exist_ok=True)
    config = StackConfig(
        home=home,
        state_dir=tmp_path / "state",
        backend_host="127.0.0.1",
        backend_port=8787,
        backend_config=home / "backend" / "waypoint.yaml",
        backend_data_dir=tmp_path / "state" / "backend-data",
        frontend_port=3000,
        frontend_dev=False,
        start_timeout=1,
        uv_cache_dir=tmp_path / "state" / "uv-cache",
        force_frontend_build=False,
        caffeinate=False,
        control_host="127.0.0.1",
        control_port=0,
        child_env={},
    )
    stack = WaypointStack(config)
    stack.backend = _StubService("backend")  # type: ignore[assignment]
    stack.frontend = _StubService("frontend")  # type: ignore[assignment]
    stack.caffeinate = _StubService("caffeinate", state="stopped")  # type: ignore[assignment]
    return stack


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(stack: WaypointStack, password: str | None) -> tuple[ControlServer, int]:
    port = _free_port()
    server = ControlServer(("127.0.0.1", port), stack, password)
    thread = threading.Thread(target=server.serve_forever, args=(0.1,), daemon=True)
    thread.start()
    return server, port


@pytest.fixture
def control(tmp_path: Path) -> Iterator[tuple[ControlServer, int, str]]:
    stack = _build_stack(tmp_path)
    server, port = _serve(stack, "s3cret")
    try:
        yield server, port, "s3cret"
    finally:
        server.shutdown()
        server.server_close()


def _request(
    port: int, path: str, method: str = "GET", token: str | None = None, body=None
) -> tuple[int, dict]:
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, (json.loads(raw) if raw else {})


def _login(port: int, password: str) -> str:
    status, body = _request(port, "/api/login", "POST", body={"password": password})
    assert status == 200
    return body["token"]


# ── unauthenticated surface ──────────────────────────────────────────
def test_page_and_health(control: tuple[ControlServer, int, str]) -> None:
    _, port, _ = control
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
        assert resp.status == 200
        assert "WAYPOINT" in resp.read().decode("utf-8")
    status, body = _request(port, "/health")
    assert status == 200 and body["status"] == "ok"


# ── auth ──────────────────────────────────────────────────────────────
def test_login_wrong_password(control: tuple[ControlServer, int, str]) -> None:
    _, port, _ = control
    status, _ = _request(port, "/api/login", "POST", body={"password": "nope"})
    assert status == 401


def test_login_lockout_after_repeated_failures(
    control: tuple[ControlServer, int, str],
) -> None:
    _, port, _ = control
    for _ in range(5):
        _request(port, "/api/login", "POST", body={"password": "nope"})
    status, body = _request(port, "/api/login", "POST", body={"password": "s3cret"})
    assert status == 429
    assert "attempts" in body["error"]


@pytest.mark.parametrize("password", ["", "change-me", None])
def test_insecure_password_disables_control(
    tmp_path: Path, password: str | None
) -> None:
    stack = _build_stack(tmp_path)
    server, port = _serve(stack, password)
    try:
        status, body = _request(
            port, "/api/login", "POST", body={"password": "anything"}
        )
        assert status == 503
        assert "WAYPOINT_PASSWORD" in body["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_protected_endpoints_require_token(
    control: tuple[ControlServer, int, str],
) -> None:
    _, port, _ = control
    for path in ("/api/status", "/api/logs"):
        status, _ = _request(port, path)
        assert status == 401
    status, _ = _request(
        port, "/api/action", "POST", body={"action": "restart", "target": "frontend"}
    )
    assert status == 401


# ── status & logs ──────────────────────────────────────────────────────
def test_status_reports_services(control: tuple[ControlServer, int, str]) -> None:
    _, port, password = control
    token = _login(port, password)
    status, body = _request(port, "/api/status", token=token)
    assert status == 200
    names = {s["name"] for s in body["services"]}
    assert {"backend", "frontend"} <= names
    assert body["ops"] == []


def test_logs_unknown_target_rejected(
    control: tuple[ControlServer, int, str],
) -> None:
    _, port, password = control
    token = _login(port, password)
    status, _ = _request(port, "/api/logs?target=teleporter", token=token)
    assert status == 400


# ── actions ─────────────────────────────────────────────────────────────
def test_action_invalid_inputs_rejected(
    control: tuple[ControlServer, int, str],
) -> None:
    _, port, password = control
    token = _login(port, password)
    status, _ = _request(
        port,
        "/api/action",
        "POST",
        token=token,
        body={"action": "explode", "target": "frontend"},
    )
    assert status == 400
    status, _ = _request(
        port,
        "/api/action",
        "POST",
        token=token,
        body={"action": "restart", "target": "teleporter"},
    )
    assert status == 400


def test_action_restart_runs_async_and_records_op(
    control: tuple[ControlServer, int, str],
) -> None:
    server, port, password = control
    token = _login(port, password)
    status, body = _request(
        port,
        "/api/action",
        "POST",
        token=token,
        body={"action": "restart", "target": "frontend"},
    )
    assert status == 202 and body["accepted"] is True

    server.join_op(timeout=5)
    status, body = _request(port, "/api/status", token=token)
    ops = {op["key"]: op for op in body["ops"]}
    assert ops["frontend"]["action"] == "restart"
    assert ops["frontend"]["target"] == "frontend" and ops["frontend"]["state"] == "ok"
    assert server.stack.frontend.calls == ["stop", "start"]  # type: ignore[attr-defined]


def test_same_lane_conflicts_but_other_lane_runs(
    control: tuple[ControlServer, int, str],
) -> None:
    server, port, password = control
    token = _login(port, password)
    # Hold the backend lane to simulate a backend op already in flight.
    assert server._lane_locks["backend"].acquire(blocking=False)
    try:
        # Same lane is refused...
        status, _ = _request(
            port,
            "/api/action",
            "POST",
            token=token,
            body={"action": "restart", "target": "backend"},
        )
        assert status == 409
        # ...while the independent frontend lane still runs.
        status, _ = _request(
            port,
            "/api/action",
            "POST",
            token=token,
            body={"action": "restart", "target": "frontend"},
        )
        assert status == 202
        server.join_op(timeout=5)
        assert server.stack.frontend.calls == ["stop", "start"]  # type: ignore[attr-defined]
    finally:
        server._lane_locks["backend"].release()


def test_all_conflicts_with_a_held_lane(
    control: tuple[ControlServer, int, str],
) -> None:
    server, port, password = control
    token = _login(port, password)
    # An `all`/redeploy op needs both lanes, so a single held lane blocks it.
    assert server._lane_locks["frontend"].acquire(blocking=False)
    try:
        status, _ = _request(
            port,
            "/api/action",
            "POST",
            token=token,
            body={"action": "restart", "target": "all"},
        )
        assert status == 409
        status, _ = _request(
            port, "/api/redeploy", "POST", token=token, body={"channel": "current"}
        )
        assert status == 409
    finally:
        server._lane_locks["frontend"].release()


# ── redeploy ─────────────────────────────────────────────────────────────
def test_redeploy_invalid_channel_rejected(
    control: tuple[ControlServer, int, str],
) -> None:
    _, port, password = control
    token = _login(port, password)
    status, _ = _request(
        port, "/api/redeploy", "POST", token=token, body={"channel": "beta"}
    )
    assert status == 400
    status, _ = _request(port, "/api/redeploy", "POST", token=token, body={})
    assert status == 400


def test_redeploy_current_restarts_without_git(
    control: tuple[ControlServer, int, str],
) -> None:
    server, port, password = control
    token = _login(port, password)
    status, body = _request(
        port, "/api/redeploy", "POST", token=token, body={"channel": "current"}
    )
    assert status == 202 and body["target"] == "current"

    server.join_op(timeout=5)
    _, body = _request(port, "/api/status", token=token)
    ops = {op["key"]: op for op in body["ops"]}
    assert ops["all"]["action"] == "redeploy" and ops["all"]["target"] == "current"
    assert ops["all"]["state"] == "ok"
    # `current` restarts the checked-out tree, no git update involved.
    assert server.stack.frontend.calls == ["stop", "start"]  # type: ignore[attr-defined]


@pytest.mark.parametrize("channel", ["stable", "nightly"])
def test_redeploy_update_channel_fails_safe_outside_git(
    control: tuple[ControlServer, int, str], channel: str
) -> None:
    server, port, password = control
    token = _login(port, password)
    status, body = _request(
        port, "/api/redeploy", "POST", token=token, body={"channel": channel}
    )
    assert status == 202 and body["target"] == channel

    server.join_op(timeout=10)
    _, body = _request(port, "/api/status", token=token)
    ops = {op["key"]: op for op in body["ops"]}
    # The stub home is not a git checkout, so the update step fails cleanly
    # and the stack is left untouched.
    assert ops["all"]["action"] == "redeploy" and ops["all"]["target"] == channel
    assert ops["all"]["state"] == "failed"
    assert server.stack.frontend.calls == []  # type: ignore[attr-defined]
