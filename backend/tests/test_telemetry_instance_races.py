import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.testclient import TestClient

from waypoint import runtime as runtime_module
from waypoint.api import create_app
from waypoint.runtime import SessionRuntime
from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.telemetry import aggregate
from waypoint.telemetry.instance import service as instance_service
from waypoint.telemetry.instance.model import InstanceSnapshot

WORKER_TIMEOUT = 10


async def _settle(task: asyncio.Task[None] | None) -> None:
    assert task is not None
    await asyncio.wait_for(asyncio.shield(task), WORKER_TIMEOUT)


def _settle_bootstrap(client: TestClient, runtime: SessionRuntime) -> None:
    assert client.portal is not None
    client.portal.call(_settle, runtime._instance_bootstrap_task)


@pytest.mark.parametrize("block_at", ["queued", "collected", "daily"])
def test_delete_invalidates_startup_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, block_at: str
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data", telemetry_enabled=True))
    runtime: SessionRuntime = app.state.context.runtime
    storage: Storage = app.state.context.storage
    headers = {"Authorization": f"Bearer {app.state.context.tokens.issue().token}"}
    entered, release = Event(), Event()
    owner: Any = instance_service
    if block_at == "queued":
        owner, name = runtime, "_bootstrap_instance_sync"
    elif block_at == "collected":
        name = "collect_snapshot"
    else:
        name = "record_instance_daily_if_due"
    original: Callable[..., Any] = getattr(owner, name)

    def paused(*args: Any, **kwargs: Any) -> Any:
        if block_at == "collected":
            result = original(*args, **kwargs)
            entered.set()
            assert release.wait(WORKER_TIMEOUT)
            return result
        entered.set()
        assert release.wait(WORKER_TIMEOUT)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, paused)
    with TestClient(app) as client:
        try:
            assert entered.wait(WORKER_TIMEOUT)
            response = client.delete("/api/telemetry", headers=headers)
            assert response.status_code == 200
            assert storage.telemetry.instance_daily_count() == 0
            assert storage.telemetry.get_instance_current() is None
        finally:
            release.set()
            _settle_bootstrap(client, runtime)
        assert storage.telemetry.instance_daily_count() == 0
        assert storage.telemetry.get_instance_current() is None


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/telemetry/instance"), ("POST", "/api/telemetry/instance/refresh")],
)
@pytest.mark.parametrize("block_at", ["queued", "collected"])
def test_delete_invalidates_instance_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    block_at: str,
) -> None:
    settings = Settings(data_dir=tmp_path / "data", telemetry_enabled=True)
    app = create_app(settings)
    runtime: SessionRuntime = app.state.context.runtime
    storage: Storage = app.state.context.storage
    headers = {"Authorization": f"Bearer {app.state.context.tokens.issue().token}"}
    entered, release = Event(), Event()
    name = "build_instance" if block_at == "queued" else "collect_snapshot"
    original = getattr(instance_service, name)

    def paused(*args: Any, **kwargs: Any) -> Any:
        if block_at == "collected":
            result = original(*args, **kwargs)
            entered.set()
            assert release.wait(WORKER_TIMEOUT)
            return result
        entered.set()
        assert release.wait(WORKER_TIMEOUT)
        return original(*args, **kwargs)

    with TestClient(app) as client:
        _settle_bootstrap(client, runtime)
        assert client.delete("/api/telemetry", headers=headers).status_code == 200
        monkeypatch.setattr(instance_service, name, paused)
        with ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(client.request, method, path, headers=headers)
            try:
                assert entered.wait(WORKER_TIMEOUT)
                assert (
                    client.delete("/api/telemetry", headers=headers).status_code == 200
                )
            finally:
                release.set()
                response = request.result(WORKER_TIMEOUT)
        assert response.status_code == 200
        assert storage.telemetry.get_instance_current() is None
        assert storage.telemetry.instance_daily_count() == 0

        assert (
            client.post("/api/telemetry/instance/refresh", headers=headers).status_code
            == 200
        )
        assert storage.telemetry.get_instance_current() is not None
        assert instance_service.record_instance_daily_if_due(storage, settings)
        assert storage.telemetry.instance_daily_count() == 1


@pytest.mark.parametrize("block_at", ["queued", "collected"])
def test_delete_invalidates_background_refresh_without_blocking_cached_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, block_at: str
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data", telemetry_enabled=True))
    runtime: SessionRuntime = app.state.context.runtime
    storage: Storage = app.state.context.storage
    headers = {"Authorization": f"Bearer {app.state.context.tokens.issue().token}"}
    entered, release = Event(), Event()
    name = "compute_current_snapshot" if block_at == "queued" else "collect_snapshot"
    original = getattr(instance_service, name)

    def paused(*args: Any, **kwargs: Any) -> Any:
        if block_at == "collected":
            result = original(*args, **kwargs)
            entered.set()
            assert release.wait(WORKER_TIMEOUT)
            return result
        entered.set()
        assert release.wait(WORKER_TIMEOUT)
        return original(*args, **kwargs)

    with TestClient(app) as client:
        _settle_bootstrap(client, runtime)
        monkeypatch.setattr(instance_service, name, paused)
        assert client.portal is not None
        client.portal.call(runtime.schedule_instance_refresh)
        try:
            assert entered.wait(WORKER_TIMEOUT)
            assert (
                client.get("/api/telemetry/instance", headers=headers).status_code
                == 200
            )
            assert client.delete("/api/telemetry", headers=headers).status_code == 200
        finally:
            release.set()
            client.portal.call(_settle, runtime._instance_refresh_task)
        assert storage.telemetry.get_instance_current() is None
        assert storage.telemetry.instance_daily_count() == 0
        client.portal.call(runtime.schedule_instance_refresh)
        client.portal.call(_settle, runtime._instance_refresh_task)
        assert storage.telemetry.get_instance_current() is not None


def test_old_stale_cache_request_cannot_schedule_a_fresh_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data", telemetry_enabled=True))
    runtime: SessionRuntime = app.state.context.runtime
    storage: Storage = app.state.context.storage
    headers = {"Authorization": f"Bearer {app.state.context.tokens.issue().token}"}
    entered, release = Event(), Event()
    original = instance_service.build_instance

    def paused(*args: Any, **kwargs: Any) -> instance_service.TelemetryInstance:
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(WORKER_TIMEOUT)
        return result

    with TestClient(app) as client:
        _settle_bootstrap(client, runtime)
        raw = storage.telemetry.get_instance_current()
        assert raw is not None
        snapshot = InstanceSnapshot.model_validate_json(raw).model_copy(
            update={"observed_at": datetime.now(UTC) - timedelta(minutes=30)}
        )
        storage.telemetry.set_instance_current(snapshot.model_dump_json())
        monkeypatch.setattr(instance_service, "build_instance", paused)
        with ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(
                client.get, "/api/telemetry/instance", headers=headers
            )
            try:
                assert entered.wait(WORKER_TIMEOUT)
                assert (
                    client.delete("/api/telemetry", headers=headers).status_code == 200
                )
            finally:
                release.set()
                response = request.result(WORKER_TIMEOUT)
        assert response.json()["refresh_due"] is True
        assert client.portal is not None
        client.portal.call(_settle, runtime._instance_refresh_task)
        assert storage.telemetry.get_instance_current() is None
        assert storage.telemetry.instance_daily_count() == 0


def test_delete_invalidates_a_canceled_nl_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data", telemetry_enabled=True))
    runtime: SessionRuntime = app.state.context.runtime
    storage: Storage = app.state.context.storage
    headers = {"Authorization": f"Bearer {app.state.context.tokens.issue().token}"}
    entered, release, finished = Event(), Event(), Event()
    original = instance_service.build_instance
    summarizer: Any = Mock(summarize_instance=AsyncMock(return_value=[]))

    def paused(*args: Any, **kwargs: Any) -> instance_service.TelemetryInstance:
        entered.set()
        assert release.wait(WORKER_TIMEOUT)
        try:
            return original(*args, **kwargs)
        finally:
            finished.set()

    with TestClient(app) as client:
        _settle_bootstrap(client, runtime)
        assert client.delete("/api/telemetry", headers=headers).status_code == 200
        monkeypatch.setattr(instance_service, "build_instance", paused)
        assert client.portal is not None
        task = client.portal.start_task_soon(
            runtime._generate_instance_bullets, summarizer
        )
        try:
            assert entered.wait(WORKER_TIMEOUT)
            assert task.cancel()
            assert client.delete("/api/telemetry", headers=headers).status_code == 200
        finally:
            release.set()
            assert finished.wait(WORKER_TIMEOUT)
        assert storage.telemetry.get_instance_current() is None
        assert storage.telemetry.instance_daily_count() == 0


def test_delete_between_current_and_daily_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(data_dir=tmp_path / "data", telemetry_enabled=True)
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    original = storage.telemetry.upsert_instance_daily

    def delete_before_daily(**kwargs: Any) -> bool:
        aggregate.delete_all(storage)
        return original(**kwargs)

    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(
                storage.telemetry, "upsert_instance_daily", delete_before_daily
            )
            assert not instance_service.record_instance_daily_if_due(storage, settings)
        assert storage.telemetry.get_instance_current() is None
        assert storage.telemetry.instance_daily_count() == 0
        assert instance_service.record_instance_daily_if_due(storage, settings)
        assert storage.telemetry.get_instance_current() is not None
        assert storage.telemetry.instance_daily_count() == 1
    finally:
        storage.close()


def test_delete_between_cold_cache_read_and_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(data_dir=tmp_path / "data", telemetry_enabled=True)
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    original = instance_service._load_current

    def delete_after_read(storage: Storage) -> InstanceSnapshot | None:
        snapshot = original(storage)
        aggregate.delete_all(storage)
        return snapshot

    monkeypatch.setattr(instance_service, "_load_current", delete_after_read)
    try:
        instance_service.build_instance(storage, settings)
        assert storage.telemetry.get_instance_current() is None
        assert storage.telemetry.instance_daily_count() == 0
    finally:
        storage.close()


def test_delete_invalidates_queued_maintenance_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data", telemetry_enabled=True))
    runtime: SessionRuntime = app.state.context.runtime
    storage: Storage = app.state.context.storage
    headers = {"Authorization": f"Bearer {app.state.context.tokens.issue().token}"}
    entered, release = Event(), Event()
    recorded: list[bool] = []
    original = asyncio.to_thread

    async def pause_maintenance(
        func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        if func is not instance_service.record_instance_daily_if_due:
            return await original(func, *args, **kwargs)
        entered.set()
        assert await original(release.wait, WORKER_TIMEOUT)
        recorded.append(await original(func, *args, **kwargs))
        # Stop after this tick so a later, valid collection cannot hide the result.
        raise asyncio.CancelledError

    async def settle_maintenance() -> None:
        with suppress(asyncio.CancelledError):
            await _settle(runtime._telemetry_maintenance_task)

    monkeypatch.setattr(runtime_module, "TELEMETRY_MAINTENANCE_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(asyncio, "to_thread", pause_maintenance)
    with TestClient(app) as client:
        _settle_bootstrap(client, runtime)
        try:
            assert entered.wait(WORKER_TIMEOUT)
            assert client.delete("/api/telemetry", headers=headers).status_code == 200
        finally:
            release.set()
            assert client.portal is not None
            client.portal.call(settle_maintenance)
        assert recorded == [False]
        assert storage.telemetry.get_instance_current() is None
        assert storage.telemetry.instance_daily_count() == 0
