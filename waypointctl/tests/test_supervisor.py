from pathlib import Path

import pytest

from waypointctl import supervisor as supervisor_module
from waypointctl.services import ServiceResult


def test_supervisor_routes_to_stack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(state))

    home = tmp_path / "repo"
    (home / "backend").mkdir(parents=True)
    (home / "frontend").mkdir()
    (home / "scripts").mkdir()

    class FakeStack:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        def start(self, log):  # type: ignore[no-untyped-def]
            log("stdout", "starting")
            self.calls.append(("start", ()))
            return ServiceResult(ok=True)

        def stop(self, log):  # type: ignore[no-untyped-def]
            self.calls.append(("stop", ()))
            return ServiceResult(ok=True)

        def restart(self, target, log):  # type: ignore[no-untyped-def]
            self.calls.append(("restart", (target,)))
            return ServiceResult(ok=True)

        def status(self, log):  # type: ignore[no-untyped-def]
            log("stdout", "backend: stopped")
            return ServiceResult(ok=True)

    monkeypatch.setattr(supervisor_module, "WaypointStack", FakeStack)

    supervisor = supervisor_module.WaypointSupervisor(home)
    result = supervisor.run("start", [])
    assert result.returncode == 0
    assert "starting" in result.stdout


def test_supervisor_status_emits_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(state))

    home = tmp_path / "repo"
    (home / "backend").mkdir(parents=True)
    (home / "frontend").mkdir()
    (home / "scripts").mkdir()

    supervisor = supervisor_module.WaypointSupervisor(home)
    result = supervisor.run("status", [])
    assert result.returncode == 0
    # Status emits at least lines for backend and frontend.
    assert "backend:" in result.stdout
    assert "frontend:" in result.stdout


def test_supervisor_unknown_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(state))
    home = tmp_path / "repo"
    (home / "backend").mkdir(parents=True)
    (home / "frontend").mkdir()
    (home / "scripts").mkdir()

    supervisor = supervisor_module.WaypointSupervisor(home)
    result = supervisor.run("teleport", [])
    assert result.returncode == 1
    assert "unknown command" in result.stderr
