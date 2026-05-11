from pathlib import Path

import pytest

from waypointctl.paths import (
    log_file_for,
    pid_file_for,
    resolve_state_dir,
    resolve_waypoint_home,
    started_marker_for,
    state_log_dir,
    state_run_dir,
    waypoint_log_path,
    waypoint_pid_path,
    waypoint_socket_path,
)


def test_resolve_waypoint_home_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WAYPOINT_HOME", str(tmp_path))
    assert resolve_waypoint_home() == tmp_path.resolve()


def test_resolve_waypoint_home_walks_up_from_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "repo"
    nested = repo_root / "backend" / "src"
    (repo_root / "backend").mkdir(parents=True)
    (repo_root / "frontend").mkdir()
    (repo_root / "scripts").mkdir()
    nested.mkdir(parents=True)

    monkeypatch.chdir(nested)
    monkeypatch.delenv("WAYPOINT_HOME", raising=False)

    assert resolve_waypoint_home() == repo_root.resolve()


def test_resolve_waypoint_home_requires_explicit_anchor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WAYPOINT_HOME", raising=False)

    with pytest.raises(RuntimeError, match="WAYPOINT_HOME"):
        resolve_waypoint_home()


def test_resolve_state_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WAYPOINTCTL_STATE_DIR", raising=False)
    assert resolve_state_dir() == Path("~/.waypoint").expanduser().resolve()


def test_resolve_state_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(tmp_path / "custom"))
    assert resolve_state_dir() == (tmp_path / "custom").resolve()


def test_state_paths_anchor_on_state_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(tmp_path))
    root = tmp_path.resolve()

    assert state_run_dir() == root / "run"
    assert state_log_dir() == root / "logs"
    assert waypoint_socket_path() == root / "run" / "waypointd.sock"
    assert waypoint_pid_path() == root / "run" / "waypointd.pid"
    assert waypoint_log_path() == root / "logs" / "waypointd.log"
    assert pid_file_for("backend") == root / "run" / "backend.pid"
    assert log_file_for("frontend") == root / "logs" / "frontend.log"
    assert started_marker_for("backend") == root / "run" / "backend.started-this-run"
