"""Remote rate-limit probe for Claude Code.

Designed to be piped to ``python3 -`` over SSH on a remote launch
target. Reads the locally-stored OAuth token (``~/.claude/.credentials.json``
or the macOS keychain), calls the Anthropic Messages API, and prints a
single JSON line to stdout describing the response (or an error
sentinel) so the backend can build a ``SessionRateLimitUsage`` from it.

Stdlib-only; targets Python 3.8+ so it runs on most pre-installed
remote interpreters without a virtualenv.

Output schema (always one line of JSON on stdout, ``\\n``-terminated):

    {"status": int, "headers": {str: str}, "body_preview": str,
     "oauth_account_notes": [str], "expires_at": float | None}
    {"error": "no_credentials" | "expired" | "network" | "internal", ...}
"""

import json
import os
import platform
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CLAUDE_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MESSAGES_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_MESSAGES_VERSION = "2023-06-01"
EXPIRY_SKEW_SECONDS = 60
HTTP_TIMEOUT = 25
BODY_PREVIEW_BYTES = 240


def emit(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def credential_paths():
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    root = os.path.expanduser(base) if base else os.path.expanduser("~/.claude")
    return [
        os.path.join(root, ".credentials.json"),
        os.path.expanduser("~/.config/claude/.credentials.json"),
    ]


def claude_config_path():
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    if base:
        return os.path.join(os.path.expanduser(base), ".claude.json")
    return os.path.expanduser("~/.claude.json")


def parse_expires_at(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return None
    return epoch / 1000.0 if epoch > 1e12 else epoch


def extract_oauth_fields(raw):
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    oauth = payload.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        return None
    return token, parse_expires_at(oauth.get("expiresAt"))


def _run_security(args, timeout):
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _read_keychain_for_service(service):
    user = os.environ.get("USER", "")
    for args in (
        ["security", "find-generic-password", "-s", service, "-a", user, "-w"],
        ["security", "find-generic-password", "-s", service, "-w"],
    ):
        completed = _run_security(args, timeout=3)
        if completed is None or completed.returncode != 0:
            continue
        token = completed.stdout.strip()
        if token:
            return token
    return None


def _discover_hashed_keychain_service():
    completed = _run_security(["security", "dump-keychain"], timeout=5)
    if completed is None or completed.returncode != 0:
        return None
    match = re.search(r"(Claude Code-credentials-[^\"\s]+)", completed.stdout)
    return match.group(1) if match is not None else None


def read_keychain_token():
    if platform.system() != "Darwin":
        return None
    token = _read_keychain_for_service("Claude Code-credentials")
    if token:
        return token
    discovered = _discover_hashed_keychain_service()
    if discovered:
        return _read_keychain_for_service(discovered)
    return None


def read_oauth_account_notes():
    path = claude_config_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    account = payload.get("oauthAccount")
    if not isinstance(account, dict):
        return []
    notes = []
    for key, prefix in (
        ("organizationName", "org"),
        ("userRateLimitTier", "user tier"),
        ("organizationRateLimitTier", "org tier"),
    ):
        value = account.get(key)
        if isinstance(value, str) and value.strip():
            notes.append(f"{prefix}: {value.strip()}")
    return notes


def read_credentials():
    for path in credential_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                raw = handle.read()
        except OSError:
            continue
        parsed = extract_oauth_fields(raw)
        if parsed is not None:
            return parsed
    keychain = read_keychain_token()
    if keychain:
        parsed = extract_oauth_fields(keychain)
        if parsed is not None:
            return parsed
        return keychain, None
    return None


def fetch_messages(token):
    body = json.dumps(
        {
            "model": CLAUDE_MESSAGES_MODEL,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }
    ).encode("utf-8")
    request = Request(
        CLAUDE_MESSAGES_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "User-Agent": "claude-code/remote-probe",
            "anthropic-beta": "oauth-2025-04-20",
            "anthropic-version": CLAUDE_MESSAGES_VERSION,
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT) as response:
            status_code = getattr(response, "status", 200)
            headers = {key: value for key, value in response.headers.items()}
            return status_code, headers, response.read(BODY_PREVIEW_BYTES)
    except HTTPError as exc:
        headers = {key: value for key, value in getattr(exc, "headers", {}).items()}
        try:
            payload = exc.read() if exc.fp is not None else b""
        except OSError:
            payload = b""
        return exc.code, headers, payload[:BODY_PREVIEW_BYTES]
    except (URLError, OSError) as exc:
        return None, {}, str(exc).encode("utf-8")


def main():
    creds = read_credentials()
    if creds is None:
        emit({"error": "no_credentials"})
        return
    token, expires_at = creds
    notes = read_oauth_account_notes()
    if expires_at is not None and expires_at <= time.time() + EXPIRY_SKEW_SECONDS:
        emit(
            {
                "error": "expired",
                "expires_at": expires_at,
                "oauth_account_notes": notes,
            }
        )
        return
    status_code, headers, body = fetch_messages(token)
    if status_code is None:
        emit(
            {
                "error": "network",
                "body_preview": body.decode("utf-8", errors="replace"),
                "oauth_account_notes": notes,
            }
        )
        return
    emit(
        {
            "status": status_code,
            "headers": headers,
            "body_preview": body.decode("utf-8", errors="replace"),
            "oauth_account_notes": notes,
            "expires_at": expires_at,
        }
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        emit({"error": "internal", "message": repr(exc)})
