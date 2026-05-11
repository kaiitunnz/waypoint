import socket

from waypointctl.net import port_in_use


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_port_in_use_true_when_listening() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        assert port_in_use(port) is True


def test_port_in_use_false_when_nothing_listens() -> None:
    port = _pick_free_port()
    assert port_in_use(port) is False
