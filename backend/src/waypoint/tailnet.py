import asyncio
import ipaddress
import json
import logging
import os
import shutil
from typing import Any

from pydantic import BaseModel, Field

log = logging.getLogger("waypoint.tailnet")

DEFAULT_PORT = 8787
MACOS_FALLBACK_BIN = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


class TailnetPeer(BaseModel):
    name: str
    dns_name: str | None = None
    ip: str
    online: bool = False
    os: str | None = None
    is_self: bool = False


class TailnetSnapshot(BaseModel):
    available: bool
    error: str | None = None
    peers: list[TailnetPeer] = Field(default_factory=list)


def _resolve_binary() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    if os.path.exists(MACOS_FALLBACK_BIN):
        return MACOS_FALLBACK_BIN
    return None


async def fetch_snapshot() -> TailnetSnapshot:
    binary = _resolve_binary()
    if binary is None:
        return TailnetSnapshot(available=False, error="tailscale binary not found on PATH")
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            "status",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
    except FileNotFoundError as exc:
        return TailnetSnapshot(available=False, error=f"failed to spawn tailscale: {exc}")
    if process.returncode != 0:
        message = stderr.decode().strip() or f"tailscale exited with {process.returncode}"
        log.warning("tailscale status failed: %s", message)
        return TailnetSnapshot(available=False, error=message)
    try:
        payload = json.loads(stdout.decode() or "{}")
    except json.JSONDecodeError as exc:
        return TailnetSnapshot(available=False, error=f"could not parse tailscale output: {exc}")
    return _parse_snapshot(payload)


def _parse_snapshot(payload: dict[str, Any]) -> TailnetSnapshot:
    backend_state = payload.get("BackendState")
    if backend_state and backend_state != "Running":
        return TailnetSnapshot(available=False, error=f"tailscale state: {backend_state}")
    peers: list[TailnetPeer] = []
    self_peer = _peer_from_node(payload.get("Self"), is_self=True)
    if self_peer is not None:
        peers.append(self_peer)
    for node in (payload.get("Peer") or {}).values():
        peer = _peer_from_node(node, is_self=False)
        if peer is not None:
            peers.append(peer)
    peers.sort(key=lambda peer: (not peer.is_self, not peer.online, peer.name.lower()))
    return TailnetSnapshot(available=True, peers=peers)


def _peer_from_node(node: dict[str, Any] | None, *, is_self: bool) -> TailnetPeer | None:
    if not node:
        return None
    ip = _first_ipv4(node.get("TailscaleIPs"))
    if ip is None:
        return None
    name = node.get("HostName") or node.get("DNSName") or ip
    return TailnetPeer(
        name=name,
        dns_name=_normalize_dns(node.get("DNSName")),
        ip=ip,
        online=bool(node.get("Online", False)) or is_self,
        os=node.get("OS"),
        is_self=is_self,
    )


def _first_ipv4(values: list[Any] | None) -> str | None:
    if not values:
        return None
    for raw in values:
        try:
            address = ipaddress.ip_address(str(raw))
        except ValueError:
            continue
        if isinstance(address, ipaddress.IPv4Address):
            return str(address)
    return None


def _normalize_dns(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).rstrip(".")
    return text or None
