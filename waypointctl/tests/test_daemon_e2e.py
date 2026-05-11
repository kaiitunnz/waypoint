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

from waypointctl.client import DaemonClient, daemon_available
from waypointctl.paths import waypoint_pid_path, waypoint_socket_path
from waypointctl.process import read_pid_file


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


def test_daemon_restart_returns_result_before_doing_work(state_dir: Path) -> None:
    home = _make_home(state_dir)
    env = {**os.environ, "WAYPOINTCTL_STATE_DIR": str(state_dir)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "waypointctl.daemon", "--home", str(home)],
        env=env,
        start_new_session=True,
    )
    try:
        assert _wait_until(daemon_available, timeout=5.0)
        client = DaemonClient(home)
        # restart is deferred: the daemon should answer immediately with ok=true
        # even though there's no managed backend/frontend to actually restart.
        start = time.monotonic()
        result = client.request("restart", ["backend"], log=lambda *_: None)
        elapsed = time.monotonic() - start
        assert result.ok is True
        # If the daemon were doing the work synchronously it would also try to
        # spawn `uv run waypoint serve` and wait for health — far slower than
        # this. A loose bound is enough to catch a regression.
        assert elapsed < 2.0
    finally:
        try:
            os.kill(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def test_daemon_status_e2e(state_dir: Path) -> None:
    home = _make_home(state_dir)
    env = {**os.environ, "WAYPOINTCTL_STATE_DIR": str(state_dir)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "waypointctl.daemon", "--home", str(home)],
        env=env,
        start_new_session=True,
    )
    try:
        assert _wait_until(daemon_available, timeout=5.0)

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
        try:
            os.kill(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)
