from __future__ import annotations

import asyncio
import errno
import importlib.resources
import json
import logging
import os
import re
import select
import shutil
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 is unsupported here
    tomllib = None  # type: ignore[assignment]

from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import SessionRateLimitUsage, UsageWindow

log = logging.getLogger("waypoint.codex.rate_limits")

_WINDOW_LABELS: dict[str, tuple[str, int]] = {
    "5h limit": ("5h", 5 * 60),
    "weekly limit": ("Weekly", 7 * 24 * 60),
}
_DEFAULT_CHATGPT_BASE_URL = "https://chatgpt.com/backend-api/"
_CHATGPT_USAGE_PATH = "/wham/usage"
_CODEX_USAGE_PATH = "/api/codex/usage"
_CODEX_OAUTH_REFRESH_ENDPOINT = "https://auth.openai.com/oauth/token"
_CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_PERCENT_LEFT_RE = re.compile(r"(?i)(\d{1,3}(?:\.\d+)?)\s*%\s*left")
_PERCENT_USED_RE = re.compile(r"(?i)(\d{1,3}(?:\.\d+)?)\s*%\s*used")
_CREDITS_RE = re.compile(r"(?i)credits:\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)")
_CACHED_OAUTH_CREDENTIALS: _CodexOAuthCredentials | None = None
_CACHED_OAUTH_ACCOUNT_ID: str | None = None


@dataclass(frozen=True)
class _CodexOAuthCredentials:
    access_token: str
    refresh_token: str
    account_id: str | None
    last_refresh: datetime | None

    @property
    def needs_refresh(self) -> bool:
        if not self.refresh_token:
            return False
        if self.last_refresh is None:
            return True
        eight_days = 8 * 24 * 60 * 60
        return (datetime.now(UTC) - self.last_refresh).total_seconds() > eight_days


def parse_codex_status(
    text: str, *, now: datetime | None = None
) -> SessionRateLimitUsage | None:
    clean = _strip_ansi(text).strip()
    if not clean:
        return None

    windows: list[UsageWindow] = []
    for line in clean.splitlines():
        lowered = line.lower()
        for needle, (label, minutes) in _WINDOW_LABELS.items():
            if needle not in lowered:
                continue
            percent = _parse_percent_used(line)
            if percent is None:
                continue
            reset_description = _extract_reset_description(line)
            windows.append(
                UsageWindow(
                    id=needle.replace(" ", "-"),
                    label=label,
                    used_percent=percent,
                    window_minutes=minutes,
                    reset_description=reset_description,
                )
            )
            break

    credits_remaining = None
    credits_currency = None
    credits_line = _first_matching_line(clean, "credits:")
    if credits_line is not None:
        match = _CREDITS_RE.search(credits_line)
        if match is not None:
            try:
                credits_remaining = float(match.group(1).replace(",", ""))
            except ValueError:
                credits_remaining = None
            credits_currency = "credits"
            if "$" in credits_line:
                credits_currency = "USD"

    if not windows and credits_remaining is None:
        return None

    return SessionRateLimitUsage(
        source="codex",
        updated_at=now or datetime.now(UTC),
        windows=windows,
        credits_remaining=credits_remaining,
        credits_currency=credits_currency,
        notes=["CLI status"],
    )


async def probe_codex_status(
    *,
    cwd: str,
    binary: str,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 8.0,
) -> SessionRateLimitUsage | None:
    resolved_env = env if env is not None else dict(os.environ)

    snapshot = await _probe_codex_oauth_usage(resolved_env)
    if snapshot is not None:
        return snapshot

    resolved = _resolve_binary(binary)
    if resolved is None:
        log.warning(
            "codex rate-limit probe could not resolve codex binary",
            extra={"cwd": cwd, "binary": binary},
        )
        return None
    text = await asyncio.to_thread(
        _run_codex_status,
        resolved,
        cwd,
        resolved_env,
        timeout_seconds,
    )
    snapshot = parse_codex_status(text)
    if snapshot is None:
        log.warning(
            "codex rate-limit probe returned no usable snapshot",
            extra={"cwd": cwd, "binary": resolved},
        )
    return snapshot


_REMOTE_PROBE_CODEX_BIN_ENV = "WAYPOINT_REMOTE_PROBE_CODEX_BIN"
_REMOTE_PROBE_SCRIPT_BYTES: bytes | None = None


def _remote_probe_script_bytes() -> bytes:
    global _REMOTE_PROBE_SCRIPT_BYTES
    if _REMOTE_PROBE_SCRIPT_BYTES is None:
        _REMOTE_PROBE_SCRIPT_BYTES = (
            importlib.resources.files("waypoint.backends.codex")
            .joinpath("remote_probe_script.py")
            .read_bytes()
        )
    return _REMOTE_PROBE_SCRIPT_BYTES


async def probe_codex_usage_remote(
    launch_target: SshLaunchTargetConfig,
    *,
    binary: str = "codex",
    timeout_seconds: float = 30.0,
) -> SessionRateLimitUsage | None:
    payload = await _run_remote_probe_script(launch_target, binary, timeout_seconds)
    if payload is None:
        return None
    error = payload.get("error")
    if error == "no_data":
        log.warning("codex remote rate-limit probe yielded no data")
        return None
    if error == "internal":
        log.warning(
            f"codex remote rate-limit probe internal error: {payload.get('message')!r}"
        )
        return None
    raw = payload.get("payload")
    if isinstance(raw, dict):
        snapshot = parse_codex_usage_payload(raw, notes=["remote OAuth"])
        if snapshot is not None:
            return snapshot
    status_text = payload.get("status_text")
    if isinstance(status_text, str) and status_text.strip():
        snapshot = parse_codex_status(status_text)
        if snapshot is not None:
            snapshot.notes = list(snapshot.notes) + ["remote /status"]
            return snapshot
    log.warning("codex remote rate-limit probe returned unparsable payload")
    return None


async def _run_remote_probe_script(
    launch_target: SshLaunchTargetConfig,
    binary: str,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    argv = launch_target.build_remote_exec_args(
        ["env", f"{_REMOTE_PROBE_CODEX_BIN_ENV}={binary}", "python3", "-"]
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as exc:
        log.warning(f"codex remote rate-limit probe failed to spawn ssh: {exc!r}")
        return None
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(_remote_probe_script_bytes()), timeout=timeout_seconds
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("codex remote rate-limit probe timed out")
        return None
    if proc.returncode != 0:
        log.warning(
            "codex remote rate-limit probe exited non-zero "
            f"(rc={proc.returncode} stderr={stderr[:240]!r})"
        )
        return None
    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        log.warning("codex remote rate-limit probe produced no output")
        return None
    last_line = text.splitlines()[-1]
    try:
        decoded = json.loads(last_line)
    except json.JSONDecodeError:
        log.warning(
            "codex remote rate-limit probe produced non-JSON output "
            f"(last_line={last_line[:240]!r})"
        )
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded


async def _probe_codex_oauth_usage(
    env: dict[str, str],
    timeout_seconds: float = 15.0,
) -> SessionRateLimitUsage | None:
    credentials = _load_oauth_credentials(env)
    if credentials is None:
        log.warning("codex OAuth credentials not found")
        return None

    global _CACHED_OAUTH_ACCOUNT_ID
    global _CACHED_OAUTH_CREDENTIALS
    if (
        _CACHED_OAUTH_CREDENTIALS is not None
        and credentials.account_id is not None
        and _CACHED_OAUTH_ACCOUNT_ID == credentials.account_id
    ):
        credentials = _CACHED_OAUTH_CREDENTIALS

    if credentials.needs_refresh:
        refreshed = await _refresh_oauth_credentials(credentials)
        if refreshed is not None:
            credentials = refreshed
            _CACHED_OAUTH_CREDENTIALS = refreshed
            _CACHED_OAUTH_ACCOUNT_ID = refreshed.account_id

    request = Request(
        _resolve_usage_url(env),
        method="GET",
        headers={
            "Authorization": f"Bearer {credentials.access_token}",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": "codex-cli",
        },
    )
    if credentials.account_id:
        request.add_header("ChatGPT-Account-Id", credentials.account_id)

    response = await asyncio.wait_for(
        asyncio.to_thread(_fetch_url, request), timeout=timeout_seconds
    )
    if response is None:
        log.warning(
            "codex OAuth usage request failed before receiving a response",
            extra={"usage_url": _resolve_usage_url(env)},
        )
        return None
    snapshot = parse_codex_usage_payload(
        response,
        notes=[note for note in ["CLI OAuth"] if note],
    )
    if snapshot is None:
        preview = response.decode("utf-8", errors="replace")[:240]
        log.warning(
            "codex OAuth usage response did not yield a snapshot "
            f"(bytes={len(response)} preview={preview!r})",
        )
    return snapshot


def parse_codex_usage_payload(
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

    windows = _collect_codex_windows(raw)
    credits_remaining, credits_currency = _parse_credits(raw.get("credits"))

    notes_out = list(notes or ["CLI OAuth"])
    plan_type = raw.get("plan_type")
    if isinstance(plan_type, str) and plan_type:
        notes_out.append(f"plan: {plan_type}")
    account_email = raw.get("email")
    if isinstance(account_email, str) and account_email:
        notes_out.append(account_email)

    return SessionRateLimitUsage(
        source="codex",
        updated_at=now or datetime.now(UTC),
        windows=windows,
        credits_remaining=credits_remaining,
        credits_currency=credits_currency,
        notes=notes_out,
    )


def _collect_codex_windows(raw: dict[str, Any]) -> list[UsageWindow]:
    windows: list[tuple[int, UsageWindow]] = []
    for path, candidate in _iter_window_candidates(raw):
        window = _parse_codex_window(candidate, path=path)
        if window is None:
            continue
        sort_key = 2
        if window.label == "5h":
            sort_key = 0
        elif window.label == "Weekly":
            sort_key = 1
        windows.append((sort_key, window))
    windows.sort(key=lambda item: (item[0], item[1].label, item[1].id))
    return [window for _, window in windows]


def _iter_window_candidates(
    value: Any, path: tuple[str, ...] = ()
) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
    candidates: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    if isinstance(value, dict):
        if "used_percent" in value:
            candidates.append((path, value))
        for key, child in value.items():
            candidates.extend(_iter_window_candidates(child, path + (str(key),)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            candidates.extend(_iter_window_candidates(child, path + (str(index),)))
    return candidates


def _parse_codex_window(
    candidate: dict[str, Any], *, path: tuple[str, ...]
) -> UsageWindow | None:
    used_percent = _parse_percent_value(candidate.get("used_percent"))
    if used_percent is None:
        return None

    limit_seconds = _parse_int(candidate.get("limit_window_seconds"))
    window_minutes = None
    if limit_seconds is not None and limit_seconds > 0:
        window_minutes = limit_seconds // 60
    elif (minutes := _parse_int(candidate.get("window_minutes"))) is not None:
        window_minutes = minutes

    resets_at = _parse_epoch_datetime(candidate.get("reset_at"))
    label, window_id = _codex_window_label(
        path=path, window_minutes=window_minutes, limit_seconds=limit_seconds
    )
    return UsageWindow(
        id=window_id,
        label=label,
        used_percent=used_percent,
        window_minutes=window_minutes,
        resets_at=resets_at,
    )


def _codex_window_label(
    *,
    path: tuple[str, ...],
    window_minutes: int | None,
    limit_seconds: int | None,
) -> tuple[str, str]:
    duration = (
        limit_seconds
        if limit_seconds is not None
        else (window_minutes * 60 if window_minutes is not None else None)
    )
    joined_path = ".".join(path).lower()
    if duration == 5 * 60 * 60 or "5h" in joined_path or "session" in joined_path:
        return "5h", "five-hour"
    if duration == 7 * 24 * 60 * 60 or "week" in joined_path:
        return "Weekly", "weekly"
    if path:
        tail = path[-1].replace("_", " ").strip()
        if tail:
            return tail.title(), tail.replace(" ", "-")
    return "Window", "window"


def _parse_credits(raw: Any) -> tuple[float | None, str | None]:
    if not isinstance(raw, dict):
        return None, None
    balance = raw.get("balance")
    if isinstance(balance, str):
        try:
            balance = float(balance.replace(",", ""))
        except ValueError:
            return None, None
    if isinstance(balance, (int, float)):
        return float(balance), "USD"
    return None, None


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _parse_percent_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        percent = float(value)
        if 0.0 <= percent <= 1.0:
            percent *= 100.0
        return _clamp_percent(percent)
    if isinstance(value, str):
        cleaned = value.strip().replace("%", "")
        try:
            percent = float(cleaned)
        except ValueError:
            return None
        if 0.0 <= percent <= 1.0:
            percent *= 100.0
        return _clamp_percent(percent)
    return None


def _parse_epoch_datetime(value: Any) -> datetime | None:
    epoch = _parse_int(value)
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _fetch_url(request: Request) -> bytes | None:
    try:
        with urlopen(request, timeout=30) as response:
            if getattr(response, "status", 200) != 200:
                return None
            return response.read()
    except (HTTPError, URLError, OSError):
        return None


async def _refresh_oauth_credentials(
    credentials: _CodexOAuthCredentials,
) -> _CodexOAuthCredentials | None:
    if not credentials.refresh_token:
        return None

    body = {
        "client_id": _CODEX_OAUTH_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": credentials.refresh_token,
        "scope": "openid profile email",
    }
    request = Request(
        _CODEX_OAUTH_REFRESH_ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    response = await asyncio.to_thread(_fetch_url, request)
    if response is None:
        return None
    try:
        raw = json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    access_token = raw.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return None
    refresh_token = raw.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        refresh_token = credentials.refresh_token
    return _CodexOAuthCredentials(
        access_token=access_token,
        refresh_token=refresh_token,
        account_id=credentials.account_id,
        last_refresh=datetime.now(UTC),
    )


def _load_oauth_credentials(env: dict[str, str]) -> _CodexOAuthCredentials | None:
    path = _codex_auth_path(env)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    api_key = payload.get("OPENAI_API_KEY")
    if isinstance(api_key, str) and api_key.strip():
        return _CodexOAuthCredentials(
            access_token=api_key.strip(),
            refresh_token="",
            account_id=None,
            last_refresh=None,
        )

    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    access_token = _string_value(tokens, "access_token", "accessToken")
    refresh_token = _string_value(tokens, "refresh_token", "refreshToken") or ""
    account_id = _string_value(tokens, "account_id", "accountId")
    if account_id is None:
        account_id = _string_value(payload, "chatgpt_account_id", "chatgptAccountId")
    if not access_token:
        return None
    return _CodexOAuthCredentials(
        access_token=access_token,
        refresh_token=refresh_token,
        account_id=account_id,
        last_refresh=_parse_last_refresh(payload.get("last_refresh")),
    )


def _codex_auth_path(env: dict[str, str]) -> Path:
    codex_home = env.get("CODEX_HOME")
    if codex_home and codex_home.strip():
        root = Path(codex_home).expanduser()
    else:
        root = Path.home() / ".codex"
    return root / "auth.json"


def _resolve_usage_url(env: dict[str, str]) -> str:
    base_url = _resolve_chatgpt_base_url(env)
    normalized = _normalize_chatgpt_base_url(base_url)
    path = _CHATGPT_USAGE_PATH if "/backend-api" in normalized else _CODEX_USAGE_PATH
    return f"{normalized}{path}"


def _resolve_chatgpt_base_url(env: dict[str, str]) -> str:
    contents = _load_config_contents(env)
    if contents:
        parsed = _parse_chatgpt_base_url(contents)
        if parsed:
            return parsed
    return _DEFAULT_CHATGPT_BASE_URL


def _normalize_chatgpt_base_url(value: str) -> str:
    trimmed = value.strip() or _DEFAULT_CHATGPT_BASE_URL
    while trimmed.endswith("/"):
        trimmed = trimmed[:-1]
    if (
        trimmed.startswith("https://chatgpt.com")
        or trimmed.startswith("https://chat.openai.com")
    ) and "/backend-api" not in trimmed:
        trimmed += "/backend-api"
    return trimmed


def _parse_chatgpt_base_url(contents: str) -> str | None:
    if tomllib is None:
        return None
    try:
        parsed = tomllib.loads(contents)
    except Exception:  # noqa: BLE001
        return None
    value = parsed.get("chatgpt_base_url")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _load_config_contents(env: dict[str, str]) -> str | None:
    root = (
        Path(env.get("CODEX_HOME", "")).expanduser()
        if env.get("CODEX_HOME")
        else Path.home() / ".codex"
    )
    config = root / "config.toml"
    try:
        return config.read_text(encoding="utf-8")
    except OSError:
        return None


def _string_value(
    dictionary: dict[str, Any],
    snake_case_key: str,
    camel_case_key: str,
) -> str | None:
    value = dictionary.get(snake_case_key)
    if isinstance(value, str) and value:
        return value
    value = dictionary.get(camel_case_key)
    if isinstance(value, str) and value:
        return value
    return None


def _parse_last_refresh(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _run_codex_status(
    binary: str,
    cwd: str,
    env: dict[str, str],
    timeout_seconds: float,
) -> str:
    import pty

    master_fd, slave_fd = pty.openpty()
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            [binary, "-s", "read-only", "-a", "untrusted"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=str(Path(cwd).expanduser()),
            env=env,
            start_new_session=True,
        )
    except Exception:
        os.close(master_fd)
        os.close(slave_fd)
        raise

    os.close(slave_fd)
    try:
        time.sleep(0.35)
        try:
            os.write(master_fd, b"/status\r")
        except OSError:
            pass

        deadline = time.monotonic() + timeout_seconds
        settled_at: float | None = None
        buffer = bytearray()
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError as exc:
                    if exc.errno in {errno.EIO, errno.EBADF}:
                        break
                    raise
                if chunk:
                    buffer.extend(chunk)
                    settled_at = None
                    continue
                break
            if proc.poll() is not None:
                if settled_at is None:
                    settled_at = time.monotonic() + 0.5
                elif time.monotonic() >= settled_at:
                    break
        return buffer.decode("utf-8", errors="replace")
    finally:
        if proc is not None and proc.poll() is None:
            with suppress(Exception):
                os.write(master_fd, b"/exit\r")
            with suppress(Exception):
                proc.terminate()
            deadline = time.monotonic() + 1.0
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if proc.poll() is None:
                with suppress(Exception):
                    os.kill(proc.pid, signal.SIGKILL)
        with suppress(Exception):
            os.close(master_fd)


def _resolve_binary(binary: str) -> str | None:
    if not binary:
        return None
    if "/" in binary:
        path = Path(binary).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(binary)


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def _first_matching_line(text: str, needle: str) -> str | None:
    for line in text.splitlines():
        if needle in line.lower():
            return line
    return None


def _parse_percent_used(line: str) -> float | None:
    match = _PERCENT_USED_RE.search(line)
    if match is not None:
        try:
            return _clamp_percent(float(match.group(1)))
        except ValueError:
            return None
    match = _PERCENT_LEFT_RE.search(line)
    if match is not None:
        try:
            return _clamp_percent(100.0 - float(match.group(1)))
        except ValueError:
            return None
    return None


def _clamp_percent(percent: float) -> float:
    return max(0.0, min(100.0, percent))


def _extract_reset_description(line: str) -> str | None:
    match = re.search(r"(?i)\breset(?:s)?(?:\s+at|\s+in)?\s*(.*)$", line)
    if match is not None:
        candidate = match.group(1).strip(" :.-\t)")
        return candidate or None
    parens = re.findall(r"\(([^()]*)\)", line)
    if parens:
        candidate = parens[-1].strip(" :.-\t)")
        return candidate or None
    return None
