import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from waypointctl.health import http_ok, wait_for_http


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args: object) -> None:
        pass


class _ErrorHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(500)
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        pass


def _serve(
    handler: type[BaseHTTPRequestHandler],
) -> tuple[HTTPServer, threading.Thread]:
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture
def ok_server() -> HTTPServer:
    server, thread = _serve(_OkHandler)
    yield server
    server.shutdown()
    thread.join(timeout=2)


@pytest.fixture
def error_server() -> HTTPServer:
    server, thread = _serve(_ErrorHandler)
    yield server
    server.shutdown()
    thread.join(timeout=2)


def test_http_ok_true_for_2xx(ok_server: HTTPServer) -> None:
    port = ok_server.server_address[1]
    assert http_ok(f"http://127.0.0.1:{port}") is True


def test_http_ok_false_for_5xx(error_server: HTTPServer) -> None:
    port = error_server.server_address[1]
    assert http_ok(f"http://127.0.0.1:{port}") is False


def test_http_ok_false_when_unreachable() -> None:
    assert http_ok("http://127.0.0.1:1") is False


def test_wait_for_http_returns_true_when_reachable(ok_server: HTTPServer) -> None:
    port = ok_server.server_address[1]
    assert wait_for_http(
        f"http://127.0.0.1:{port}", timeout_seconds=2.0, poll_interval_seconds=0.05
    )


def test_wait_for_http_returns_false_on_dead_pid() -> None:
    # PID 1 always exists; pick a clearly-dead PID instead.
    assert (
        wait_for_http(
            "http://127.0.0.1:1",
            timeout_seconds=2.0,
            pid=999_999,
            poll_interval_seconds=0.05,
        )
        is False
    )


def test_wait_for_http_returns_false_on_timeout() -> None:
    assert (
        wait_for_http(
            "http://127.0.0.1:1",
            timeout_seconds=0.2,
            poll_interval_seconds=0.05,
        )
        is False
    )
