"""Notification service: durable outbox worker and delivery orchestration.

Owns channel lifecycle, fan-out of an intent to one outbox row per available
channel, and an async worker that claims due rows under a short lease, sends
outside the storage lock, and records ``sent`` / retry / ``failed`` with bounded
exponential backoff. A slow or unavailable channel never blocks an API request
or a coding session.
"""

import asyncio
import contextlib
import logging
import random
from datetime import UTC, datetime, timedelta

from waypoint.notifications.contracts import (
    ChannelHealth,
    DeliveryRecord,
    NotificationChannel,
    NotificationIntent,
    NotificationStatus,
)
from waypoint.notifications.registry import build_channels
from waypoint.notifications.render import render_message
from waypoint.settings import NotificationSettings
from waypoint.storage import Storage

log = logging.getLogger(__name__)

# Cadence the worker re-checks for due retries when idle (no wake).
_POLL_INTERVAL_SECONDS = 5.0
# A claimed row's lease; a crash before completion returns it to the queue.
_LEASE_SECONDS = 120.0
_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_CAP_SECONDS = 300.0
_CLEANUP_INTERVAL = timedelta(hours=1)


class NotificationService:
    def __init__(self, settings: NotificationSettings, storage: Storage) -> None:
        self._settings = settings
        self._storage = storage
        self._channels: dict[str, NotificationChannel] = {
            channel.id: channel for channel in build_channels(settings)
        }
        self._health: dict[str, ChannelHealth] = {}
        self._available: list[str] = []
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False
        self._last_cleanup: datetime | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        await self._prepare_channels()
        if not self._available:
            log.info("notification center enabled but no channel is available")
            return
        self._task = asyncio.create_task(self._run(), name="notification-worker")

    async def _prepare_channels(self) -> None:
        for channel in self._channels.values():
            try:
                health = await channel.start()
            except Exception as exc:  # noqa: BLE001 - never let a channel abort boot
                log.warning(
                    "notification channel failed to start",
                    extra={"channel_id": channel.id, "error": type(exc).__name__},
                )
                health = ChannelHealth(
                    channel_id=channel.id, available=False, detail="failed to start"
                )
            self._health[channel.id] = health
        self._available = [cid for cid, h in self._health.items() if h.available]
        if self._available:
            # Return any in-flight rows from a prior crash to the queue.
            self._storage.recover_stale_deliveries(datetime.now(UTC))

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for channel in self._channels.values():
            with contextlib.suppress(Exception):
                await channel.stop()

    def has_targets(self) -> bool:
        return bool(self._available)

    def targets(self) -> list[str]:
        return list(self._available)

    def delivery_rows(self, intent: NotificationIntent) -> list[tuple[str, str, str]]:
        """Fan an intent out to one ``(channel_id, dedupe_key, intent_json)``
        outbox row per available channel, for atomic persistence with the
        source record."""
        intent_json = intent.model_dump_json()
        return [
            (channel_id, intent.dedupe_key, intent_json)
            for channel_id in self._available
        ]

    def wake(self) -> None:
        self._wake.set()

    def status(self) -> NotificationStatus:
        return NotificationStatus(
            enabled=self._settings.enabled,
            channels=list(self._health.values()),
            counts=self._storage.count_deliveries_by_status(),
        )

    async def _run(self) -> None:
        while not self._stopping:
            try:
                did_work = await self._drain_once()
                await self._maybe_cleanup()
            except Exception:  # noqa: BLE001 - a transient error must not kill the loop
                log.exception("notification worker iteration failed")
                did_work = False
            if did_work:
                continue
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._wake.wait(), timeout=_POLL_INTERVAL_SECONDS
                )

    async def _drain_once(self) -> bool:
        now = datetime.now(UTC)
        rows = self._storage.claim_due_deliveries(
            now=now,
            limit=self._settings.worker_concurrency,
            lease_seconds=_LEASE_SECONDS,
        )
        if not rows:
            return False
        records: list[DeliveryRecord] = []
        for row in rows:
            try:
                records.append(
                    DeliveryRecord(
                        id=row["id"],
                        channel_id=row["channel_id"],
                        dedupe_key=row["dedupe_key"],
                        intent=NotificationIntent.model_validate_json(
                            row["intent_json"]
                        ),
                        status=row["status"],
                        attempts=row["attempts"],
                    )
                )
            except ValueError:
                # A malformed persisted intent can never succeed; retire it.
                self._storage.fail_delivery(
                    row["id"],
                    attempts=row["attempts"] + 1,
                    last_error="malformed intent",
                )
        await asyncio.gather(*(self._deliver(record, now) for record in records))
        return True

    async def _deliver(self, record: DeliveryRecord, now: datetime) -> None:
        attempts = record.attempts + 1
        channel = self._channels.get(record.channel_id)
        if channel is None or record.channel_id not in self._available:
            self._storage.fail_delivery(
                record.id, attempts=attempts, last_error="channel unavailable"
            )
            return
        message = render_message(
            record.intent,
            public_base_url=self._settings.public_base_url or "",
            preview_chars=self._settings.preview_chars,
            title_chars=self._settings.title_chars,
        )
        result = await channel.send(message)
        log.info(
            "notification delivery attempt",
            extra={
                "channel_id": record.channel_id,
                "kind": record.intent.kind,
                "delivery_id": record.id,
                "attempt": attempts,
                "result": result.status,
                "http_status": result.http_status,
            },
        )
        if result.status == "sent":
            self._storage.mark_delivery_sent(record.id, sent_at=datetime.now(UTC))
        elif result.status == "retry" and attempts < self._settings.max_attempts:
            delay = result.retry_after
            if delay is None:
                delay = _backoff_seconds(attempts)
            self._storage.requeue_delivery(
                record.id,
                next_attempt_at=now + timedelta(seconds=delay),
                attempts=attempts,
                last_error=result.error,
            )
        else:
            self._storage.fail_delivery(
                record.id,
                attempts=attempts,
                last_error=result.error or "max attempts reached",
            )

    async def _maybe_cleanup(self) -> None:
        now = datetime.now(UTC)
        if (
            self._last_cleanup is not None
            and now - self._last_cleanup < _CLEANUP_INTERVAL
        ):
            return
        self._last_cleanup = now
        cutoff = now - timedelta(days=self._settings.retention_days)
        removed = self._storage.delete_old_deliveries(cutoff)
        if removed:
            log.info("notification retention cleanup", extra={"removed": removed})


def _backoff_seconds(attempts: int) -> float:
    base = min(_BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)), _BACKOFF_CAP_SECONDS)
    return base + random.uniform(0, _BACKOFF_BASE_SECONDS)
