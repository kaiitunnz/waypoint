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


def _record(session_id: str = "src", **overrides: Any) -> SessionRecord:
    now = datetime.now(UTC)
    base: dict[str, Any] = dict(
        id=session_id,
        backend="codex",
        source=SessionSource.MANAGED,
        title="Session",
        cwd="/tmp",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=f"/tmp/{session_id}.raw",
        structured_log_path=f"/tmp/{session_id}.json",
        transport_state={"thread_id": f"thread-{session_id}"},
    )
    base.update(overrides)
    return SessionRecord(**base)


def _session(storage: Storage, session_id: str = "sess", **overrides) -> SessionRecord:
    record = _record(session_id, **overrides)
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


class _CodexAdapter:
    """Minimal codex adapter double so create_session runs the real pipeline."""

    async def start_session(self, *args: Any, **kwargs: Any) -> str:
        return "thread-child"

    async def register_rate_limit_probe(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def force_refresh_rate_limit_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


def _derive_runtime(
    tmp_path: Path, source_id: str, source_fields: dict[str, Any]
) -> tuple[SessionRuntime, Storage]:
    runtime, storage = _make_runtime(tmp_path, _FakeProvider())
    _session(storage, source_id, **source_fields)
    runtime.registry.get("codex").adapter = _CodexAdapter()  # type: ignore[attr-defined]
    return runtime, storage


def _assert_provider_source(session: SessionRecord) -> None:
    assert session.usage_limit_source == "usage_provider"
    assert session.usage_provider_id == "lumid"
    assert session.usage_provider_account_key == _ACCOUNT_KEY
    assert session.rate_limit_usage is not None
    assert session.rate_limit_usage.origin == "usage_provider"


# ── fork ─────────────────────────────────────────────────────────────────────


class _ForkPlugin:
    """Plugin double whose forks build a fresh plugin-source record, as the real
    plugins do."""

    capabilities = SimpleNamespace(supports_fork=True)

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fork_session(
        self, runtime: SessionRuntime, session: SessionRecord, new_id: str, *_: Any
    ) -> SessionRecord:
        self.calls.append("fork")
        return _session(runtime.storage, new_id)

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
        return _session(runtime.storage, new_session_id)


async def _fork(runtime: SessionRuntime, kind: str) -> SessionRecord:
    if kind == "fork":
        return await runtime.fork_session("src")
    return await runtime.fork_side_question("src", "sq1")


def _fork_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_fields: dict[str, Any]
) -> tuple[SessionRuntime, Storage, _ForkPlugin]:
    runtime, storage = _derive_runtime(tmp_path, "src", source_fields)
    plugin = _ForkPlugin()
    monkeypatch.setattr(runtime.registry, "plugin_for", lambda _session: plugin)
    monkeypatch.setattr(runtime, "_warm_command_completions", lambda *_a, **_k: None)
    return runtime, storage, plugin


@pytest.mark.parametrize("kind", ["fork", "side_question"])
async def test_fork_inherits_provider_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    runtime, _, plugin = _fork_runtime(tmp_path, monkeypatch, _PROVIDER_FIELDS)
    child = await _fork(runtime, kind)
    assert plugin.calls == [kind]
    assert child.id != "src"
    _assert_provider_source(child)


async def test_fork_keeps_plugin_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _, _ = _fork_runtime(tmp_path, monkeypatch, {})
    child = await runtime.fork_session("src")
    assert child.usage_limit_source == "plugin"
    assert child.usage_provider_id is None


@pytest.mark.parametrize("kind", ["fork", "side_question"])
async def test_fork_with_stale_source_fails_before_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    runtime, storage, plugin = _fork_runtime(tmp_path, monkeypatch, _STALE_FIELDS)
    with pytest.raises(HTTPException) as exc_info:
        await _fork(runtime, kind)
    assert exc_info.value.status_code == 409
    assert "cannot fork session src" in str(exc_info.value.detail)
    assert plugin.calls == []
    assert [s.id for s in storage.list_sessions()] == ["src"]


# ── /new (clone) ─────────────────────────────────────────────────────────────


async def test_clone_inherits_provider_source(tmp_path: Path) -> None:
    runtime, _ = _derive_runtime(tmp_path, "src", _PROVIDER_FIELDS)
    child = await runtime.clone_session_launch("src")
    assert child.id != "src"
    _assert_provider_source(runtime.get_session(child.id))


async def test_clone_with_stale_source_fails_without_launching(
    tmp_path: Path,
) -> None:
    runtime, storage = _derive_runtime(tmp_path, "src", _STALE_FIELDS)
    with pytest.raises(HTTPException) as exc_info:
        await runtime.clone_session_launch("src")
    assert exc_info.value.status_code == 409
    assert "start a new session from session src" in str(exc_info.value.detail)
    assert [s.id for s in storage.list_sessions()] == ["src"]


# ── spawned children ─────────────────────────────────────────────────────────


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


def _child_request(**fields: Any) -> SessionCreateRequest:
    return SessionCreateRequest(
        **{"backend": "codex", "cwd": "/tmp", "spawner_session_id": "parent", **fields}
    )


async def test_spawned_child_inherits_provider_source(tmp_path: Path) -> None:
    runtime, _ = _derive_runtime(tmp_path, "parent", _PROVIDER_FIELDS)
    child = await runtime.create_session(_child_request())
    _assert_provider_source(runtime.get_session(child.id))


@pytest.mark.parametrize(
    ("spawner", "request_fields"),
    [
        pytest.param(
            _PROVIDER_FIELDS, {"usage_limit_source": "plugin"}, id="explicit-plugin"
        ),
        pytest.param(_PROVIDER_FIELDS, {"backend": "claude_code"}, id="cross-backend"),
        pytest.param(
            {**_PROVIDER_FIELDS, "account_profile_id": "work"}, {}, id="other-profile"
        ),
        pytest.param(
            {**_PROVIDER_FIELDS, "launch_target_id": "remote-a"}, {}, id="other-target"
        ),
        pytest.param(_STALE_FIELDS, {}, id="stale-spawner"),
    ],
)
async def test_spawned_child_keeps_plugin_source(
    tmp_path: Path, spawner: dict[str, Any], request_fields: dict[str, Any]
) -> None:
    runtime, _ = _derive_runtime(tmp_path, "parent", spawner)
    selection = runtime._effective_usage_selection(_child_request(**request_fields))
    assert selection == ("plugin", None, None)


async def test_partial_provider_fields_do_not_inherit(tmp_path: Path) -> None:
    runtime, _ = _derive_runtime(tmp_path, "parent", _PROVIDER_FIELDS)
    with pytest.raises(HTTPException) as exc_info:
        runtime._effective_usage_selection(_child_request(usage_provider_id="lumid"))
    assert exc_info.value.status_code == 400


async def test_preset_provider_source_overrides_spawner(tmp_path: Path) -> None:
    runtime, storage = _derive_runtime(tmp_path, "parent", {})
    _seed_preset(storage, "provider-preset", **_PROVIDER_FIELDS)
    resolved, _ = resolve_session_create_request(
        storage,
        SessionLaunchRequest(
            backend="codex",
            cwd="/tmp",
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
    runtime, storage = _derive_runtime(tmp_path, "parent", _PROVIDER_FIELDS)
    _seed_preset(storage, "ui-preset", backend="codex", usage_limit_source="plugin")
    storage.set_default_session_preset("ui-preset")
    resolved, _ = resolve_session_create_request(
        storage,
        SessionLaunchRequest(
            cwd="/tmp", spawner_session_id="parent", use_default_preset=True
        ),
    )
    assert runtime._effective_usage_selection(resolved).source == "usage_provider"


# ── assistant clear-context ──────────────────────────────────────────────────


def _assistant_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_fields: dict[str, Any]
) -> tuple[SessionRuntime, dict[str, Any]]:
    runtime, storage = _make_runtime(tmp_path, _FakeProvider())
    runtime.settings.assistant = AssistantConfig(backend="codex")
    _session(storage, "assistant-live", source=SessionSource.ASSISTANT, **live_fields)
    runtime.assistant_session_id = "assistant-live"
    captured: dict[str, Any] = {}

    async def fake_create(backend: str, **kwargs: Any) -> SessionRecord:
        captured.update(backend=backend, **kwargs)
        return _session(storage, "assistant-fresh", source=SessionSource.ASSISTANT)

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
