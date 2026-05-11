import io
import json
from pathlib import Path

import pytest

from waypointctl import client as client_module
from waypointctl.client import DaemonUnavailableError, _read_frames
from waypointctl.protocol import DaemonResult


def test_read_frames_emits_logs_and_returns_result() -> None:
    payload = (
        json.dumps({"type": "log", "stream": "stdout", "line": "hello"})
        + "\n"
        + json.dumps({"type": "log", "stream": "stderr", "line": "uh oh"})
        + "\n"
        + json.dumps({"type": "result", "ok": False, "returncode": 7, "error": "bad"})
        + "\n"
    )
    reader = io.StringIO(payload)
    received: list[tuple[str, str]] = []

    result = _read_frames(reader, lambda s, line: received.append((s, line)))

    assert received == [("stdout", "hello"), ("stderr", "uh oh")]
    assert result == DaemonResult(ok=False, returncode=7, error="bad")


def test_read_frames_raises_without_result() -> None:
    reader = io.StringIO(
        json.dumps({"type": "log", "stream": "stdout", "line": "alone"}) + "\n"
    )
    with pytest.raises(DaemonUnavailableError, match="without a result"):
        _read_frames(reader, lambda *_: None)


def test_read_frames_ignores_unknown_types() -> None:
    payload = (
        json.dumps({"type": "telemetry", "ignored": True})
        + "\n"
        + json.dumps({"type": "result", "ok": True, "returncode": 0})
        + "\n"
    )
    result = _read_frames(io.StringIO(payload), lambda *_: None)
    assert result.ok is True


def test_daemon_available_false_without_socket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        client_module, "waypoint_socket_path", lambda: tmp_path / "missing.sock"
    )
    assert client_module.daemon_available() is False
    assert client_module.daemon_available(tmp_path) is False


def test_ensure_daemon_waits_when_pid_already_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(tmp_path))

    spawn_count = {"n": 0}
    ready_after = {"n": 2}

    def fake_start_daemon(_home: Path) -> None:
        spawn_count["n"] += 1

    available_calls = {"n": 0}

    def fake_daemon_available(_home: Path | None = None) -> bool:
        available_calls["n"] += 1
        return available_calls["n"] > ready_after["n"]

    monkeypatch.setattr(client_module, "start_daemon", fake_start_daemon)
    monkeypatch.setattr(client_module, "daemon_available", fake_daemon_available)
    # A peer is already starting waypointd; advertise its pid as alive.
    monkeypatch.setattr(client_module, "_live_daemon_pid", lambda: 4242)
    monkeypatch.setattr(client_module, "DAEMON_POLL_INTERVAL_SECONDS", 0.0)

    client = client_module.ensure_daemon(tmp_path)

    assert isinstance(client, client_module.DaemonClient)
    assert spawn_count["n"] == 0
