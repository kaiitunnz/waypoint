from waypointctl.protocol import DaemonRequest, DaemonResponse


def test_daemon_request_round_trip() -> None:
    request = DaemonRequest(command="restart", args=["backend"])
    payload = request.to_payload()

    assert payload == {"command": "restart", "args": ["backend"]}


def test_daemon_response_round_trip() -> None:
    response = DaemonResponse(
        ok=False, returncode=17, stdout="out", stderr="err", error="bad"
    )
    payload = response.to_payload()

    assert payload == {
        "ok": False,
        "returncode": 17,
        "stdout": "out",
        "stderr": "err",
        "error": "bad",
    }
    assert DaemonResponse.from_payload(payload) == response
