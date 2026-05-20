import json
from typing import Any

import pytest

from waypoint import tailnet
from waypoint.tailnet import _parse_snapshot, fetch_snapshot


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


class _FakeProcess:
    def __init__(
        self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0
    ):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


def _running_payload(hostname: str) -> dict[str, Any]:
    return {
        "BackendState": "Running",
        "Self": {
            "HostName": hostname,
            "TailscaleIPs": ["100.64.0.1"],
            "Online": True,
        },
        "Peer": {},
    }


def _exec_recorder(
    plans: dict[tuple[str, ...], _FakeProcess],
    calls: list[tuple[str, ...]],
):
    async def fake_exec(*argv: str, **_: Any) -> _FakeProcess:
        calls.append(argv)
        try:
            return plans[argv]
        except KeyError as exc:
            raise AssertionError(f"unexpected subprocess call: {argv}") from exc

    return fake_exec


@pytest.mark.asyncio
async def test_fetch_snapshot_prefers_host_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tailnet.shutil, "which", lambda name: "/usr/bin/tailscale")
    payload = _running_payload("host-machine")
    calls: list[tuple[str, ...]] = []
    plans: dict[tuple[str, ...], _FakeProcess] = {
        ("/usr/bin/tailscale", "status", "--json"): _FakeProcess(
            stdout=json.dumps(payload).encode()
        ),
    }
    monkeypatch.setattr(
        tailnet.asyncio,
        "create_subprocess_exec",
        _exec_recorder(plans, calls),
    )

    snapshot = await fetch_snapshot()

    assert snapshot.available is True
    assert snapshot.peers[0].name == "host-machine"
    assert calls == [("/usr/bin/tailscale", "status", "--json")]


@pytest.mark.asyncio
async def test_fetch_snapshot_falls_back_to_docker_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tailnet.os.path, "exists", lambda _path: False)

    def fake_which(name: str) -> str | None:
        if name == "tailscale":
            return None
        if name == "docker":
            return "/usr/local/bin/docker"
        return None

    monkeypatch.setattr(tailnet.shutil, "which", fake_which)

    payload = _running_payload("waypoint-nat")
    calls: list[tuple[str, ...]] = []
    plans: dict[tuple[str, ...], _FakeProcess] = {
        (
            "/usr/local/bin/docker",
            "ps",
            "--filter",
            "label=waypoint.role=tailscale",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ): _FakeProcess(stdout=b"waypoint-tailscale-nat\n"),
        (
            "/usr/local/bin/docker",
            "exec",
            "waypoint-tailscale-nat",
            "tailscale",
            "status",
            "--json",
        ): _FakeProcess(stdout=json.dumps(payload).encode()),
    }
    monkeypatch.setattr(
        tailnet.asyncio,
        "create_subprocess_exec",
        _exec_recorder(plans, calls),
    )

    snapshot = await fetch_snapshot()

    assert snapshot.available is True
    assert snapshot.peers[0].name == "waypoint-nat"
    assert calls[0][1] == "ps"
    assert calls[1][1] == "exec"


@pytest.mark.asyncio
async def test_fetch_snapshot_reports_no_binary_when_neither_path_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tailnet.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(tailnet.shutil, "which", lambda _name: None)

    snapshot = await fetch_snapshot()

    assert snapshot.available is False
    assert snapshot.error == "tailscale binary not found on PATH"


@pytest.mark.asyncio
async def test_fetch_snapshot_reports_no_sidecar_when_docker_present_but_no_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tailnet.os.path, "exists", lambda _path: False)

    def fake_which(name: str) -> str | None:
        return "/usr/local/bin/docker" if name == "docker" else None

    monkeypatch.setattr(tailnet.shutil, "which", fake_which)

    calls: list[tuple[str, ...]] = []
    plans: dict[tuple[str, ...], _FakeProcess] = {
        (
            "/usr/local/bin/docker",
            "ps",
            "--filter",
            "label=waypoint.role=tailscale",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ): _FakeProcess(stdout=b""),
    }
    monkeypatch.setattr(
        tailnet.asyncio,
        "create_subprocess_exec",
        _exec_recorder(plans, calls),
    )

    snapshot = await fetch_snapshot()

    assert snapshot.available is False
    assert snapshot.error == "tailscale binary not found on PATH"


@pytest.mark.asyncio
async def test_fetch_snapshot_picks_first_when_multiple_sidecars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tailnet.os.path, "exists", lambda _path: False)

    def fake_which(name: str) -> str | None:
        return "/usr/local/bin/docker" if name == "docker" else None

    monkeypatch.setattr(tailnet.shutil, "which", fake_which)

    payload = _running_payload("waypoint-a")
    calls: list[tuple[str, ...]] = []
    plans: dict[tuple[str, ...], _FakeProcess] = {
        (
            "/usr/local/bin/docker",
            "ps",
            "--filter",
            "label=waypoint.role=tailscale",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ): _FakeProcess(stdout=b"waypoint-tailscale-a\nwaypoint-tailscale-b\n"),
        (
            "/usr/local/bin/docker",
            "exec",
            "waypoint-tailscale-a",
            "tailscale",
            "status",
            "--json",
        ): _FakeProcess(stdout=json.dumps(payload).encode()),
    }
    monkeypatch.setattr(
        tailnet.asyncio,
        "create_subprocess_exec",
        _exec_recorder(plans, calls),
    )

    snapshot = await fetch_snapshot()

    assert snapshot.available is True
    assert calls[1][2] == "waypoint-tailscale-a"
