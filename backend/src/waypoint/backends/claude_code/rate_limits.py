from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from waypoint.schemas import SessionRateLimitUsage, UsageWindow

_WINDOWS: tuple[tuple[str, str, int], ...] = (
    ("five_hour", "5h", 5 * 60),
    ("seven_day", "Weekly", 7 * 24 * 60),
    ("seven_day_opus", "Opus", 7 * 24 * 60),
    ("seven_day_sonnet", "Sonnet", 7 * 24 * 60),
)


def parse_claude_usage_payload(
    payload: bytes | dict[str, Any],
    *,
    now: datetime | None = None,
    notes: list[str] | None = None,
) -> SessionRateLimitUsage | None:
    if isinstance(payload, bytes):
        try:
            raw = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
    else:
        raw = payload
    if not isinstance(raw, dict):
        return None

    windows: list[UsageWindow] = []
    for key, label, minutes in _WINDOWS:
        window = raw.get(key)
        if not isinstance(window, dict):
            continue
        percent = _parse_utilization(window.get("utilization"))
        if percent is None:
            continue
        windows.append(
            UsageWindow(
                id=key,
                label=label,
                used_percent=percent,
                window_minutes=minutes,
                resets_at=_parse_iso_datetime(window.get("resets_at")),
            )
        )

    if not windows:
        return None

    return SessionRateLimitUsage(
        source="claude_code",
        updated_at=now or datetime.now(UTC),
        windows=windows,
        notes=list(notes or ["CLI creds"]),
    )


async def probe_claude_usage(
    *,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> SessionRateLimitUsage | None:
    resolved_env = env if env is not None else dict(os.environ)
    credentials = _read_cli_credentials_for_env(resolved_env)
    if credentials is None:
        return None
    access_token, credential_note = credentials
    request = Request(
        "https://api.anthropic.com/api/oauth/usage",
        method="GET",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "claude-code/2.1.5",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    response = await asyncio.wait_for(
        asyncio.to_thread(_fetch_url, request), timeout=timeout_seconds
    )
    if response is None:
        return None
    return parse_claude_usage_payload(response, notes=[credential_note])


def _fetch_url(request: Request) -> bytes | None:
    try:
        with urlopen(request, timeout=30) as response:
            if getattr(response, "status", 200) != 200:
                return None
            return response.read()
    except (HTTPError, URLError, OSError):
        return None


def _read_cli_credentials_for_env(env: dict[str, str]) -> tuple[str, str] | None:
    for path in _credential_paths(env):
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        token = _extract_access_token(raw)
        if token:
            return token, "CLI creds"

    token = _read_keychain_access_token(env)
    if token:
        return token, "CLI creds"
    return None


def _credential_paths(env: dict[str, str]) -> tuple[Path, ...]:
    base = env.get("CLAUDE_CONFIG_DIR")
    root = Path(base).expanduser() if base else Path.home() / ".claude"
    return (
        root / ".credentials.json",
        root / "credentials.json",
    )


def _read_keychain_access_token(env: dict[str, str]) -> str | None:
    if platform.system() != "Darwin":
        return None
    token = _read_keychain_access_token_for_service(
        _canonical_keychain_service_name(), env
    )
    if token:
        return token
    discovered = _discover_hashed_keychain_service_name()
    if discovered is None:
        return None
    return _read_keychain_access_token_for_service(discovered, env)


def _read_keychain_access_token_for_service(
    service: str, env: dict[str, str]
) -> str | None:
    try:
        completed = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                service,
                "-a",
                env.get("USER", ""),
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    token = completed.stdout.strip()
    return token or None


_HASHED_KEYCHAIN_SERVICE_NAME: str | None = None
_HASHED_KEYCHAIN_SERVICE_NAME_RESOLVED = False


def _canonical_keychain_service_name() -> str:
    return "Claude Code-credentials"


def _discover_hashed_keychain_service_name() -> str | None:
    global _HASHED_KEYCHAIN_SERVICE_NAME_RESOLVED
    global _HASHED_KEYCHAIN_SERVICE_NAME
    if _HASHED_KEYCHAIN_SERVICE_NAME_RESOLVED:
        return _HASHED_KEYCHAIN_SERVICE_NAME
    try:
        completed = subprocess.run(
            ["security", "dump-keychain"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        _HASHED_KEYCHAIN_SERVICE_NAME_RESOLVED = True
        return None
    match = re.search(r"(Claude Code-credentials-[^\"\s]+)", completed.stdout)
    if match is not None:
        _HASHED_KEYCHAIN_SERVICE_NAME = match.group(1)
    _HASHED_KEYCHAIN_SERVICE_NAME_RESOLVED = True
    return _HASHED_KEYCHAIN_SERVICE_NAME


def _extract_access_token(raw: str) -> str | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    oauth = payload.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    return token if isinstance(token, str) and token else None


def _parse_utilization(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        percent = float(value)
        if 0.0 <= percent <= 1.0:
            percent *= 100.0
        return max(0.0, min(100.0, percent))
    if isinstance(value, str):
        cleaned = value.strip().replace("%", "")
        try:
            percent = float(cleaned)
        except ValueError:
            return None
        if 0.0 <= percent <= 1.0:
            percent *= 100.0
        return max(0.0, min(100.0, percent))
    return None


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
