"""Tests for the opt-in telemetry gate (RFC: opt-in usage telemetry dashboard).

Covers the master switch defaulting off, the separate historical-import
switch, the NL cross-field requirement, runtime task gating, the endpoint
guard (404 while disabled, DELETE still available), backfill-marker
preservation through deletion, and the `/api/me` capability field.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from waypoint.api import create_app
from waypoint.runtime import SessionRuntime
from waypoint.settings import Settings, TelemetryNLConfig, load_settings
from waypoint.storage import Storage
from waypoint.telemetry import aggregate
from waypoint.telemetry.facts import (
    FactDimensions,
    LifecycleTransition,
    SessionLifecycleFact,
)

GUARDED_GET_ENDPOINTS = [
    "/api/telemetry/overview",
    "/api/telemetry/tokens",
    "/api/telemetry/activity",
    "/api/telemetry/health",
    "/api/telemetry/insights",
    "/api/telemetry/settings",
    "/api/telemetry/nl-insight",
]


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _build(tmp_path: Path, *, enabled: bool, backfill: bool = False) -> tuple[Any, str]:
    settings = Settings(
        data_dir=tmp_path / "data",
        telemetry_enabled=enabled,
        telemetry_backfill=backfill,
    )
    app = create_app(settings)
    token = app.state.context.tokens.issue().token
    return app, token


# ── settings defaults / overrides / validation ──────────────────────────────


def test_defaults_disable_telemetry_and_backfill() -> None:
    settings = Settings()
    assert settings.telemetry_enabled is False
    assert settings.telemetry_backfill is False


def test_yaml_enables_both_switches(tmp_path: Path) -> None:
    config = tmp_path / "waypoint.yaml"
    config.write_text("telemetry_enabled: true\ntelemetry_backfill: true\n")
    settings = load_settings(config)
    assert settings.telemetry_enabled is True
    assert settings.telemetry_backfill is True


def test_env_overrides_take_precedence_over_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "waypoint.yaml"
    config.write_text("telemetry_enabled: true\ntelemetry_backfill: true\n")
    monkeypatch.setenv("WAYPOINT_TELEMETRY_ENABLED", "false")
    monkeypatch.setenv("WAYPOINT_TELEMETRY_BACKFILL", "0")
    settings = load_settings(config)
    assert settings.telemetry_enabled is False
    assert settings.telemetry_backfill is False


def test_env_enables_backfill_without_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "waypoint.yaml"
    config.write_text("telemetry_enabled: true\n")
    monkeypatch.setenv("WAYPOINT_TELEMETRY_BACKFILL", "yes")
    settings = load_settings(config)
    assert settings.telemetry_backfill is True


def test_nl_enabled_requires_master_switch() -> None:
    with pytest.raises(ValidationError):
        Settings(telemetry_nl=TelemetryNLConfig(enabled=True))


def test_nl_enabled_ok_with_master_switch() -> None:
    settings = Settings(
        telemetry_enabled=True, telemetry_nl=TelemetryNLConfig(enabled=True)
    )
    assert settings.telemetry_nl.enabled is True


# ── runtime construction / task gating ───────────────────────────────────────


def test_disabled_runtime_has_no_ingester(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", telemetry_enabled=False)
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    assert runtime.telemetry_ingester is None


def test_enabled_runtime_has_ingester(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", telemetry_enabled=True)
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    assert runtime.telemetry_ingester is not None


def test_disabled_runtime_starts_no_telemetry_tasks(tmp_path: Path) -> None:
    app, _ = _build(tmp_path, enabled=False)
    with TestClient(app):
        runtime = app.state.context.runtime
        assert runtime._telemetry_backfill_task is None
        assert runtime._telemetry_maintenance_task is None
        assert runtime._telemetry_broadcast_task is None


def test_enabled_without_backfill_runs_maintenance_and_broadcast_only(
    tmp_path: Path,
) -> None:
    app, _ = _build(tmp_path, enabled=True, backfill=False)
    with TestClient(app):
        runtime = app.state.context.runtime
        assert runtime._telemetry_backfill_task is None
        assert runtime._telemetry_maintenance_task is not None
        assert runtime._telemetry_broadcast_task is not None


def test_enabled_with_backfill_starts_backfill_task(tmp_path: Path) -> None:
    app, _ = _build(tmp_path, enabled=True, backfill=True)
    with TestClient(app):
        runtime = app.state.context.runtime
        assert runtime._telemetry_backfill_task is not None


# ── deletion preserves the one-shot backfill marker ──────────────────────────


def test_delete_preserves_backfill_marker(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    storage.telemetry.set_meta("backfill_done", "true")
    storage.telemetry.ingest_fact(
        SessionLifecycleFact(
            fact_id="s1:created",
            source="runtime",
            session_id="s1",
            occurred_at=datetime.now(UTC),
            dims=FactDimensions.model_validate(
                {
                    "backend": "codex",
                    "repo_name": "waypoint",
                    "source": "managed",
                    "transport": "tmux",
                    "spawner_session_id": None,
                    "is_child": False,
                }
            ),
            transition=LifecycleTransition.CREATED,
        )
    )

    result = aggregate.delete_all(storage)

    assert result.removed.facts >= 1
    assert result.transcripts_unaffected is True
    # The one-shot marker survives so a later enabled restart does not
    # re-derive the erased pre-enable history.
    assert storage.telemetry.get_meta("backfill_done") == "true"


# ── endpoint guard ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", GUARDED_GET_ENDPOINTS)
async def test_guarded_get_endpoints_404_when_disabled(
    tmp_path: Path, path: str
) -> None:
    app, token = _build(tmp_path, enabled=False)
    async with _client(app) as client:
        resp = await client.get(path, headers=_auth(token))
    assert resp.status_code == 404
    assert "telemetry is disabled" in resp.json()["detail"]


async def test_guarded_drilldown_404_when_disabled(tmp_path: Path) -> None:
    app, token = _build(tmp_path, enabled=False)
    async with _client(app) as client:
        resp = await client.get(
            "/api/telemetry/drilldown",
            params={"kind": "turn"},
            headers=_auth(token),
        )
    assert resp.status_code == 404


async def test_insight_dismiss_404_when_disabled(tmp_path: Path) -> None:
    app, token = _build(tmp_path, enabled=False)
    async with _client(app) as client:
        resp = await client.post(
            "/api/telemetry/insights/context_pressure/dismiss", headers=_auth(token)
        )
    assert resp.status_code == 404


async def test_nl_insight_generate_404_when_disabled(tmp_path: Path) -> None:
    app, token = _build(tmp_path, enabled=False)
    async with _client(app) as client:
        resp = await client.post("/api/telemetry/nl-insight", headers=_auth(token))
    assert resp.status_code == 404


async def test_guarded_endpoint_requires_auth_before_gate(tmp_path: Path) -> None:
    # The gate runs after auth: an unauthenticated request 401s, not 404s.
    app, _ = _build(tmp_path, enabled=False)
    async with _client(app) as client:
        resp = await client.get("/api/telemetry/overview")
    assert resp.status_code == 401


async def test_delete_available_while_disabled(tmp_path: Path) -> None:
    app, token = _build(tmp_path, enabled=False)
    async with _client(app) as client:
        resp = await client.delete("/api/telemetry", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["transcripts_unaffected"] is True


# ── /api/me capability ───────────────────────────────────────────────────────


async def test_me_advertises_disabled_capability(tmp_path: Path) -> None:
    app, token = _build(tmp_path, enabled=False)
    async with _client(app) as client:
        resp = await client.get("/api/me", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["telemetry_enabled"] is False


async def test_me_advertises_enabled_capability(tmp_path: Path) -> None:
    app, token = _build(tmp_path, enabled=True)
    async with _client(app) as client:
        resp = await client.get("/api/me", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["telemetry_enabled"] is True
