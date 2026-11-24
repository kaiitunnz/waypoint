import os
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from waypointctl import services
from waypointctl.config import StackConfig
from waypointctl.workspace import FrontendVariant


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
    frontend = _frontend(config, port=port)
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


def _frontend(
    config: StackConfig,
    *,
    name: str = "frontend",
    variant: FrontendVariant = "v1",
    port: int | None = None,
    optional: bool = False,
) -> services.FrontendService:
    return services.FrontendService(
        config, name=name, variant=variant, port=port, optional=optional
    )


@pytest.fixture
def fake_runtime(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Stub out npm, the spawned server and its health probe."""
    spawned: list[dict[str, object]] = []

    def fake_spawn(argv, *, cwd, env, log_path):  # type: ignore[no-untyped-def]
        spawned.append({"argv": argv, "cwd": cwd, "env": env})
        return os.getpid()

    monkeypatch.setattr(services, "spawn_detached", fake_spawn)
    monkeypatch.setattr(services, "wait_for_http", lambda *_a, **_kw: True)
    monkeypatch.setattr(services, "http_ok", lambda *_a, **_kw: True)
    monkeypatch.setattr(services.frontend_install, "needs_install", lambda _home: False)
    monkeypatch.setattr(services.frontend_build, "is_fresh", lambda *_a, **_kw: True)
    return spawned


@pytest.mark.parametrize(
    ("variant", "app_dir"), [("v1", "frontend"), ("v2", "frontend-v2")]
)
def test_frontend_serves_its_variant_from_the_app_dir(
    state_dir: Path,
    tmp_path: Path,
    fake_runtime: list[dict[str, object]],
    variant: FrontendVariant,
    app_dir: str,
) -> None:
    config = _build_config(tmp_path, state_dir, backend_port=8797)
    port = _free_port()
    frontend = _frontend(config, variant=variant, port=port)

    result = frontend.start(lambda *_: None)

    assert result.ok is True
    (call,) = fake_runtime
    assert call["argv"] == ["npm", "run", "start"]
    assert call["cwd"] == config.home / app_dir
    env = call["env"]
    assert isinstance(env, dict)
    assert env["PORT"] == str(port)
    assert env["NEXT_PUBLIC_BACKEND_PORT"] == "8797"
    status = frontend.status()
    assert (status.state, status.variant, status.port) == ("running", variant, port)


def test_frontend_dev_mode_runs_dev_in_the_app_dir(
    state_dir: Path, tmp_path: Path, fake_runtime: list[dict[str, object]]
) -> None:
    config = _build_config(tmp_path, state_dir, frontend_dev=True)
    frontend = _frontend(config, variant="v2", port=_free_port())

    assert frontend.start(lambda *_: None).ok is True

    (call,) = fake_runtime
    assert call["argv"] == ["npm", "run", "dev"]
    assert call["cwd"] == config.home / "frontend-v2"


def test_frontend_builds_in_the_app_dir_and_records_it(
    monkeypatch: pytest.MonkeyPatch,
    state_dir: Path,
    tmp_path: Path,
    fake_runtime: list[dict[str, object]],
) -> None:
    config = _build_config(tmp_path, state_dir, backend_port=8797)
    monkeypatch.setattr(services.frontend_build, "is_fresh", lambda *_a, **_kw: False)
    builds: list[object] = []

    real_run = subprocess.run

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[0] != "npm":
            return real_run(cmd, **kwargs)
        builds.append((cmd, kwargs["cwd"]))
        (kwargs["cwd"] / ".next").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(services.subprocess, "run", fake_run)
    frontend = _frontend(config, variant="v2", port=_free_port())

    assert frontend.start(lambda *_: None).ok is True

    assert builds == [(["npm", "run", "build"], config.home / "frontend-v2")]
    marker = config.home / "frontend-v2" / ".next" / "BUILD_BACKEND_PORT"
    assert marker.read_text().strip() == "8797"
    assert not (config.home / "frontend" / ".next").exists()


def test_frontend_installs_at_the_workspace_root(
    monkeypatch: pytest.MonkeyPatch,
    state_dir: Path,
    tmp_path: Path,
    fake_runtime: list[dict[str, object]],
) -> None:
    config = _build_config(tmp_path, state_dir)
    monkeypatch.setattr(services.frontend_install, "needs_install", lambda _home: True)
    installs: list[Path] = []

    def fake_install(home, log_path, env):  # type: ignore[no-untyped-def]
        installs.append(home)
        return 0

    monkeypatch.setattr(services.frontend_install, "run_install", fake_install)

    assert _frontend(config, port=_free_port()).start(lambda *_: None).ok is True
    assert installs == [config.home]


def test_frontend_install_failure_fails_the_start(
    monkeypatch: pytest.MonkeyPatch,
    state_dir: Path,
    tmp_path: Path,
    fake_runtime: list[dict[str, object]],
) -> None:
    config = _build_config(tmp_path, state_dir)
    monkeypatch.setattr(services.frontend_install, "needs_install", lambda _home: True)
    monkeypatch.setattr(services.frontend_install, "run_install", lambda *_a: 3)

    logs: list[tuple[str, str]] = []

    result = _frontend(config, port=_free_port()).start(
        lambda stream, line: logs.append((stream, line))
    )

    assert result.ok is False
    assert "exited with 3" in result.message
    assert ("stderr", "frontend dependency install failed") in logs
    assert fake_runtime == []


def test_frontend_build_failure_fails_the_start_and_records_nothing(
    monkeypatch: pytest.MonkeyPatch,
    state_dir: Path,
    tmp_path: Path,
    fake_runtime: list[dict[str, object]],
) -> None:
    config = _build_config(tmp_path, state_dir, backend_port=8797)
    monkeypatch.setattr(services.frontend_build, "is_fresh", lambda *_a, **_kw: False)
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[0] != "npm":
            return real_run(cmd, **kwargs)
        (kwargs["cwd"] / ".next").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(cmd, 5)

    monkeypatch.setattr(services.subprocess, "run", fake_run)
    logs: list[tuple[str, str]] = []

    result = _frontend(config, variant="v2", port=_free_port()).start(
        lambda stream, line: logs.append((stream, line))
    )

    assert result.ok is False
    assert "build exited with 5" in result.message
    assert ("stderr", "frontend build failed") in logs
    assert fake_runtime == []
    marker = config.home / "frontend-v2" / ".next" / "BUILD_BACKEND_PORT"
    assert not marker.exists()


def test_concurrent_frontend_starts_install_once(
    monkeypatch: pytest.MonkeyPatch,
    state_dir: Path,
    tmp_path: Path,
    fake_runtime: list[dict[str, object]],
) -> None:
    config = _build_config(tmp_path, state_dir)
    installed = threading.Event()
    installs: list[str] = []

    def fake_needs_install(_home: Path) -> bool:
        return not installed.is_set()

    def fake_install(home, log_path, env):  # type: ignore[no-untyped-def]
        installs.append("npm ci")
        time.sleep(0.2)
        installed.set()
        return 0

    monkeypatch.setattr(services.frontend_install, "needs_install", fake_needs_install)
    monkeypatch.setattr(services.frontend_install, "run_install", fake_install)
    primary = _frontend(config, port=_free_port())
    preview = _frontend(
        config,
        name="frontend-preview",
        variant="v2",
        port=_free_port(),
        optional=True,
    )
    results: list[services.ServiceResult] = []
    threads = [
        threading.Thread(
            target=lambda svc=svc: results.append(svc.start(lambda *_: None))
        )
        for svc in (primary, preview)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert installs == ["npm ci"]
    assert [r.ok for r in results] == [True, True]


def test_frontend_services_keep_separate_state_files(
    state_dir: Path, tmp_path: Path, fake_runtime: list[dict[str, object]]
) -> None:
    config = _build_config(tmp_path, state_dir)
    primary = _frontend(config, port=_free_port())
    preview = _frontend(
        config, name="frontend-preview", variant="v2", port=_free_port(), optional=True
    )

    assert primary.start(lambda *_: None).ok is True
    assert preview.start(lambda *_: None).ok is True

    assert primary.pid_path.name == "frontend.pid"
    assert preview.pid_path.name == "frontend-preview.pid"
    assert primary.log_path != preview.log_path
    assert primary.status().variant == "v1"
    assert preview.status().variant == "v2"


def test_status_reports_the_running_variant_not_the_configured_one(
    state_dir: Path, tmp_path: Path, fake_runtime: list[dict[str, object]]
) -> None:
    config = _build_config(tmp_path, state_dir)
    started_as_v1 = _frontend(config, variant="v1", port=_free_port())
    started_as_v1.start(lambda *_: None)

    reconfigured = _frontend(config, variant="v2", port=started_as_v1.port)

    assert reconfigured.status().variant == "v1"


def test_status_variant_is_absent_for_an_unrecorded_process(
    state_dir: Path, tmp_path: Path, fake_runtime: list[dict[str, object]]
) -> None:
    config = _build_config(tmp_path, state_dir)
    frontend = _frontend(config, port=_free_port())
    frontend.start(lambda *_: None)
    frontend.variant_path.unlink()

    status = frontend.status()
    assert status.state == "running" and status.variant is None


def test_status_variant_is_absent_for_a_blank_record(
    state_dir: Path, tmp_path: Path, fake_runtime: list[dict[str, object]]
) -> None:
    config = _build_config(tmp_path, state_dir)
    frontend = _frontend(config, port=_free_port())
    frontend.start(lambda *_: None)
    frontend.variant_path.write_text("\n")

    assert frontend.status().variant is None


def test_stop_clears_the_recorded_variant(
    monkeypatch: pytest.MonkeyPatch,
    state_dir: Path,
    tmp_path: Path,
    fake_runtime: list[dict[str, object]],
) -> None:
    monkeypatch.setattr(services, "terminate_group", lambda _pid: None)
    config = _build_config(tmp_path, state_dir)
    frontend = _frontend(config, port=_free_port())
    frontend.start(lambda *_: None)
    assert frontend.variant_path.exists()

    assert frontend.stop(lambda *_: None).ok is True

    assert not frontend.variant_path.exists()
    assert not frontend.pid_path.exists()


def test_unconfigured_preview_is_a_noop(
    state_dir: Path, tmp_path: Path, fake_runtime: list[dict[str, object]]
) -> None:
    config = _build_config(tmp_path, state_dir)
    preview = _frontend(config, name="frontend-preview", variant="v2", port=None)

    assert preview.configured is False
    assert preview.start(lambda *_: None).ok is True
    assert fake_runtime == []
    assert preview.status().state == "stopped"


def test_unconfigured_but_running_preview_still_reports_status(
    state_dir: Path, tmp_path: Path
) -> None:
    config = _build_config(tmp_path, state_dir)
    preview = _frontend(config, name="frontend-preview", variant="v2", port=None)
    preview.pid_path.parent.mkdir(parents=True, exist_ok=True)
    preview.pid_path.write_text(f"{os.getpid()}\n")

    status = preview.status()

    assert (status.state, status.pid) == ("running", os.getpid())
    assert status.port is None


def test_frontend_start_refuses_when_its_port_is_in_use(
    state_dir: Path,
    tmp_path: Path,
    http_server: HTTPServer,
    fake_runtime: list[dict[str, object]],
) -> None:
    config = _build_config(tmp_path, state_dir)
    preview = _frontend(
        config,
        name="frontend-preview",
        variant="v2",
        port=http_server.server_address[1],
        optional=True,
    )

    result = preview.start(lambda *_: None)

    assert result.ok is False
    assert "frontend-preview port" in result.message


def _mark_running(name: str, variant: str | None) -> None:
    pid_path = services.pid_file_for(name)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{os.getpid()}\n")
    if variant is not None:
        services.variant_file_for(name).write_text(f"{variant}\n")


@pytest.mark.parametrize(
    ("name", "sibling"),
    [("frontend-preview", "frontend"), ("frontend", "frontend-preview")],
)
def test_install_is_refused_under_a_running_sibling(
    monkeypatch: pytest.MonkeyPatch,
    state_dir: Path,
    tmp_path: Path,
    fake_runtime: list[dict[str, object]],
    name: str,
    sibling: str,
) -> None:
    config = _build_config(tmp_path, state_dir)
    monkeypatch.setattr(services.frontend_install, "needs_install", lambda _home: True)
    installs: list[object] = []
    monkeypatch.setattr(
        services.frontend_install, "run_install", lambda *a: installs.append(a) or 0
    )
    _mark_running(sibling, "v1")
    frontend = _frontend(config, name=name, variant="v2", port=_free_port())

    result = frontend.start(lambda *_: None)

    assert result.ok is False
    assert f"while {sibling} is running" in result.message
    assert "waypointctl restart frontend" in result.message
    assert installs == [] and fake_runtime == []


def test_conflict_previews_the_start_guard_without_starting(
    monkeypatch: pytest.MonkeyPatch,
    state_dir: Path,
    tmp_path: Path,
    fake_runtime: list[dict[str, object]],
) -> None:
    config = _build_config(tmp_path, state_dir)
    preview = _frontend(
        config, name="frontend-preview", variant="v2", port=_free_port(), optional=True
    )
    assert preview.conflict() is None

    _mark_running("frontend", "v1")
    monkeypatch.setattr(services.frontend_install, "needs_install", lambda _home: True)

    assert "while frontend is running" in (preview.conflict() or "")
    assert fake_runtime == []


def test_start_beside_a_sibling_serving_a_different_app_is_fine(
    state_dir: Path, tmp_path: Path, fake_runtime: list[dict[str, object]]
) -> None:
    config = _build_config(tmp_path, state_dir)
    _mark_running("frontend", "v1")
    preview = _frontend(
        config, name="frontend-preview", variant="v2", port=_free_port(), optional=True
    )

    assert preview.start(lambda *_: None).ok is True
    assert len(fake_runtime) == 1


def test_start_is_refused_when_the_sibling_already_serves_that_app(
    state_dir: Path, tmp_path: Path, fake_runtime: list[dict[str, object]]
) -> None:
    config = _build_config(tmp_path, state_dir)
    _mark_running("frontend", "v1")
    preview = _frontend(
        config, name="frontend-preview", variant="v1", port=_free_port(), optional=True
    )

    result = preview.start(lambda *_: None)

    assert result.ok is False
    assert "already serving v1" in result.message
    assert fake_runtime == []
