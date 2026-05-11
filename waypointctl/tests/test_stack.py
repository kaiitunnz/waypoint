from pathlib import Path

import pytest

from waypointctl.config import StackConfig
from waypointctl.services import ServiceResult, ServiceStatus
from waypointctl.stack import WaypointStack, _format_status


@pytest.fixture
def state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    state = tmp_path / "state"
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(state))
    return state


def _build_config(tmp_path: Path, state_dir: Path) -> StackConfig:
    home = tmp_path / "repo"
    home.mkdir(exist_ok=True)
    return StackConfig(
        home=home,
        state_dir=state_dir.resolve(),
        backend_host="127.0.0.1",
        backend_port=8787,
        backend_config=home / "backend" / "waypoint.yaml",
        backend_data_dir=state_dir / "backend-data",
        frontend_port=3000,
        frontend_dev=False,
        start_timeout=1,
        uv_cache_dir=state_dir / "uv-cache",
        force_frontend_build=False,
        caffeinate=False,
        child_env={},
    )


class _StubService:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []
        self.started_marker_value = False

    @property
    def started_marker(self):  # type: ignore[no-untyped-def]
        class _Marker:
            def __init__(self, parent: _StubService) -> None:
                self.parent = parent

            def exists(self) -> bool:
                return self.parent.started_marker_value

            def unlink(self, missing_ok: bool = False) -> None:
                self.parent.started_marker_value = False

        return _Marker(self)

    def start(self, log):  # type: ignore[no-untyped-def]
        log("stdout", f"start {self.name}")
        self.calls.append("start")
        self.started_marker_value = True
        return ServiceResult(ok=True)

    def stop(self, log):  # type: ignore[no-untyped-def]
        log("stdout", f"stop {self.name}")
        self.calls.append("stop")
        return ServiceResult(ok=True)

    def status(self) -> ServiceStatus:
        return ServiceStatus(name=self.name, state="stopped")


def _install_stubs(
    stack: WaypointStack,
) -> tuple[_StubService, _StubService, _StubService]:
    backend = _StubService("backend")
    frontend = _StubService("frontend")
    caffeinate = _StubService("caffeinate")
    stack.backend = backend  # type: ignore[assignment]
    stack.frontend = frontend  # type: ignore[assignment]
    stack.caffeinate = caffeinate  # type: ignore[assignment]
    return backend, frontend, caffeinate


def test_start_runs_services_in_parallel_and_caffeinate_after(
    state_dir: Path, tmp_path: Path
) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))
    backend, frontend, caffeinate = _install_stubs(stack)
    logs: list[tuple[str, str]] = []

    result = stack.start(lambda s, line: logs.append((s, line)))

    assert result.ok is True
    assert backend.calls == ["start"]
    assert frontend.calls == ["start"]
    assert caffeinate.calls == ["start"]


def test_start_partial_failure_stops_started_services(
    state_dir: Path, tmp_path: Path
) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))
    backend, frontend, caffeinate = _install_stubs(stack)

    def failing_start(log):  # type: ignore[no-untyped-def]
        log("stderr", "frontend failed")
        return ServiceResult(ok=False, message="frontend failed")

    frontend.start = failing_start  # type: ignore[method-assign]

    logs: list[tuple[str, str]] = []
    result = stack.start(lambda s, line: logs.append((s, line)))

    assert result.ok is False
    assert "frontend failed" in result.message
    assert backend.calls == ["start", "stop"]
    assert caffeinate.calls == []


def test_stop_runs_all_services(state_dir: Path, tmp_path: Path) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))
    backend, frontend, caffeinate = _install_stubs(stack)
    logs: list[tuple[str, str]] = []

    result = stack.stop(lambda s, line: logs.append((s, line)))

    assert result.ok is True
    assert backend.calls == ["stop"]
    assert frontend.calls == ["stop"]
    assert caffeinate.calls == ["stop"]


def test_restart_target_backend(state_dir: Path, tmp_path: Path) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))
    backend, frontend, caffeinate = _install_stubs(stack)
    logs: list[tuple[str, str]] = []

    result = stack.restart("backend", lambda s, line: logs.append((s, line)))

    assert result.ok is True
    assert backend.calls == ["stop", "start"]
    assert frontend.calls == []


def test_restart_unknown_target(state_dir: Path, tmp_path: Path) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))
    _install_stubs(stack)
    result = stack.restart("teleporter", lambda *_: None)
    assert result.ok is False
    assert "unknown service" in result.message


def test_logs_argv_targets(state_dir: Path, tmp_path: Path) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))
    backend_argv = stack.logs_argv("backend")
    frontend_argv = stack.logs_argv("frontend")
    all_argv = stack.logs_argv("all")

    assert backend_argv[:4] == ["tail", "-n", "50", "-f"]
    assert backend_argv[-1].endswith("/backend.log")
    assert frontend_argv[-1].endswith("/frontend.log")
    assert len(all_argv) == len(backend_argv) + 1  # +1 second log file

    with pytest.raises(ValueError):
        stack.logs_argv("teleporter")


def test_format_status_variants() -> None:
    running = ServiceStatus(
        name="backend", state="running", pid=42, port=8787, health="healthy"
    )
    assert _format_status(running) == "backend: running pid=42 port=8787 health=healthy"

    unmanaged = ServiceStatus(name="frontend", state="unmanaged", port=3000)
    assert _format_status(unmanaged) == "frontend: unmanaged port=3000 in-use"

    stopped = ServiceStatus(name="backend", state="stopped")
    assert _format_status(stopped) == "backend: stopped"
