from pathlib import Path

import pytest

from waypointctl.paths import resolve_waypoint_home


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
