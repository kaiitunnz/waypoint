from pathlib import Path

from waypointctl import supervisor as supervisor_module


def test_supervisor_delegates_to_legacy_runner(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeCompleted:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def fake_run_legacy_command(
        home: Path, command: str, args: list[str]
    ) -> FakeCompleted:
        captured["home"] = home
        captured["command"] = command
        captured["args"] = args
        return FakeCompleted()

    monkeypatch.setattr(
        supervisor_module, "run_legacy_command", fake_run_legacy_command
    )

    supervisor = supervisor_module.WaypointSupervisor(tmp_path)
    result = supervisor.restart("backend")

    assert captured == {
        "home": tmp_path,
        "command": "restart",
        "args": ["backend"],
    }
    assert result.returncode == 0
    assert result.stdout == "ok\n"
