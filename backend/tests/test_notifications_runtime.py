"""Integration coverage for the runtime notification seams and status API."""

from pathlib import Path
from typing import Any

import httpx

from waypoint.api import create_app
from waypoint.notifications.contracts import (
    ChannelCapabilities,
    ChannelHealth,
    DeliveryResult,
    OutboundMessage,
)
from waypoint.runtime import SessionRuntime
from waypoint.schemas import InboxMarkdownBlockInput, InboxPostRequest
from waypoint.settings import (
    NotificationSettings,
    Settings,
    TelegramChannelConfig,
)
from waypoint.storage import Storage


class FakeChannel:
    def __init__(self, channel_id: str = "t") -> None:
        self.id = channel_id
        self.capabilities = ChannelCapabilities()
        self.sent: list[OutboundMessage] = []

    async def start(self) -> ChannelHealth:
        return ChannelHealth(channel_id=self.id, available=True)

    async def send(self, message: OutboundMessage) -> DeliveryResult:
        self.sent.append(message)
        return DeliveryResult(status="sent")

    async def stop(self) -> None:
        return None


def _notifications() -> NotificationSettings:
    return NotificationSettings(
        enabled=True,
        public_base_url="https://wp.example.ts.net",
        channels=[
            TelegramChannelConfig(
                id="t", bot_token_env="WAYPOINT_TEST_TG", chat_ids=["1"]
            )
        ],
    )


async def test_runtime_inbox_post_enqueues_and_delivers(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", notifications=_notifications())
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    assert runtime.notifications is not None
    fake = FakeChannel(channel_id="t")
    runtime.notifications._channels = {"t": fake}
    await runtime.notifications._prepare_channels()

    item = await runtime.post_inbox_item(
        InboxPostRequest(
            subject="Deploy?", blocks=[InboxMarkdownBlockInput(text="ready to ship")]
        )
    )
    # The item and its outbox row committed together.
    assert storage.get_inbox_item(item.id) is not None
    assert storage.count_deliveries_by_status() == {"queued": 1}

    await runtime.notifications._drain_once()
    assert len(fake.sent) == 1
    assert fake.sent[0].url == f"https://wp.example.ts.net/inbox/{item.id}"
    assert "Deploy?" in fake.sent[0].text
    assert storage.count_deliveries_by_status() == {"sent": 1}
    await runtime.notifications.stop()


async def test_runtime_inbox_post_without_notifications(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    assert runtime.notifications is None
    item = await runtime.post_inbox_item(
        InboxPostRequest(subject="Hi", blocks=[InboxMarkdownBlockInput(text="x")])
    )
    assert storage.get_inbox_item(item.id) is not None
    assert storage.count_deliveries_by_status() == {}


def _build(
    tmp_path: Path, notifications: NotificationSettings | None
) -> tuple[Any, str]:
    settings = Settings(
        data_dir=tmp_path / "data",
        notifications=notifications or NotificationSettings(),
    )
    app = create_app(settings)
    token = app.state.context.tokens.issue().token
    return app, token


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_status_requires_auth(tmp_path: Path) -> None:
    app, _ = _build(tmp_path, None)
    async with _client(app) as client:
        resp = await client.get("/api/notifications/status")
    assert resp.status_code == 401


async def test_status_disabled_by_default(tmp_path: Path) -> None:
    app, token = _build(tmp_path, None)
    async with _client(app) as client:
        resp = await client.get(
            "/api/notifications/status",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


async def test_status_enabled_is_redacted(tmp_path: Path) -> None:
    app, token = _build(tmp_path, _notifications())
    async with _client(app) as client:
        resp = await client.get(
            "/api/notifications/status",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    # No secret material anywhere in the payload.
    serialized = resp.text
    assert "WAYPOINT_TEST_TG" not in serialized
    assert "bot_token" not in serialized
