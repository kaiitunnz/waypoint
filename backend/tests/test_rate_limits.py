import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from waypoint.backends.claude_code.rate_limits import (
    _ClaudeHTTPResponse,
    _local_probe_cache_key,
    _read_cli_credentials_for_env,
    _read_oauth_account_notes,
    _remote_probe_cache_key,
    invalidate_shared_probe_local,
    parse_claude_rate_limit_headers,
    parse_claude_usage_payload,
    probe_claude_usage,
    probe_claude_usage_remote,
    probe_claude_usage_shared,
)
from waypoint.backends.codex.rate_limits import (
    _load_oauth_credentials,
    _oauth_snapshot_is_actionable,
    _resolve_usage_url,
    parse_codex_status,
    parse_codex_usage_payload,
    probe_codex_usage_remote,
)
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import SessionRateLimitUsage, UsageWindow


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
    # Isolate the file-based source: on a host with a real ~/.claude
    # credentials file it would otherwise win over the keychain fallback.
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._credential_paths",
        lambda env: (),
    )
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
        "waypoint.backends.claude_code.rate_limits._credential_paths",
        lambda env: (),
    )
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
        "waypoint.backends.claude_code.rate_limits._fetch_claude_oauth_usage",
        lambda token: None,
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
        "waypoint.backends.claude_code.rate_limits._fetch_claude_oauth_usage",
        lambda token: None,
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._fetch_claude_messages_usage",
        lambda request: _ClaudeHTTPResponse(
            status=200,
            headers={
                "anthropic-ratelimit-unified-5h-utilization": "0.25",
                "anthropic-ratelimit-unified-5h-reset": str(
                    (datetime.now(UTC) + timedelta(hours=4)).timestamp()
                ),
                "anthropic-ratelimit-unified-7d-utilization": "0.812",
                "anthropic-ratelimit-unified-7d-reset": str(
                    (datetime.now(UTC) + timedelta(days=6)).timestamp()
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
        "waypoint.backends.claude_code.rate_limits._fetch_claude_oauth_usage",
        _should_not_be_called,
    )
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


def test_probe_claude_usage_prefers_oauth_usage_endpoint(
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
        lambda env: ["org: lumid"],
    )
    body = json.dumps(
        {
            "five_hour": {"utilization": 11.0, "resets_at": "2026-06-21T09:20:00Z"},
            "seven_day": {"utilization": 2.0, "resets_at": "2026-06-23T12:00:00Z"},
            # Armed once Sonnet usage accrues this week.
            "seven_day_sonnet": {
                "utilization": 3.0,
                "resets_at": "2026-06-23T12:00:00Z",
            },
            # No Opus weekly limit on this plan — null entry must be skipped.
            "seven_day_opus": None,
        }
    ).encode("utf-8")
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._fetch_claude_oauth_usage",
        lambda token: _ClaudeHTTPResponse(status=200, headers={}, body=body),
    )

    def _messages_must_not_run(request: object) -> object:
        raise AssertionError("Messages API must not be called when usage succeeds")

    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._fetch_claude_messages_usage",
        _messages_must_not_run,
    )

    snapshot = asyncio.run(probe_claude_usage(env={}))
    assert snapshot is not None
    assert snapshot.source == "claude_code"
    assert [window.label for window in snapshot.windows] == ["5h", "Weekly", "Sonnet"]
    assert snapshot.windows[2].used_percent == 3.0
    assert snapshot.notes == ["CLI creds", "org: lumid"]


def test_probe_claude_usage_falls_back_when_usage_endpoint_disabled(
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
    # The usage endpoint has historically been disabled and answers a 4xx.
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._fetch_claude_oauth_usage",
        lambda token: _ClaudeHTTPResponse(status=404, headers={}, body=b"not found"),
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._fetch_claude_messages_usage",
        lambda request: _ClaudeHTTPResponse(
            status=200,
            headers={
                "anthropic-ratelimit-unified-5h-utilization": "0.25",
                "anthropic-ratelimit-unified-5h-reset": str(
                    (datetime.now(UTC) + timedelta(hours=4)).timestamp()
                ),
                "anthropic-ratelimit-unified-7d-utilization": "0.5",
                "anthropic-ratelimit-unified-7d-reset": str(
                    (datetime.now(UTC) + timedelta(days=6)).timestamp()
                ),
            },
            body=b"{}",
        ),
    )

    snapshot = asyncio.run(probe_claude_usage(env={}))
    assert snapshot is not None
    assert [window.label for window in snapshot.windows] == ["5h", "Weekly"]
    assert snapshot.windows[0].used_percent == 25.0


def test_parse_claude_usage_payload_skips_unarmed_per_model_window() -> None:
    snapshot = parse_claude_usage_payload(
        {
            "five_hour": {"utilization": 0.04, "resets_at": "2026-06-21T09:20:00Z"},
            "seven_day": {"utilization": 0.02, "resets_at": "2026-06-23T12:00:00Z"},
            # Before any Sonnet usage: 0% utilization and no reset → skipped.
            "seven_day_sonnet": {"utilization": 0.0, "resets_at": None},
        },
        now=datetime(2026, 6, 21, 8, 0, tzinfo=UTC),
    )
    assert snapshot is not None
    assert [window.label for window in snapshot.windows] == ["5h", "Weekly"]


def test_parse_claude_usage_payload_shows_armed_zero_per_model_window() -> None:
    snapshot = parse_claude_usage_payload(
        {
            "five_hour": {"utilization": 0.04, "resets_at": "2026-06-21T09:20:00Z"},
            "seven_day": {"utilization": 0.02, "resets_at": "2026-06-23T12:00:00Z"},
            # Armed once Sonnet usage accrues: 0% but a reset is set → surfaced,
            # so the row appears before weekly usage rounds above 0%.
            "seven_day_sonnet": {
                "utilization": 0.0,
                "resets_at": "2026-06-23T12:00:00Z",
            },
        },
        now=datetime(2026, 6, 21, 8, 0, tzinfo=UTC),
    )
    assert snapshot is not None
    assert [window.label for window in snapshot.windows] == ["5h", "Weekly", "Sonnet"]
    assert snapshot.windows[2].used_percent == 0.0


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
        "waypoint.backends.claude_code.rate_limits._fetch_claude_oauth_usage",
        lambda token: None,
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


def _ssh_target() -> SshLaunchTargetConfig:
    return SshLaunchTargetConfig(
        id="rover",
        name="rover",
        ssh_destination="user@rover.lan",
        ssh_args=["-o", "ControlMaster=no"],
        remote_shell="",
    )


def test_probe_claude_usage_remote_parses_messages_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "status": 200,
        "headers": {
            "anthropic-ratelimit-unified-5h-utilization": "0.4",
            "anthropic-ratelimit-unified-5h-reset": str(
                (datetime.now(UTC) + timedelta(hours=4)).timestamp()
            ),
            "anthropic-ratelimit-unified-7d-utilization": "0.92",
            "anthropic-ratelimit-unified-7d-reset": str(
                (datetime.now(UTC) + timedelta(days=6)).timestamp()
            ),
        },
        "body_preview": "{}",
        "oauth_account_notes": ["org: lumid", "user tier: default_claude_max_5x"],
        "expires_at": None,
    }

    async def _fake_runner(launch_target, timeout_seconds, launch_env=None):
        assert launch_target.ssh_destination == "user@rover.lan"
        return payload

    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._run_remote_probe_script",
        _fake_runner,
    )
    snapshot = asyncio.run(probe_claude_usage_remote(_ssh_target()))
    assert snapshot is not None
    assert snapshot.source == "claude_code"
    assert [w.label for w in snapshot.windows] == ["5h", "Weekly"]
    assert snapshot.windows[0].used_percent == 40.0
    assert snapshot.windows[1].used_percent == 92.0
    assert snapshot.notes == [
        "remote CLI creds",
        "org: lumid",
        "user tier: default_claude_max_5x",
    ]


def test_probe_claude_usage_remote_parses_oauth_usage_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "usage": {
            "five_hour": {"utilization": 11.0, "resets_at": "2026-06-21T09:20:00Z"},
            "seven_day": {"utilization": 2.0, "resets_at": "2026-06-23T12:00:00Z"},
            "seven_day_sonnet": {
                "utilization": 3.0,
                "resets_at": "2026-06-23T12:00:00Z",
            },
        },
        "oauth_account_notes": ["org: lumid"],
        "expires_at": None,
    }

    async def _fake_runner(launch_target, timeout_seconds, launch_env=None):
        return payload

    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._run_remote_probe_script",
        _fake_runner,
    )
    snapshot = asyncio.run(probe_claude_usage_remote(_ssh_target()))
    assert snapshot is not None
    assert snapshot.source == "claude_code"
    assert [w.label for w in snapshot.windows] == ["5h", "Weekly", "Sonnet"]
    assert snapshot.notes == ["remote CLI creds", "org: lumid"]


def test_probe_claude_usage_remote_ignores_windowless_usage_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The remote script only emits the usage variant when it carries a window,
    # but if a window-less usage payload ever arrives the backend returns None
    # cleanly rather than misrouting into the malformed-payload branch.
    async def _fake_runner(launch_target, timeout_seconds, launch_env=None):
        return {"usage": {}, "oauth_account_notes": ["org: lumid"], "expires_at": None}

    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._run_remote_probe_script",
        _fake_runner,
    )
    snapshot = asyncio.run(probe_claude_usage_remote(_ssh_target()))
    assert snapshot is None


def test_probe_claude_usage_remote_handles_expired_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_runner(launch_target, timeout_seconds, launch_env=None):
        return {
            "error": "expired",
            "expires_at": 1700000000.0,
            "oauth_account_notes": ["org: lumid"],
        }

    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._run_remote_probe_script",
        _fake_runner,
    )
    snapshot = asyncio.run(probe_claude_usage_remote(_ssh_target()))
    assert snapshot is not None
    assert snapshot.source == "claude_code"
    assert snapshot.windows == []
    assert snapshot.notes == [
        "remote CLI creds",
        "credentials expired — run `claude` to refresh",
        "org: lumid",
    ]


def test_probe_claude_usage_remote_handles_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_runner(launch_target, timeout_seconds, launch_env=None):
        return {"error": "no_credentials"}

    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._run_remote_probe_script",
        _fake_runner,
    )
    snapshot = asyncio.run(probe_claude_usage_remote(_ssh_target()))
    assert snapshot is None


def test_probe_claude_usage_remote_surfaces_401_as_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_runner(launch_target, timeout_seconds, launch_env=None):
        return {
            "status": 401,
            "headers": {},
            "body_preview": '{"type":"error"}',
            "oauth_account_notes": [],
            "expires_at": None,
        }

    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._run_remote_probe_script",
        _fake_runner,
    )
    snapshot = asyncio.run(probe_claude_usage_remote(_ssh_target()))
    assert snapshot is not None
    assert snapshot.windows == []
    assert "credentials expired" in snapshot.notes[1]


def test_probe_codex_usage_remote_parses_oauth_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_runner(launch_target, binary, timeout_seconds, launch_env=None):
        return {
            "payload": {
                "plan_type": "pro",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 30,
                        "limit_window_seconds": 18000,
                    }
                },
                "email": "user@example.com",
            },
            "usage_url": "https://chatgpt.com/backend-api/wham/usage",
        }

    monkeypatch.setattr(
        "waypoint.backends.codex.rate_limits._run_remote_probe_script",
        _fake_runner,
    )
    snapshot = asyncio.run(probe_codex_usage_remote(_ssh_target(), binary="codex"))
    assert snapshot is not None
    assert snapshot.source == "codex"
    assert [w.label for w in snapshot.windows] == ["5h"]
    assert snapshot.windows[0].used_percent == 30.0
    assert "remote OAuth" in snapshot.notes
    assert "plan: pro" in snapshot.notes


def test_probe_codex_usage_remote_falls_back_to_status_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_text = (
        "Credits: $5.00\n"
        "5h limit: 12% used (resets in 1h)\n"
        "Weekly limit: 80% left, resets at 2026-05-11 00:00 UTC\n"
    )

    async def _fake_runner(launch_target, binary, timeout_seconds, launch_env=None):
        return {"status_text": status_text}

    monkeypatch.setattr(
        "waypoint.backends.codex.rate_limits._run_remote_probe_script",
        _fake_runner,
    )
    snapshot = asyncio.run(probe_codex_usage_remote(_ssh_target(), binary="codex"))
    assert snapshot is not None
    assert snapshot.credits_remaining == 5.0
    assert [w.label for w in snapshot.windows] == ["5h", "Weekly"]
    assert "remote /status" in snapshot.notes


def test_probe_codex_usage_remote_returns_none_for_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_runner(launch_target, binary, timeout_seconds, launch_env=None):
        return {"error": "no_data"}

    monkeypatch.setattr(
        "waypoint.backends.codex.rate_limits._run_remote_probe_script",
        _fake_runner,
    )
    snapshot = asyncio.run(probe_codex_usage_remote(_ssh_target(), binary="codex"))
    assert snapshot is None


def test_oauth_snapshot_is_actionable_recognizes_useful_data() -> None:
    now = datetime(2026, 5, 11, tzinfo=UTC)

    def _snap(**kwargs: object) -> SessionRateLimitUsage:
        return SessionRateLimitUsage(source="codex", updated_at=now, **kwargs)

    assert _oauth_snapshot_is_actionable(
        _snap(
            windows=[UsageWindow(id="five_hour", label="5h", used_percent=10.0)],
            notes=["CLI OAuth"],
        )
    )
    assert _oauth_snapshot_is_actionable(
        _snap(credits_remaining=42.5, credits_currency="USD", notes=["CLI OAuth"])
    )
    assert _oauth_snapshot_is_actionable(_snap(notes=["CLI OAuth", "plan: education"]))
    assert _oauth_snapshot_is_actionable(_snap(notes=["CLI OAuth", "user@example.com"]))
    # Default seed notes only — no real signal that we hit a real account.
    assert not _oauth_snapshot_is_actionable(_snap(notes=["CLI OAuth"]))
    assert not _oauth_snapshot_is_actionable(_snap(notes=["remote OAuth"]))
    assert not _oauth_snapshot_is_actionable(_snap(notes=[]))


def test_probe_codex_usage_remote_falls_back_when_oauth_payload_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_text = "5h limit: 12% used (resets in 1h)\nWeekly limit: 80% left\n"

    async def _fake_runner(launch_target, binary, timeout_seconds, launch_env=None):
        return {
            "payload": {
                "rate_limit": None,
                "additional_rate_limits": None,
            },
            "status_text": status_text,
        }

    monkeypatch.setattr(
        "waypoint.backends.codex.rate_limits._run_remote_probe_script",
        _fake_runner,
    )
    snapshot = asyncio.run(probe_codex_usage_remote(_ssh_target(), binary="codex"))
    # OAuth payload had no actionable data, so the runner should have fallen
    # through to the /status text instead of returning the empty snapshot.
    assert snapshot is not None
    assert [w.label for w in snapshot.windows] == ["5h", "Weekly"]
    assert "remote /status" in snapshot.notes


def test_probe_cache_keys_distinguish_local_and_remote() -> None:
    local_default = _local_probe_cache_key({})
    local_scoped = _local_probe_cache_key({"CLAUDE_CONFIG_DIR": "/tmp/acct-a"})
    remote = _remote_probe_cache_key(_ssh_target())

    assert local_default != local_scoped
    assert local_scoped == "local:/tmp/acct-a"
    assert remote == "remote:rover:~"
    assert remote != local_default and remote != local_scoped


def test_remote_probe_cache_key_distinguishes_config_dirs_on_one_target() -> None:
    target = _ssh_target()
    default_key = _remote_probe_cache_key(target)
    scoped_key = _remote_probe_cache_key(target, {"CLAUDE_CONFIG_DIR": "/team/alice"})
    other_scoped_key = _remote_probe_cache_key(
        target, {"CLAUDE_CONFIG_DIR": "/team/bob"}
    )

    assert default_key != scoped_key != other_scoped_key
    assert scoped_key == "remote:rover:/team/alice"


def _usage_snapshot(used: float) -> SessionRateLimitUsage:
    return SessionRateLimitUsage(
        source="claude_code",
        updated_at=datetime.now(UTC),
        windows=[UsageWindow(id="five_hour", label="5h", used_percent=used)],
        notes=["CLI creds"],
    )


def test_probe_claude_usage_shared_round_trips_force_and_invalidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = {"CLAUDE_CONFIG_DIR": "/tmp/shared-wrapper-test"}
    invalidate_shared_probe_local(env)
    calls = 0

    async def _fake_probe(*, env, timeout_seconds):
        nonlocal calls
        calls += 1
        return _usage_snapshot(float(calls))

    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits.probe_claude_usage",
        _fake_probe,
    )

    first = asyncio.run(probe_claude_usage_shared(env=env))
    cached = asyncio.run(probe_claude_usage_shared(env=env))
    assert calls == 1
    assert first is cached

    forced = asyncio.run(probe_claude_usage_shared(env=env, force=True))
    assert calls == 2
    assert forced is not None and forced.windows[0].used_percent == 2.0

    invalidate_shared_probe_local(env)
    asyncio.run(probe_claude_usage_shared(env=env))
    assert calls == 3

    invalidate_shared_probe_local(env)
