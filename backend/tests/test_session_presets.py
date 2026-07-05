"""Tests for session presets: storage CRUD, resolver merge, API, and CLI."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from typer.testing import CliRunner

from waypoint.api import create_app
from waypoint.cli import app as cli_app
from waypoint.client import WaypointClient
from waypoint.presets import (
    PresetManager,
    redact_preset,
    resolve_schedule_create_request,
    resolve_session_create_request,
)
from waypoint.schemas import (
    ScheduleLaunchRequest,
    SessionLaunchRequest,
    SessionPresetCreateRequest,
    SessionPresetRecord,
    SessionPresetSpec,
    SessionPresetUpdateRequest,
)
from waypoint.settings import Settings
from waypoint.storage import Storage

runner = CliRunner()


def _storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "waypoint.db")


def _record(name: str, **spec_kwargs: Any) -> SessionPresetRecord:
    now = datetime.now(UTC)
    return SessionPresetRecord(
        id=f"preset-{name}",
        name=name,
        spec=SessionPresetSpec(**spec_kwargs),
        created_at=now,
        updated_at=now,
    )


# ── Storage ──────────────────────────────────────────────────────────────────


def test_storage_round_trip(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    record = _record("Worker", backend="codex", model="gpt-5", launch_env={"A": "1"})
    storage.create_session_preset(record)
    fetched = storage.get_session_preset(record.id)
    assert fetched is not None
    assert fetched.name == "Worker"
    assert fetched.spec.backend == "codex"
    assert fetched.spec.launch_env == {"A": "1"}
    assert storage.get_session_preset_by_name("worker") is not None  # case-insensitive
    assert [p.name for p in storage.list_session_presets()] == ["Worker"]


def test_storage_default_invariant(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    storage.create_session_preset(_record("A", backend="codex"))
    storage.create_session_preset(_record("B", backend="codex"))
    storage.set_default_session_preset("preset-A")
    assert storage.get_default_session_preset().id == "preset-A"  # type: ignore[union-attr]
    # Switching the default clears the previous one (partial unique index holds).
    storage.set_default_session_preset("preset-B")
    default = storage.get_default_session_preset()
    assert default is not None and default.id == "preset-B"
    assert storage.get_session_preset("preset-A").is_default is False  # type: ignore[union-attr]
    # Clearing removes the default entirely.
    storage.set_default_session_preset(None)
    assert storage.get_default_session_preset() is None


def test_storage_name_uniqueness_is_case_insensitive(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    storage.create_session_preset(_record("Worker", backend="codex"))
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        storage.create_session_preset(_record("worker", backend="claude_code"))


def test_storage_delete_default_clears_default(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    storage.create_session_preset(_record("A", backend="codex"))
    storage.set_default_session_preset("preset-A")
    assert storage.delete_session_preset("preset-A") is True
    assert storage.get_default_session_preset() is None


# ── PresetManager ────────────────────────────────────────────────────────────


def test_manager_create_and_default(tmp_path: Path) -> None:
    manager = PresetManager(_storage(tmp_path))
    created = manager.create(
        SessionPresetCreateRequest(
            name="Worker",
            spec=SessionPresetSpec(backend="codex"),
            is_default=True,
        )
    )
    assert created.is_default is True
    assert manager.default() is not None
    # Duplicate name is a 409.
    with pytest.raises(HTTPException) as exc:
        manager.create(SessionPresetCreateRequest(name="worker"))
    assert exc.value.status_code == 409


def test_preset_spec_excludes_cwd_and_title() -> None:
    # cwd/title are per-launch specifics, not preset fields; a spec built from a
    # payload carrying them (e.g. an older persisted preset) silently drops them.
    spec = SessionPresetSpec.model_validate(
        {"backend": "codex", "cwd": "/x", "title": "t", "model": "m"}
    )
    assert not hasattr(spec, "cwd")
    assert not hasattr(spec, "title")
    assert spec.model == "m"
    assert "cwd" not in spec.model_dump()
    assert "title" not in spec.model_dump()


def test_manager_rejects_reserved_default_name(tmp_path: Path) -> None:
    manager = PresetManager(_storage(tmp_path))
    with pytest.raises(HTTPException) as exc:
        manager.create(SessionPresetCreateRequest(name="Default"))
    assert exc.value.status_code == 400


def test_manager_update_preserves_omitted_spec_fields(tmp_path: Path) -> None:
    manager = PresetManager(_storage(tmp_path))
    manager.create(
        SessionPresetCreateRequest(
            name="Worker",
            spec=SessionPresetSpec(
                backend="codex", model="gpt-5", tags={"role": "worker"}
            ),
        )
    )
    # Update only the model; tags and backend must survive (PATCH merge).
    updated = manager.update(
        "Worker",
        SessionPresetUpdateRequest(spec=SessionPresetSpec(model="gpt-5-mini")),
    )
    assert updated.spec.model == "gpt-5-mini"
    assert updated.spec.backend == "codex"
    assert updated.spec.tags == {"role": "worker"}


# ── Resolver ─────────────────────────────────────────────────────────────────


def _seed(storage: Storage, **spec_kwargs: Any) -> SessionPresetRecord:
    return PresetManager(storage).create(
        SessionPresetCreateRequest(name="P", spec=SessionPresetSpec(**spec_kwargs))
    )


def test_resolve_scalar_override(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    preset = _seed(storage, backend="codex", model="gpt-5")
    request = SessionLaunchRequest(preset_id=preset.id, cwd="/x", model="gpt-5-mini")
    resolved, matched = resolve_session_create_request(storage, request)
    assert matched is not None
    assert resolved.backend == "codex"  # from preset
    assert resolved.model == "gpt-5-mini"  # explicit wins


def test_resolve_list_replaces_not_appends(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    preset = _seed(storage, backend="codex", args=["--a", "--b"])
    request = SessionLaunchRequest(preset_id=preset.id, cwd="/x", args=["--c"])
    resolved, _ = resolve_session_create_request(storage, request)
    assert resolved.args == ["--c"]


def test_resolve_preset_fills_omitted_list(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    preset = _seed(storage, backend="codex", args=["--a"])
    resolved, _ = resolve_session_create_request(
        storage, SessionLaunchRequest(preset_id=preset.id, cwd="/x")
    )
    assert resolved.args == ["--a"]


def test_resolve_empty_preset_env_preserves_backend_defaults(tmp_path: Path) -> None:
    # A persisted preset always has launch_env={} present; the resolver must NOT
    # mark it as explicitly set, or the runtime would skip the backend default env.
    storage = _storage(tmp_path)
    preset = _seed(storage, backend="codex")
    resolved, _ = resolve_session_create_request(
        storage, SessionLaunchRequest(preset_id=preset.id, cwd="/x")
    )
    assert "launch_env" not in resolved.model_fields_set


def test_resolve_nonempty_preset_env_is_applied(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    preset = _seed(storage, backend="codex", launch_env={"A": "1"})
    resolved, _ = resolve_session_create_request(
        storage, SessionLaunchRequest(preset_id=preset.id, cwd="/x")
    )
    assert "launch_env" in resolved.model_fields_set
    assert resolved.launch_env == {"A": "1"}


def test_resolve_explicit_empty_env_wins(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    preset = _seed(storage, backend="codex", launch_env={"A": "1"})
    request = SessionLaunchRequest(preset_id=preset.id, cwd="/x", launch_env={})
    resolved, _ = resolve_session_create_request(storage, request)
    assert "launch_env" in resolved.model_fields_set
    assert resolved.launch_env == {}


def test_resolve_unknown_backend_preset_is_400_but_listable(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    preset = _seed(storage, backend="nope-backend")
    with pytest.raises(HTTPException) as exc:
        resolve_session_create_request(
            storage, SessionLaunchRequest(preset_id=preset.id, cwd="/x")
        )
    assert exc.value.status_code == 400
    # Still listable despite the stale backend.
    assert storage.get_session_preset(preset.id) is not None


def test_resolve_missing_backend_is_400(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    with pytest.raises(HTTPException) as exc:
        resolve_session_create_request(storage, SessionLaunchRequest(cwd="/x"))
    assert exc.value.status_code == 400


def test_resolve_default_only_with_flag(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    manager = PresetManager(storage)
    manager.create(
        SessionPresetCreateRequest(
            name="D", spec=SessionPresetSpec(backend="codex"), is_default=True
        )
    )
    # No flag → no preset applied → missing backend → 400.
    with pytest.raises(HTTPException):
        resolve_session_create_request(storage, SessionLaunchRequest(cwd="/x"))
    # With the flag the default is applied.
    resolved, matched = resolve_session_create_request(
        storage, SessionLaunchRequest(cwd="/x", use_default_preset=True)
    )
    assert matched is not None and resolved.backend == "codex"


def test_resolve_schedule_snapshots_and_ignores_tags(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    preset = _seed(storage, backend="codex", model="gpt-5", tags={"role": "x"})
    resolved, matched = resolve_schedule_create_request(
        storage, ScheduleLaunchRequest(preset_id=preset.id, cwd="/x", delay_seconds=60)
    )
    assert matched is not None
    assert resolved.backend == "codex"
    assert resolved.model == "gpt-5"
    assert resolved.delay_seconds == 60
    assert not hasattr(resolved, "tags")  # schedule has no tags field


# ── Redaction ────────────────────────────────────────────────────────────────


def test_redact_drops_env_values_keeps_keys(tmp_path: Path) -> None:
    record = _record("W", backend="codex", launch_env={"SECRET": "v", "B": "w"})
    summary = redact_preset(record)
    assert summary.spec.launch_env_keys == ["B", "SECRET"]
    assert not hasattr(summary.spec, "launch_env")


# ── API ──────────────────────────────────────────────────────────────────────


def _build(tmp_path: Path) -> tuple[Any, str]:
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    token = app.state.context.tokens.issue().token
    return app, token


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_api_requires_auth(tmp_path: Path) -> None:
    app, _ = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get("/api/session-presets")
    assert resp.status_code == 401


async def test_api_crud_and_redaction(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        create = await client.post(
            "/api/session-presets",
            headers=_auth(token),
            json={
                "name": "Worker",
                "spec": {"backend": "codex", "launch_env": {"SECRET": "v"}},
            },
        )
        assert create.status_code == 200
        preset_id = create.json()["preset"]["id"]
        # List is redacted.
        listing = await client.get("/api/session-presets", headers=_auth(token))
        spec = listing.json()["presets"][0]["spec"]
        assert spec["launch_env_keys"] == ["SECRET"]
        assert "launch_env" not in spec
        # Plain GET is redacted; include_secret_values reveals values.
        redacted = await client.get(
            f"/api/session-presets/{preset_id}", headers=_auth(token)
        )
        assert "launch_env" not in redacted.json()["preset"]["spec"]
        full = await client.get(
            f"/api/session-presets/{preset_id}?include_secret_values=true",
            headers=_auth(token),
        )
        assert full.json()["preset"]["spec"]["launch_env"] == {"SECRET": "v"}
        # Duplicate name is a 409.
        dup = await client.post(
            "/api/session-presets", headers=_auth(token), json={"name": "worker"}
        )
        assert dup.status_code == 409
        # Delete.
        deleted = await client.delete(
            f"/api/session-presets/{preset_id}", headers=_auth(token)
        )
        assert deleted.status_code == 200
        missing = await client.get(
            f"/api/session-presets/{preset_id}", headers=_auth(token)
        )
        assert missing.status_code == 404


async def test_api_default_lifecycle_and_me(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        a = (
            await client.post(
                "/api/session-presets",
                headers=_auth(token),
                json={
                    "name": "A",
                    "spec": {"backend": "codex", "launch_env": {"K": "v"}},
                },
            )
        ).json()["preset"]["id"]
        await client.post(f"/api/session-presets/{a}/default", headers=_auth(token))
        me = await client.get("/api/me", headers=_auth(token))
        body = me.json()
        assert body["default_preset_id"] == a
        assert len(body["session_presets"]) == 1
        # /api/me must redact env values, exposing only keys (same as list).
        me_spec = body["session_presets"][0]["spec"]
        assert "launch_env" not in me_spec
        assert me_spec["launch_env_keys"] == ["K"]
        # Clearing the default.
        await client.delete("/api/session-presets/default", headers=_auth(token))
        me2 = await client.get("/api/me", headers=_auth(token))
        assert me2.json()["default_preset_id"] is None


async def test_api_schedule_snapshots_preset(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        preset = (
            await client.post(
                "/api/session-presets",
                headers=_auth(token),
                json={"name": "S", "spec": {"backend": "codex", "model": "gpt-5"}},
            )
        ).json()["preset"]
        sched = await client.post(
            "/api/schedules",
            headers=_auth(token),
            json={"preset_id": preset["id"], "cwd": "/tmp", "delay_seconds": 60},
        )
        assert sched.status_code == 200
        record = sched.json()["schedule"]
        assert record["backend"] == "codex"
        assert record["model"] == "gpt-5"
        assert record["preset_id"] == preset["id"]
        assert record["preset_name"] == "S"
        # Deleting the preset leaves the snapshotted schedule intact.
        await client.delete(
            f"/api/session-presets/{preset['id']}", headers=_auth(token)
        )
        schedules = await client.get("/api/schedules", headers=_auth(token))
        assert schedules.json()["schedules"][0]["backend"] == "codex"


# ── CLI ──────────────────────────────────────────────────────────────────────


def _cli_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "waypoint.yaml"
    cfg.write_text(
        f"default_backend: codex\ndata_dir: {tmp_path / 'data'}\n", encoding="utf-8"
    )
    return cfg


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "WAYPOINT_DATA_DIR",
        "WAYPOINT_CONFIG_PATH",
        "WAYPOINT_HOST",
        "WAYPOINT_PORT",
        "WAYPOINT_PASSWORD",
        "WAYPOINT_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


def _mock_cli(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr("waypoint.cli.WaypointClient", fake_client)


def test_cli_presets_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/session-presets"
        return httpx.Response(
            200, json={"presets": [{"id": "preset-1"}], "default_preset_id": "preset-1"}
        )

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app, ["--config", str(_cli_config(tmp_path)), "presets", "list"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["default_preset_id"] == "preset-1"


def test_cli_presets_create_sends_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"preset": {"id": "preset-1"}})

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "presets",
            "create",
            "--name",
            "Worker",
            "--backend",
            "codex",
            "--model",
            "gpt-5",
            "--launch-env",
            "A=1",
        ],
    )
    assert result.exit_code == 0
    body = captured["body"]
    assert body["name"] == "Worker"
    assert body["spec"]["backend"] == "codex"
    assert body["spec"]["launch_env"] == {"A": "1"}


def test_cli_sessions_start_sends_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/session-presets/worker":
            return httpx.Response(200, json={"preset": {"spec": {"backend": "codex"}}})
        if path == "/api/sessions":
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"session": {"id": "codex-1"}})
        return httpx.Response(404, json={"detail": f"unexpected {path}"})

    _mock_cli(monkeypatch, handler)
    result = runner.invoke(
        cli_app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "sessions",
            "start",
            "--preset",
            "worker",
            "--cwd",
            "/tmp",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["preset_id"] == "worker"
    assert captured["body"]["cwd"] == "/tmp"
