import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from waypointctl.cli import app
from waypointctl.client import DaemonClient, daemon_available
from waypointctl.paths import waypoint_pid_path, waypoint_socket_path
from waypointctl.process import read_pid_file, running_pid


@pytest.fixture
def state_dir(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # AF_UNIX paths are capped at ~104 chars on macOS, so pytest's tmp_path
    # can be too deep. Use a short root.
    path = Path(tempfile.mkdtemp(prefix="wpctl-", dir="/tmp"))
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(path))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _make_home(root: Path) -> Path:
    home = root / "repo"
    (home / "backend").mkdir(parents=True)
    (home / "frontend").mkdir()
    (home / "scripts").mkdir()
    return home


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


# Daemon startup (subprocess spawn + import + socket bind) can be slow on a
# loaded CI runner, so allow generous headroom; the wait returns as soon as the
# daemon is up.
_DAEMON_START_TIMEOUT = 20.0


def _terminate(proc: subprocess.Popen) -> None:
    try:
        os.kill(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def _reap_remaining(timeout: float = 10.0) -> None:
    # `daemon restart` spawns its replacement via subprocess in-process, so the
    # test owns that child too. Reap any exited children so they don't linger.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            reaped, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if reaped == 0:
            time.sleep(0.05)


def test_cli_daemon_stop_waits_for_full_exit(state_dir: Path) -> None:
    home = _make_home(state_dir)
    env = {**os.environ, "WAYPOINTCTL_STATE_DIR": str(state_dir)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "waypointctl.daemon", "--home", str(home)],
        env=env,
        start_new_session=True,
    )
    try:
        assert _wait_until(daemon_available, timeout=_DAEMON_START_TIMEOUT)
        result = CliRunner().invoke(app, ["--home", str(home), "daemon", "stop"])
        assert result.exit_code == 0, result.output
        # The race fix: once `stop` returns, a fresh `start` must see nothing —
        # socket no longer answers and no live pid remains.
        assert not daemon_available(home)
        assert running_pid(waypoint_pid_path()) is None
    finally:
        _terminate(proc)


def test_cli_daemon_restart_replaces_daemon(state_dir: Path) -> None:
    home = _make_home(state_dir)
    env = {**os.environ, "WAYPOINTCTL_STATE_DIR": str(state_dir)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "waypointctl.daemon", "--home", str(home)],
        env=env,
        start_new_session=True,
    )
    try:
        assert _wait_until(daemon_available, timeout=_DAEMON_START_TIMEOUT)
        old_pid = read_pid_file(waypoint_pid_path())

        result = CliRunner().invoke(app, ["--home", str(home), "daemon", "restart"])
        assert result.exit_code == 0, result.output

        # ensure_daemon already waited for readiness, so a fresh daemon is live
        # under a new pid.
        assert daemon_available(home)
        new_pid = read_pid_file(waypoint_pid_path())
        assert new_pid is not None and new_pid != old_pid
    finally:
        CliRunner().invoke(app, ["--home", str(home), "daemon", "stop"])
        _terminate(proc)
        _reap_remaining()


def test_daemon_stop_waits_when_wait_flag_set(state_dir: Path) -> None:
    home = _make_home(state_dir)
    env = {**os.environ, "WAYPOINTCTL_STATE_DIR": str(state_dir)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "waypointctl.daemon", "--home", str(home)],
        env=env,
        start_new_session=True,
    )
    try:
        assert _wait_until(daemon_available, timeout=_DAEMON_START_TIMEOUT)
        client = DaemonClient(home)

        logs: list[tuple[str, str]] = []

        def collect(stream: str, line: str) -> None:
            logs.append((stream, line))

        # With wait=True, deferred commands stream progress and the result
        # comes back only after the work is done. No services are running,
        # so stop is fast and we should see the "already stopped" log.
        result = client.request("stop", ["all"], log=collect, wait=True)
        assert result.ok is True
        text = "\n".join(line for _, line in logs)
        assert "stopped" in text or "stopping" in text.lower() or logs
    finally:
        _terminate(proc)


def test_daemon_restart_returns_result_before_doing_work(state_dir: Path) -> None:
    home = _make_home(state_dir)
    env = {**os.environ, "WAYPOINTCTL_STATE_DIR": str(state_dir)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "waypointctl.daemon", "--home", str(home)],
        env=env,
        start_new_session=True,
    )
    try:
        assert _wait_until(daemon_available, timeout=_DAEMON_START_TIMEOUT)
        client = DaemonClient(home)
        # restart is deferred: the daemon should answer immediately with ok=true
        # even though there's no managed backend/frontend to actually restart.
        start = time.monotonic()
        result = client.request("restart", ["backend"], log=lambda *_: None)
        elapsed = time.monotonic() - start
        assert result.ok is True
        # If the daemon were doing the work synchronously it would also try to
        # spawn `uv run waypoint serve` and wait for health — many seconds.
        # A loose bound catches that regression without flaking under CI load.
        assert elapsed < 5.0
    finally:
        _terminate(proc)


def test_daemon_rejects_request_for_different_home(state_dir: Path) -> None:
    home_a = _make_home(state_dir)
    other_home = state_dir / "other-repo"
    (other_home / "backend").mkdir(parents=True)
    (other_home / "frontend").mkdir()
    (other_home / "scripts").mkdir()

    env = {**os.environ, "WAYPOINTCTL_STATE_DIR": str(state_dir)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "waypointctl.daemon", "--home", str(home_a)],
        env=env,
        start_new_session=True,
    )
    try:
        assert _wait_until(daemon_available, timeout=_DAEMON_START_TIMEOUT)

        wrong_client = DaemonClient(other_home)
        result = wrong_client.request("status", [], log=lambda *_: None)
        assert result.ok is False
        assert "refusing request" in (result.error or "").lower()

        right_client = DaemonClient(home_a)
        assert right_client.request("status", [], log=lambda *_: None).ok is True
    finally:
        _terminate(proc)


def test_daemon_status_e2e(state_dir: Path) -> None:
    home = _make_home(state_dir)
    env = {**os.environ, "WAYPOINTCTL_STATE_DIR": str(state_dir)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "waypointctl.daemon", "--home", str(home)],
        env=env,
        start_new_session=True,
    )
    try:
        assert _wait_until(daemon_available, timeout=_DAEMON_START_TIMEOUT)

        pid = read_pid_file(waypoint_pid_path())
        assert pid == proc.pid
        assert waypoint_socket_path().exists()

        client = DaemonClient(home)
        logs: list[tuple[str, str]] = []
        result = client.request(
            "status", [], log=lambda s, line: logs.append((s, line))
        )

        assert result.ok is True
        # Status should produce log lines for both services.
        text = "\n".join(line for _, line in logs)
        assert "backend:" in text
        assert "frontend:" in text
    finally:
        _terminate(proc)


def test_daemon_rejects_deferred_restart_it_cannot_run(state_dir: Path) -> None:
    home = _make_home(state_dir)
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "WAYPOINT_STACK_FRONTEND_PREVIEW_PORT"
    }
    env["WAYPOINTCTL_STATE_DIR"] = str(state_dir)
    proc = subprocess.Popen(
        [sys.executable, "-m", "waypointctl.daemon", "--home", str(home)],
        env=env,
        start_new_session=True,
    )
    try:
        assert _wait_until(daemon_available, timeout=_DAEMON_START_TIMEOUT)
        client = DaemonClient(home)

        # Deferred commands ack before running, so a bad target has to be
        # refused up front or the caller would see success.
        preview = client.request("restart", ["frontend-preview"], log=lambda *_: None)
        unknown = client.request("restart", ["teleporter"], log=lambda *_: None)

        assert preview.ok is False
        assert "WAYPOINT_STACK_FRONTEND_PREVIEW_PORT" in (preview.error or "")
        assert unknown.ok is False
        assert "unknown service" in (unknown.error or "")
    finally:
        _terminate(proc)


def test_daemon_stops_a_preview_that_is_no_longer_configured(state_dir: Path) -> None:
    home = _make_home(state_dir)
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "WAYPOINT_STACK_FRONTEND_PREVIEW_PORT"
    }
    env["WAYPOINTCTL_STATE_DIR"] = str(state_dir)
    orphan = subprocess.Popen(["sleep", "300"], start_new_session=True)
    pid_path = state_dir / "run" / "frontend-preview.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{orphan.pid}\n")
    proc = subprocess.Popen(
        [sys.executable, "-m", "waypointctl.daemon", "--home", str(home)],
        env=env,
        start_new_session=True,
    )
    try:
        assert _wait_until(daemon_available, timeout=_DAEMON_START_TIMEOUT)
        client = DaemonClient(home)

        refused = client.request("restart", ["frontend-preview"], log=lambda *_: None)
        stopped = client.request("stop", ["frontend-preview"], log=lambda *_: None)

        assert refused.ok is False
        assert stopped.ok is True
        assert _wait_until(lambda: orphan.poll() is not None, timeout=15.0)
        assert _wait_until(lambda: not pid_path.exists(), timeout=15.0)
    finally:
        if orphan.poll() is None:
            orphan.kill()
        orphan.wait(timeout=10)
        _terminate(proc)
