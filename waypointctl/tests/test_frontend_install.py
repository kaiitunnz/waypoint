import os
import subprocess
from pathlib import Path

from waypointctl.frontend_install import needs_install, run_install


def _make_frontend(root: Path) -> Path:
    frontend = root / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text("{}")
    (frontend / "package-lock.json").write_text("{}")
    return frontend


def _mark_installed(frontend: Path, *, mtime: float | None = None) -> Path:
    node_modules = frontend / "node_modules"
    node_modules.mkdir(exist_ok=True)
    marker = node_modules / ".package-lock.json"
    marker.write_text("{}")
    if mtime is not None:
        os.utime(marker, (mtime, mtime))
    return marker


def test_needs_install_when_node_modules_missing(tmp_path: Path) -> None:
    _make_frontend(tmp_path)
    assert needs_install(tmp_path) is True


def test_needs_install_when_install_marker_missing(tmp_path: Path) -> None:
    frontend = _make_frontend(tmp_path)
    (frontend / "node_modules").mkdir()
    assert needs_install(tmp_path) is True


def test_no_install_when_marker_is_newer(tmp_path: Path) -> None:
    frontend = _make_frontend(tmp_path)
    os.utime(frontend / "package-lock.json", (1000, 1000))
    os.utime(frontend / "package.json", (1000, 1000))
    _mark_installed(frontend, mtime=2000)
    assert needs_install(tmp_path) is False


def test_needs_install_when_lockfile_is_newer(tmp_path: Path) -> None:
    frontend = _make_frontend(tmp_path)
    _mark_installed(frontend, mtime=1000)
    os.utime(frontend / "package-lock.json", (2000, 2000))
    assert needs_install(tmp_path) is True


def test_needs_install_when_package_json_is_newer(tmp_path: Path) -> None:
    frontend = _make_frontend(tmp_path)
    _mark_installed(frontend, mtime=1000)
    os.utime(frontend / "package.json", (2000, 2000))
    assert needs_install(tmp_path) is True


def test_run_install_prefers_npm_ci(tmp_path: Path, monkeypatch) -> None:
    frontend = _make_frontend(tmp_path)
    log_path = tmp_path / "frontend.log"
    log_path.write_text("")
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = run_install(frontend, log_path)
    assert rc == 0
    assert captured["cmd"] == ["npm", "ci"]
    assert captured["cwd"] == frontend


def test_run_install_falls_back_to_npm_install_without_lockfile(
    tmp_path: Path, monkeypatch
) -> None:
    frontend = _make_frontend(tmp_path)
    (frontend / "package-lock.json").unlink()
    log_path = tmp_path / "frontend.log"
    log_path.write_text("")
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = run_install(frontend, log_path)
    assert rc == 0
    assert captured["cmd"] == ["npm", "install"]


def test_run_install_returns_nonzero_on_failure(tmp_path: Path, monkeypatch) -> None:
    frontend = _make_frontend(tmp_path)
    log_path = tmp_path / "frontend.log"
    log_path.write_text("")

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(cmd, 7)

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = run_install(frontend, log_path)
    assert rc == 7
