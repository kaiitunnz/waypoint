"""Live launch-settings switch: probe composition, GET projection, gates.

Phase 4. Covers the deterministic pre-termination logic of
``update_launch_settings`` (the terminate→restore itself needs a live backend
and is exercised end-to-end in the app). ``probe_account`` composition and the
GET projection are covered directly.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from waypoint.backends.account_profiles import probe_account
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    AccountProbeResult,
    EventKind,
    EventRecord,
    LaunchSettingsUpdateRequest,
    SessionRateLimitUsage,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage

TID = "11111111-1111-1111-1111-111111111111"


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
    assert resp.supports_launch_settings_with_restart is True
    assert resp.config_dir_env_var == "CODEX_HOME"
    # WAYPOINT_SESSION_ID is always runtime-owned; the config-dir key is
    # profile-owned while a profile is selected. Neither value ever leaks.
    assert "WAYPOINT_SESSION_ID" in resp.protected_launch_env_keys
    assert "CODEX_HOME" in resp.protected_launch_env_keys
    assert "SECRET" not in resp.protected_launch_env_keys


def test_get_launch_settings_omits_config_dir_key_when_no_profile(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    _session(runtime, launch_env={"CODEX_HOME": "/x"})
    resp = runtime.get_launch_settings("s1")
    # No profile selected → config-dir key is not profile-owned; only the
    # runtime-owned key is protected.
    assert resp.protected_launch_env_keys == ["WAYPOINT_SESSION_ID"]
    assert resp.config_dir_env_var == "CODEX_HOME"


def test_get_launch_settings_attached_tmux_disables_restart(tmp_path: Path) -> None:
    # A bare attached pane advertises the restart capability at the transport
    # level, but Waypoint does not own the process; the projection must be
    # honest so the client gates the editor correctly.
    runtime = _runtime(tmp_path)
    _session(
        runtime,
        backend="tmux",
        transport="tmux",
        source=SessionSource.ATTACHED_TMUX,
    )
    resp = runtime.get_launch_settings("s1")
    assert resp.supports_launch_settings_with_restart is False


def test_get_launch_settings_opencode_disables_restart(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _session(runtime, backend="opencode", transport="opencode_http")
    resp = runtime.get_launch_settings("s1")
    assert resp.supports_launch_settings_with_restart is False


async def test_update_rejects_env_edit_for_attached_tmux_pane(tmp_path: Path) -> None:
    # The gap the capability gate alone misses: an env-only edit on an attached
    # pane passes every capability gate (tmux transport flips restart on) but
    # Waypoint does not own the process. The server must reject it regardless of
    # what a client hides.
    runtime = _runtime(tmp_path)
    _session(
        runtime,
        backend="tmux",
        transport="tmux",
        source=SessionSource.ATTACHED_TMUX,
    )
    with pytest.raises(Exception) as exc:
        await runtime.update_launch_settings(
            "s1",
            LaunchSettingsUpdateRequest(env_set={"HTTP_PROXY": "x"}, restart=True),
        )
    assert getattr(exc.value, "status_code", None) == 400


def test_update_session_round_trips_list_columns(tmp_path: Path) -> None:
    # The switch persists args/config_overrides (lists) via update_session; the
    # generic serializer must JSON-encode lists (JSON TEXT columns), not just
    # dicts, or sqlite rejects the bind.
    runtime = _runtime(tmp_path)
    _session(runtime)
    updated = runtime.storage.update_session(
        "s1", args=["--a", "--b"], config_overrides=['x="1"']
    )
    assert updated.args == ["--a", "--b"]
    assert updated.config_overrides == ['x="1"']
    reloaded = runtime.storage.get_session("s1")
    assert reloaded is not None
    assert reloaded.args == ["--a", "--b"]
    assert reloaded.config_overrides == ['x="1"']


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


async def test_switch_marks_exited_before_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: a pane-wrapping transport (claude_tty) only relaunches on an
    # EXITED reattach, so the switch must persist EXITED before restore — else
    # restore keeps the dead pane and the next input fails "can't find pane".
    # An env-only edit reaches terminate→persist→restore without the
    # account-probe branch.
    runtime = _runtime(tmp_path)
    _session(runtime, status=SessionStatus.IDLE)
    plugin = runtime.registry.plugin_for(runtime.get_session("s1"))
    seen: dict[str, Any] = {}

    async def fake_terminate(*_a: Any, **_k: Any) -> None:
        return None

    async def fake_restore(_self_rt: Any, session: SessionRecord) -> None:
        # Capture the status the transport is asked to restore from, then
        # simulate a healthy relaunch so the post-restore gate passes.
        seen["restore_status"] = session.status
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)

    monkeypatch.setattr(plugin, "terminate_session", fake_terminate)
    monkeypatch.setattr(plugin, "restore_session", fake_restore)

    await runtime.update_launch_settings(
        "s1", LaunchSettingsUpdateRequest(env_set={"FOO": "bar"}, restart=True)
    )
    assert seen["restore_status"] == SessionStatus.EXITED


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


# ── tmux-wrapped account-profile switching (composed caps) ──────────────────
#
# These exercise the full real stack — composed capabilities gate the switch
# (S1/S3), TmuxPlugin delegates the transcript-artifact lookup to the wrapped
# agent (S2), and TmuxTransport.flush_before_restart runs after the interrupt
# (S4) — with only terminate_session/restore_session/probe_account mocked
# (as the native-transport tests above do), so a real terminate/restore cycle
# never actually needs to spawn tmux.


def _write_claude_thread(config_dir: Path, project: str = "-repo-app") -> Path:
    proj = config_dir / "projects" / project
    proj.mkdir(parents=True)
    path = proj / f"{TID}.jsonl"
    path.write_text("{}")
    return path


def _write_claude_onboarding_complete(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / ".claude.json").write_text(
        json.dumps({"hasCompletedOnboarding": True})
    )


def _write_codex_auth(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "auth.json").write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "account_id": "account-123",
                },
                "last_refresh": "2026-05-01T12:34:56Z",
            }
        )
    )


async def test_update_switches_account_profile_for_tmux_wrapped_claude_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_dir = tmp_path / "claude-target"
    _write_claude_thread(target_dir)
    _write_claude_onboarding_complete(target_dir)
    profiles = {
        "account_profiles": {
            "work": {
                "label": "Work",
                "config_dir": str(target_dir),
                "transcript_policy": "require_existing",
                "expected_account_key": "claude:work@co",
            }
        }
    }
    runtime = _runtime(tmp_path, claude_code=profiles)
    _session(
        runtime,
        backend="claude_code",
        transport="tmux",
        cwd="/repo/app",
        launch_env={"CLAUDE_CONFIG_DIR": str(tmp_path / "claude-current")},
    )
    # A (claude_code, tmux) pair drives through the tmux transport-owning
    # plugin (registry.resolve keys the pair to the transport owner), not the
    # claude_code plugin directly.
    plugin = runtime.registry.plugin_for(runtime.get_session("s1"))
    assert plugin.id == "tmux"
    seen: dict[str, Any] = {}

    async def fake_terminate(*_a: Any, **_k: Any) -> None:
        seen["terminated"] = True

    async def fake_restore(_self_rt: Any, session: SessionRecord) -> None:
        seen["restore_status"] = session.status
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)

    monkeypatch.setattr(plugin, "terminate_session", fake_terminate)
    monkeypatch.setattr(plugin, "restore_session", fake_restore)

    async def fake_probe(*_a: Any, **_k: Any) -> AccountProbeResult:
        return AccountProbeResult(account_key="claude:work@co", account_label="work@co")

    monkeypatch.setattr("waypoint.runtime.probe_account", fake_probe)

    updated = await runtime.update_launch_settings(
        "s1", LaunchSettingsUpdateRequest(account_profile_id="work", restart=True)
    )

    assert seen["terminated"] is True
    # Marked EXITED before restore — same regression coverage as the native
    # pane-wrapping (claude_tty) case, now proven for the tmux pair too.
    assert seen["restore_status"] == SessionStatus.EXITED
    assert updated.account_profile_id == "work"
    assert updated.launch_env["CLAUDE_CONFIG_DIR"] == str(target_dir)


async def test_update_switches_account_profile_for_tmux_wrapped_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_dir = tmp_path / "codex-current"
    target_dir = tmp_path / "codex-target"
    day = current_dir / "sessions" / "2026" / "07" / "08"
    day.mkdir(parents=True)
    (day / f"rollout-2026-07-08T00-00-00-{TID}.jsonl").write_text("{}")
    _write_codex_auth(target_dir)
    profiles = {
        "account_profiles": {
            "work": {
                "label": "Work",
                "config_dir": str(target_dir),
                # copy_thread_on_switch, unlike the claude case above, so this
                # also covers the wrapper's delegated copy path.
                "transcript_policy": "copy_thread_on_switch",
                "expected_account_key": "codex:work@co",
            }
        }
    }
    runtime = _runtime(tmp_path, codex=profiles)
    _session(
        runtime,
        backend="codex",
        transport="tmux",
        cwd="/repo/app",
        launch_env={"CODEX_HOME": str(current_dir)},
    )
    plugin = runtime.registry.plugin_for(runtime.get_session("s1"))
    assert plugin.id == "tmux"

    async def fake_terminate(*_a: Any, **_k: Any) -> None:
        return None

    async def fake_restore(_self_rt: Any, session: SessionRecord) -> None:
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)

    monkeypatch.setattr(plugin, "terminate_session", fake_terminate)
    monkeypatch.setattr(plugin, "restore_session", fake_restore)

    async def fake_probe(*_a: Any, **_k: Any) -> AccountProbeResult:
        return AccountProbeResult(account_key="codex:work@co", account_label="work@co")

    monkeypatch.setattr("waypoint.runtime.probe_account", fake_probe)

    updated = await runtime.update_launch_settings(
        "s1", LaunchSettingsUpdateRequest(account_profile_id="work", restart=True)
    )

    assert updated.launch_env["CODEX_HOME"] == str(target_dir)
    # The wrapper delegated the copy to codex's real artifact locator: the
    # rollout is now visible under the target dir too.
    copied = (
        target_dir
        / day.relative_to(current_dir)
        / f"rollout-2026-07-08T00-00-00-{TID}.jsonl"
    )
    assert copied.is_file()


async def test_update_rejects_tmux_switch_for_a_pure_attached_pane(
    tmp_path: Path,
) -> None:
    """A pure attached-tmux session (no agent axis, backend == 'tmux') has no
    config-dir env var on its agent axis, so it stays refused after the
    tmux transport's own restart-with-resume flag was flipped on — a
    regression guard for the composed-caps gate."""
    runtime = _runtime(tmp_path)
    _session(runtime, backend="tmux", transport="tmux")
    with pytest.raises(Exception) as exc:
        await runtime.update_launch_settings(
            "s1",
            LaunchSettingsUpdateRequest(account_profile_id="work", restart=True),
        )
    assert getattr(exc.value, "status_code", None) == 400


async def test_update_aborts_tmux_switch_before_terminate_when_transcript_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-before-destroy: when the target profile can't see the native
    thread (require_existing, nothing written under the target dir), the
    switch must raise before terminate_session is ever called — the real
    ensure_thread_available runs unmocked here."""
    target_dir = tmp_path / "claude-target-empty"
    _write_claude_onboarding_complete(target_dir)
    profiles = {
        "account_profiles": {
            "work": {
                "label": "Work",
                "config_dir": str(target_dir),
                "transcript_policy": "require_existing",
                "expected_account_key": "claude:work@co",
            }
        }
    }
    runtime = _runtime(tmp_path, claude_code=profiles)
    _session(
        runtime,
        backend="claude_code",
        transport="tmux",
        cwd="/repo/app",
        launch_env={"CLAUDE_CONFIG_DIR": str(tmp_path / "claude-current")},
    )
    plugin = runtime.registry.plugin_for(runtime.get_session("s1"))
    terminate_calls: list[str] = []

    async def spy_terminate(*_a: Any, **_k: Any) -> None:
        terminate_calls.append("terminated")

    monkeypatch.setattr(plugin, "terminate_session", spy_terminate)

    async def fake_probe(*_a: Any, **_k: Any) -> AccountProbeResult:
        return AccountProbeResult(account_key="claude:work@co", account_label="work@co")

    monkeypatch.setattr("waypoint.runtime.probe_account", fake_probe)

    with pytest.raises(Exception) as exc:
        await runtime.update_launch_settings(
            "s1", LaunchSettingsUpdateRequest(account_profile_id="work", restart=True)
        )

    assert getattr(exc.value, "status_code", None) == 400
    assert "cannot switch account profile" in str(getattr(exc.value, "detail", ""))
    assert terminate_calls == []
    assert runtime.get_session("s1").status != SessionStatus.EXITED


async def test_update_starts_fresh_codex_thread_when_transcript_is_unpersisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_dir = tmp_path / "codex-current"
    target_dir = tmp_path / "codex-target"
    profiles = {
        "account_profiles": {
            "work": {
                "label": "Work",
                "config_dir": str(target_dir),
                "transcript_policy": "symlink_shared",
                "shared_transcript_dir": str(tmp_path / "shared"),
                "expected_account_key": "codex:work@co",
            }
        }
    }
    runtime = _runtime(tmp_path, codex=profiles)
    pinned_at = datetime.now(UTC)
    _session(
        runtime,
        source=SessionSource.ASSISTANT,
        title="Personal Assistant",
        pinned_at=pinned_at,
        launch_env={"CODEX_HOME": str(current_dir)},
    )
    plugin = runtime.registry.plugin_for(runtime.get_session("s1"))
    seen: dict[str, Any] = {}

    async def fake_terminate(*_a: Any, **_k: Any) -> None:
        seen["terminated"] = True

    async def fake_fresh_start(_runtime: Any, session: SessionRecord) -> None:
        seen["fresh_status"] = session.status
        seen["fresh_home"] = session.launch_env["CODEX_HOME"]
        runtime.storage.update_session(
            session.id,
            transport_state={**session.transport_state, "thread_id": "new-thread"},
            status=SessionStatus.IDLE,
        )

    async def fake_probe(*_a: Any, **_k: Any) -> AccountProbeResult:
        return AccountProbeResult(account_key="codex:work@co", account_label="work@co")

    monkeypatch.setattr(plugin, "terminate_session", fake_terminate)
    monkeypatch.setattr(plugin, "restart_unpersisted_session", fake_fresh_start)
    monkeypatch.setattr("waypoint.runtime.probe_account", fake_probe)

    updated = await runtime.update_launch_settings(
        "s1", LaunchSettingsUpdateRequest(account_profile_id="work", restart=True)
    )

    assert seen == {
        "terminated": True,
        "fresh_status": SessionStatus.EXITED,
        "fresh_home": str(target_dir),
    }
    assert updated.id == "s1"
    assert updated.transport_state["thread_id"] == "new-thread"
    assert updated.account_profile_id == "work"
    assert updated.source == SessionSource.ASSISTANT
    assert updated.title == "Personal Assistant"
    assert updated.pinned_at == pinned_at
    assert (target_dir / "sessions").is_symlink()


async def test_update_uses_codex_default_source_home_when_env_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_dir = tmp_path / "codex-target"
    profiles = {
        "account_profiles": {
            "work": {
                "label": "Work",
                "config_dir": str(target_dir),
                "transcript_policy": "symlink_shared",
                "shared_transcript_dir": str(tmp_path / "shared"),
                "expected_account_key": "codex:work@co",
            }
        }
    }
    runtime = _runtime(tmp_path, codex=profiles)
    _session(runtime)
    plugin = runtime.registry.plugin_for(runtime.get_session("s1"))
    seen: dict[str, str] = {}

    async def fake_terminate(*_a: Any, **_k: Any) -> None:
        return None

    async def fake_fresh_start(_runtime: Any, session: SessionRecord) -> None:
        seen["home"] = session.launch_env["CODEX_HOME"]
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)

    async def fake_probe(*_a: Any, **_k: Any) -> AccountProbeResult:
        return AccountProbeResult(account_key="codex:work@co", account_label="work@co")

    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(plugin, "terminate_session", fake_terminate)
    monkeypatch.setattr(plugin, "restart_unpersisted_session", fake_fresh_start)
    monkeypatch.setattr("waypoint.runtime.probe_account", fake_probe)

    updated = await runtime.update_launch_settings(
        "s1", LaunchSettingsUpdateRequest(account_profile_id="work", restart=True)
    )

    assert seen["home"] == str(target_dir)
    assert updated.account_profile_id == "work"


async def test_update_rejects_unpersisted_hookless_switch_before_terminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_dir = tmp_path / "codex-current"
    target_dir = tmp_path / "codex-target"
    profiles = {
        "account_profiles": {
            "work": {
                "label": "Work",
                "config_dir": str(target_dir),
                "transcript_policy": "symlink_shared",
                "shared_transcript_dir": str(tmp_path / "shared"),
                "expected_account_key": "codex:work@co",
            }
        }
    }
    runtime = _runtime(tmp_path, codex=profiles)
    _session(
        runtime,
        transport="tmux",
        launch_env={"CODEX_HOME": str(current_dir)},
    )
    plugin = runtime.registry.plugin_for(runtime.get_session("s1"))
    terminated: list[bool] = []

    async def fake_terminate(*_a: Any, **_k: Any) -> None:
        terminated.append(True)

    async def fake_probe(*_a: Any, **_k: Any) -> AccountProbeResult:
        return AccountProbeResult(account_key="codex:work@co", account_label="work@co")

    monkeypatch.setattr(plugin, "terminate_session", fake_terminate)
    monkeypatch.setattr("waypoint.runtime.probe_account", fake_probe)

    with pytest.raises(Exception) as exc:
        await runtime.update_launch_settings(
            "s1", LaunchSettingsUpdateRequest(account_profile_id="work", restart=True)
        )

    assert getattr(exc.value, "status_code", None) == 400
    assert terminated == []
    unchanged = runtime.get_session("s1")
    assert unchanged.status == SessionStatus.IDLE
    assert unchanged.launch_env["CODEX_HOME"] == str(current_dir)


async def test_update_rejects_unpersisted_thread_with_conversation_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_dir = tmp_path / "codex-current"
    target_dir = tmp_path / "codex-target"
    profiles = {
        "account_profiles": {
            "work": {
                "label": "Work",
                "config_dir": str(target_dir),
                "transcript_policy": "copy_thread_on_switch",
                "expected_account_key": "codex:work@co",
            }
        }
    }
    runtime = _runtime(tmp_path, codex=profiles)
    _session(runtime, launch_env={"CODEX_HOME": str(current_dir)})
    runtime.storage.append_event(
        EventRecord(
            session_id="s1",
            ts=datetime.now(UTC),
            kind=EventKind.USER_INPUT,
            text="hello",
            metadata={"submit": True},
            sequence=runtime.storage.next_sequence("s1"),
        )
    )
    plugin = runtime.registry.plugin_for(runtime.get_session("s1"))
    terminated: list[bool] = []

    async def fake_terminate(*_a: Any, **_k: Any) -> None:
        terminated.append(True)

    async def fake_probe(*_a: Any, **_k: Any) -> AccountProbeResult:
        return AccountProbeResult(account_key="codex:work@co", account_label="work@co")

    monkeypatch.setattr(plugin, "terminate_session", fake_terminate)
    monkeypatch.setattr("waypoint.runtime.probe_account", fake_probe)

    with pytest.raises(Exception) as exc:
        await runtime.update_launch_settings(
            "s1", LaunchSettingsUpdateRequest(account_profile_id="work", restart=True)
        )

    assert getattr(exc.value, "status_code", None) == 400
    assert "conversation events" in str(getattr(exc.value, "detail", ""))
    assert terminated == []
