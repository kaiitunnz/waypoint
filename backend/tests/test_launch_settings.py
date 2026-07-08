"""Live launch-settings switch: probe composition, GET projection, gates.

Phase 4. Covers the deterministic pre-termination logic of
``update_launch_settings`` (the terminate→restore itself needs a live backend
and is exercised end-to-end in the app). ``probe_account`` composition and the
GET projection are covered directly.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from waypoint.backends.account_profiles import probe_account
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    AccountProbeResult,
    LaunchSettingsUpdateRequest,
    SessionRateLimitUsage,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage


def _codex_profiles() -> dict[str, object]:
    return {
        "account_profiles": {
            "work": {
                "label": "Work",
                "config_dir": "~/.codex-work",
                "transcript_policy": "require_existing",
                "expected_account_key": "codex:work@co",
            }
        }
    }


def _runtime(tmp_path: Path, **plugin_configs: object) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data", plugin_configs=plugin_configs)
    return SessionRuntime(settings, Storage(settings.database_path))


def _session(runtime: SessionRuntime, **kw: Any) -> SessionRecord:
    now = datetime.now(UTC)
    base: dict[str, Any] = dict(
        id="s1",
        backend="codex",
        # Structured transport: phase-1 restart-applied settings live on the
        # agent/native transports; tmux-wrapped switching needs the agent×
        # transport caps composition and is out of phase-4 scope.
        transport="codex_app_server",
        source=SessionSource.MANAGED,
        title="t",
        cwd="/repo/app",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/r",
        structured_log_path="/e",
        transport_state={"thread_id": "11111111-1111-1111-1111-111111111111"},
    )
    base.update(kw)
    record = SessionRecord(**base)
    runtime.storage.create_session(record)
    return record


# ── probe_account composition ───────────────────────────────────────────────


async def test_probe_account_composes_probe_and_account_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    plugin = runtime.registry.get("codex")

    async def fake_probe(*_a: Any, **_k: Any) -> SessionRateLimitUsage:
        return SessionRateLimitUsage(
            source="codex", updated_at=datetime.now(UTC), windows=[]
        )

    monkeypatch.setattr(plugin, "probe_account_rate_limit", fake_probe)
    monkeypatch.setattr(
        plugin, "rate_limit_account", lambda _s: ("codex:work@co", "work@co")
    )
    result = await probe_account(runtime, "codex", {"CODEX_HOME": "/x"})
    assert isinstance(result, AccountProbeResult)
    assert result.account_key == "codex:work@co"
    assert result.account_label == "work@co"


async def test_probe_account_none_when_no_account_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    plugin = runtime.registry.get("codex")

    async def fake_probe(*_a: Any, **_k: Any) -> SessionRateLimitUsage:
        return SessionRateLimitUsage(
            source="codex", updated_at=datetime.now(UTC), windows=[]
        )

    monkeypatch.setattr(plugin, "probe_account_rate_limit", fake_probe)
    monkeypatch.setattr(plugin, "rate_limit_account", lambda _s: None)
    assert await probe_account(runtime, "codex", {}) is None


# ── GET projection ───────────────────────────────────────────────────────────


def test_get_launch_settings_projection(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    _session(
        runtime,
        args=["--foo"],
        config_overrides=['model_reasoning_effort="high"'],
        launch_env={"CODEX_HOME": "/x", "SECRET": "s"},
        account_profile_id="work",
        account_profile_label="Work",
    )
    resp = runtime.get_launch_settings("s1")
    assert resp.backend == "codex"
    assert resp.account_profile_id == "work"
    assert {p.id for p in resp.account_profiles} == {"work"}
    assert resp.args == ["--foo"]
    assert resp.config_overrides == ['model_reasoning_effort="high"']
    # Redacted: keys only, never values.
    assert resp.launch_env_keys == ["CODEX_HOME", "SECRET"]
    assert resp.supports_account_profile_with_restart is True


# ── pre-termination gates ────────────────────────────────────────────────────


async def test_update_rejects_during_starting(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    _session(runtime, status=SessionStatus.STARTING)
    with pytest.raises(Exception) as exc:
        await runtime.update_launch_settings(
            "s1", LaunchSettingsUpdateRequest(account_profile_id="work", restart=True)
        )
    assert getattr(exc.value, "status_code", None) == 409


async def test_update_requires_restart_true(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    _session(runtime)
    with pytest.raises(Exception) as exc:
        await runtime.update_launch_settings(
            "s1", LaunchSettingsUpdateRequest(account_profile_id="work", restart=False)
        )
    assert getattr(exc.value, "status_code", None) == 400


async def test_update_rejects_concurrent_operation(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    _session(runtime)
    lock = runtime._session_lock("s1")
    await lock.acquire()
    try:
        with pytest.raises(Exception) as exc:
            await runtime.update_launch_settings(
                "s1",
                LaunchSettingsUpdateRequest(account_profile_id="work", restart=True),
            )
        assert getattr(exc.value, "status_code", None) == 409
    finally:
        lock.release()


async def test_update_rejects_unknown_profile(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    _session(runtime)
    with pytest.raises(Exception) as exc:
        await runtime.update_launch_settings(
            "s1", LaunchSettingsUpdateRequest(account_profile_id="nope", restart=True)
        )
    assert getattr(exc.value, "status_code", None) == 400


async def test_update_rejects_noop_account_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A profile with no expected_account_key that resolves to the current
    # account is a no-op (e.g. macOS Keychain) — refuse rather than falsely
    # report a switch.
    profiles = {
        "account_profiles": {
            "personal": {
                "label": "Personal",
                "config_dir": "~/.codex-personal",
                "transcript_policy": "require_existing",
            }
        }
    }
    runtime = _runtime(tmp_path, codex=profiles)
    _session(runtime)
    monkeypatch.setattr(
        "waypoint.runtime.ensure_thread_available", lambda *a, **k: None
    )

    async def same_account(*_a: Any, **_k: Any) -> AccountProbeResult:
        return AccountProbeResult(account_key="codex:same", account_label="same")

    monkeypatch.setattr("waypoint.runtime.probe_account", same_account)
    with pytest.raises(Exception) as exc:
        await runtime.update_launch_settings(
            "s1",
            LaunchSettingsUpdateRequest(account_profile_id="personal", restart=True),
        )
    assert getattr(exc.value, "status_code", None) == 400
    assert "same account" in str(getattr(exc.value, "detail", ""))


async def test_update_rejects_config_overrides_when_unsupported(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    # claude_code advertises supports_config_overrides=False.
    _session(runtime, backend="claude_code", transport="claude_cli")
    with pytest.raises(Exception) as exc:
        await runtime.update_launch_settings(
            "s1", LaunchSettingsUpdateRequest(config_overrides=["x=1"], restart=True)
        )
    assert getattr(exc.value, "status_code", None) == 400
    assert "config overrides" in str(getattr(exc.value, "detail", ""))


async def test_update_rejects_expected_account_key_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    _session(runtime)

    # Isolate the expected-key gate: skip the transcript step and return a
    # probe whose account_key differs from the profile's expected key.
    monkeypatch.setattr(
        "waypoint.runtime.ensure_thread_available", lambda *a, **k: None
    )

    async def fake_probe(*_a: Any, **_k: Any) -> AccountProbeResult:
        return AccountProbeResult(account_key="codex:wrong@co", account_label="wrong")

    monkeypatch.setattr("waypoint.runtime.probe_account", fake_probe)
    with pytest.raises(Exception) as exc:
        await runtime.update_launch_settings(
            "s1", LaunchSettingsUpdateRequest(account_profile_id="work", restart=True)
        )
    assert getattr(exc.value, "status_code", None) == 400
    assert "expected" in str(getattr(exc.value, "detail", ""))
