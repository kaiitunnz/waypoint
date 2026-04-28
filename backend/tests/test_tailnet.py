from typing import Any

from waypoint.tailnet import _parse_snapshot


def test_parse_snapshot_orders_self_then_online_then_offline() -> None:
    payload = {
        "BackendState": "Running",
        "Self": {
            "HostName": "macbook",
            "DNSName": "macbook.tail-scale.ts.net.",
            "OS": "macOS",
            "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"],
            "Online": True,
        },
        "Peer": {
            "p1": {
                "HostName": "phone",
                "TailscaleIPs": ["100.64.0.2"],
                "Online": True,
                "OS": "iOS",
            },
            "p2": {
                "HostName": "linux-box",
                "TailscaleIPs": ["100.64.0.3"],
                "Online": False,
                "OS": "linux",
            },
            "p3": {
                "HostName": "ipad",
                "TailscaleIPs": ["100.64.0.4"],
                "Online": True,
                "OS": "iPadOS",
            },
        },
    }
    snapshot = _parse_snapshot(payload)
    assert snapshot.available is True
    names = [peer.name for peer in snapshot.peers]
    assert names == ["macbook", "ipad", "phone", "linux-box"]
    assert snapshot.peers[0].is_self is True
    assert snapshot.peers[0].ip == "100.64.0.1"
    assert snapshot.peers[0].dns_name == "macbook.tail-scale.ts.net"


def test_parse_snapshot_returns_unavailable_when_backend_stopped() -> None:
    payload: dict[str, Any] = {"BackendState": "Stopped", "Self": None, "Peer": {}}
    snapshot = _parse_snapshot(payload)
    assert snapshot.available is False
    assert "Stopped" in (snapshot.error or "")


def test_parse_snapshot_skips_peers_without_ipv4() -> None:
    payload = {
        "BackendState": "Running",
        "Self": {
            "HostName": "macbook",
            "TailscaleIPs": ["100.64.0.1"],
            "Online": True,
        },
        "Peer": {
            "p1": {
                "HostName": "ipv6-only",
                "TailscaleIPs": ["fd7a:115c:a1e0::2"],
                "Online": True,
            },
        },
    }
    snapshot = _parse_snapshot(payload)
    assert [peer.name for peer in snapshot.peers] == ["macbook"]
