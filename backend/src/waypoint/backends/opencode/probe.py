"""Which API an OpenCode server serves."""

from dataclasses import dataclass

from waypoint.backends.opencode.client import (
    OpenCodeHttpClient,
    OpenCodeNonJsonError,
    http_status,
)

SUPPORTED_MAJOR_VERSIONS = frozenset({1})

# Statuses that mean the server is up but does not serve this API.
_REFUSALS = frozenset({401, 403, 404})


class IncompatibleServerError(RuntimeError):
    """The server answers, with an API Waypoint does not speak."""


@dataclass(frozen=True)
class ServerInfo:
    major: int
    # 1.x does not report one on the route that identifies it.
    version: str | None = None


async def probe_server(client: OpenCodeHttpClient) -> ServerInfo:
    """Identify the server, raising ``IncompatibleServerError`` if it is not one
    Waypoint can drive. Any other error means it is not answering yet."""
    info = await _identify(client)
    if info.major not in SUPPORTED_MAJOR_VERSIONS:
        raise IncompatibleServerError(f"OpenCode {info.version} is not supported")
    return info


async def _identify(client: OpenCodeHttpClient) -> ServerInfo:
    # 1.x serves /config. 2.x answers it with its web page and reports its
    # version at /api/info.
    try:
        await client.get("/config")
        return ServerInfo(major=1)
    except OpenCodeNonJsonError as exc:
        not_v1 = str(exc)
    except Exception as exc:
        if (status := http_status(exc)) not in _REFUSALS:
            raise
        not_v1 = f"GET /config returned HTTP {status}"
        if status != 404:
            raise IncompatibleServerError(not_v1) from exc
    try:
        payload = await client.get("/api/info")
    except OpenCodeNonJsonError as exc:
        raise IncompatibleServerError(not_v1) from exc
    except Exception as exc:
        if http_status(exc) not in _REFUSALS:
            raise
        raise IncompatibleServerError(not_v1) from exc
    version = payload.get("version") if isinstance(payload, dict) else None
    major = version.split(".")[0] if isinstance(version, str) else ""
    if not major.isdecimal():
        raise IncompatibleServerError(not_v1)
    return ServerInfo(major=int(major), version=version)
