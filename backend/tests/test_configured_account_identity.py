"""Configured-account identity: a Claude config dir that declares its own auth.

A ``CLAUDE_CONFIG_DIR`` whose ``settings.json`` carries an ``env`` block with a
bearer token (typically against a custom ``ANTHROPIC_BASE_URL``) has no OAuth
credentials and no ``oauthAccount``, so the rate-limit probe that normally
identifies an account returns nothing and the runtime refused to switch a session
onto such a profile at all. These pin the local identity that replaces the probe
for those dirs, the conservatism that keeps OAuth profiles resolving exactly as
before, and the two-tier resolution in ``probe_account``.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from waypoint.backends.account_profiles import probe_account
from waypoint.backends.claude_code.configured_account import (
    configured_account_identity,
    read_settings_env,
)
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.runtime import SessionRuntime
from waypoint.schemas import AccountProbeResult, SessionRateLimitUsage
from waypoint.settings import Settings
from waypoint.storage import Storage

TOKEN = "sk-ant-oat01-secret-token-value"


@pytest.fixture(autouse=True)
def _isolate_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The fallback chain reads CLAUDE_CONFIG_DIR before ~/.claude, and this host
    # runs Waypoint itself, so both have to be neutralised or a test with no
    # explicit dir would read the developer's real config.
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)


def _pool_dir(base: Path, name: str = "pool", **env: str) -> str:
    config_dir = base / name
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.json").write_text(json.dumps({"env": env}))
    return str(config_dir)


# ── settings-env reader ─────────────────────────────────────────────────────


def test_reads_env_block(tmp_path: Path) -> None:
    config_dir = _pool_dir(tmp_path, ANTHROPIC_AUTH_TOKEN=TOKEN, OTHER="x")
    assert read_settings_env(config_dir) == {
        "ANTHROPIC_AUTH_TOKEN": TOKEN,
        "OTHER": "x",
    }


def test_reader_degrades_on_unusable_settings(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    assert read_settings_env(str(missing)) == {}

    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "settings.json").write_text("{not json")
    assert read_settings_env(str(malformed)) == {}

    not_an_object = tmp_path / "list"
    not_an_object.mkdir()
    (not_an_object / "settings.json").write_text("[]")
    assert read_settings_env(str(not_an_object)) == {}

    env_not_an_object = tmp_path / "env-scalar"
    env_not_an_object.mkdir()
    (env_not_an_object / "settings.json").write_text(json.dumps({"env": "nope"}))
    assert read_settings_env(str(env_not_an_object)) == {}


def test_reader_skips_non_string_and_empty_values(tmp_path: Path) -> None:
    config_dir = tmp_path / "mixed"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps({"env": {"A": "keep", "B": 5, "C": None, "D": ""}})
    )
    assert read_settings_env(str(config_dir)) == {"A": "keep"}


def test_reader_ignores_user_level_settings_local(tmp_path: Path) -> None:
    # The CLI reads settings.local.json only as a *project* layer; at the config
    # dir it honours settings.json alone (verified against the real CLI).
    config_dir = _pool_dir(tmp_path, ANTHROPIC_AUTH_TOKEN=TOKEN)
    (Path(config_dir) / "settings.local.json").write_text(
        json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "other"}})
    )
    assert read_settings_env(config_dir)["ANTHROPIC_AUTH_TOKEN"] == TOKEN


# ── identity: positive cases ─────────────────────────────────────────────────


def test_token_and_custom_endpoint_yield_identity(tmp_path: Path) -> None:
    config_dir = _pool_dir(
        tmp_path,
        ANTHROPIC_BASE_URL="https://gw.example.com",
        ANTHROPIC_AUTH_TOKEN=TOKEN,
    )
    identity = configured_account_identity(config_dir, {})
    assert identity is not None
    assert identity.account_key.startswith("claude_code:endpoint:gw.example.com:")
    assert identity.account_label == "gw.example.com · token auth"
    assert identity.source == "api"


def test_setup_token_alone_yields_identity(tmp_path: Path) -> None:
    # `claude setup-token` writes CLAUDE_CODE_OAUTH_TOKEN; it authenticates a pool
    # dir with no interactive login and no custom endpoint.
    config_dir = _pool_dir(tmp_path, CLAUDE_CODE_OAUTH_TOKEN=TOKEN)
    identity = configured_account_identity(config_dir, {})
    assert identity is not None
    assert identity.account_key.startswith("claude_code:endpoint:api.anthropic.com:")


def test_identity_never_carries_the_secret_or_url_userinfo(tmp_path: Path) -> None:
    config_dir = _pool_dir(
        tmp_path,
        ANTHROPIC_BASE_URL="https://svc:gateway-password@gw.example.com:8443/v1",
        ANTHROPIC_AUTH_TOKEN=TOKEN,
        ANTHROPIC_CUSTOM_HEADERS="X-Auth: header-secret",
    )
    identity = configured_account_identity(config_dir, {})
    assert identity is not None
    # The label is not redacted by the probe endpoint and both fields reach the
    # frontend through the usage dashboard, so neither may carry a secret.
    for field in (identity.account_key, identity.account_label or ""):
        assert TOKEN not in field
        assert "gateway-password" not in field
        assert "header-secret" not in field
        assert "svc" not in field
    assert identity.account_label == "gw.example.com:8443 · token auth"


def test_endpoint_case_and_path_do_not_split_the_plaintext_host(tmp_path: Path) -> None:
    upper = _pool_dir(
        tmp_path,
        "upper",
        ANTHROPIC_BASE_URL="HTTPS://GW.Example.COM/v1",
        ANTHROPIC_AUTH_TOKEN=TOKEN,
    )
    lower = _pool_dir(
        tmp_path,
        "lower",
        ANTHROPIC_BASE_URL="https://gw.example.com/v1",
        ANTHROPIC_AUTH_TOKEN=TOKEN,
    )
    upper_identity = configured_account_identity(upper, {})
    lower_identity = configured_account_identity(lower, {})
    assert upper_identity is not None and lower_identity is not None
    # One endpoint, one key — a user who normalises their URL keeps the same
    # expected_account_key.
    assert upper_identity.account_key == lower_identity.account_key


def test_base_url_comes_from_launch_env_when_settings_omits_it(tmp_path: Path) -> None:
    # Token in the profile's settings.json, endpoint from a plugin-level env: the
    # session talks to the gateway, so the key and label must say so.
    config_dir = _pool_dir(tmp_path, ANTHROPIC_AUTH_TOKEN=TOKEN)
    identity = configured_account_identity(
        config_dir, {"ANTHROPIC_BASE_URL": "https://gw.example.com"}
    )
    assert identity is not None
    assert identity.account_key.startswith("claude_code:endpoint:gw.example.com:")


def test_settings_base_url_overrides_launch_env(tmp_path: Path) -> None:
    # The CLI applies settings.json's env over the inherited process env.
    config_dir = _pool_dir(
        tmp_path,
        ANTHROPIC_BASE_URL="https://from-settings.example.com",
        ANTHROPIC_AUTH_TOKEN=TOKEN,
    )
    identity = configured_account_identity(
        config_dir, {"ANTHROPIC_BASE_URL": "https://from-launch-env.example.com"}
    )
    assert identity is not None
    assert "from-settings.example.com" in identity.account_key
    assert "from-launch-env" not in identity.account_key


# ── identity: the key must distinguish accounts ──────────────────────────────


def _key(config_dir: str) -> str:
    identity = configured_account_identity(config_dir, {})
    assert identity is not None
    return identity.account_key


def test_same_endpoint_and_token_resolve_to_the_same_key(tmp_path: Path) -> None:
    env = {
        "ANTHROPIC_BASE_URL": "https://gw.example.com",
        "ANTHROPIC_AUTH_TOKEN": TOKEN,
    }
    # Two dirs, same credential: genuinely the same account, so the runtime's
    # no-op guard should still refuse a switch between them.
    assert _key(_pool_dir(tmp_path, "a", **env)) == _key(
        _pool_dir(tmp_path, "b", **env)
    )


@pytest.mark.parametrize(
    ("field", "left", "right"),
    [
        ("ANTHROPIC_AUTH_TOKEN", TOKEN, "sk-ant-oat01-a-different-token"),
        ("ANTHROPIC_API_KEY", "key-a", "key-b"),
        ("ANTHROPIC_CUSTOM_HEADERS", "X-Team: a", "X-Team: b"),
        ("ANTHROPIC_BASE_URL", "https://gw.example.com", "http://gw.example.com"),
        (
            "ANTHROPIC_BASE_URL",
            "https://gw.example.com/v1/team-a",
            "https://gw.example.com/v1/team-b",
        ),
    ],
)
def test_differing_credential_material_yields_a_different_key(
    tmp_path: Path, field: str, left: str, right: str
) -> None:
    # Scheme and path matter as much as the token: a path-routed gateway is a
    # normal shape, and collapsing two of its accounts onto one key would make the
    # no-op guard refuse a legitimate switch.
    base = {
        "ANTHROPIC_BASE_URL": "https://gw.example.com",
        "ANTHROPIC_AUTH_TOKEN": TOKEN,
    }
    assert _key(_pool_dir(tmp_path, "a", **{**base, field: left})) != _key(
        _pool_dir(tmp_path, "b", **{**base, field: right})
    )


# ── identity: negative cases (must not re-identify a working OAuth profile) ──


def test_no_identity_without_a_declared_bearer_secret(tmp_path: Path) -> None:
    oauth_dir = tmp_path / "oauth"
    oauth_dir.mkdir()
    assert configured_account_identity(str(oauth_dir), {}) is None

    # A gateway can proxy an OAuth login, so an endpoint alone says nothing about
    # which account authenticates.
    assert (
        configured_account_identity(
            _pool_dir(
                tmp_path, "url-only", ANTHROPIC_BASE_URL="https://gw.example.com"
            ),
            {},
        )
        is None
    )

    # The CLI gates an env api key on interactivity, not on the endpoint: the
    # headless transport uses it, the TUI needs it pre-approved in .claude.json.
    # A transport-blind identity can't be right for both, so it never triggers.
    assert (
        configured_account_identity(_pool_dir(tmp_path, "k", ANTHROPIC_API_KEY="k"), {})
        is None
    )
    assert (
        configured_account_identity(
            _pool_dir(
                tmp_path,
                "k-url",
                ANTHROPIC_BASE_URL="https://gw.example.com",
                ANTHROPIC_API_KEY="k",
            ),
            {},
        )
        is None
    )

    # Header-only gateway auth, same reasoning as the bare endpoint.
    assert (
        configured_account_identity(
            _pool_dir(
                tmp_path,
                "hdr",
                ANTHROPIC_BASE_URL="https://gw.example.com",
                ANTHROPIC_CUSTOM_HEADERS="X-Auth: v",
            ),
            {},
        )
        is None
    )


def test_no_identity_from_a_secret_outside_the_profiles_settings(
    tmp_path: Path,
) -> None:
    # A token in the launch env comes from the global plugin env for *every*
    # profile, so it cannot distinguish accounts; honouring it would collapse a
    # working multi-profile setup onto one key and refuse every switch.
    config_dir = tmp_path / "oauth"
    config_dir.mkdir()
    assert (
        configured_account_identity(str(config_dir), {"ANTHROPIC_AUTH_TOKEN": TOKEN})
        is None
    )


def test_no_identity_from_an_empty_secret(tmp_path: Path) -> None:
    assert (
        configured_account_identity(
            _pool_dir(tmp_path, "blank", ANTHROPIC_AUTH_TOKEN="   "), {}
        )
        is None
    )


# ── probe_account: two-tier resolution ───────────────────────────────────────


def _runtime(tmp_path: Path) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    return SessionRuntime(settings, Storage(settings.database_path))


def _record_probe(
    monkeypatch: pytest.MonkeyPatch, runtime: SessionRuntime, calls: list[str]
) -> None:
    plugin = runtime.registry.get("claude_code")

    async def fake_probe(*_a: Any, **_k: Any) -> SessionRateLimitUsage:
        calls.append("probed")
        return SessionRateLimitUsage(
            source="claude_code",
            updated_at=datetime.now(UTC),
            windows=[],
            notes=["org: acme"],
        )

    monkeypatch.setattr(plugin, "probe_account_rate_limit", fake_probe)
    monkeypatch.setattr(
        plugin, "rate_limit_account", lambda _s: ("claude_code:acme", "acme")
    )


async def test_probe_account_prefers_the_configured_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    calls: list[str] = []
    _record_probe(monkeypatch, runtime, calls)
    config_dir = _pool_dir(
        tmp_path,
        ANTHROPIC_BASE_URL="https://gw.example.com",
        ANTHROPIC_AUTH_TOKEN=TOKEN,
    )

    result = await probe_account(
        runtime, "claude_code", {"CLAUDE_CONFIG_DIR": config_dir}
    )

    assert isinstance(result, AccountProbeResult)
    assert result.account_key.startswith("claude_code:endpoint:gw.example.com:")
    # A declared credential is what the agent authenticates as, so the live probe
    # is not just redundant here — for a dir holding stale OAuth credentials too,
    # it would name the account the agent will *not* use.
    assert calls == []


async def test_probe_account_falls_back_to_the_live_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    calls: list[str] = []
    _record_probe(monkeypatch, runtime, calls)
    oauth_dir = tmp_path / "oauth"
    oauth_dir.mkdir()

    result = await probe_account(
        runtime, "claude_code", {"CLAUDE_CONFIG_DIR": str(oauth_dir)}
    )

    assert result is not None
    assert result.account_key == "claude_code:acme"
    assert calls == ["probed"]


async def test_probe_account_ignores_local_settings_for_a_remote_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The config dir named by a remote profile lives on the remote host; reading a
    # same-path local dir would mint a false identity for it.
    runtime = _runtime(tmp_path)
    calls: list[str] = []
    _record_probe(monkeypatch, runtime, calls)
    config_dir = _pool_dir(tmp_path, ANTHROPIC_AUTH_TOKEN=TOKEN)
    target = SshLaunchTargetConfig(
        id="rover",
        name="rover",
        ssh_destination="user@rover.lan",
        ssh_args=[],
        remote_shell="",
    )

    result = await probe_account(
        runtime,
        "claude_code",
        {"CLAUDE_CONFIG_DIR": config_dir},
        launch_target=target,
    )

    assert result is not None
    assert result.account_key == "claude_code:acme"
    assert calls == ["probed"]
