from pathlib import Path

import pytest

from waypointctl import client as client_module
from waypointctl.protocol import DaemonResponse


def test_ensure_daemon_starts_background_daemon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called: dict[str, object] = {}

    def fake_start_daemon(home: Path) -> None:
        called["home"] = home

    class FakeClient:
        def __init__(self, home: Path) -> None:
            self.home = home

        def request(self, command: str, args: list[str]) -> DaemonResponse:
            called["request"] = (command, args)
            return DaemonResponse(ok=True)

    monkeypatch.setattr(client_module, "start_daemon", fake_start_daemon)
    monkeypatch.setattr(client_module, "DaemonClient", FakeClient)
    monkeypatch.setattr(client_module, "read_pid_file", lambda path: None)
    monkeypatch.setattr(client_module, "remove_if_stale", lambda path: None)
    monkeypatch.setattr(client_module, "is_pid_running", lambda pid: False)
    monkeypatch.setattr(
        client_module, "waypoint_socket_path", lambda home: tmp_path / "waypointd.sock"
    )
    monkeypatch.setattr(
        client_module, "waypoint_pid_path", lambda home: tmp_path / "waypointd.pid"
    )

    client = client_module.ensure_daemon(tmp_path)

    assert isinstance(client, FakeClient)
    assert called["home"] == tmp_path
    assert called["request"] == ("ping", [])
