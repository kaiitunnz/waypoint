import asyncio
from datetime import UTC, datetime

from waypoint.notifications.contracts import (
    ChannelCapabilities,
    ChannelHealth,
    DeliveryResult,
    OutboundMessage,
)
from waypoint.notifications.render import intent_from_inbox_item
from waypoint.notifications.service import NotificationService
from waypoint.settings import NotificationSettings
from waypoint.storage import Storage


class FakeChannel:
    def __init__(
        self,
        channel_id: str = "fake",
        results: list[DeliveryResult] | None = None,
        available: bool = True,
    ) -> None:
        self.id = channel_id
        self.capabilities = ChannelCapabilities()
        self.sent: list[OutboundMessage] = []
        self._results = list(results or [])
        self._available = available
        self.stopped = False

    async def start(self) -> ChannelHealth:
        return ChannelHealth(
            channel_id=self.id,
            available=self._available,
            detail=None if self._available else "unavailable",
        )

    async def send(self, message: OutboundMessage) -> DeliveryResult:
        self.sent.append(message)
        return self._results.pop(0) if self._results else DeliveryResult(status="sent")

    async def stop(self) -> None:
        self.stopped = True


def _settings(**kwargs) -> NotificationSettings:
    return NotificationSettings(
        enabled=True,
        public_base_url="https://wp.example.ts.net",
        **kwargs,
    )


async def _service_with(
    tmp_path, channel: FakeChannel, **kwargs
) -> tuple[NotificationService, Storage]:
    # Prepare channels without spawning the background worker, so tests drive
    # ``_drain_once`` deterministically (no loop racing the assertions).
    storage = Storage(tmp_path / "waypoint.db")
    service = NotificationService(_settings(**kwargs), storage)
    service._channels = {channel.id: channel}
    await service._prepare_channels()
    return service, storage


def _enqueue(
    storage: Storage, service: NotificationService, subject: str = "s"
) -> None:
    storage.create_inbox_item_with_notifications(
        from_session_id="",
        from_label=None,
        subject=subject,
        blocks=[],
        make_deliveries=lambda item: service.delivery_rows(
            intent_from_inbox_item(item)
        ),
    )


async def test_successful_delivery(tmp_path) -> None:
    channel = FakeChannel()
    service, storage = await _service_with(tmp_path, channel)
    try:
        assert service.has_targets() is True
        _enqueue(storage, service)
        did_work = await service._drain_once()
        assert did_work is True
        assert len(channel.sent) == 1
        assert channel.sent[0].url.startswith("https://wp.example.ts.net/inbox/")
        assert storage.count_deliveries_by_status() == {"sent": 1}
    finally:
        await service.stop()


async def test_retry_then_not_yet_due(tmp_path) -> None:
    channel = FakeChannel(results=[DeliveryResult(status="retry", error="net")])
    service, storage = await _service_with(tmp_path, channel)
    try:
        _enqueue(storage, service)
        await service._drain_once()
        assert storage.count_deliveries_by_status() == {"queued": 1}
        # Backoff pushes next_attempt into the future, so an immediate re-drain
        # claims nothing.
        assert await service._drain_once() is False
    finally:
        await service.stop()


async def test_non_retryable_fails_immediately(tmp_path) -> None:
    channel = FakeChannel(
        results=[DeliveryResult(status="failed", http_status=400, error="bad request")]
    )
    service, storage = await _service_with(tmp_path, channel)
    try:
        _enqueue(storage, service)
        await service._drain_once()
        assert storage.count_deliveries_by_status() == {"failed": 1}
    finally:
        await service.stop()


async def test_retry_after_is_honored(tmp_path) -> None:
    channel = FakeChannel(
        results=[DeliveryResult(status="retry", retry_after=45, http_status=429)]
    )
    service, storage = await _service_with(tmp_path, channel)
    try:
        _enqueue(storage, service)
        now = datetime.now(UTC)
        await service._drain_once()
        # Not due within retry_after.
        from datetime import timedelta

        assert (
            storage.claim_due_deliveries(
                now=now + timedelta(seconds=44), limit=10, lease_seconds=1
            )
            == []
        )
        assert (
            len(
                storage.claim_due_deliveries(
                    now=now + timedelta(seconds=46), limit=10, lease_seconds=1
                )
            )
            == 1
        )
    finally:
        await service.stop()


async def test_max_attempts_exhausted_fails(tmp_path) -> None:
    channel = FakeChannel(results=[DeliveryResult(status="retry", error="net")])
    service, storage = await _service_with(tmp_path, channel, max_attempts=1)
    try:
        _enqueue(storage, service)
        await service._drain_once()
        # attempts (1) is not < max_attempts (1): the row is retired, not requeued.
        assert storage.count_deliveries_by_status() == {"failed": 1}
    finally:
        await service.stop()


async def test_unavailable_channel_has_no_targets(tmp_path) -> None:
    channel = FakeChannel(available=False)
    service, storage = await _service_with(tmp_path, channel)
    try:
        assert service.has_targets() is False
        assert service.targets() == []
        status = service.status()
        assert status.enabled is True
        assert status.channels[0].available is False
    finally:
        await service.stop()


async def test_concurrency_limits_claim_batch(tmp_path) -> None:
    channel = FakeChannel()
    service, storage = await _service_with(tmp_path, channel, worker_concurrency=2)
    try:
        for i in range(5):
            _enqueue(storage, service, subject=f"s{i}")
        await service._drain_once()
        # Only worker_concurrency rows are claimed and sent per tick.
        assert len(channel.sent) == 2
        counts = storage.count_deliveries_by_status()
        assert counts.get("sent") == 2
        assert counts.get("queued") == 3
    finally:
        await service.stop()


async def test_stop_closes_channels(tmp_path) -> None:
    channel = FakeChannel()
    service, _ = await _service_with(tmp_path, channel)
    await service.stop()
    assert channel.stopped is True
    assert service._task is None


async def test_recovers_stale_sending_on_start(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    # Enqueue a row and claim it, simulating a crash mid-send.
    item = storage.create_inbox_item_with_notifications(
        from_session_id="",
        from_label=None,
        subject="s",
        blocks=[],
        make_deliveries=lambda created: [("fake", f"inbox:{created.id}", "{}")],
    )
    assert item is not None
    storage.claim_due_deliveries(now=datetime.now(UTC), limit=10, lease_seconds=120)
    assert storage.count_deliveries_by_status() == {"sending": 1}
    service = NotificationService(_settings(), storage)
    service._channels = {"fake": FakeChannel()}
    await service._prepare_channels()  # recovers the stale 'sending' row
    assert storage.count_deliveries_by_status() == {"queued": 1}


async def test_worker_loop_delivers_end_to_end(tmp_path) -> None:
    channel = FakeChannel()
    storage = Storage(tmp_path / "waypoint.db")
    service = NotificationService(_settings(), storage)
    service._channels = {channel.id: channel}
    await service.start()  # spawns the background worker
    try:
        _enqueue(storage, service)
        service.wake()
        for _ in range(100):
            if storage.count_deliveries_by_status().get("sent") == 1:
                break
            await asyncio.sleep(0.01)
        assert storage.count_deliveries_by_status() == {"sent": 1}
        assert len(channel.sent) == 1
    finally:
        await service.stop()
