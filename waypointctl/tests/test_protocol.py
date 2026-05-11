from waypointctl.protocol import DaemonLog, DaemonRequest, DaemonResult


def test_request_round_trip() -> None:
    request = DaemonRequest(command="restart", args=["backend"])
    assert request.to_payload() == {"command": "restart", "args": ["backend"]}


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
