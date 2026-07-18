"""Integration coverage for the runtime notification seams and status API."""

from pathlib import Path
from typing import Any

import httpx

from waypoint.api import create_app
from waypoint.backends.events import InteractionChoice, InteractionEnvelope
from waypoint.notifications import NotificationService
from waypoint.notifications.contracts import (
    ChannelCapabilities,
    ChannelHealth,
    DeliveryResult,
    OutboundMessage,
)
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    EventKind,
    InboxMarkdownBlockInput,
    InboxPostRequest,
    SessionStatus,
)
from waypoint.settings import (
    NotificationSettings,
    NotificationSignalSettings,
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


def _notifications(
    signals: NotificationSignalSettings | None = None,
) -> NotificationSettings:
    return NotificationSettings(
        enabled=True,
        public_base_url="https://wp.example.ts.net",
        signals=signals or NotificationSignalSettings(),
        channels=[
            TelegramChannelConfig(
                id="t", bot_token_env="WAYPOINT_TEST_TG", chat_ids=["1"]
            )
        ],
    )


async def _runtime_with_channel(
    tmp_path: Path, notifications: NotificationSettings
) -> tuple[SessionRuntime, Storage, NotificationService]:
    settings = Settings(data_dir=tmp_path / "data", notifications=notifications)
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    service = runtime.notifications
    assert service is not None
    service._channels = {"t": FakeChannel(channel_id="t")}
    await service._prepare_channels()
    return runtime, storage, service


async def _emit_approval(runtime: SessionRuntime, session_id: str) -> None:
    envelope = InteractionEnvelope(
        kind="approval",
        request_id="req1",
        title="Approve Bash",
        body="pytest -q",
        choices=[InteractionChoice(label="approve")],
    )
    await runtime._emit_adapter_event(
        session_id,
        EventKind.APPROVAL_REQUEST,
        "approval",
        {"interaction": envelope.to_metadata()},
        SessionStatus.WAITING_INPUT,
    )


async def test_interaction_suppressed_by_active_presence(tmp_path: Path) -> None:
    runtime, storage, service = await _runtime_with_channel(tmp_path, _notifications())
    try:
        runtime.session_presence.touch("sessX", "viewer-1")
        await _emit_approval(runtime, "sessX")
        # The event is durable, but presence blocked the outbox row.
        assert len(storage.list_events("sessX")) == 1
        assert storage.count_deliveries_by_status() == {}
    finally:
        await service.stop()


async def test_interaction_delivered_when_not_present(tmp_path: Path) -> None:
    runtime, storage, service = await _runtime_with_channel(tmp_path, _notifications())
    try:
        await _emit_approval(runtime, "sessX")
        assert len(storage.list_events("sessX")) == 1
        assert storage.count_deliveries_by_status() == {"queued": 1}
    finally:
        await service.stop()


async def test_interaction_delivered_after_lease_released(tmp_path: Path) -> None:
    runtime, storage, service = await _runtime_with_channel(tmp_path, _notifications())
    try:
        runtime.session_presence.touch("sessX", "viewer-1")
        runtime.session_presence.release("sessX", "viewer-1")
        await _emit_approval(runtime, "sessX")
        assert storage.count_deliveries_by_status() == {"queued": 1}
    finally:
        await service.stop()


async def test_disabled_permission_signal_persists_event_without_delivery(
    tmp_path: Path,
) -> None:
    runtime, storage, service = await _runtime_with_channel(
        tmp_path, _notifications(NotificationSignalSettings(permission=False))
    )
    try:
        await _emit_approval(runtime, "sessX")
        assert len(storage.list_events("sessX")) == 1
        assert storage.count_deliveries_by_status() == {}
    finally:
        await service.stop()


async def test_inbox_not_affected_by_session_presence(tmp_path: Path) -> None:
    runtime, storage, service = await _runtime_with_channel(tmp_path, _notifications())
    try:
        runtime.session_presence.touch("sender", "viewer-1")
        item = await runtime.post_inbox_item(
            InboxPostRequest(
                subject="Deploy?",
                from_session_id="sender",
                blocks=[InboxMarkdownBlockInput(text="ready")],
            )
        )
        assert storage.get_inbox_item(item.id) is not None
        # Inbox is never presence-suppressed even when its origin session is open.
        assert storage.count_deliveries_by_status() == {"queued": 1}
    finally:
        await service.stop()


async def test_disabled_inbox_signal_persists_item_without_delivery(
    tmp_path: Path,
) -> None:
    runtime, storage, service = await _runtime_with_channel(
        tmp_path, _notifications(NotificationSignalSettings(inbox=False))
    )
    try:
        item = await runtime.post_inbox_item(
            InboxPostRequest(subject="Hi", blocks=[InboxMarkdownBlockInput(text="x")])
        )
        assert storage.get_inbox_item(item.id) is not None
        assert storage.count_deliveries_by_status() == {}
    finally:
        await service.stop()


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
