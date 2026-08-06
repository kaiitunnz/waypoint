"""Clone-launch (`/new`) coverage — ticket 1357.

`clone_session_launch` starts a fresh managed session from a source session's
effective launch settings, reusing the source's private ``launch_env`` (secrets
included) without ever serializing it to the API. These tests cover the request
the clone builds, the env plumbed into the plugin create path, the redaction of
env from public payloads, the profile-snapshot contract, and the failure paths.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from waypoint.api import create_app
from waypoint.runtime import CloneLaunchSnapshot, SessionRuntime
from waypoint.schemas import (
    LaunchMode,
    SessionCreateRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage


def _runtime(tmp_path: Path) -> tuple[SessionRuntime, Storage, Settings]:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    return SessionRuntime(settings, storage), storage, settings


def _codex_source(tmp_path: Path, **overrides: Any) -> SessionRecord:
    """A codex source session whose cwd exists so create validation passes."""
    now = datetime.now(UTC)
    base: dict[str, Any] = dict(
        id="src",
        backend="codex",
        source=SessionSource.MANAGED,
        transport="codex_app_server",
        title="Source title",
        cwd=str(tmp_path),
        launch_mode=LaunchMode.AUTO,
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=str(tmp_path / "raw.log"),
        structured_log_path=str(tmp_path / "events.jsonl"),
        transport_state={"thread_id": "thread-src"},
        model="gpt-5-codex",
        effort="high",
        args=["--foo", "bar"],
        config_overrides=["k=v"],
        launch_env={"SECRET_TOKEN": "sk-super-secret", "CODEX_HOME": "/snap/dir"},
    )
    base.update(overrides)
    return SessionRecord(**base)


class _CloneCodexAdapter:
    """Minimal codex adapter double capturing the process env at start."""

    def __init__(self) -> None:
        self.start_env: dict[str, str] | None = None

    async def start_session(
        self,
        session_id: str,
        cwd: str,
        client_factory: Any,
        *,
        model: str | None = None,
        effort: str | None = None,
        custom_args: list[str] | None = None,
        config_overrides: list[str] | None = None,
        launch_env: dict[str, str] | None = None,
    ) -> str:
        self.start_env = dict(launch_env or {})
        return "thread-child"

    async def register_rate_limit_probe(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def force_refresh_rate_limit_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


# ── request/snapshot contract ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clone_builds_request_and_snapshot_from_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, storage, _ = _runtime(tmp_path)
    source = _codex_source(
        tmp_path,
        launch_target_id=None,
        permission_mode="agent",
        account_profile_id="work",
        account_profile_label="Work",
    )
    storage.create_session(source)

    captured: dict[str, Any] = {}

    async def fake_create_session(
        request: SessionCreateRequest,
        *,
        clone_snapshot: CloneLaunchSnapshot | None = None,
        **_: Any,
    ) -> SessionRecord:
        captured["request"] = request
        captured["snapshot"] = clone_snapshot
        return source.model_copy(update={"id": "codex-child"})

    monkeypatch.setattr(runtime, "create_session", fake_create_session)

    result = await runtime.clone_session_launch("src")

    request: SessionCreateRequest = captured["request"]
    snapshot: CloneLaunchSnapshot = captured["snapshot"]
    # Every launch-affecting field is copied; transport is pinned explicitly.
    assert request.backend == "codex"
    assert request.cwd == str(tmp_path)
    assert request.transport == "codex_app_server"
    assert request.launch_mode == LaunchMode.AUTO
    assert request.model == "gpt-5-codex"
    assert request.effort == "high"
    assert request.permission_mode == "agent"
    assert request.args == ["--foo", "bar"]
    assert request.config_overrides == ["k=v"]
    assert request.account_profile_id == "work"
    assert request.source_mode == SessionSource.MANAGED
    # Fresh session: no title carried, and env never rides on the request.
    assert request.title is None
    assert request.launch_env == {}
    assert "launch_env" not in request.model_fields_set
    # The snapshot carries the effective env + profile provenance verbatim.
    assert snapshot is not None
    assert snapshot.launch_env == {
        "SECRET_TOKEN": "sk-super-secret",
        "CODEX_HOME": "/snap/dir",
    }
    assert snapshot.launch_env is not source.launch_env
    assert snapshot.account_profile_id == "work"
    assert snapshot.account_profile_label == "Work"
    assert result.id == "codex-child"


# ── integration through the real codex create pipeline ───────────────────────


@pytest.mark.asyncio
async def test_clone_reaches_plugin_create_with_private_env_and_new_id(
    tmp_path: Path,
) -> None:
    runtime, storage, _ = _runtime(tmp_path)
    plugin = runtime.registry.get("codex")
    adapter = _CloneCodexAdapter()
    plugin.adapter = adapter  # type: ignore[attr-defined]
    source = _codex_source(tmp_path)
    storage.create_session(source)

    child = await runtime.clone_session_launch("src")

    # Distinct id and a fresh title, not a copy of the source.
    assert child.id != "src"
    assert child.id.startswith("codex-")
    assert child.title != source.title
    assert child.source == SessionSource.MANAGED
    # The child persists the source's private launch env verbatim.
    assert child.launch_env == source.launch_env
    assert child.model == "gpt-5-codex"
    assert child.effort == "high"
    assert child.args == ["--foo", "bar"]
    assert child.config_overrides == ["k=v"]
    assert child.transport == "codex_app_server"
    # The secret reached the process env, and WAYPOINT_SESSION_ID is the child's
    # own regenerated id — never an inherited source value.
    assert adapter.start_env is not None
    assert adapter.start_env["SECRET_TOKEN"] == "sk-super-secret"
    assert adapter.start_env["WAYPOINT_SESSION_ID"] == child.id
    # Source is untouched.
    assert storage.get_session("src") is not None


@pytest.mark.asyncio
async def test_clone_public_payload_omits_env_while_stored_retains(
    tmp_path: Path,
) -> None:
    runtime, storage, _ = _runtime(tmp_path)
    plugin = runtime.registry.get("codex")
    plugin.adapter = _CloneCodexAdapter()  # type: ignore[attr-defined]
    storage.create_session(_codex_source(tmp_path))

    child = await runtime.clone_session_launch("src")

    public = child.model_dump(mode="json")
    assert "launch_env" not in public
    # The list serialization (the /api/sessions payload) is redacted too.
    listed = [s.model_dump(mode="json") for s in storage.list_sessions()]
    assert all("launch_env" not in row for row in listed)
    # But the stored record still carries the values for relaunch/restore.
    stored = storage.get_session(child.id)
    assert stored is not None
    assert stored.launch_env == {
        "SECRET_TOKEN": "sk-super-secret",
        "CODEX_HOME": "/snap/dir",
    }


@pytest.mark.asyncio
async def test_clone_uses_saved_env_even_when_profile_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No account profiles are configured on this runtime, yet the source records
    # provenance for a profile that once existed. A clone must reuse the saved
    # effective env verbatim (not re-resolve the now-absent profile) and stamp
    # the saved provenance — never a 400.
    runtime, storage, _ = _runtime(tmp_path)
    plugin = runtime.registry.get("codex")
    plugin.adapter = _CloneCodexAdapter()  # type: ignore[attr-defined]
    monkeypatch.setattr(
        runtime, "_schedule_verified_account_probe", lambda *a, **k: None
    )
    source = _codex_source(
        tmp_path,
        account_profile_id="ghost",
        account_profile_label="Ghost",
        launch_env={"CODEX_HOME": "/frozen/config", "TOKEN": "t"},
    )
    storage.create_session(source)

    child = await runtime.clone_session_launch("src")

    assert child.launch_env == {"CODEX_HOME": "/frozen/config", "TOKEN": "t"}
    assert child.account_profile_id == "ghost"
    assert child.account_profile_label == "Ghost"


# ── failure paths ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clone_invalid_inherited_cwd_fails_and_persists_no_child(
    tmp_path: Path,
) -> None:
    runtime, storage, _ = _runtime(tmp_path)
    plugin = runtime.registry.get("codex")
    plugin.adapter = _CloneCodexAdapter()  # type: ignore[attr-defined]
    storage.create_session(_codex_source(tmp_path, cwd="/no/such/dir"))

    with pytest.raises(HTTPException) as exc:
        await runtime.clone_session_launch("src")
    assert exc.value.status_code == 400
    # Validation failed before any child record was written.
    assert [s.id for s in storage.list_sessions()] == ["src"]


@pytest.mark.asyncio
async def test_clone_missing_source_is_404(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    with pytest.raises(HTTPException) as exc:
        await runtime.clone_session_launch("nope")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_clone_persistent_assistant_is_rejected(tmp_path: Path) -> None:
    runtime, storage, _ = _runtime(tmp_path)
    storage.create_session(_codex_source(tmp_path, source=SessionSource.ASSISTANT))

    with pytest.raises(HTTPException) as exc:
        await runtime.clone_session_launch("src")
    assert exc.value.status_code == 400
    # No child was created off the launchpad.
    assert [s.id for s in storage.list_sessions()] == ["src"]


# ── API endpoint ─────────────────────────────────────────────────────────────


def _app(tmp_path: Path, *sessions: SessionRecord) -> tuple[Any, Any, str]:
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    context = app.state.context
    for session in sessions:
        context.storage.create_session(session)
    token = context.tokens.issue().token
    return app, context, token


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_clone_endpoint_returns_redacted_session(tmp_path: Path) -> None:
    app, context, token = _app(tmp_path, _codex_source(tmp_path))
    context.runtime.registry.get("codex").adapter = _CloneCodexAdapter()

    async with _client(app) as client:
        resp = await client.post(
            "/api/sessions/src/clone",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    session = body["session"]
    assert session["id"] != "src"
    # The response carries the normal redacted shape — no env crosses the wire.
    assert "launch_env" not in session
    # The clone still retained the private env server-side.
    stored = context.storage.get_session(session["id"])
    assert stored is not None
    assert stored.launch_env["SECRET_TOKEN"] == "sk-super-secret"


@pytest.mark.asyncio
async def test_clone_endpoint_missing_source_404(tmp_path: Path) -> None:
    app, _, token = _app(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/sessions/nope/clone",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_clone_endpoint_rejects_assistant(tmp_path: Path) -> None:
    app, _, token = _app(
        tmp_path, _codex_source(tmp_path, source=SessionSource.ASSISTANT)
    )
    async with _client(app) as client:
        resp = await client.post(
            "/api/sessions/src/clone",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_clone_endpoint_requires_auth(tmp_path: Path) -> None:
    app, _, _ = _app(tmp_path, _codex_source(tmp_path))
    async with _client(app) as client:
        resp = await client.post("/api/sessions/src/clone")
    assert resp.status_code in (401, 403)
