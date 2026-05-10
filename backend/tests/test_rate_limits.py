from datetime import UTC, datetime

import pytest

from waypoint.backends.claude_code.rate_limits import (
    _read_cli_credentials_for_env,
    parse_claude_usage_payload,
)
from waypoint.backends.codex.rate_limits import parse_codex_status


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
        '{"claudeAiOauth":{"accessToken":"file-token"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._read_keychain_access_token",
        lambda env: "keychain-token",
    )
    result = _read_cli_credentials_for_env({"CLAUDE_CONFIG_DIR": str(claude_dir)})
    assert result is not None
    token, note = result
    assert token == "file-token"
    assert note == "CLI creds"


def test_read_cli_credentials_falls_back_to_keychain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "waypoint.backends.claude_code.rate_limits._read_keychain_access_token",
        lambda env: "keychain-token",
    )
    result = _read_cli_credentials_for_env({})
    assert result is not None
    token, note = result
    assert token == "keychain-token"
    assert note == "CLI creds"
