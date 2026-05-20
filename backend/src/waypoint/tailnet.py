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
    if binary is not None:
        return await _run_status([binary, "status", "--json"])

    docker = shutil.which("docker")
    if docker is None:
        return TailnetSnapshot(
            available=False, error="tailscale binary not found on PATH"
        )

    container, lookup_error = await _find_sidecar_container(docker)
    if lookup_error is not None:
        return TailnetSnapshot(available=False, error=lookup_error)
    if container is None:
        return TailnetSnapshot(
            available=False, error="no waypoint tailscale sidecar running"
        )

    return await _run_status(
        [docker, "exec", container, "tailscale", "status", "--json"]
    )


async def _find_sidecar_container(docker: str) -> tuple[str | None, str | None]:
    """Return (container_name, error). `error` is set only when `docker ps` itself fails."""
    try:
        process = await asyncio.create_subprocess_exec(
            docker,
            "ps",
            "--filter",
            "label=waypoint.role=tailscale",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
    except (FileNotFoundError, OSError) as exc:
        return None, f"failed to run docker ps: {exc}"
    if process.returncode != 0:
        message = (
            stderr.decode().strip() or f"docker ps exited with {process.returncode}"
        )
        return None, message
    names = [line for line in stdout.decode().splitlines() if line.strip()]
    if not names:
        return None, None
    if len(names) > 1:
        log.warning(
            "multiple tailscale sidecars running (%s); using %s",
            ", ".join(names),
            names[0],
        )
    return names[0], None


async def _run_status(argv: list[str]) -> TailnetSnapshot:
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
    except (FileNotFoundError, OSError) as exc:
        return TailnetSnapshot(
            available=False, error=f"failed to spawn {argv[0]}: {exc}"
        )
    if process.returncode != 0:
        message = (
            stderr.decode().strip() or f"{argv[0]} exited with {process.returncode}"
        )
        log.warning("%s failed: %s", argv[0], message)
        return TailnetSnapshot(available=False, error=message)
    try:
        payload = json.loads(stdout.decode() or "{}")
    except json.JSONDecodeError as exc:
        return TailnetSnapshot(
            available=False, error=f"could not parse tailscale output: {exc}"
        )
    return _parse_snapshot(payload)


def _parse_snapshot(payload: dict[str, Any]) -> TailnetSnapshot:
    backend_state = payload.get("BackendState")
    if backend_state and backend_state != "Running":
        return TailnetSnapshot(
            available=False, error=f"tailscale state: {backend_state}"
        )
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


def _peer_from_node(
    node: dict[str, Any] | None, *, is_self: bool
) -> TailnetPeer | None:
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
