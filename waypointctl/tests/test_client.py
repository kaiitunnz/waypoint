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
