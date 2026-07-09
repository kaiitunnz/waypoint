"""Server-owned ``verified_account_*`` provenance on ``SessionRecord``.

The switch path already probes and verifies the account a profile
authenticates as, but the result was discarded. These tests cover
persistence at each population point (launch, thread-import, switch,
reattach, boot-restore), redaction, and that clients can never set the
fields themselves.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from waypoint.backends.codex.schemas import CodexThreadImportRequest
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    AccountProbeResult,
    LaunchSettingsUpdateRequest,
    ScheduleCreateRequest,
    SessionCreateRequest,
    SessionPresetSpec,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage


def _codex_profiles(**extra: Any) -> dict[str, object]:
    return {
        "account_profiles": {
            "work": {
                "label": "Work",
                "config_dir": "~/.codex-work",
                "transcript_policy": "require_existing",
                **extra,
            }
        }
    }


def _runtime(tmp_path: Path, **plugin_configs: object) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data", plugin_configs=plugin_configs)
    settings.ensure_dirs()
    return SessionRuntime(settings, Storage(settings.database_path))


def _session(runtime: SessionRuntime, **kw: Any) -> SessionRecord:
    # verified_account_* are never written by create_session's INSERT (they're
    # only known post-probe, stamped via a follow-up update_session — same as
    # account_profile_id historically); seed them the same way here.
    verified_kwargs = {
        key: kw.pop(key)
        for key in (
            "verified_account_key",
            "verified_account_label",
            "verified_account_probed_at",
        )
        if key in kw
    }
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
    if verified_kwargs:
        record = runtime.storage.update_session(record.id, **verified_kwargs)
    return record


def _fake_probe(calls: list[Any], result: AccountProbeResult | None) -> Any:
    async def fake(*_a: Any, **_k: Any) -> AccountProbeResult | None:
        calls.append(1)
        return result

    return fake


# ── Server-set-only ─────────────────────────────────────────────────────────


def test_create_request_drops_verified_fields() -> None:
    req = SessionCreateRequest(
        backend="codex",
        cwd="/tmp",
        **{
            "verified_account_key": "leaked",
            "verified_account_label": "leaked",
            "verified_account_probed_at": datetime.now(UTC),
        },
    )
    assert "verified_account_key" not in req.model_dump()
    assert not hasattr(req, "verified_account_key")


def test_schedule_request_drops_verified_fields() -> None:
    req = ScheduleCreateRequest(
        backend="codex",
        cwd="/tmp",
        **{"verified_account_key": "leaked"},
    )
    assert "verified_account_key" not in req.model_dump()


def test_preset_spec_drops_verified_fields() -> None:
    spec = SessionPresetSpec(**{"verified_account_key": "leaked"})
    assert "verified_account_key" not in spec.model_dump()


def test_launch_settings_patch_drops_verified_fields() -> None:
    req = LaunchSettingsUpdateRequest(
        restart=True, **{"verified_account_key": "leaked"}
    )
    assert "verified_account_key" not in req.model_dump()


def test_thread_import_request_drops_verified_fields() -> None:
    req = CodexThreadImportRequest(thread_id="t", **{"verified_account_key": "leaked"})
    assert "verified_account_key" not in req.model_dump()


# ── Redaction ────────────────────────────────────────────────────────────────


def test_verified_key_and_label_excluded_from_public_dump(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    session = _session(
        runtime,
        verified_account_key="codex:work@co",
        verified_account_label="work@co",
        verified_account_probed_at=datetime.now(UTC),
    )
    dumped = session.model_dump(mode="json")
    assert "verified_account_key" not in dumped
    assert "verified_account_label" not in dumped
    assert dumped["verified_account_probed_at"] is not None


# ── Storage round-trip ──────────────────────────────────────────────────────


def test_storage_round_trips_verified_fields(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    probed_at = datetime.now(UTC)
    _session(
        runtime,
        verified_account_key="codex:work@co",
        verified_account_label="work@co",
        verified_account_probed_at=probed_at,
    )
    got = runtime.storage.get_session("s1")
    assert got is not None
    assert got.verified_account_key == "codex:work@co"
    assert got.verified_account_label == "work@co"
    assert got.verified_account_probed_at == probed_at


def test_storage_defaults_verified_fields_to_none(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _session(runtime)
    got = runtime.storage.get_session("s1")
    assert got is not None
    assert got.verified_account_key is None
    assert got.verified_account_label is None
    assert got.verified_account_probed_at is None


# ── Switch: the only synchronous stamp ──────────────────────────────────────


async def test_switch_stamps_from_existing_probe_with_no_second_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(
        tmp_path, codex=_codex_profiles(expected_account_key="codex:work@co")
    )
    _session(runtime)
    monkeypatch.setattr(
        "waypoint.runtime.ensure_thread_available", lambda *a, **k: None
    )
    calls: list[Any] = []
    monkeypatch.setattr(
        "waypoint.runtime.probe_account",
        _fake_probe(
            calls,
            AccountProbeResult(account_key="codex:work@co", account_label="Work Co"),
        ),
    )
    plugin = runtime.registry.plugin_for(runtime.get_session("s1"))

    async def fake_terminate(*_a: Any, **_k: Any) -> None:
        return None

    async def fake_restore(_rt: Any, session: SessionRecord) -> None:
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)

    monkeypatch.setattr(plugin, "terminate_session", fake_terminate)
    monkeypatch.setattr(plugin, "restore_session", fake_restore)

    refreshed = await runtime.update_launch_settings(
        "s1", LaunchSettingsUpdateRequest(account_profile_id="work", restart=True)
    )
    # Synchronous: asserted immediately, no drain needed.
    assert refreshed.verified_account_key == "codex:work@co"
    assert refreshed.verified_account_label == "Work Co"
    assert refreshed.verified_account_probed_at is not None
    # Only the single gate probe ran — no second probe for the stamp.
    assert len(calls) == 1


async def test_switch_clearing_profile_nulls_verified_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    _session(
        runtime,
        account_profile_id="work",
        account_profile_label="Work",
        verified_account_key="codex:work@co",
        verified_account_label="work@co",
        verified_account_probed_at=datetime.now(UTC),
    )
    plugin = runtime.registry.plugin_for(runtime.get_session("s1"))

    async def fake_terminate(*_a: Any, **_k: Any) -> None:
        return None

    async def fake_restore(_rt: Any, session: SessionRecord) -> None:
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)

    monkeypatch.setattr(plugin, "terminate_session", fake_terminate)
    monkeypatch.setattr(plugin, "restore_session", fake_restore)

    refreshed = await runtime.update_launch_settings(
        "s1",
        LaunchSettingsUpdateRequest(account_profile_id=None, restart=True),
    )
    assert refreshed.account_profile_id is None
    assert refreshed.verified_account_key is None
    assert refreshed.verified_account_label is None
    assert refreshed.verified_account_probed_at is None


async def test_switch_model_only_edit_leaves_verified_fields_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probed_at = datetime.now(UTC)
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    _session(
        runtime,
        account_profile_id="work",
        account_profile_label="Work",
        verified_account_key="codex:work@co",
        verified_account_label="work@co",
        verified_account_probed_at=probed_at,
    )
    plugin = runtime.registry.plugin_for(runtime.get_session("s1"))

    async def fake_terminate(*_a: Any, **_k: Any) -> None:
        return None

    async def fake_restore(_rt: Any, session: SessionRecord) -> None:
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)

    monkeypatch.setattr(plugin, "terminate_session", fake_terminate)
    monkeypatch.setattr(plugin, "restore_session", fake_restore)

    refreshed = await runtime.update_launch_settings(
        "s1", LaunchSettingsUpdateRequest(env_set={"FOO": "bar"}, restart=True)
    )
    assert refreshed.verified_account_key == "codex:work@co"
    assert refreshed.verified_account_label == "work@co"
    assert refreshed.verified_account_probed_at == probed_at


# ── Launch: fire-and-forget, tracked ────────────────────────────────────────


async def test_launch_with_profile_stamps_after_draining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    codex = runtime.registry.get("codex")
    tmux = runtime.registry.fallback_for_managed_launch()
    assert tmux is not None

    async def fake_codex_create_session(
        _rt: SessionRuntime,
        request: SessionCreateRequest,
        *,
        session_id: str,
        launch_target: Any,
        title: str,
        raw_log: Any,
        structured_log: Any,
        git_meta: Any,
        permission_mode: str | None,
        resolved_model: str | None,
        resolved_effort: str | None,
    ) -> SessionRecord:
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=request.backend,
            source=SessionSource.MANAGED,
            transport="codex_app_server",
            title=title,
            cwd=request.cwd,
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"thread_id": "thread-1"},
            launch_env=request.launch_env,
        )
        runtime.storage.create_session(session)
        return session

    monkeypatch.setattr(codex, "create_session", fake_codex_create_session)
    monkeypatch.setattr(codex, "is_available_for_managed_launch", lambda _rt: True)
    monkeypatch.setattr(runtime, "_warm_command_completions", lambda *_a, **_k: None)
    calls: list[Any] = []
    monkeypatch.setattr(
        "waypoint.runtime.probe_account",
        _fake_probe(
            calls,
            AccountProbeResult(account_key="codex:work@co", account_label="Work Co"),
        ),
    )

    session = await runtime.create_session(
        SessionCreateRequest(
            backend="codex",
            cwd=str(tmp_path),
            account_profile_id="work",
            args=[],
            source_mode=SessionSource.MANAGED,
        )
    )
    # Fire-and-forget: not stamped the instant the call returns.
    assert session.verified_account_key is None

    await runtime._drain_account_probe_tasks()
    refreshed = runtime.storage.get_session(session.id)
    assert refreshed is not None
    assert refreshed.verified_account_key == "codex:work@co"
    assert refreshed.verified_account_label == "Work Co"
    assert refreshed.verified_account_probed_at is not None
    assert len(calls) == 1


async def test_launch_without_profile_leaves_verified_fields_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    codex = runtime.registry.get("codex")

    async def fake_codex_create_session(
        _rt: SessionRuntime,
        request: SessionCreateRequest,
        *,
        session_id: str,
        launch_target: Any,
        title: str,
        raw_log: Any,
        structured_log: Any,
        git_meta: Any,
        permission_mode: str | None,
        resolved_model: str | None,
        resolved_effort: str | None,
    ) -> SessionRecord:
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=request.backend,
            source=SessionSource.MANAGED,
            transport="codex_app_server",
            title=title,
            cwd=request.cwd,
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"thread_id": "thread-1"},
            launch_env=request.launch_env,
        )
        runtime.storage.create_session(session)
        return session

    monkeypatch.setattr(codex, "create_session", fake_codex_create_session)
    monkeypatch.setattr(codex, "is_available_for_managed_launch", lambda _rt: True)
    monkeypatch.setattr(runtime, "_warm_command_completions", lambda *_a, **_k: None)
    calls: list[Any] = []
    monkeypatch.setattr(
        "waypoint.runtime.probe_account",
        _fake_probe(
            calls, AccountProbeResult(account_key="codex:x", account_label="x")
        ),
    )

    session = await runtime.create_session(
        SessionCreateRequest(
            backend="codex",
            cwd=str(tmp_path),
            args=[],
            source_mode=SessionSource.MANAGED,
        )
    )
    await runtime._drain_account_probe_tasks()
    refreshed = runtime.storage.get_session(session.id)
    assert refreshed is not None
    assert refreshed.verified_account_key is None
    assert calls == []


async def test_launch_probe_raising_does_not_fail_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    codex = runtime.registry.get("codex")

    async def fake_codex_create_session(
        _rt: SessionRuntime,
        request: SessionCreateRequest,
        *,
        session_id: str,
        launch_target: Any,
        title: str,
        raw_log: Any,
        structured_log: Any,
        git_meta: Any,
        permission_mode: str | None,
        resolved_model: str | None,
        resolved_effort: str | None,
    ) -> SessionRecord:
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=request.backend,
            source=SessionSource.MANAGED,
            transport="codex_app_server",
            title=title,
            cwd=request.cwd,
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"thread_id": "thread-1"},
            launch_env=request.launch_env,
        )
        runtime.storage.create_session(session)
        return session

    async def raising_probe(*_a: Any, **_k: Any) -> AccountProbeResult:
        raise TimeoutError("probe timed out")

    monkeypatch.setattr(codex, "create_session", fake_codex_create_session)
    monkeypatch.setattr(codex, "is_available_for_managed_launch", lambda _rt: True)
    monkeypatch.setattr(runtime, "_warm_command_completions", lambda *_a, **_k: None)
    monkeypatch.setattr("waypoint.runtime.probe_account", raising_probe)

    session = await runtime.create_session(
        SessionCreateRequest(
            backend="codex",
            cwd=str(tmp_path),
            account_profile_id="work",
            args=[],
            source_mode=SessionSource.MANAGED,
        )
    )
    assert session.status == SessionStatus.IDLE
    await runtime._drain_account_probe_tasks()
    refreshed = runtime.storage.get_session(session.id)
    assert refreshed is not None
    assert refreshed.verified_account_key is None


# ── Thread import: parity with launch ───────────────────────────────────────


async def test_thread_import_with_profile_stamps_after_draining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    codex = runtime.registry.get("codex")

    async def fake_import_thread(
        _rt: SessionRuntime, request: Any, *, agent: str
    ) -> SessionRecord:
        now = datetime.now(UTC)
        session = SessionRecord(
            id="imported-1",
            backend=agent,
            source=SessionSource.MANAGED,
            transport="codex_app_server",
            title="imported",
            cwd="/repo/app",
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path="/r",
            structured_log_path="/e",
            transport_state={"thread_id": request.thread_id},
            launch_env=request.launch_env,
        )
        runtime.storage.create_session(session)
        return session

    monkeypatch.setattr(codex, "import_thread", fake_import_thread)
    calls: list[Any] = []
    monkeypatch.setattr(
        "waypoint.runtime.probe_account",
        _fake_probe(
            calls,
            AccountProbeResult(account_key="codex:work@co", account_label="Work Co"),
        ),
    )

    session = await runtime.import_thread(
        "codex", {"thread_id": "abc", "account_profile_id": "work"}
    )
    assert session.verified_account_key is None
    await runtime._drain_account_probe_tasks()
    refreshed = runtime.storage.get_session(session.id)
    assert refreshed is not None
    assert refreshed.verified_account_key == "codex:work@co"
    assert len(calls) == 1


# ── Reattach ─────────────────────────────────────────────────────────────────


async def test_reattach_reprobes_and_overwrites_stale_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    session = _session(
        runtime,
        account_profile_id="work",
        account_profile_label="Work",
        verified_account_key="codex:stale@co",
        verified_account_label="stale@co",
        verified_account_probed_at=datetime.now(UTC),
    )
    plugin = runtime.registry.plugin_for(session)

    async def fake_terminate(*_a: Any, **_k: Any) -> None:
        return None

    async def fake_restore(_rt: Any, s: SessionRecord) -> None:
        runtime.storage.update_session(s.id, status=SessionStatus.IDLE)

    monkeypatch.setattr(plugin, "terminate_session", fake_terminate)
    monkeypatch.setattr(plugin, "restore_session", fake_restore)
    calls: list[Any] = []
    monkeypatch.setattr(
        "waypoint.runtime.probe_account",
        _fake_probe(
            calls,
            AccountProbeResult(account_key="codex:fresh@co", account_label="fresh@co"),
        ),
    )

    refreshed = await runtime._reattach_session(session)
    # Fire-and-forget: not yet stamped.
    assert refreshed.verified_account_key == "codex:stale@co"

    await runtime._drain_account_probe_tasks()
    final = runtime.storage.get_session(session.id)
    assert final is not None
    assert final.verified_account_key == "codex:fresh@co"
    assert final.verified_account_label == "fresh@co"
    assert len(calls) == 1


async def test_reattach_probe_raising_does_not_fail_reattach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    session = _session(runtime, account_profile_id="work", account_profile_label="Work")
    plugin = runtime.registry.plugin_for(session)

    async def fake_terminate(*_a: Any, **_k: Any) -> None:
        return None

    async def fake_restore(_rt: Any, s: SessionRecord) -> None:
        runtime.storage.update_session(s.id, status=SessionStatus.IDLE)

    monkeypatch.setattr(plugin, "terminate_session", fake_terminate)
    monkeypatch.setattr(plugin, "restore_session", fake_restore)

    async def raising_probe(*_a: Any, **_k: Any) -> AccountProbeResult:
        raise TimeoutError("probe timed out")

    monkeypatch.setattr("waypoint.runtime.probe_account", raising_probe)

    refreshed = await runtime._reattach_session(session)
    assert refreshed.status == SessionStatus.IDLE
    await runtime._drain_account_probe_tasks()
    final = runtime.storage.get_session(session.id)
    assert final is not None
    assert final.verified_account_key is None


async def test_reattach_without_profile_does_not_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Gated on a profile being set, same as launch/thread-import/boot-restore
    # (a no-profile session has nothing to re-verify, and this avoids a live
    # probe against every plain session's reattach).
    runtime = _runtime(tmp_path)
    session = _session(runtime)
    plugin = runtime.registry.plugin_for(session)

    async def fake_terminate(*_a: Any, **_k: Any) -> None:
        return None

    async def fake_restore(_rt: Any, s: SessionRecord) -> None:
        runtime.storage.update_session(s.id, status=SessionStatus.IDLE)

    monkeypatch.setattr(plugin, "terminate_session", fake_terminate)
    monkeypatch.setattr(plugin, "restore_session", fake_restore)
    calls: list[Any] = []
    monkeypatch.setattr(
        "waypoint.runtime.probe_account",
        _fake_probe(
            calls,
            AccountProbeResult(account_key="codex:fresh@co", account_label="fresh@co"),
        ),
    )

    refreshed = await runtime._reattach_session(session)
    await runtime._drain_account_probe_tasks()
    final = runtime.storage.get_session(refreshed.id)
    assert final is not None
    assert final.verified_account_key is None
    assert calls == []


# ── Boot-restore ─────────────────────────────────────────────────────────────


async def test_boot_restore_local_session_reprobes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    session = _session(
        runtime,
        account_profile_id="work",
        account_profile_label="Work",
        verified_account_key="codex:stale@co",
        verified_account_label="stale@co",
        verified_account_probed_at=datetime.now(UTC),
    )
    plugin = runtime.registry.plugin_for(session)

    async def fake_restore(_rt: Any, s: SessionRecord) -> None:
        runtime.storage.update_session(s.id, status=SessionStatus.IDLE)

    monkeypatch.setattr(plugin, "restore_session", fake_restore)
    monkeypatch.setattr(runtime, "_warm_command_completions", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime, "_start_context_usage_source", lambda *_a, **_k: None)
    calls: list[Any] = []
    monkeypatch.setattr(
        "waypoint.runtime.probe_account",
        _fake_probe(
            calls,
            AccountProbeResult(account_key="codex:fresh@co", account_label="fresh@co"),
        ),
    )

    await runtime._restore_session_and_warm_completions(plugin, session)
    await runtime._drain_account_probe_tasks()
    final = runtime.storage.get_session(session.id)
    assert final is not None
    assert final.verified_account_key == "codex:fresh@co"
    assert len(calls) == 1


async def test_boot_restore_failed_probe_leaves_prior_value_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    probed_at = datetime.now(UTC)
    session = _session(
        runtime,
        account_profile_id="work",
        account_profile_label="Work",
        verified_account_key="codex:good@co",
        verified_account_label="good@co",
        verified_account_probed_at=probed_at,
    )
    plugin = runtime.registry.plugin_for(session)

    async def fake_restore(_rt: Any, s: SessionRecord) -> None:
        runtime.storage.update_session(s.id, status=SessionStatus.IDLE)

    monkeypatch.setattr(plugin, "restore_session", fake_restore)
    monkeypatch.setattr(runtime, "_warm_command_completions", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime, "_start_context_usage_source", lambda *_a, **_k: None)
    monkeypatch.setattr("waypoint.runtime.probe_account", _fake_probe([], None))

    await runtime._restore_session_and_warm_completions(plugin, session)
    await runtime._drain_account_probe_tasks()
    final = runtime.storage.get_session(session.id)
    assert final is not None
    assert final.verified_account_key == "codex:good@co"
    assert final.verified_account_label == "good@co"
    assert final.verified_account_probed_at == probed_at


async def test_boot_restore_without_profile_does_not_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A no-profile session is stable across a restart: verified_account_*
    # stays None rather than getting stamped only after the first restart,
    # and boot doesn't mass-probe the provider for every plain session.
    runtime = _runtime(tmp_path)
    session = _session(runtime)
    plugin = runtime.registry.plugin_for(session)

    async def fake_restore(_rt: Any, s: SessionRecord) -> None:
        runtime.storage.update_session(s.id, status=SessionStatus.IDLE)

    monkeypatch.setattr(plugin, "restore_session", fake_restore)
    monkeypatch.setattr(runtime, "_warm_command_completions", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime, "_start_context_usage_source", lambda *_a, **_k: None)
    calls: list[Any] = []
    monkeypatch.setattr(
        "waypoint.runtime.probe_account",
        _fake_probe(
            calls,
            AccountProbeResult(account_key="codex:fresh@co", account_label="fresh@co"),
        ),
    )

    await runtime._restore_session_and_warm_completions(plugin, session)
    await runtime._drain_account_probe_tasks()
    final = runtime.storage.get_session(session.id)
    assert final is not None
    assert final.verified_account_key is None
    assert calls == []


async def test_boot_restore_skips_remote_launch_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, codex=_codex_profiles())
    session = _session(
        runtime,
        account_profile_id="work",
        account_profile_label="Work",
        launch_target_id="devbox",
    )
    plugin = runtime.registry.plugin_for(session)

    async def fake_restore(_rt: Any, s: SessionRecord) -> None:
        runtime.storage.update_session(s.id, status=SessionStatus.IDLE)

    monkeypatch.setattr(plugin, "restore_session", fake_restore)
    monkeypatch.setattr(runtime, "_warm_command_completions", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime, "_start_context_usage_source", lambda *_a, **_k: None)
    calls: list[Any] = []
    monkeypatch.setattr(
        "waypoint.runtime.probe_account",
        _fake_probe(
            calls,
            AccountProbeResult(account_key="codex:fresh@co", account_label="fresh@co"),
        ),
    )

    await runtime._restore_session_and_warm_completions(plugin, session)
    await runtime._drain_account_probe_tasks()
    assert calls == []
