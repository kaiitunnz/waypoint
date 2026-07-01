from __future__ import annotations

import asyncio
import importlib.resources
import json
import logging
import os
import platform
import re
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from waypoint.backends.claude_code.version import claude_cli_version_string
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.perf import debug_timer
from waypoint.schemas import SessionRateLimitUsage, UsageWindow

log = logging.getLogger("waypoint.claude_code.rate_limits")

_CLAUDE_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_CLAUDE_MESSAGES_MODEL = "claude-haiku-4-5-20251001"
_CLAUDE_MESSAGES_VERSION = "2023-06-01"
_WINDOWS: tuple[tuple[str, str, int], ...] = (
    ("five_hour", "5h", 5 * 60),
    ("seven_day", "Weekly", 7 * 24 * 60),
    ("seven_day_opus", "Opus", 7 * 24 * 60),
    ("seven_day_sonnet", "Sonnet", 7 * 24 * 60),
    ("seven_day_fable", "Fable", 7 * 24 * 60),
)
# Windows the account-wide plan always carries; the per-model windows are
# gated in parse_claude_usage_payload.
_PRIMARY_WINDOW_KEYS = frozenset({"five_hour", "seven_day"})


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
        resets_at = _parse_iso_datetime(window.get("resets_at"))
        # Skip a per-model scoped window (Opus/Sonnet/Fable) only while it is
        # both un-armed (no reset) and at 0% — the API's "no usage yet this
        # week" state. The 5h / weekly windows always surface.
        if key not in _PRIMARY_WINDOW_KEYS and resets_at is None and percent <= 0.0:
            continue
        windows.append(
            UsageWindow(
                id=key,
                label=label,
                used_percent=percent,
                window_minutes=minutes,
                resets_at=resets_at,
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
    with debug_timer(log, "probe_claude_usage"):
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
        account_notes = _read_oauth_account_notes(resolved_env)

        # Prefer the OAuth usage endpoint: it carries per-model windows
        # (Sonnet/Opus/Fable) that the Messages API rate-limit headers omit.
        usage_response = await asyncio.wait_for(
            asyncio.to_thread(_fetch_claude_oauth_usage, access_token),
            timeout=timeout_seconds,
        )
        snapshot = _build_oauth_usage_snapshot(
            usage_response, credential_note, account_notes
        )
        if snapshot is not None:
            return snapshot

        # Fall back to the Messages API rate-limit headers (5h + weekly only)
        # when the usage endpoint is unavailable or returns no windows.
        response = await asyncio.wait_for(
            asyncio.to_thread(_fetch_claude_messages_usage, access_token),
            timeout=timeout_seconds,
        )
        if response is None:
            log.warning("claude rate-limit request failed before receiving a response")
            return None
        return _build_messages_snapshot(
            status=response.status,
            headers=response.headers,
            body=response.body,
            credential_note=credential_note,
            account_notes=account_notes,
        )


async def probe_claude_usage_remote(
    launch_target: SshLaunchTargetConfig,
    *,
    timeout_seconds: float = 30.0,
) -> SessionRateLimitUsage | None:
    payload = await _run_remote_probe_script(launch_target, timeout_seconds)
    if payload is None:
        return None
    error = payload.get("error")
    account_notes = _string_list(payload.get("oauth_account_notes"))
    credential_note = "remote CLI creds"
    if error == "no_credentials":
        log.warning("claude remote rate-limit probe found no CLI credentials")
        return None
    if error == "expired":
        log.info(
            "claude remote rate-limit probe: cached access token expired; "
            "skipping HTTP and surfacing expiry"
        )
        return _expired_credentials_snapshot_from_notes(credential_note, account_notes)
    if error == "network":
        log.warning(
            "claude remote rate-limit probe network error "
            f"(preview={payload.get('body_preview')!r})"
        )
        return None
    if error == "internal":
        log.warning(
            f"claude remote rate-limit probe internal error: {payload.get('message')!r}"
        )
        return None
    if "usage" in payload:
        # The remote script emits the usage variant only when it carries a
        # usable window; parse it and return whatever it yields rather than
        # misrouting into the Messages-API header branch below.
        usage = payload.get("usage")
        snapshot = (
            parse_claude_usage_payload(
                usage, notes=_unique_notes([credential_note, *account_notes])
            )
            if isinstance(usage, dict)
            else None
        )
        if snapshot is None:
            log.info("claude remote usage payload carried no usable windows")
        return snapshot
    status = payload.get("status")
    headers = payload.get("headers")
    body_preview = payload.get("body_preview", "")
    if not isinstance(status, int) or not isinstance(headers, dict):
        log.warning("claude remote rate-limit probe returned malformed payload")
        return None
    return _build_messages_snapshot(
        status=status,
        headers={str(k): str(v) for k, v in headers.items()},
        body=body_preview.encode("utf-8") if isinstance(body_preview, str) else b"",
        credential_note=credential_note,
        account_notes=account_notes,
    )


# Matched to the 300s per-session refresh cadence in plugin.py. The rate-limit
# snapshot is account-wide, so one real probe per account per refresh window is
# enough; every other session reuses the cached snapshot. A lone session still
# refreshes each cycle (its probe age exceeds the TTL by the loop's sleep), so
# liveness is unchanged while N concurrent sessions collapse to ~1 HTTP probe.
_SHARED_PROBE_TTL_SECONDS = 300.0


@dataclass
class _ProbeCacheEntry:
    stored_at: float
    result: SessionRateLimitUsage | None


class SharedRateLimitProbeCache:
    """Process-wide TTL cache that coalesces account-wide rate-limit probes.

    Claude rate limits are scoped to an account, not a session, yet every
    Claude session runs its own refresh loop. Without sharing, N sessions make
    N identical Anthropic probes per window. This cache serves one probe's
    result to every session in the same account/TTL window and de-duplicates
    concurrent in-flight probes behind a per-key lock. Only successful (non
    ``None``) snapshots are cached, so a transient probe failure does not blank
    the rate-limit UI for the whole window — the next session retries.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = _SHARED_PROBE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[str, _ProbeCacheEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    def _fresh(self, key: str) -> _ProbeCacheEntry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() - entry.stored_at >= self._ttl:
            return None
        return entry

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    def invalidate(self, key: str) -> None:
        self._entries.pop(key, None)

    async def get_or_probe(
        self,
        key: str,
        fetch: Callable[[], Awaitable[SessionRateLimitUsage | None]],
        *,
        force: bool = False,
    ) -> SessionRateLimitUsage | None:
        if not force:
            entry = self._fresh(key)
            if entry is not None:
                return entry.result
        lock = await self._lock_for(key)
        async with lock:
            # Re-check inside the lock: a concurrent probe for the same key may
            # have populated the cache while we waited.
            if not force:
                entry = self._fresh(key)
                if entry is not None:
                    return entry.result
            result = await fetch()
            if result is not None:
                self._entries[key] = _ProbeCacheEntry(
                    stored_at=self._clock(), result=result
                )
            return result


_SHARED_PROBE_CACHE = SharedRateLimitProbeCache()


def _local_probe_cache_key(env: dict[str, str]) -> str:
    return f"local:{env.get('CLAUDE_CONFIG_DIR') or '~'}"


def _remote_probe_cache_key(launch_target: SshLaunchTargetConfig) -> str:
    return f"remote:{launch_target.id}"


async def probe_claude_usage_shared(
    *,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
    force: bool = False,
) -> SessionRateLimitUsage | None:
    """Account-shared variant of :func:`probe_claude_usage`.

    Sessions sharing the same credentials (keyed by ``CLAUDE_CONFIG_DIR``)
    reuse one probe per TTL window. Pass ``force=True`` to bypass a cache hit
    for a user-driven refresh.
    """
    resolved_env = env if env is not None else dict(os.environ)
    return await _SHARED_PROBE_CACHE.get_or_probe(
        _local_probe_cache_key(resolved_env),
        lambda: probe_claude_usage(env=resolved_env, timeout_seconds=timeout_seconds),
        force=force,
    )


async def probe_claude_usage_remote_shared(
    launch_target: SshLaunchTargetConfig,
    *,
    timeout_seconds: float = 30.0,
    force: bool = False,
) -> SessionRateLimitUsage | None:
    """Account-shared variant of :func:`probe_claude_usage_remote`, keyed by
    launch-target id."""
    return await _SHARED_PROBE_CACHE.get_or_probe(
        _remote_probe_cache_key(launch_target),
        lambda: probe_claude_usage_remote(
            launch_target, timeout_seconds=timeout_seconds
        ),
        force=force,
    )


def invalidate_shared_probe_local(env: dict[str, str] | None = None) -> None:
    resolved_env = env if env is not None else dict(os.environ)
    _SHARED_PROBE_CACHE.invalidate(_local_probe_cache_key(resolved_env))


def invalidate_shared_probe_remote(launch_target: SshLaunchTargetConfig) -> None:
    _SHARED_PROBE_CACHE.invalidate(_remote_probe_cache_key(launch_target))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


_REMOTE_PROBE_SCRIPT_BYTES: bytes | None = None


def _remote_probe_script_bytes() -> bytes:
    global _REMOTE_PROBE_SCRIPT_BYTES
    if _REMOTE_PROBE_SCRIPT_BYTES is None:
        _REMOTE_PROBE_SCRIPT_BYTES = (
            importlib.resources.files("waypoint.backends.claude_code")
            .joinpath("remote_probe_script.py")
            .read_bytes()
        )
    return _REMOTE_PROBE_SCRIPT_BYTES


async def _run_remote_probe_script(
    launch_target: SshLaunchTargetConfig, timeout_seconds: float
) -> dict[str, Any] | None:
    argv = launch_target.build_remote_exec_args(["python3", "-"])
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as exc:
        log.warning(f"claude remote rate-limit probe failed to spawn ssh: {exc!r}")
        return None
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(_remote_probe_script_bytes()), timeout=timeout_seconds
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("claude remote rate-limit probe timed out")
        return None
    if proc.returncode != 0:
        log.warning(
            "claude remote rate-limit probe exited non-zero "
            f"(rc={proc.returncode} stderr={stderr[:240]!r})"
        )
        return None
    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        log.warning("claude remote rate-limit probe produced no output")
        return None
    last_line = text.splitlines()[-1]
    try:
        decoded = json.loads(last_line)
    except json.JSONDecodeError:
        log.warning(
            "claude remote rate-limit probe produced non-JSON output "
            f"(last_line={last_line[:240]!r})"
        )
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded


def _build_oauth_usage_snapshot(
    response: _ClaudeHTTPResponse | None,
    credential_note: str,
    account_notes: list[str],
) -> SessionRateLimitUsage | None:
    """Build a snapshot from the OAuth usage endpoint, or ``None`` to fall back.

    Returns ``None`` whenever the endpoint is unreachable, returns a non-200
    (it has historically been disabled and answered 4xx), or yields no usable
    windows — in every such case the caller drops back to the Messages API
    rate-limit headers. A 401/expiry surfaces through that header path, which
    already renders the "credentials expired" snapshot.
    """
    if response is None:
        log.info(
            "claude usage endpoint unreachable; falling back to rate-limit headers"
        )
        return None
    if response.status != 200:
        log.info(
            "claude usage endpoint returned status %s; "
            "falling back to rate-limit headers",
            response.status,
        )
        return None
    snapshot = parse_claude_usage_payload(
        response.body,
        notes=_unique_notes([credential_note, *account_notes]),
    )
    if snapshot is None:
        log.info(
            "claude usage endpoint returned no usable windows; "
            "falling back to rate-limit headers"
        )
    return snapshot


def _build_messages_snapshot(
    *,
    status: int,
    headers: dict[str, str],
    body: bytes,
    credential_note: str,
    account_notes: list[str],
) -> SessionRateLimitUsage | None:
    notes = [credential_note, *account_notes]
    usage = parse_claude_rate_limit_headers(
        headers,
        now=datetime.now(UTC),
        notes=_unique_notes(notes),
    )
    if usage is not None:
        return usage
    if status == 401:
        log.warning(
            "claude rate-limit response was unauthenticated "
            f"(status=401 preview={body[:240].decode('utf-8', errors='replace')!r})",
        )
        return _expired_credentials_snapshot_from_notes(credential_note, account_notes)
    if status == 429:
        retry_after = _header_value(headers, "Retry-After")
        if retry_after:
            notes.append(f"rate limited; retry after {retry_after}s")
        else:
            notes.append("rate limited")
        log.warning(
            "claude rate-limit response was throttled "
            f"(status={status} retry_after={retry_after!r} "
            f"preview={body[:240].decode('utf-8', errors='replace')!r})",
        )
        return SessionRateLimitUsage(
            source="claude_code",
            updated_at=datetime.now(UTC),
            windows=[],
            notes=_unique_notes(notes),
        )
    if status != 200:
        log.warning(
            "claude rate-limit response was not successful "
            f"(status={status} "
            f"preview={body[:240].decode('utf-8', errors='replace')!r})",
        )
        return None
    log.warning(
        "claude rate-limit response did not include usable rate-limit headers "
        f"(status={status} headers={sorted(_normalize_headers(headers).keys())})",
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
    return _expired_credentials_snapshot_from_notes(
        credential_note, _read_oauth_account_notes(env)
    )


def _expired_credentials_snapshot_from_notes(
    credential_note: str, account_notes: list[str]
) -> SessionRateLimitUsage:
    return SessionRateLimitUsage(
        source="claude_code",
        updated_at=datetime.now(UTC),
        windows=[],
        notes=_unique_notes(
            [credential_note, _EXPIRED_CREDENTIALS_NOTE, *account_notes]
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
    version = claude_cli_version_string()
    return f"claude-code/{version}" if version else "claude-code/2.1.5"


def _fetch_claude_oauth_usage(access_token: str) -> _ClaudeHTTPResponse | None:
    request = Request(
        _CLAUDE_USAGE_URL,
        method="GET",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": _claude_user_agent(),
            "anthropic-beta": "oauth-2025-04-20",
            "Accept": "application/json",
        },
    )
    return _fetch_url(request)


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
