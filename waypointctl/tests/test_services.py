import os
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from waypointctl import services
from waypointctl.config import StackConfig


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    state = tmp_path / "state"
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(state))
    return state


def _build_config(tmp_path: Path, state_dir: Path, **overrides: object) -> StackConfig:
    home = tmp_path / "repo"
    home.mkdir(exist_ok=True)
    defaults: dict[str, object] = dict(
        home=home,
        state_dir=state_dir.resolve(),
        backend_host="127.0.0.1",
        backend_port=8787,
        backend_config=home / "backend" / "waypoint.yaml",
        backend_data_dir=state_dir / "backend-data",
        frontend_port=3000,
        frontend_dev=False,
        start_timeout=2,
        uv_cache_dir=state_dir / "uv-cache",
        force_frontend_build=False,
        caffeinate=False,
        control_host="127.0.0.1",
        control_port=0,
        child_env={},
    )
    defaults.update(overrides)
    return StackConfig(**defaults)  # type: ignore[arg-type]


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def http_server() -> Iterator[HTTPServer]:
    server = HTTPServer(("127.0.0.1", 0), _OkHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_backend_status_stopped_when_no_pid(state_dir: Path, tmp_path: Path) -> None:
    config = _build_config(tmp_path, state_dir, backend_port=_free_port())
    backend = services.BackendService(config)
    status = backend.status()
    assert status.state == "stopped"
    assert status.pid is None


def test_backend_status_unmanaged_when_port_in_use(
    state_dir: Path, tmp_path: Path, http_server: HTTPServer
) -> None:
    port = http_server.server_address[1]
    config = _build_config(tmp_path, state_dir, backend_port=port)
    backend = services.BackendService(config)
    status = backend.status()
    assert status.state == "unmanaged"
    assert status.port == port


def test_backend_start_refuses_when_port_in_use(
    state_dir: Path, tmp_path: Path, http_server: HTTPServer
) -> None:
    port = http_server.server_address[1]
    config = _build_config(tmp_path, state_dir, backend_port=port)
    backend = services.BackendService(config)
    logs: list[tuple[str, str]] = []
    result = backend.start(lambda s, line: logs.append((s, line)))
    assert result.ok is False
    assert "already in use" in result.message


def test_backend_start_running_already(
    monkeypatch: pytest.MonkeyPatch, state_dir: Path, tmp_path: Path
) -> None:
    config = _build_config(tmp_path, state_dir, backend_port=_free_port())
    backend = services.BackendService(config)

    state_dir.mkdir(exist_ok=True)
    (state_dir / "run").mkdir(exist_ok=True)
    pid_path = backend.pid_path
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{os.getpid()}\n")

    logs: list[tuple[str, str]] = []
    result = backend.start(lambda s, line: logs.append((s, line)))
    assert result.ok is True
    assert any("already running" in line for _, line in logs)


def test_caffeinate_noop_on_non_darwin(
    monkeypatch: pytest.MonkeyPatch, state_dir: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr(services.platform, "system", lambda: "Linux")
    config = _build_config(tmp_path, state_dir, caffeinate=True)
    cf = services.CaffeinateService(config)
    assert cf.available is False
    logs: list[tuple[str, str]] = []
    result = cf.start(lambda s, line: logs.append((s, line)))
    assert result.ok is True
    assert logs == []
    assert cf.status().state == "stopped"


def test_caffeinate_status_stopped_when_off(
    monkeypatch: pytest.MonkeyPatch, state_dir: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr(services.platform, "system", lambda: "Darwin")
    config = _build_config(tmp_path, state_dir, caffeinate=False)
    cf = services.CaffeinateService(config)
    assert cf.available is False
    assert cf.status().state == "stopped"


def test_frontend_status_unmanaged_when_port_in_use(
    state_dir: Path, tmp_path: Path, http_server: HTTPServer
) -> None:
    port = http_server.server_address[1]
    config = _build_config(tmp_path, state_dir, frontend_port=port)
    frontend = services.FrontendService(config)
    status = frontend.status()
    assert status.state == "unmanaged"
    assert status.port == port


def test_stop_when_no_pid_is_idempotent(state_dir: Path, tmp_path: Path) -> None:
    config = _build_config(tmp_path, state_dir, backend_port=_free_port())
    backend = services.BackendService(config)
    logs: list[tuple[str, str]] = []
    result = backend.stop(lambda s, line: logs.append((s, line)))
    assert result.ok is True
    assert any("already stopped" in line for _, line in logs)
