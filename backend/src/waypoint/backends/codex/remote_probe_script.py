"""Remote rate-limit probe for Codex.

Designed to be piped to ``python3 -`` over SSH on a remote launch
target. First tries the OAuth path (``~/.codex/auth.json`` →
``chatgpt.com/backend-api/wham/usage``). If that yields nothing,
spawns ``codex -s read-only -a untrusted`` with a local PTY on the
remote, drives ``/status``, and emits the captured TUI output for the
backend to parse with ``parse_codex_status``.

Stdlib-only; targets Python 3.8+. ``tomllib`` (3.11+) is used when
available to read ``config.toml`` for a custom ``chatgpt_base_url``;
older interpreters fall back to a regex.

The ``WAYPOINT_REMOTE_PROBE_CODEX_BIN`` env var (set by the SSH caller)
selects the remote ``codex`` binary path; defaults to ``codex``.

Output schema (always one line of JSON on stdout, ``\\n``-terminated):

    {"payload": {usage-json from server}, "usage_url": str}
    {"status_text": "..."}    # /status PTY scrape
    {"error": "no_data" | "internal", ...}
"""

import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import tomllib
except ImportError:
    tomllib = None  # type: ignore[assignment]

DEFAULT_CHATGPT_BASE_URL = "https://chatgpt.com/backend-api/"
CHATGPT_USAGE_PATH = "/wham/usage"
CODEX_USAGE_PATH = "/api/codex/usage"
OAUTH_REFRESH_ENDPOINT = "https://auth.openai.com/oauth/token"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
HTTP_TIMEOUT = 25
CODEX_BIN_ENV = "WAYPOINT_REMOTE_PROBE_CODEX_BIN"
STATUS_TIMEOUT = 8.0


def emit(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def codex_home():
    base = os.environ.get("CODEX_HOME")
    if base and base.strip():
        return os.path.expanduser(base)
    return os.path.expanduser("~/.codex")


def auth_path():
    return os.path.join(codex_home(), "auth.json")


def config_path():
    return os.path.join(codex_home(), "config.toml")


def _string_value(mapping, snake_case_key, camel_case_key):
    value = mapping.get(snake_case_key)
    if isinstance(value, str) and value:
        return value
    value = mapping.get(camel_case_key)
    if isinstance(value, str) and value:
        return value
    return None


def load_credentials():
    path = auth_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None

    api_key = payload.get("OPENAI_API_KEY")
    if isinstance(api_key, str) and api_key.strip():
        return {
            "access_token": api_key.strip(),
            "refresh_token": "",
            "account_id": None,
        }

    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    access_token = _string_value(tokens, "access_token", "accessToken")
    if not access_token:
        return None
    account_id = _string_value(tokens, "account_id", "accountId") or _string_value(
        payload, "chatgpt_account_id", "chatgptAccountId"
    )
    return {
        "access_token": access_token,
        "refresh_token": _string_value(tokens, "refresh_token", "refreshToken") or "",
        "account_id": account_id,
    }


def _resolve_chatgpt_base_url():
    path = config_path()
    try:
        with open(path, encoding="utf-8") as handle:
            contents = handle.read()
    except OSError:
        return DEFAULT_CHATGPT_BASE_URL
    if tomllib is not None:
        try:
            parsed = tomllib.loads(contents)
        except Exception:  # noqa: BLE001
            parsed = None
        if isinstance(parsed, dict):
            value = parsed.get("chatgpt_base_url")
            if isinstance(value, str) and value.strip():
                return value.strip()
    match = re.search(
        r'^\s*chatgpt_base_url\s*=\s*"([^"]+)"', contents, flags=re.MULTILINE
    )
    if match is not None:
        return match.group(1).strip()
    return DEFAULT_CHATGPT_BASE_URL


def _normalize_chatgpt_base_url(value):
    trimmed = value.strip() or DEFAULT_CHATGPT_BASE_URL
    while trimmed.endswith("/"):
        trimmed = trimmed[:-1]
    if (
        trimmed.startswith("https://chatgpt.com")
        or trimmed.startswith("https://chat.openai.com")
    ) and "/backend-api" not in trimmed:
        trimmed += "/backend-api"
    return trimmed


def resolve_usage_url():
    base = _normalize_chatgpt_base_url(_resolve_chatgpt_base_url())
    path = CHATGPT_USAGE_PATH if "/backend-api" in base else CODEX_USAGE_PATH
    return base + path


def _post_json(url, payload, headers):
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method="POST", headers=headers)
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT) as response:
            if getattr(response, "status", 200) != 200:
                return None
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, json.JSONDecodeError, ValueError):
        return None


def refresh_credentials(credentials):
    if not credentials.get("refresh_token"):
        return credentials
    refreshed = _post_json(
        OAUTH_REFRESH_ENDPOINT,
        {
            "client_id": OAUTH_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": credentials["refresh_token"],
            "scope": "openid profile email",
        },
        {"Content-Type": "application/json"},
    )
    if not isinstance(refreshed, dict):
        return credentials
    access_token = refreshed.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return credentials
    new_refresh = refreshed.get("refresh_token")
    return {
        "access_token": access_token,
        "refresh_token": (
            new_refresh
            if isinstance(new_refresh, str) and new_refresh
            else credentials["refresh_token"]
        ),
        "account_id": credentials.get("account_id"),
    }


def fetch_usage(credentials, usage_url):
    headers = {
        "Authorization": "Bearer " + credentials["access_token"],
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": "codex-cli",
    }
    if credentials.get("account_id"):
        headers["ChatGPT-Account-Id"] = credentials["account_id"]
    request = Request(usage_url, method="GET", headers=headers)
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT) as response:
            if getattr(response, "status", 200) != 200:
                return None
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, json.JSONDecodeError, ValueError):
        return None


def run_codex_status_pty(timeout_seconds=STATUS_TIMEOUT):
    binary = os.environ.get(CODEX_BIN_ENV) or "codex"
    master_fd, slave_fd = pty.openpty()
    proc = None
    try:
        try:
            proc = subprocess.Popen(
                [binary, "-s", "read-only", "-a", "untrusted"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
            )
        except OSError:
            os.close(master_fd)
            os.close(slave_fd)
            return None
        os.close(slave_fd)
        time.sleep(0.35)
        try:
            os.write(master_fd, b"/status\r")
        except OSError:
            pass
        deadline = time.monotonic() + timeout_seconds
        settled_at = None
        buffer = bytearray()
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
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
            try:
                os.write(master_fd, b"/exit\r")
            except OSError:
                pass
            try:
                proc.terminate()
            except OSError:
                pass
            stop = time.monotonic() + 1.0
            while proc.poll() is None and time.monotonic() < stop:
                time.sleep(0.05)
            if proc.poll() is None:
                try:
                    os.kill(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
        try:
            os.close(master_fd)
        except OSError:
            pass


def main():
    credentials = load_credentials()
    if credentials is not None:
        credentials = refresh_credentials(credentials)
        usage_url = resolve_usage_url()
        payload = fetch_usage(credentials, usage_url)
        if payload is not None:
            emit({"payload": payload, "usage_url": usage_url})
            return

    status_text = run_codex_status_pty()
    if status_text and status_text.strip():
        emit({"status_text": status_text})
        return

    emit({"error": "no_data"})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        emit({"error": "internal", "message": repr(exc)})
