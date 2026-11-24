import os
import subprocess
from pathlib import Path

import pytest

from waypointctl.frontend_install import needs_install, run_install


def _make_workspace(root: Path) -> None:
    (root / "package.json").write_text("{}")
    (root / "package-lock.json").write_text("{}")
    for rel in ("frontend", "frontend-v2", "packages/web-core"):
        (root / rel).mkdir(parents=True)
        (root / rel / "package.json").write_text("{}")


def _age_manifests(root: Path, mtime: float = 1000) -> None:
    for rel in (
        "package.json",
        "package-lock.json",
        "frontend/package.json",
        "frontend-v2/package.json",
        "packages/web-core/package.json",
    ):
        os.utime(root / rel, (mtime, mtime))


def _mark_installed(root: Path, *, mtime: float | None = None) -> Path:
    node_modules = root / "node_modules"
    node_modules.mkdir(exist_ok=True)
    marker = node_modules / ".package-lock.json"
    marker.write_text("{}")
    if mtime is not None:
        os.utime(marker, (mtime, mtime))
    return marker


def test_needs_install_when_root_node_modules_missing(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    assert needs_install(tmp_path) is True


def test_needs_install_when_install_marker_missing(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    (tmp_path / "node_modules").mkdir()
    assert needs_install(tmp_path) is True


def test_no_install_when_marker_is_newer(tmp_path: Path) -> None:
    _make_workspace(tmp_path)
    _age_manifests(tmp_path)
    _mark_installed(tmp_path, mtime=2000)
    assert needs_install(tmp_path) is False


@pytest.mark.parametrize(
    "touched",
    [
        "package-lock.json",
        "package.json",
        "frontend/package.json",
        "frontend-v2/package.json",
        "packages/web-core/package.json",
    ],
)
def test_needs_install_when_any_manifest_is_newer(tmp_path: Path, touched: str) -> None:
    _make_workspace(tmp_path)
    _age_manifests(tmp_path)
    _mark_installed(tmp_path, mtime=1500)
    os.utime(tmp_path / touched, (2000, 2000))
    assert needs_install(tmp_path) is True


def test_needs_install_when_an_app_still_has_a_standalone_install(
    tmp_path: Path,
) -> None:
    _make_workspace(tmp_path)
    _age_manifests(tmp_path)
    _mark_installed(tmp_path, mtime=2000)
    legacy = tmp_path / "frontend" / "node_modules"
    legacy.mkdir()
    (legacy / ".package-lock.json").write_text("{}")
    assert needs_install(tmp_path) is True


def test_workspace_bin_links_in_an_app_are_not_a_standalone_install(
    tmp_path: Path,
) -> None:
    _make_workspace(tmp_path)
    _age_manifests(tmp_path)
    _mark_installed(tmp_path, mtime=2000)
    (tmp_path / "frontend" / "node_modules" / ".bin").mkdir(parents=True)
    assert needs_install(tmp_path) is False


def test_run_install_runs_npm_ci_at_the_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_workspace(tmp_path)
    log_path = tmp_path / "frontend.log"
    log_path.write_text("")
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        captured["stdin"] = kwargs.get("stdin")
        captured["stderr"] = kwargs.get("stderr")
        stdout = kwargs.get("stdout")
        if stdout is not None:
            stdout.write("npm output\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = run_install(tmp_path, log_path, {"FOO": "bar"})
    assert rc == 0
    assert captured["cmd"] == ["npm", "ci"]
    assert captured["cwd"] == tmp_path
    assert captured["env"] == {"FOO": "bar"}
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.STDOUT
    assert log_path.read_text() == "npm output\n"


def test_run_install_falls_back_to_npm_install_without_lockfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_workspace(tmp_path)
    (tmp_path / "package-lock.json").unlink()
    log_path = tmp_path / "frontend.log"
    log_path.write_text("")
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = run_install(tmp_path, log_path, {})
    assert rc == 0
    assert captured["cmd"] == ["npm", "install"]


def test_run_install_returns_nonzero_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_workspace(tmp_path)
    log_path = tmp_path / "frontend.log"
    log_path.write_text("")

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(cmd, 7)

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = run_install(tmp_path, log_path, {})
    assert rc == 7


def test_run_install_removes_standalone_app_installs_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_workspace(tmp_path)
    standalone = tmp_path / "frontend" / "node_modules"
    standalone.mkdir()
    (standalone / ".package-lock.json").write_text("{}")
    (standalone / "next").mkdir()
    kept = tmp_path / "frontend-v2" / "node_modules" / ".bin"
    kept.mkdir(parents=True)
    log_path = tmp_path / "frontend.log"
    log_path.write_text("")
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0)
    )

    run_install(tmp_path, log_path, {})

    assert not standalone.exists()
    assert kept.exists()
