import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from waypoint.backends.claude_code.rate_limits import (
    _ClaudeHTTPResponse,
    _read_cli_credentials_for_env,
    _read_oauth_account_notes,
    parse_claude_rate_limit_headers,
    parse_claude_usage_payload,
    probe_claude_usage,
)
from waypoint.backends.codex.rate_limits import (
    _load_oauth_credentials,
    _resolve_usage_url,
    parse_codex_status,
    parse_codex_usage_payload,
)


def test_parse_codex_status_extracts_windows_and_credits() -> None:
    snapshot = parse_codex_status("""
        \x1b[32mCredits: $12.34\x1b[0m
        5h limit: 42% used (resets in 2h 10m)
        Weekly limit: 73% left, resets at 2026-05-11 12:00 UTC
        """)
    assert snapshot is not None
    assert snapshot.source == "codex"
    assert snapshot.credits_currency == "USD"
    assert snapshot.credits_remaining == 12.34
    assert [window.label for window in snapshot.windows] == ["5h", "Weekly"]
    assert snapshot.windows[0].used_percent == 42.0
    assert snapshot.windows[0].reset_description == "2h 10m"
    assert snapshot.windows[1].used_percent == 27.0
    assert snapshot.windows[1].reset_description == "2026-05-11 12:00 UTC"


def test_parse_codex_usage_payload_extracts_windows_and_credits() -> None:
    snapshot = parse_codex_usage_payload(
        {
            "plan_type": "education",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 22,
                    "reset_at": 1766948068,
                    "limit_window_seconds": 18000,
                },
                "secondary_window": {
                    "used_percent": 43,
                    "reset_at": 1767407914,
                    "limit_window_seconds": 604800,
                },
            },
            "credits": {
                "has_credits": True,
                "unlimited": False,
                "balance": 42.5,
            },
            "email": "noppanat@example.com",
        },
        now=datetime(2026, 5, 10, 8, 0, tzinfo=UTC),
        notes=["CLI OAuth"],
    )
    assert snapshot is not None
    assert snapshot.source == "codex"
    assert snapshot.updated_at == datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    assert snapshot.notes == ["CLI OAuth", "plan: education", "noppanat@example.com"]
    assert [window.label for window in snapshot.windows] == ["5h", "Weekly"]
    assert snapshot.windows[0].used_percent == 22.0
    assert snapshot.windows[0].window_minutes == 300
    assert snapshot.windows[0].resets_at == datetime.fromtimestamp(1766948068, tz=UTC)
    assert snapshot.windows[1].used_percent == 43.0
    assert snapshot.windows[1].window_minutes == 10080
    assert snapshot.credits_remaining == 42.5
    assert snapshot.credits_currency == "USD"


def test_load_oauth_credentials_reads_auth_json(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(
        """
        {
          "tokens": {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "account_id": "account-123"
          },
          "last_refresh": "2026-05-01T12:34:56Z"
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    creds = _load_oauth_credentials({"CODEX_HOME": str(codex_home)})
    assert creds is not None
    assert creds.access_token == "access-token"
    assert creds.refresh_token == "refresh-token"
    assert creds.account_id == "account-123"
    assert creds.last_refresh == datetime(2026, 5, 1, 12, 34, 56, tzinfo=UTC)


def test_resolve_usage_url_prefers_chatgpt_backend_api(tmp_path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        'chatgpt_base_url = "https://chatgpt.com/"\n',
        encoding="utf-8",
    )
    url = _resolve_usage_url({"CODEX_HOME": str(codex_home)})
    assert url == "https://chatgpt.com/backend-api/wham/usage"


def test_parse_claude_usage_payload_normalizes_windows() -> None:
    snapshot = parse_claude_usage_payload(
        {
            "five_hour": {
                "utilization": 0.54,
                "resets_at": "2026-05-10T12:34:56Z",
            },
            "seven_day": {
                "utilization": "81.2",
                "resets_at": "2026-05-12T01:00:00Z",
            },
            "seven_day_opus": {
                "utilization": 0.12,
                "resets_at": "2026-05-12T01:00:00Z",
            },
        },
        now=datetime(2026, 5, 10, 8, 0, tzinfo=UTC),
        notes=["CLI creds"],
    )
    assert snapshot is not None
    assert snapshot.source == "claude_code"
    assert snapshot.updated_at == datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    assert snapshot.notes == ["CLI creds"]
    assert [window.label for window in snapshot.windows] == ["5h", "Weekly", "Opus"]
    assert snapshot.windows[0].used_percent == 54.0
    assert snapshot.windows[0].window_minutes == 300
    assert snapshot.windows[0].resets_at == datetime(
        2026, 5, 10, 12, 34, 56, tzinfo=UTC
    )
    assert snapshot.windows[1].used_percent == 81.2
    assert snapshot.windows[2].used_percent == 12.0


def test_read_cli_credentials_prefers_file_before_keychain(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / ".credentials.json").write_text(
        '{"claudeAiOauth":{"accessToken":"file-token","expiresAt":1893456000000}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._read_keychain_access_token",
        lambda env: "keychain-token",
    )
    result = _read_cli_credentials_for_env({"CLAUDE_CONFIG_DIR": str(claude_dir)})
    assert result is not None
    token, expires_at, note = result
    assert token == "file-token"
    assert expires_at == datetime(2030, 1, 1, tzinfo=UTC)
    assert note == "CLI creds"


def test_read_cli_credentials_falls_back_to_keychain_json_blob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._read_keychain_access_token",
        lambda env: (
            '{"claudeAiOauth":{"accessToken":"keychain-token",'
            '"expiresAt":1893456000000}}'
        ),
    )
    result = _read_cli_credentials_for_env({})
    assert result is not None
    token, expires_at, note = result
    assert token == "keychain-token"
    assert expires_at == datetime(2030, 1, 1, tzinfo=UTC)
    assert note == "CLI creds"


def test_read_cli_credentials_keychain_legacy_bare_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._read_keychain_access_token",
        lambda env: "bare-token-no-json",
    )
    result = _read_cli_credentials_for_env({})
    assert result is not None
    token, expires_at, note = result
    assert token == "bare-token-no-json"
    assert expires_at is None
    assert note == "CLI creds"


def test_read_oauth_account_notes_extract_tiers(tmp_path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / ".claude.json").write_text(
        """
        {
          "oauthAccount": {
            "organizationName": "lumid",
            "userRateLimitTier": "default_claude_max_5x",
            "organizationRateLimitTier": "default_raven"
          }
        }
        """,
        encoding="utf-8",
    )
    notes = _read_oauth_account_notes({"CLAUDE_CONFIG_DIR": str(claude_dir)})
    assert notes == [
        "org: lumid",
        "user tier: default_claude_max_5x",
        "org tier: default_raven",
    ]


def test_parse_claude_rate_limit_headers_extracts_windows() -> None:
    snapshot = parse_claude_rate_limit_headers(
        {
            "Anthropic-RateLimit-Unified-5h-Utilization": "0.25",
            "Anthropic-RateLimit-Unified-5h-Reset": str(
                datetime(2026, 5, 10, 13, 0, tzinfo=UTC).timestamp()
            ),
            "anthropic-ratelimit-unified-7d-utilization": "81.2",
            "anthropic-ratelimit-unified-7d-reset": str(
                datetime(2026, 5, 12, 1, 0, tzinfo=UTC).timestamp()
            ),
        },
        now=datetime(2026, 5, 10, 8, 0, tzinfo=UTC),
        notes=["CLI creds"],
    )
    assert snapshot is not None
    assert snapshot.source == "claude_code"
    assert snapshot.updated_at == datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    assert snapshot.notes == ["CLI creds"]
    assert [window.label for window in snapshot.windows] == ["5h", "Weekly"]
    assert snapshot.windows[0].used_percent == 25.0
    assert snapshot.windows[0].resets_at == datetime(2026, 5, 10, 13, 0, tzinfo=UTC)
    assert snapshot.windows[1].used_percent == 81.2
    assert snapshot.windows[1].resets_at == datetime(2026, 5, 12, 1, 0, tzinfo=UTC)


def test_probe_claude_usage_surfaces_rate_limit_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._read_cli_credentials_for_env",
        lambda env: ("access-token", None, "CLI creds"),
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._claude_user_agent",
        lambda: "claude-code/2.1.136",
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._read_oauth_account_notes",
        lambda env: ["org: lumid", "user tier: default_claude_max_5x"],
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._fetch_claude_messages_usage",
        lambda request: _ClaudeHTTPResponse(
            status=429,
            headers={"Retry-After": "42"},
            body=b'{"error":{"type":"rate_limit_error"}}',
        ),
    )

    snapshot = asyncio.run(probe_claude_usage(env={}))
    assert snapshot is not None
    assert snapshot.source == "claude_code"
    assert snapshot.windows == []
    assert snapshot.notes == [
        "CLI creds",
        "org: lumid",
        "user tier: default_claude_max_5x",
        "rate limited; retry after 42s",
    ]


def test_probe_claude_usage_parses_messages_api_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._read_cli_credentials_for_env",
        lambda env: ("access-token", None, "CLI creds"),
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._claude_user_agent",
        lambda: "claude-code/2.1.136",
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._read_oauth_account_notes",
        lambda env: ["org: lumid", "user tier: default_claude_max_5x"],
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._fetch_claude_messages_usage",
        lambda request: _ClaudeHTTPResponse(
            status=200,
            headers={
                "anthropic-ratelimit-unified-5h-utilization": "0.25",
                "anthropic-ratelimit-unified-5h-reset": str(
                    datetime(2026, 5, 12, 13, 0, tzinfo=UTC).timestamp()
                ),
                "anthropic-ratelimit-unified-7d-utilization": "0.812",
                "anthropic-ratelimit-unified-7d-reset": str(
                    datetime(2026, 5, 19, 1, 0, tzinfo=UTC).timestamp()
                ),
            },
            body=b'{"content":[{"type":"text","text":"hi"}]}',
        ),
    )

    snapshot = asyncio.run(probe_claude_usage(env={}))
    assert snapshot is not None
    assert snapshot.source == "claude_code"
    assert [window.label for window in snapshot.windows] == ["5h", "Weekly"]
    assert snapshot.windows[0].used_percent == 25.0
    assert snapshot.windows[1].used_percent == 81.2
    assert snapshot.notes == [
        "CLI creds",
        "org: lumid",
        "user tier: default_claude_max_5x",
    ]


def test_probe_claude_usage_bails_when_token_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expired_at = datetime.now(UTC) - timedelta(hours=1)
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._read_cli_credentials_for_env",
        lambda env: ("access-token", expired_at, "CLI creds"),
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._read_oauth_account_notes",
        lambda env: ["org: lumid"],
    )

    def _should_not_be_called(request: object) -> object:
        raise AssertionError("HTTP call must not happen when token is expired")

    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._fetch_claude_messages_usage",
        _should_not_be_called,
    )
    snapshot = asyncio.run(probe_claude_usage(env={}))
    assert snapshot is not None
    assert snapshot.source == "claude_code"
    assert snapshot.windows == []
    assert snapshot.notes == [
        "CLI creds",
        "credentials expired — run `claude` to refresh",
        "org: lumid",
    ]


def test_probe_claude_usage_surfaces_401_as_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._read_cli_credentials_for_env",
        lambda env: ("access-token", None, "CLI creds"),
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._claude_user_agent",
        lambda: "claude-code/2.1.136",
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._read_oauth_account_notes",
        lambda env: [],
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._fetch_claude_messages_usage",
        lambda request: _ClaudeHTTPResponse(
            status=401,
            headers={},
            body=b'{"type":"error","error":{"type":"authentication_error",'
            b'"message":"Invalid bearer token"}}',
        ),
    )
    snapshot = asyncio.run(probe_claude_usage(env={}))
    assert snapshot is not None
    assert snapshot.source == "claude_code"
    assert snapshot.windows == []
    assert snapshot.notes == [
        "CLI creds",
        "credentials expired — run `claude` to refresh",
    ]


def test_parse_codex_usage_payload_emits_empty_snapshot_for_education_plan() -> None:
    snapshot = parse_codex_usage_payload(
        {
            "plan_type": "education",
            "rate_limit": None,
            "code_review_rate_limit": None,
            "additional_rate_limits": None,
            "email": "noppanat@example.com",
        },
        now=datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
    )
    assert snapshot is not None
    assert snapshot.source == "codex"
    assert snapshot.windows == []
    assert snapshot.credits_remaining is None
    assert snapshot.notes == [
        "CLI OAuth",
        "plan: education",
        "noppanat@example.com",
    ]
