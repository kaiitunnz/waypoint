"""Static-credential account identity for token-auth claude profiles.

A claude account profile that sets a static ``ANTHROPIC_AUTH_TOKEN``/
``ANTHROPIC_API_KEY`` (typically with a custom ``ANTHROPIC_BASE_URL``) — either
in its ``CLAUDE_CONFIG_DIR/settings.json`` ``env`` block or in the session's
configured launch env — has no OAuth ``.credentials.json``. The live rate-limit
probe therefore can't verify it, and a running-session switch onto it used to
400 with "could not verify the target account". ``probe_account`` now derives a
stable identity from that static config for such profiles, and falls through to
the live probe for OAuth ones.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from waypoint.backends.account_profiles import probe_account
from waypoint.backends.claude_code.rate_limits import claude_static_account_identity
from waypoint.backends.claude_code.threads import claude_settings_env
from waypoint.runtime import SessionRuntime
from waypoint.schemas import AccountProbeResult, SessionRateLimitUsage
from waypoint.settings import Settings
from waypoint.storage import Storage


def _runtime(tmp_path: Path) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    return SessionRuntime(settings, Storage(settings.database_path))


def _config_dir(tmp_path: Path, env: dict[str, Any] | None) -> Path:
    d = tmp_path / "pool"
    d.mkdir(parents=True, exist_ok=True)
    if env is not None:
        (d / "settings.json").write_text(json.dumps({"env": env}))
    return d


# ── claude_settings_env ─────────────────────────────────────────────────────


def test_settings_env_reads_string_values(tmp_path: Path) -> None:
    d = _config_dir(
        tmp_path, {"ANTHROPIC_BASE_URL": "https://x", "MAX_THINKING_TOKENS": 5}
    )
    # Non-string values are dropped (the CLI env block is a string map).
    assert claude_settings_env(str(d)) == {"ANTHROPIC_BASE_URL": "https://x"}


def test_settings_env_missing_file(tmp_path: Path) -> None:
    assert claude_settings_env(str(tmp_path / "nope")) == {}


def test_settings_env_malformed_or_no_env_block(tmp_path: Path) -> None:
    d = tmp_path / "pool"
    d.mkdir()
    (d / "settings.json").write_text("{ not json")
    assert claude_settings_env(str(d)) == {}
    (d / "settings.json").write_text(json.dumps({"model": "opus"}))  # no env key
    assert claude_settings_env(str(d)) == {}


# ── claude_static_account_identity ──────────────────────────────────────────


def test_identity_from_auth_token(tmp_path: Path) -> None:
    d = _config_dir(
        tmp_path,
        {
            "ANTHROPIC_BASE_URL": "https://proxy.example.com",
            "ANTHROPIC_AUTH_TOKEN": "t",
        },
    )
    result = claude_static_account_identity("claude_code", str(d), {})
    assert result is not None
    assert result.account_key.startswith("claude_code:token:proxy.example.com:")
    assert result.account_label == "proxy.example.com (auth token)"
    assert result.source == "api"


def test_identity_from_api_key_defaults_host(tmp_path: Path) -> None:
    d = _config_dir(tmp_path, {"ANTHROPIC_API_KEY": "k"})
    result = claude_static_account_identity("claude_code", str(d), {})
    assert result is not None
    assert result.account_key.startswith("claude_code:token:api.anthropic.com:")
    assert result.account_label == "api.anthropic.com (API key)"


def test_identity_stable_and_distinct(tmp_path: Path) -> None:
    d1 = _config_dir(tmp_path / "a", {"ANTHROPIC_AUTH_TOKEN": "t"})
    d2 = _config_dir(tmp_path / "b", {"ANTHROPIC_AUTH_TOKEN": "other"})
    k1 = claude_static_account_identity("claude_code", str(d1), {})
    k1_again = claude_static_account_identity("claude_code", str(d1), {})
    k2 = claude_static_account_identity("claude_code", str(d2), {})
    assert k1 is not None and k1_again is not None and k2 is not None
    assert k1.account_key == k1_again.account_key  # stable
    assert k1.account_key != k2.account_key  # distinct credential


def test_identity_none_without_token(tmp_path: Path) -> None:
    # A custom base_url but no static credential is still OAuth -> no identity.
    d = _config_dir(tmp_path, {"ANTHROPIC_BASE_URL": "https://proxy"})
    assert claude_static_account_identity("claude_code", str(d), {}) is None


def test_identity_none_without_settings(tmp_path: Path) -> None:
    d = _config_dir(tmp_path, None)
    assert claude_static_account_identity("claude_code", str(d), {}) is None


def test_identity_from_configured_env_without_settings(tmp_path: Path) -> None:
    # The token comes from the session's configured env (e.g. the plugin `env`
    # block in waypoint.yaml or a per-session env_set), not settings.json.
    d = _config_dir(tmp_path, None)  # no settings.json
    result = claude_static_account_identity(
        "claude_code",
        str(d),
        {
            "ANTHROPIC_BASE_URL": "https://proxy.example.com",
            "ANTHROPIC_AUTH_TOKEN": "t",
        },
    )
    assert result is not None
    assert result.account_key.startswith("claude_code:token:proxy.example.com:")
    assert result.account_label == "proxy.example.com (auth token)"


def test_identity_from_env_with_no_config_dir(tmp_path: Path) -> None:
    # No profile config dir at all — a token in the configured env still verifies.
    result = claude_static_account_identity(
        "claude_code", None, {"ANTHROPIC_API_KEY": "k"}
    )
    assert result is not None
    assert result.account_key.startswith("claude_code:token:api.anthropic.com:")


def test_settings_json_wins_tie_over_env(tmp_path: Path) -> None:
    # The CLI applies settings.json env last, so it wins when both set the host.
    d = _config_dir(tmp_path, {"ANTHROPIC_BASE_URL": "https://from-settings"})
    result = claude_static_account_identity(
        "claude_code",
        str(d),
        {"ANTHROPIC_AUTH_TOKEN": "t", "ANTHROPIC_BASE_URL": "https://from-env"},
    )
    assert result is not None
    assert result.account_label == "from-settings (auth token)"


# ── probe_account wiring ────────────────────────────────────────────────────


async def test_probe_account_uses_static_identity_and_skips_live_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    plugin = runtime.registry.get("claude_code")
    d = _config_dir(tmp_path, {"ANTHROPIC_AUTH_TOKEN": "t"})

    calls: list[int] = []

    async def fake_probe(*_a: Any, **_k: Any) -> SessionRateLimitUsage | None:
        calls.append(1)
        return None

    monkeypatch.setattr(plugin, "probe_account_rate_limit", fake_probe)

    result = await probe_account(runtime, "claude_code", {"CLAUDE_CONFIG_DIR": str(d)})
    assert isinstance(result, AccountProbeResult)
    assert ":token:" in result.account_key
    assert calls == []  # static identity short-circuits before the live probe


async def test_probe_account_uses_env_token_from_launch_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    plugin = runtime.registry.get("claude_code")
    d = _config_dir(tmp_path, None)  # profile config dir without a token settings.json

    calls: list[int] = []

    async def fake_probe(*_a: Any, **_k: Any) -> SessionRateLimitUsage | None:
        calls.append(1)
        return None

    monkeypatch.setattr(plugin, "probe_account_rate_limit", fake_probe)

    # The token is set in the session's configured launch env (plugin/per-session
    # env), alongside the profile's config-dir key.
    result = await probe_account(
        runtime,
        "claude_code",
        {"CLAUDE_CONFIG_DIR": str(d), "ANTHROPIC_AUTH_TOKEN": "t"},
    )
    assert isinstance(result, AccountProbeResult)
    assert ":token:" in result.account_key
    assert calls == []  # env token short-circuits before the live probe


async def test_probe_account_falls_through_to_live_probe_for_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    plugin = runtime.registry.get("claude_code")
    d = _config_dir(tmp_path, None)  # no settings.json token -> OAuth profile

    calls: list[int] = []

    async def fake_probe(*_a: Any, **_k: Any) -> SessionRateLimitUsage:
        calls.append(1)
        # rate_limit_account derives the key from the ``org:`` note.
        return SessionRateLimitUsage(
            source="claude_code",
            updated_at=datetime.now(UTC),
            notes=["org: TestOrg"],
        )

    monkeypatch.setattr(plugin, "probe_account_rate_limit", fake_probe)

    result = await probe_account(runtime, "claude_code", {"CLAUDE_CONFIG_DIR": str(d)})
    assert isinstance(result, AccountProbeResult)
    assert result.account_key == "claude_code:TestOrg"
    assert calls == [1]  # OAuth profile fell through to the live probe
