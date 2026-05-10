from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from waypoint.schemas import SessionRateLimitUsage, UsageWindow

log = logging.getLogger("waypoint.claude_code.rate_limits")

_CLAUDE_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_CLAUDE_MESSAGES_MODEL = "claude-haiku-4-5-20251001"
_CLAUDE_MESSAGES_VERSION = "2023-06-01"
_WINDOWS: tuple[tuple[str, str, int], ...] = (
    ("five_hour", "5h", 5 * 60),
    ("seven_day", "Weekly", 7 * 24 * 60),
    ("seven_day_opus", "Opus", 7 * 24 * 60),
    ("seven_day_sonnet", "Sonnet", 7 * 24 * 60),
)
_CLAUDE_USER_AGENT: str | None = None
_CLAUDE_USER_AGENT_RESOLVED = False


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
        log.warning("claude rate-limit probe found no CLI credentials")
        return None
    access_token, expires_at, credential_note = credentials
    if _is_access_token_expired(expires_at):
        log.info(
            "claude rate-limit probe: cached access token is expired; "
            "skipping HTTP call and surfacing expiry"
        )
        return _expired_credentials_snapshot(credential_note, resolved_env)
    response = await asyncio.wait_for(
        asyncio.to_thread(_fetch_claude_messages_usage, access_token),
        timeout=timeout_seconds,
    )
    if response is None:
        log.warning("claude rate-limit request failed before receiving a response")
        return None
    notes = [credential_note, *_read_oauth_account_notes(resolved_env)]
    usage = parse_claude_rate_limit_headers(
        response.headers,
        now=datetime.now(UTC),
        notes=_unique_notes(notes),
    )
    if usage is not None:
        return usage
    if response.status == 401:
        log.warning(
            "claude rate-limit response was unauthenticated "
            f"(status=401 preview={response.body[:240].decode('utf-8', errors='replace')!r})",
        )
        return _expired_credentials_snapshot(credential_note, resolved_env)
    if response.status == 429:
        retry_after = _header_value(response.headers, "Retry-After")
        if retry_after:
            notes.append(f"rate limited; retry after {retry_after}s")
        else:
            notes.append("rate limited")
        log.warning(
            "claude rate-limit response was throttled "
            f"(status={response.status} retry_after={retry_after!r} "
            f"preview={response.body[:240].decode('utf-8', errors='replace')!r})",
        )
        return SessionRateLimitUsage(
            source="claude_code",
            updated_at=datetime.now(UTC),
            windows=[],
            notes=_unique_notes(notes),
        )
    if response.status != 200:
        log.warning(
            "claude rate-limit response was not successful "
            f"(status={response.status} "
            f"preview={response.body[:240].decode('utf-8', errors='replace')!r})",
        )
        return None
    log.warning(
        "claude rate-limit response did not include usable rate-limit headers "
        f"(status={response.status} "
        f"headers={sorted(_normalize_headers(response.headers).keys())})",
    )
    return None


_EXPIRED_CREDENTIALS_NOTE = "credentials expired — run `claude` to refresh"
_EXPIRY_SKEW = timedelta(seconds=60)


def _is_access_token_expired(expires_at: datetime | None) -> bool:
    # Missing expiry: matches the reference Swift app (assume valid; let the
    # API tell us otherwise).
    if expires_at is None:
        return False
    return expires_at <= datetime.now(UTC) + _EXPIRY_SKEW


def _expired_credentials_snapshot(
    credential_note: str, env: dict[str, str]
) -> SessionRateLimitUsage:
    return SessionRateLimitUsage(
        source="claude_code",
        updated_at=datetime.now(UTC),
        windows=[],
        notes=_unique_notes(
            [
                credential_note,
                _EXPIRED_CREDENTIALS_NOTE,
                *_read_oauth_account_notes(env),
            ]
        ),
    )


def parse_claude_rate_limit_headers(
    headers: dict[str, str] | Any,
    *,
    now: datetime | None = None,
    notes: list[str] | None = None,
) -> SessionRateLimitUsage | None:
    normalized = _normalize_headers(headers)
    windows: list[UsageWindow] = []
    for window_id, label, minutes, header_prefix in (
        ("five_hour", "5h", 5 * 60, "5h"),
        ("seven_day", "Weekly", 7 * 24 * 60, "7d"),
    ):
        utilization = _header_value(
            normalized, f"anthropic-ratelimit-unified-{header_prefix}-utilization"
        )
        resets_at = _header_value(
            normalized, f"anthropic-ratelimit-unified-{header_prefix}-reset"
        )
        percent = _parse_utilization(utilization)
        if percent is None:
            continue
        reset_at = _parse_iso_datetime_from_unix_seconds(resets_at)
        if reset_at is not None and reset_at < (now or datetime.now(UTC)):
            percent = 0.0
        windows.append(
            UsageWindow(
                id=window_id,
                label=label,
                used_percent=percent,
                window_minutes=minutes,
                resets_at=reset_at,
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


def _claude_user_agent() -> str:
    global _CLAUDE_USER_AGENT
    global _CLAUDE_USER_AGENT_RESOLVED
    if _CLAUDE_USER_AGENT_RESOLVED:
        return _CLAUDE_USER_AGENT or "claude-code/2.1.5"
    _CLAUDE_USER_AGENT_RESOLVED = True
    try:
        completed = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _CLAUDE_USER_AGENT = "claude-code/2.1.5"
        return _CLAUDE_USER_AGENT
    if completed.returncode != 0:
        _CLAUDE_USER_AGENT = "claude-code/2.1.5"
        return _CLAUDE_USER_AGENT
    match = re.search(r"\d+(?:\.\d+)+(?:[-+][A-Za-z0-9.-]+)?", completed.stdout)
    version = (
        match.group(0)
        if match is not None
        else completed.stdout.strip().split()[0] if completed.stdout.strip() else ""
    )
    _CLAUDE_USER_AGENT = f"claude-code/{version}" if version else "claude-code/2.1.5"
    return _CLAUDE_USER_AGENT


def _fetch_claude_messages_usage(access_token: str) -> _ClaudeHTTPResponse | None:
    request = Request(
        _CLAUDE_MESSAGES_URL,
        data=json.dumps(
            {
                "model": _CLAUDE_MESSAGES_MODEL,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}],
            }
        ).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": _claude_user_agent(),
            "anthropic-beta": "oauth-2025-04-20",
            "anthropic-version": _CLAUDE_MESSAGES_VERSION,
            "Accept": "application/json",
        },
    )
    return _fetch_url(request)


@dataclass(frozen=True)
class _ClaudeHTTPResponse:
    status: int
    headers: dict[str, str]
    body: bytes


def _fetch_url(request: Request) -> _ClaudeHTTPResponse | None:
    try:
        with urlopen(request, timeout=30) as response:
            headers = {key: value for key, value in response.headers.items()}
            return _ClaudeHTTPResponse(
                status=getattr(response, "status", 200),
                headers=headers,
                body=response.read(),
            )
    except HTTPError as exc:
        headers = {key: value for key, value in getattr(exc, "headers", {}).items()}
        try:
            body = exc.read() if exc.fp is not None else b""
        except OSError:
            body = b""
        return _ClaudeHTTPResponse(status=exc.code, headers=headers, body=body)
    except (URLError, OSError):
        return None


def _read_cli_credentials_for_env(
    env: dict[str, str],
) -> tuple[str, datetime | None, str] | None:
    for path in _credential_paths(env):
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        parsed = _extract_oauth_fields(raw)
        if parsed is not None:
            access_token, expires_at = parsed
            return access_token, expires_at, "CLI creds"

    keychain_raw = _read_keychain_access_token(env)
    if keychain_raw:
        parsed = _extract_oauth_fields(keychain_raw)
        if parsed is not None:
            access_token, expires_at = parsed
            return access_token, expires_at, "CLI creds"
        # Legacy path: keychain entry held a bare token rather than the JSON
        # blob. We have no expiry hint in that case.
        return keychain_raw, None, "CLI creds"
    return None


def _read_oauth_account_notes(env: dict[str, str]) -> list[str]:
    account = _read_oauth_account(env)
    if account is None:
        return []
    notes: list[str] = []
    organization_name = account.get("organizationName")
    if isinstance(organization_name, str) and organization_name.strip():
        notes.append(f"org: {organization_name.strip()}")
    user_tier = account.get("userRateLimitTier")
    if isinstance(user_tier, str) and user_tier.strip():
        notes.append(f"user tier: {user_tier.strip()}")
    org_tier = account.get("organizationRateLimitTier")
    if isinstance(org_tier, str) and org_tier.strip():
        notes.append(f"org tier: {org_tier.strip()}")
    return notes


def _normalize_headers(headers: dict[str, str] | Any) -> dict[str, str]:
    if hasattr(headers, "items"):
        return {str(key).lower(): str(value) for key, value in headers.items()}
    return {}


def _header_value(headers: dict[str, str] | Any, name: str) -> str | None:
    normalized = _normalize_headers(headers)
    value = normalized.get(name.lower())
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _read_oauth_account(env: dict[str, str]) -> dict[str, Any] | None:
    path = _claude_config_path(env)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    account = payload.get("oauthAccount")
    if not isinstance(account, dict):
        return None
    return account


def _claude_config_path(env: dict[str, str]) -> Path:
    base = env.get("CLAUDE_CONFIG_DIR")
    if base:
        return Path(base).expanduser() / ".claude.json"
    return Path.home() / ".claude.json"


def _unique_notes(notes: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for note in notes:
        cleaned = note.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return unique


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
    for args in (
        [
            "security",
            "find-generic-password",
            "-s",
            service,
            "-a",
            env.get("USER", ""),
            "-w",
        ],
        ["security", "find-generic-password", "-s", service, "-w"],
    ):
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode != 0:
            continue
        token = completed.stdout.strip()
        if token:
            return token
    return None


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


def _extract_oauth_fields(raw: str) -> tuple[str, datetime | None] | None:
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
    if not isinstance(token, str) or not token:
        return None
    return token, _parse_expires_at(oauth.get("expiresAt"))


def _parse_expires_at(value: Any) -> datetime | None:
    # Claude Code CLI stores expiresAt in milliseconds since epoch; older
    # builds used seconds. Treat values > 1e12 as ms, per the reference Swift
    # app (ClaudeCodeSyncService.swift:587-589).
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        epoch = float(value)
    elif isinstance(value, str):
        try:
            epoch = float(value)
        except ValueError:
            return None
    else:
        return None
    seconds = epoch / 1000.0 if epoch > 1e12 else epoch
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_iso_datetime_from_unix_seconds(value: Any) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except ValueError:
            return None
    return None


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
