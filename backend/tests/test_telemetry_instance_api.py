"""API + store + history tests for the instance-health surface (PRD FR-1/FR-4)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from starlette.testclient import TestClient

from waypoint.api import create_app
from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.telemetry.instance import service as instance_service
from waypoint.telemetry.instance.model import DataQuality, InstanceSnapshot
from waypoint.telemetry.query import host_tz_name, host_utc_offset_minutes


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    return Settings(data_dir=tmp_path / "data", telemetry_enabled=True, **kw)


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── store: daily-point upsert semantics ────────────────────────────────────


def test_upsert_first_complete_wins_partial_replaced(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    try:
        tel = storage.telemetry
        day, offset = "2026-07-12", 480

        def upsert(quality: str, total: int) -> bool:
            return tel.upsert_instance_daily(
                day=day,
                utc_offset_minutes=offset,
                tz="ICT",
                observed_at=datetime.now(UTC),
                data_quality=quality,
                total_bytes=total,
                payload_json="{}",
            )

        assert upsert("partial", 100) is True
        # a later partial replaces the stored partial
        assert upsert("partial", 200) is True
        rows = tel.query_instance_history(start_day=day, end_day=day)
        assert rows[0]["total_bytes"] == 200
        # a complete point replaces the partial and then wins forever
        assert upsert("complete", 300) is True
        assert upsert("complete", 400) is False  # first complete wins
        assert upsert("partial", 500) is False
        rows = tel.query_instance_history(start_day=day, end_day=day)
        assert rows[0]["total_bytes"] == 300 and rows[0]["data_quality"] == "complete"
        # never duplicated a period
        assert len(rows) == 1
    finally:
        storage.close()


def test_prune_and_clear_instance_snapshots(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    try:
        tel = storage.telemetry
        tel.upsert_instance_daily(
            day="2020-01-01",
            utc_offset_minutes=0,
            tz="UTC",
            observed_at=datetime(2020, 1, 1, tzinfo=UTC),
            data_quality="complete",
            total_bytes=1,
            payload_json="{}",
        )
        removed = tel.prune_instance_snapshots(rollups_before=datetime.now(UTC))
        assert removed == 1
        assert tel.instance_daily_count() == 0

        tel.set_instance_current("{}")
        tel.upsert_instance_daily(
            day="2026-07-12",
            utc_offset_minutes=0,
            tz="UTC",
            observed_at=datetime.now(UTC),
            data_quality="complete",
            total_bytes=1,
            payload_json="{}",
        )
        tel.clear_instance_snapshots()
        assert tel.instance_daily_count() == 0
        assert tel.get_instance_current() is None
    finally:
        storage.close()


# ── service: first-ever writes immediately; cache staleness ────────────────


def test_first_ever_daily_point_written_regardless_of_clock(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    try:
        # 00:02 local — before the 00:05 gate — must still write the first point.
        midnight = datetime.now(UTC).replace(hour=0, minute=2)
        wrote = instance_service.record_instance_daily_if_due(
            storage, settings, now=midnight
        )
        assert wrote is True
        assert storage.telemetry.instance_daily_count() == 1
    finally:
        storage.close()


def test_build_instance_serves_stale_cache(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    try:
        old = datetime.now(UTC) - timedelta(minutes=30)
        stale_snap = InstanceSnapshot(
            observed_at=old,
            tz=host_tz_name(),
            utc_offset_minutes=host_utc_offset_minutes(),
            data_quality=DataQuality.COMPLETE,
            total_bytes=123,
        )
        storage.telemetry.set_instance_current(stale_snap.model_dump_json())
        result = instance_service.build_instance(storage, settings)
        assert result.stale is True
        assert result.refresh_due is True
        assert result.snapshot.total_bytes == 123  # served the cached values
    finally:
        storage.close()


def test_build_instance_unavailable_past_24h(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    try:
        ancient = datetime.now(UTC) - timedelta(hours=30)
        snap = InstanceSnapshot(
            observed_at=ancient,
            tz="UTC",
            utc_offset_minutes=0,
            data_quality=DataQuality.COMPLETE,
            total_bytes=999,
        )
        storage.telemetry.set_instance_current(snap.model_dump_json())
        result = instance_service.build_instance(storage, settings)
        assert result.snapshot.data_quality == DataQuality.UNAVAILABLE
        assert result.snapshot.total_bytes == 0  # never reuses old values past 24h
    finally:
        storage.close()


# ── API: gating, refresh, privacy, delete ──────────────────────────────────


def test_instance_endpoint_gated_when_disabled(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "d", telemetry_enabled=False)
    app = create_app(settings)
    token = app.state.context.tokens.issue().token
    with TestClient(app) as client:
        resp = client.get("/api/telemetry/instance", headers=_auth(token))
        assert resp.status_code == 404


def test_instance_endpoint_ok_and_path_free(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    token = app.state.context.tokens.issue().token
    with TestClient(app) as client:
        resp = client.get("/api/telemetry/instance", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
        assert "snapshot" in body and "categories" in body["snapshot"]
        assert body["cli_note"]
        # No filesystem path anywhere in the serialized instance response.
        assert "/home/" not in resp.text
        assert str(settings.data_dir) not in resp.text


def test_instance_refresh_recomputes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    token = app.state.context.tokens.issue().token
    with TestClient(app) as client:
        resp = client.post("/api/telemetry/instance/refresh", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["stale"] is False


def test_delete_clears_instance_snapshots(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    token = app.state.context.tokens.issue().token
    storage: Storage = app.state.context.storage
    storage.telemetry.upsert_instance_daily(
        day="2026-07-12",
        utc_offset_minutes=0,
        tz="UTC",
        observed_at=datetime.now(UTC),
        data_quality="complete",
        total_bytes=1,
        payload_json="{}",
    )
    with TestClient(app) as client:
        resp = client.delete("/api/telemetry", headers=_auth(token))
        assert resp.status_code == 200
        assert storage.telemetry.instance_daily_count() == 0
