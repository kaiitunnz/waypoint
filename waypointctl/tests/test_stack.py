import os
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
        start_timeout=1,
        uv_cache_dir=state_dir / "uv-cache",
        force_frontend_build=False,
        caffeinate=False,
        control_host="127.0.0.1",
        control_port=0,
        child_env={},
    )
    defaults.update(overrides)
    return StackConfig(**defaults)  # type: ignore[arg-type]


class _StubService:
    def __init__(
        self,
        name: str,
        *,
        optional: bool = False,
        configured: bool = True,
        pid_path: Path | None = None,
    ) -> None:
        self.name = name
        self.optional = optional
        self.configured = configured
        self.pid_path = pid_path or Path("/nonexistent") / f"{name}.pid"
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


def test_start_target_backend_skips_frontend(state_dir: Path, tmp_path: Path) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))
    backend, frontend, caffeinate = _install_stubs(stack)

    result = stack.start(lambda *_: None, "backend")

    assert result.ok is True
    assert backend.calls == ["start"]
    assert frontend.calls == []
    assert caffeinate.calls == ["start"]


def test_start_target_frontend_skips_backend(state_dir: Path, tmp_path: Path) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))
    backend, frontend, caffeinate = _install_stubs(stack)

    result = stack.start(lambda *_: None, "frontend")

    assert result.ok is True
    assert backend.calls == []
    assert frontend.calls == ["start"]
    assert caffeinate.calls == ["start"]


def test_start_unknown_target(state_dir: Path, tmp_path: Path) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))
    backend, frontend, caffeinate = _install_stubs(stack)
    result = stack.start(lambda *_: None, "teleporter")
    assert result.ok is False
    assert "unknown service" in result.message
    assert backend.calls == []
    assert frontend.calls == []
    assert caffeinate.calls == []


def test_stop_target_backend_leaves_frontend_and_caffeinate(
    state_dir: Path, tmp_path: Path
) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))
    backend, frontend, caffeinate = _install_stubs(stack)

    result = stack.stop(lambda *_: None, "backend")

    assert result.ok is True
    assert backend.calls == ["stop"]
    assert frontend.calls == []
    assert caffeinate.calls == []


def test_stop_unknown_target(state_dir: Path, tmp_path: Path) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))
    backend, frontend, caffeinate = _install_stubs(stack)
    result = stack.stop(lambda *_: None, "teleporter")
    assert result.ok is False
    assert backend.calls == []
    assert frontend.calls == []
    assert caffeinate.calls == []


def test_start_partial_failure_only_stops_started_in_target(
    state_dir: Path, tmp_path: Path
) -> None:
    """A failed `start backend` must not stop an already-running frontend."""
    stack = WaypointStack(_build_config(tmp_path, state_dir))
    backend, frontend, caffeinate = _install_stubs(stack)
    frontend.started_marker_value = True  # pretend frontend was already running

    def failing_start(log):  # type: ignore[no-untyped-def]
        return ServiceResult(ok=False, message="backend failed")

    backend.start = failing_start  # type: ignore[method-assign]

    result = stack.start(lambda *_: None, "backend")

    assert result.ok is False
    assert frontend.calls == []  # cleanup did not touch frontend


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


def _install_preview_stub(
    stack: WaypointStack, *, configured: bool = True, pid_path: Path | None = None
) -> _StubService:
    preview = _StubService(
        "frontend-preview", optional=True, configured=configured, pid_path=pid_path
    )
    stack.frontend_preview = preview  # type: ignore[assignment]
    return preview


def _names(services: tuple[object, ...] | None) -> list[str]:
    assert services is not None
    return [svc.name for svc in services]  # type: ignore[attr-defined]


def test_stack_wires_the_preview_to_the_other_variant(
    state_dir: Path, tmp_path: Path
) -> None:
    stack = WaypointStack(
        _build_config(
            tmp_path,
            state_dir,
            frontend_variant="v2",
            frontend_port=3010,
            frontend_preview_port=3011,
        )
    )

    assert (stack.frontend.name, stack.frontend.variant, stack.frontend.port) == (
        "frontend",
        "v2",
        3010,
    )
    preview = stack.frontend_preview
    assert (preview.name, preview.variant, preview.port) == (
        "frontend-preview",
        "v1",
        3011,
    )
    assert preview.optional is True and stack.frontend.optional is False


def test_select_without_a_preview_matches_the_two_service_stack(
    state_dir: Path, tmp_path: Path
) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))

    assert _names(stack.select("backend")) == ["backend"]
    assert _names(stack.select("frontend")) == ["frontend"]
    assert _names(stack.select("all")) == ["backend", "frontend"]
    assert stack.select("teleporter") is None
    assert stack.preview_active is False


def test_select_with_a_preview_groups_it_under_frontend(
    state_dir: Path, tmp_path: Path
) -> None:
    stack = WaypointStack(
        _build_config(tmp_path, state_dir, frontend_preview_port=3011)
    )

    assert _names(stack.select("backend")) == ["backend"]
    assert _names(stack.select("frontend")) == ["frontend", "frontend-preview"]
    assert _names(stack.select("frontend-preview")) == ["frontend-preview"]
    assert _names(stack.select("all")) == ["backend", "frontend", "frontend-preview"]


def test_unconfigured_but_running_preview_stays_selectable(
    state_dir: Path, tmp_path: Path
) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))
    pid_path = stack.frontend_preview.pid_path
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{os.getpid()}\n")

    assert stack.preview_active is True
    assert _names(stack.select("frontend")) == ["frontend", "frontend-preview"]


def test_group_actions_cover_the_preview(state_dir: Path, tmp_path: Path) -> None:
    stack = WaypointStack(
        _build_config(tmp_path, state_dir, frontend_preview_port=3011)
    )
    backend, frontend, caffeinate = _install_stubs(stack)
    preview = _install_preview_stub(stack)

    assert stack.restart("frontend", lambda *_: None).ok is True

    assert frontend.calls == ["stop", "start"]
    assert preview.calls == ["stop", "start"]
    assert backend.calls == []


def test_preview_target_leaves_the_primary_alone(
    state_dir: Path, tmp_path: Path
) -> None:
    stack = WaypointStack(
        _build_config(tmp_path, state_dir, frontend_preview_port=3011)
    )
    backend, frontend, caffeinate = _install_stubs(stack)
    preview = _install_preview_stub(stack)

    assert stack.restart("frontend-preview", lambda *_: None).ok is True

    assert preview.calls == ["stop", "start"]
    assert frontend.calls == [] and backend.calls == []


def test_all_starts_and_stops_the_preview_too(state_dir: Path, tmp_path: Path) -> None:
    stack = WaypointStack(
        _build_config(tmp_path, state_dir, frontend_preview_port=3011)
    )
    backend, frontend, caffeinate = _install_stubs(stack)
    preview = _install_preview_stub(stack)

    assert stack.start(lambda *_: None).ok is True
    assert stack.stop(lambda *_: None).ok is True

    assert preview.calls == ["start", "stop"]
    assert caffeinate.calls == ["start", "stop"]


def test_a_failing_preview_leaves_the_rest_of_the_stack_up(
    state_dir: Path, tmp_path: Path
) -> None:
    stack = WaypointStack(
        _build_config(tmp_path, state_dir, frontend_preview_port=3011)
    )
    backend, frontend, caffeinate = _install_stubs(stack)
    preview = _install_preview_stub(stack)

    def failing_start(log):  # type: ignore[no-untyped-def]
        preview.calls.append("start")
        preview.started_marker_value = True
        return ServiceResult(ok=False, message="frontend-preview build failed")

    preview.start = failing_start  # type: ignore[method-assign]
    logs: list[tuple[str, str]] = []

    result = stack.start(lambda s, line: logs.append((s, line)))

    assert result.ok is False
    assert "frontend-preview build failed" in result.message
    assert preview.calls == ["start", "stop"]
    assert backend.calls == ["start"] and frontend.calls == ["start"]
    assert caffeinate.calls == ["start"]
    assert any("backend: stopped" in line for _, line in logs)  # status still emitted


def test_a_failing_primary_still_stops_everything_started(
    state_dir: Path, tmp_path: Path
) -> None:
    stack = WaypointStack(
        _build_config(tmp_path, state_dir, frontend_preview_port=3011)
    )
    backend, frontend, caffeinate = _install_stubs(stack)
    preview = _install_preview_stub(stack)

    def failing_start(log):  # type: ignore[no-untyped-def]
        return ServiceResult(ok=False, message="frontend failed")

    frontend.start = failing_start  # type: ignore[method-assign]

    result = stack.start(lambda *_: None)

    assert result.ok is False
    assert backend.calls == ["start", "stop"]
    assert preview.calls == ["start", "stop"]
    assert caffeinate.calls == []


def test_preview_target_requires_a_configured_preview(
    state_dir: Path, tmp_path: Path
) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))
    _, frontend, _ = _install_stubs(stack)
    preview = _install_preview_stub(stack, configured=False)

    started = stack.start(lambda *_: None, "frontend-preview")
    restarted = stack.restart("frontend-preview", lambda *_: None)

    for result in (started, restarted):
        assert result.ok is False
        assert "WAYPOINT_STACK_FRONTEND_PREVIEW_PORT" in result.message
    assert preview.calls == []  # a refused restart doesn't stop anything


def test_check_target_reports_unknown_and_unconfigured(
    state_dir: Path, tmp_path: Path
) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))

    for action in ("start", "stop", "restart"):
        assert stack.check_target("all", action) is None
        assert stack.check_target("", action) is None
        assert stack.check_target("frontend", action) is None
        assert "unknown service" in (stack.check_target("teleporter", action) or "")
    assert "not configured" in (stack.check_target("frontend-preview", "start") or "")
    assert "not configured" in (stack.check_target("frontend-preview", "restart") or "")


def test_stop_is_allowed_for_an_unconfigured_preview(
    state_dir: Path, tmp_path: Path
) -> None:
    stack = WaypointStack(_build_config(tmp_path, state_dir))

    assert stack.check_target("frontend-preview", "stop") is None


def test_logs_argv_includes_the_preview_log(state_dir: Path, tmp_path: Path) -> None:
    stack = WaypointStack(
        _build_config(tmp_path, state_dir, frontend_preview_port=3011)
    )

    assert [Path(p).name for p in stack.logs_argv("frontend")[4:]] == [
        "frontend.log",
        "frontend-preview.log",
    ]
    assert [Path(p).name for p in stack.logs_argv("frontend-preview")[4:]] == [
        "frontend-preview.log"
    ]
    assert [Path(p).name for p in stack.logs_argv("all")[4:]] == [
        "backend.log",
        "frontend.log",
        "frontend-preview.log",
    ]


def test_status_lists_the_preview_only_when_active(
    state_dir: Path, tmp_path: Path
) -> None:
    plain = WaypointStack(_build_config(tmp_path, state_dir))
    with_preview = WaypointStack(
        _build_config(tmp_path, state_dir, frontend_preview_port=3011)
    )

    def lines(stack: WaypointStack) -> list[str]:
        out: list[str] = []
        stack.status(lambda _stream, line: out.append(line))
        return out

    assert [line.split(":")[0] for line in lines(plain)] == ["backend", "frontend"]
    assert [line.split(":")[0] for line in lines(with_preview)] == [
        "backend",
        "frontend",
        "frontend-preview",
    ]


def test_format_status_shows_the_variant() -> None:
    running = ServiceStatus(
        name="frontend",
        state="running",
        pid=42,
        port=3010,
        health="healthy",
        variant="v2",
    )
    assert _format_status(running) == (
        "frontend: running pid=42 port=3010 health=healthy variant=v2"
    )
