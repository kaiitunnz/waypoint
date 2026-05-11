from waypointctl.protocol import DaemonLog, DaemonRequest, DaemonResult


def test_request_round_trip() -> None:
    request = DaemonRequest(command="restart", args=["backend"])
    assert request.to_payload() == {"command": "restart", "args": ["backend"]}


def test_request_omits_wait_when_false() -> None:
    request = DaemonRequest(command="stop", args=["backend"])
    assert "wait" not in request.to_payload()


def test_request_includes_wait_when_true() -> None:
    request = DaemonRequest(command="stop", args=["backend"], wait=True)
    assert request.to_payload()["wait"] is True


def test_log_frame_payload() -> None:
    frame = DaemonLog(stream="stderr", line="failure")
    assert frame.to_payload() == {
        "type": "log",
        "stream": "stderr",
        "line": "failure",
    }


def test_result_payload_includes_type_field() -> None:
    result = DaemonResult(ok=False, returncode=17, error="bad")
    payload = result.to_payload()
    assert payload == {
        "type": "result",
        "ok": False,
        "returncode": 17,
        "error": "bad",
    }
    assert DaemonResult.from_payload(payload) == result


def test_result_payload_omits_error_when_none() -> None:
    result = DaemonResult(ok=True, returncode=0)
    payload = result.to_payload()
    assert payload == {"type": "result", "ok": True, "returncode": 0}
