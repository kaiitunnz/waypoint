"""Runtime behavior for per-session usage-provider selection (ticket 1273).

Covers the source switch, the central origin-ownership guard, provider
projection, no-fallback on unavailability, the non-restart settings path, and
inheritance by derived sessions (`/new`, fork, side-question fork, spawned
children).
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from waypoint.presets import resolve_session_create_request
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    ProviderRateLimitUsage,
    ProviderRefreshResult,
    ProviderUsageSnapshot,
    ProviderUsageStatus,
    SessionCreateRequest,
    SessionLaunchRequest,
    SessionPresetRecord,
    SessionPresetSpec,
    SessionRateLimitUsage,
    SessionRecord,
    SessionSource,
    SessionStatus,
    UsageLimitSourceUpdateRequest,
    UsageWindow,
)
from waypoint.settings import AssistantConfig, Settings
from waypoint.storage import Storage
from waypoint.usage_providers.service import UsageProviderService

pytestmark = pytest.mark.asyncio

_ACCOUNT_KEY = "hmac:v1:a@x.com"


class _FakeProvider:
    type = "lumid"

    def __init__(self, provider_id: str = "lumid", *, empty: bool = False) -> None:
        self.id = provider_id
        self.label = "Lumid"
        self.refresh_interval_seconds = 300
        self.refresh_calls = 0
        self._empty = empty

    def load_durable(self) -> None:
        return None

    async def refresh(self, *, force: bool) -> ProviderRefreshResult:
        self.refresh_calls += 1
        return ProviderRefreshResult(
            provider_id=self.id, ok_count=1, last_success_at=datetime.now(UTC)
        )

    def buckets(self) -> list[ProviderUsageSnapshot]:
        if self._empty:
            return []
        now = datetime.now(UTC)
        return [
            ProviderUsageSnapshot(
                provider_id=self.id,
                provider_type=self.type,
                account_key=_ACCOUNT_KEY,
                account_label="a@x.com",
                snapshot=ProviderRateLimitUsage(
                    source_id="lumid",
                    updated_at=now,
                    windows=[UsageWindow(id="5h", label="5h", used_percent=61.9)],
                ),
                observed_at=now,
                last_success_at=now,
            )
        ]

    def status(self) -> ProviderUsageStatus:
        return ProviderUsageStatus(
            provider_id=self.id,
            provider_type=self.type,
            provider_label=self.label,
            enabled=True,
            last_success_at=datetime.now(UTC),
        )

    async def aclose(self) -> None:
        return None


def _make_runtime(
    tmp_path, provider: _FakeProvider | None
) -> tuple[SessionRuntime, Storage]:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    if provider is not None:
        runtime.usage_providers = UsageProviderService(
            [provider], observer=runtime._project_provider_sessions
        )
    return runtime, storage


def _session(storage: Storage, session_id: str = "sess", **overrides) -> SessionRecord:
    now = datetime.now(UTC)
    record = SessionRecord(
        id=session_id,
        backend="codex",
        source=SessionSource.MANAGED,
        title="Session",
        cwd="/tmp",
        status=SessionStatus.RUNNING,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=f"/tmp/{session_id}.raw",
        structured_log_path=f"/tmp/{session_id}.json",
        **overrides,
    )
    storage.create_session(record)
    return record


async def test_set_source_to_provider_projects_cached_snapshot(tmp_path) -> None:
    provider = _FakeProvider()
    runtime, storage = _make_runtime(tmp_path, provider)
    _session(storage)
    session = await runtime.set_usage_limit_source(
        "sess",
        UsageLimitSourceUpdateRequest(
            usage_limit_source="usage_provider",
            usage_provider_id="lumid",
            usage_provider_account_key=_ACCOUNT_KEY,
        ),
    )
    assert session.usage_limit_source == "usage_provider"
    assert session.rate_limit_usage is not None
    assert session.rate_limit_usage.origin == "usage_provider"
    assert session.rate_limit_usage.source == "lumid"
    assert "Lumid" in (session.rate_limit_usage.source_label or "")
    # No agent process refresh happened for a provider selection.
    assert provider.refresh_calls == 0


async def test_set_source_rejects_malformed_selection(tmp_path) -> None:
    provider = _FakeProvider()
    runtime, storage = _make_runtime(tmp_path, provider)
    _session(storage)
    with pytest.raises(HTTPException) as exc:
        await runtime.set_usage_limit_source(
            "sess",
            UsageLimitSourceUpdateRequest(
                usage_limit_source="usage_provider", usage_provider_id="lumid"
            ),
        )
    assert exc.value.status_code == 400


async def test_set_source_rejects_unavailable_account(tmp_path) -> None:
    provider = _FakeProvider()
    runtime, storage = _make_runtime(tmp_path, provider)
    _session(storage)
    with pytest.raises(HTTPException) as exc:
        await runtime.set_usage_limit_source(
            "sess",
            UsageLimitSourceUpdateRequest(
                usage_limit_source="usage_provider",
                usage_provider_id="lumid",
                usage_provider_account_key="hmac:v1:missing",
            ),
        )
    assert exc.value.status_code == 409


async def test_switch_back_to_plugin_clears_provider_projection(tmp_path) -> None:
    provider = _FakeProvider()
    runtime, storage = _make_runtime(tmp_path, provider)
    _session(storage)
    await runtime.set_usage_limit_source(
        "sess",
        UsageLimitSourceUpdateRequest(
            usage_limit_source="usage_provider",
            usage_provider_id="lumid",
            usage_provider_account_key=_ACCOUNT_KEY,
        ),
    )
    session = await runtime.set_usage_limit_source(
        "sess", UsageLimitSourceUpdateRequest(usage_limit_source="plugin")
    )
    assert session.usage_limit_source == "plugin"
    assert session.usage_provider_id is None
    # The provider projection was cleared; a plugin session with no resolver
    # produces no snapshot rather than showing the old provider readout.
    assert (
        session.rate_limit_usage is None or session.rate_limit_usage.origin == "plugin"
    )


async def test_origin_guard_drops_plugin_write_under_provider_selection(
    tmp_path,
) -> None:
    provider = _FakeProvider()
    runtime, storage = _make_runtime(tmp_path, provider)
    _session(storage)
    await runtime.set_usage_limit_source(
        "sess",
        UsageLimitSourceUpdateRequest(
            usage_limit_source="usage_provider",
            usage_provider_id="lumid",
            usage_provider_account_key=_ACCOUNT_KEY,
        ),
    )
    # An adapter publishes a native plugin-origin snapshot (dict, model_dump'd).
    plugin_snapshot = SessionRateLimitUsage(
        source="codex",
        updated_at=datetime.now(UTC),
        windows=[UsageWindow(id="5h", label="5h", used_percent=99.0)],
    ).model_dump(mode="json")
    session = await runtime.update_session_fields(
        "sess", rate_limit_usage=plugin_snapshot
    )
    # The guard drops the plugin write; the provider projection survives.
    assert session.rate_limit_usage is not None
    assert session.rate_limit_usage.origin == "usage_provider"


async def test_provider_poll_marks_unavailable_when_account_gone(tmp_path) -> None:
    provider = _FakeProvider()
    runtime, storage = _make_runtime(tmp_path, provider)
    _session(storage)
    await runtime.set_usage_limit_source(
        "sess",
        UsageLimitSourceUpdateRequest(
            usage_limit_source="usage_provider",
            usage_provider_id="lumid",
            usage_provider_account_key=_ACCOUNT_KEY,
        ),
    )
    # The account vanishes from the provider's buckets; a refresh re-projects.
    provider._empty = True
    await runtime.usage_providers.refresh_one("lumid", force=True)  # type: ignore[union-attr]
    session = runtime.get_session("sess")
    assert session.rate_limit_usage is not None
    assert session.rate_limit_usage.origin == "usage_provider"
    assert session.rate_limit_usage.unavailable is True
    # Never substituted plugin data.
    assert session.rate_limit_usage.source == "lumid"


async def test_reconcile_marks_unavailable_when_provider_removed(tmp_path) -> None:
    # A session persisted as provider-selected, but no provider is enabled now.
    runtime, storage = _make_runtime(tmp_path, None)
    now = datetime.now(UTC)
    _session(
        storage,
        usage_limit_source="usage_provider",
        usage_provider_id="lumid",
        usage_provider_account_key=_ACCOUNT_KEY,
        rate_limit_usage=SessionRateLimitUsage(
            origin="usage_provider",
            source="lumid",
            source_label="Lumid — a@x.com",
            updated_at=now,
            windows=[UsageWindow(id="5h", label="5h", used_percent=42.0)],
        ),
    )
    runtime._reconcile_provider_selections()
    session = runtime.get_session("sess")
    assert session.rate_limit_usage is not None
    assert session.rate_limit_usage.unavailable is True
    assert session.rate_limit_usage.stale is True
    # Retains the last-good windows, does not fall back to plugin.
    assert session.rate_limit_usage.windows[0].used_percent == 42.0


# ── inheritance by derived sessions ─────────────────────────────────────────

_PROVIDER_FIELDS: dict[str, Any] = {
    "usage_limit_source": "usage_provider",
    "usage_provider_id": "lumid",
    "usage_provider_account_key": _ACCOUNT_KEY,
}
_STALE_FIELDS: dict[str, Any] = {
    "usage_limit_source": "usage_provider",
    "usage_provider_id": "lumid",
    "usage_provider_account_key": "hmac:v1:gone@x.com",
}


def _record(tmp_path: Path, session_id: str = "src", **overrides: Any) -> SessionRecord:
    now = datetime.now(UTC)
    base: dict[str, Any] = dict(
        id=session_id,
        backend="codex",
        source=SessionSource.MANAGED,
        transport="codex_app_server",
        title="Source",
        cwd=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=str(tmp_path / f"{session_id}.raw"),
        structured_log_path=str(tmp_path / f"{session_id}.jsonl"),
        transport_state={"thread_id": "thread-src"},
    )
    base.update(overrides)
    return SessionRecord(**base)


class _ForkPlugin:
    """Plugin double whose forks build a fresh plugin-source record, as the real
    plugins do."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.capabilities = SimpleNamespace(supports_fork=True)
        self.calls: list[str] = []

    def _child(self, runtime: SessionRuntime, new_session_id: str) -> SessionRecord:
        child = _record(self.tmp_path, new_session_id)
        runtime.storage.create_session(child)
        return child

    async def fork_session(
        self,
        runtime: SessionRuntime,
        session: SessionRecord,
        new_session_id: str,
        *_: Any,
    ) -> SessionRecord:
        self.calls.append("fork")
        return self._child(runtime, new_session_id)

    async def fork_side_question(
        self,
        runtime: SessionRuntime,
        session: SessionRecord,
        side_question_id: str,
        *,
        new_session_id: str,
        **_: Any,
    ) -> SessionRecord:
        self.calls.append("side_question")
        return self._child(runtime, new_session_id)


def _fork_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_fields: dict[str, Any]
) -> tuple[SessionRuntime, Storage, _ForkPlugin]:
    runtime, storage = _make_runtime(tmp_path, _FakeProvider())
    storage.create_session(_record(tmp_path, **source_fields))
    plugin = _ForkPlugin(tmp_path)
    monkeypatch.setattr(runtime.registry, "plugin_for", lambda _session: plugin)
    monkeypatch.setattr(runtime, "_warm_command_completions", lambda *_a, **_k: None)
    return runtime, storage, plugin


# ── fork ─────────────────────────────────────────────────────────────────────


async def test_fork_inherits_provider_source_and_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _, plugin = _fork_runtime(tmp_path, monkeypatch, _PROVIDER_FIELDS)
    child = await runtime.fork_session("src")
    assert plugin.calls == ["fork"]
    assert child.id != "src"
    assert child.usage_limit_source == "usage_provider"
    assert child.usage_provider_id == "lumid"
    assert child.usage_provider_account_key == _ACCOUNT_KEY
    assert child.rate_limit_usage is not None
    assert child.rate_limit_usage.origin == "usage_provider"


async def test_fork_keeps_plugin_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _, _ = _fork_runtime(tmp_path, monkeypatch, {})
    child = await runtime.fork_session("src")
    assert child.usage_limit_source == "plugin"
    assert child.usage_provider_id is None


async def test_fork_with_stale_source_fails_before_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, storage, plugin = _fork_runtime(tmp_path, monkeypatch, _STALE_FIELDS)
    with pytest.raises(HTTPException) as exc_info:
        await runtime.fork_session("src")
    assert exc_info.value.status_code == 409
    assert "cannot fork session src" in str(exc_info.value.detail)
    assert plugin.calls == []
    assert [s.id for s in storage.list_sessions()] == ["src"]


async def test_side_question_fork_inherits_provider_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _, plugin = _fork_runtime(tmp_path, monkeypatch, _PROVIDER_FIELDS)
    child = await runtime.fork_side_question("src", "sq1")
    assert plugin.calls == ["side_question"]
    assert child.usage_limit_source == "usage_provider"
    assert child.usage_provider_account_key == _ACCOUNT_KEY


async def test_side_question_fork_with_stale_source_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _, plugin = _fork_runtime(tmp_path, monkeypatch, _STALE_FIELDS)
    with pytest.raises(HTTPException) as exc_info:
        await runtime.fork_side_question("src", "sq1")
    assert exc_info.value.status_code == 409
    assert plugin.calls == []


# ── /new (clone) ─────────────────────────────────────────────────────────────


async def test_clone_request_carries_provider_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, storage = _make_runtime(tmp_path, _FakeProvider())
    storage.create_session(_record(tmp_path, **_PROVIDER_FIELDS))
    captured: dict[str, SessionCreateRequest] = {}

    async def fake_create_session(
        request: SessionCreateRequest, **_: Any
    ) -> SessionRecord:
        captured["request"] = request
        return _record(tmp_path, "child")

    monkeypatch.setattr(runtime, "create_session", fake_create_session)
    await runtime.clone_session_launch("src")
    request = captured["request"]
    assert request.usage_limit_source == "usage_provider"
    assert request.usage_provider_id == "lumid"
    assert request.usage_provider_account_key == _ACCOUNT_KEY


async def test_clone_with_stale_source_fails_without_launching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, storage = _make_runtime(tmp_path, _FakeProvider())
    storage.create_session(_record(tmp_path, **_STALE_FIELDS))
    calls: list[SessionCreateRequest] = []

    async def fake_create_session(
        request: SessionCreateRequest, **_: Any
    ) -> SessionRecord:
        calls.append(request)
        return _record(tmp_path, "child")

    monkeypatch.setattr(runtime, "create_session", fake_create_session)
    with pytest.raises(HTTPException) as exc_info:
        await runtime.clone_session_launch("src")
    assert exc_info.value.status_code == 409
    assert "start a new session from session src" in str(exc_info.value.detail)
    assert calls == []


# ── spawned children ─────────────────────────────────────────────────────────


def _spawn_runtime(
    tmp_path: Path, spawner_fields: dict[str, Any]
) -> tuple[SessionRuntime, Storage]:
    runtime, storage = _make_runtime(tmp_path, _FakeProvider())
    storage.create_session(_record(tmp_path, "parent", **spawner_fields))
    return runtime, storage


async def test_child_inherits_same_backend_spawner_source(tmp_path: Path) -> None:
    runtime, _ = _spawn_runtime(tmp_path, _PROVIDER_FIELDS)
    request = SessionCreateRequest(
        backend="codex", cwd=str(tmp_path), spawner_session_id="parent"
    )
    assert runtime._effective_usage_selection(request) == (
        "usage_provider",
        "lumid",
        _ACCOUNT_KEY,
    )


async def test_explicit_plugin_source_overrides_spawner(tmp_path: Path) -> None:
    runtime, _ = _spawn_runtime(tmp_path, _PROVIDER_FIELDS)
    request = SessionCreateRequest(
        backend="codex",
        cwd=str(tmp_path),
        spawner_session_id="parent",
        usage_limit_source="plugin",
    )
    assert runtime._effective_usage_selection(request) == ("plugin", None, None)


async def test_cross_backend_child_keeps_plugin_source(tmp_path: Path) -> None:
    runtime, _ = _spawn_runtime(tmp_path, _PROVIDER_FIELDS)
    request = SessionCreateRequest(
        backend="claude_code", cwd=str(tmp_path), spawner_session_id="parent"
    )
    assert runtime._effective_usage_selection(request) == ("plugin", None, None)


def _seed_preset(storage: Storage, preset_id: str, **spec: Any) -> None:
    now = datetime.now(UTC)
    storage.create_session_preset(
        SessionPresetRecord(
            id=preset_id,
            name=preset_id,
            spec=SessionPresetSpec(**spec),
            created_at=now,
            updated_at=now,
        )
    )


async def test_preset_provider_source_overrides_spawner(tmp_path: Path) -> None:
    runtime, storage = _spawn_runtime(tmp_path, {})
    _seed_preset(storage, "provider-preset", **_PROVIDER_FIELDS)
    resolved, _ = resolve_session_create_request(
        storage,
        SessionLaunchRequest(
            backend="codex",
            cwd=str(tmp_path),
            spawner_session_id="parent",
            preset_id="provider-preset",
        ),
    )
    assert runtime._effective_usage_selection(resolved) == (
        "usage_provider",
        "lumid",
        _ACCOUNT_KEY,
    )


async def test_default_preset_with_plugin_source_still_inherits(
    tmp_path: Path,
) -> None:
    # The launch sheet saves ``plugin`` on every preset; that must not block a
    # spawned child from inheriting its spawner's provider source.
    runtime, storage = _spawn_runtime(tmp_path, _PROVIDER_FIELDS)
    _seed_preset(storage, "ui-preset", backend="codex", usage_limit_source="plugin")
    storage.set_default_session_preset("ui-preset")
    resolved, _ = resolve_session_create_request(
        storage,
        SessionLaunchRequest(
            cwd=str(tmp_path), spawner_session_id="parent", use_default_preset=True
        ),
    )
    assert runtime._effective_usage_selection(resolved)[0] == "usage_provider"


async def test_child_with_different_account_profile_keeps_plugin_source(
    tmp_path: Path,
) -> None:
    runtime, _ = _spawn_runtime(
        tmp_path, {**_PROVIDER_FIELDS, "account_profile_id": "work"}
    )
    request = SessionCreateRequest(
        backend="codex", cwd=str(tmp_path), spawner_session_id="parent"
    )
    assert runtime._effective_usage_selection(request) == ("plugin", None, None)


async def test_api_shaped_child_without_source_inherits(tmp_path: Path) -> None:
    runtime, storage = _spawn_runtime(tmp_path, _PROVIDER_FIELDS)
    resolved, _ = resolve_session_create_request(
        storage,
        SessionLaunchRequest(
            backend="codex", cwd=str(tmp_path), spawner_session_id="parent"
        ),
    )
    assert runtime._effective_usage_selection(resolved)[0] == "usage_provider"


async def test_stale_spawner_source_falls_back_to_plugin(tmp_path: Path) -> None:
    runtime, _ = _spawn_runtime(tmp_path, _STALE_FIELDS)
    request = SessionCreateRequest(
        backend="codex", cwd=str(tmp_path), spawner_session_id="parent"
    )
    assert runtime._effective_usage_selection(request) == ("plugin", None, None)


async def test_child_on_different_launch_target_keeps_plugin_source(
    tmp_path: Path,
) -> None:
    runtime, _ = _spawn_runtime(
        tmp_path, {**_PROVIDER_FIELDS, "launch_target_id": "remote-a"}
    )
    request = SessionCreateRequest(
        backend="codex", cwd=str(tmp_path), spawner_session_id="parent"
    )
    assert runtime._effective_usage_selection(request) == ("plugin", None, None)


async def test_partial_provider_fields_do_not_inherit(tmp_path: Path) -> None:
    runtime, _ = _spawn_runtime(tmp_path, _PROVIDER_FIELDS)
    request = SessionCreateRequest(
        backend="codex",
        cwd=str(tmp_path),
        spawner_session_id="parent",
        usage_provider_id="lumid",
    )
    with pytest.raises(HTTPException) as exc_info:
        runtime._effective_usage_selection(request)
    assert exc_info.value.status_code == 400


class _CodexAdapter:
    """Minimal codex adapter double so create_session runs the real pipeline."""

    async def start_session(self, *args: Any, **kwargs: Any) -> str:
        return "thread-child"

    async def register_rate_limit_probe(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def force_refresh_rate_limit_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


async def test_spawned_child_record_carries_provider_source(tmp_path: Path) -> None:
    runtime, _ = _spawn_runtime(tmp_path, _PROVIDER_FIELDS)
    runtime.registry.get("codex").adapter = _CodexAdapter()  # type: ignore[attr-defined]
    child = await runtime.create_session(
        SessionCreateRequest(
            backend="codex", cwd=str(tmp_path), spawner_session_id="parent"
        )
    )
    stored = runtime.get_session(child.id)
    assert stored.usage_limit_source == "usage_provider"
    assert stored.usage_provider_id == "lumid"
    assert stored.usage_provider_account_key == _ACCOUNT_KEY
    assert stored.rate_limit_usage is not None
    assert stored.rate_limit_usage.origin == "usage_provider"


async def test_clone_record_carries_provider_source(tmp_path: Path) -> None:
    runtime, storage = _make_runtime(tmp_path, _FakeProvider())
    storage.create_session(_record(tmp_path, **_PROVIDER_FIELDS))
    runtime.registry.get("codex").adapter = _CodexAdapter()  # type: ignore[attr-defined]
    child = await runtime.clone_session_launch("src")
    stored = runtime.get_session(child.id)
    assert stored.id != "src"
    assert stored.usage_limit_source == "usage_provider"
    assert stored.usage_provider_account_key == _ACCOUNT_KEY
    assert stored.rate_limit_usage is not None
    assert stored.rate_limit_usage.origin == "usage_provider"


# ── assistant clear-context ──────────────────────────────────────────────────


def _assistant_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_fields: dict[str, Any]
) -> tuple[SessionRuntime, dict[str, Any]]:
    runtime, storage = _make_runtime(tmp_path, _FakeProvider())
    runtime.settings.assistant = AssistantConfig(backend="codex")
    storage.create_session(
        _record(
            tmp_path, "assistant-live", source=SessionSource.ASSISTANT, **live_fields
        )
    )
    runtime.assistant_session_id = "assistant-live"
    captured: dict[str, Any] = {}

    async def fake_create(backend: str, **kwargs: Any) -> SessionRecord:
        captured.update(backend=backend, **kwargs)
        child = _record(tmp_path, "assistant-fresh", source=SessionSource.ASSISTANT)
        storage.create_session(child)
        return child

    async def fake_retire(*_: Any) -> None:
        return None

    monkeypatch.setattr(runtime, "_create_assistant_session", fake_create)
    monkeypatch.setattr(runtime, "_retire_previous_assistant", fake_retire)
    return runtime, captured


async def test_clear_context_keeps_provider_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, captured = _assistant_runtime(tmp_path, monkeypatch, _PROVIDER_FIELDS)
    await runtime.reset_assistant()
    assert captured["usage_selection"] == ("usage_provider", "lumid", _ACCOUNT_KEY)


async def test_assistant_backend_switch_resets_to_plugin_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, captured = _assistant_runtime(tmp_path, monkeypatch, _PROVIDER_FIELDS)
    await runtime.reset_assistant(backend="claude_code")
    assert captured["usage_selection"] == ("plugin", None, None)


async def test_clear_context_with_stale_source_keeps_live_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, captured = _assistant_runtime(tmp_path, monkeypatch, _STALE_FIELDS)
    with pytest.raises(HTTPException) as exc_info:
        await runtime.reset_assistant()
    assert exc_info.value.status_code == 409
    assert captured == {}
    assert runtime.assistant_session_id == "assistant-live"
