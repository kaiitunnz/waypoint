from typing import Any

from waypoint.notifications.contracts import OutboundMessage
from waypoint.notifications.telegram import TelegramChannel


class _FakeResponse:
    def __init__(self, status: int, payload: dict[str, Any] | None = None) -> None:
        self.status = status
        self._payload = payload or {}

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        return self._payload


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
        self.calls.append((url, json))
        return self._response

    async def close(self) -> None:
        return None


def _channel(chat_ids: list[str] | None = None) -> TelegramChannel:
    return TelegramChannel(
        channel_id="tg",
        bot_token_env="WAYPOINT_TEST_TG_TOKEN",
        chat_ids=chat_ids or ["12345"],
        http_timeout_seconds=10.0,
    )


def _wire(channel: TelegramChannel, response: _FakeResponse) -> _FakeSession:
    session = _FakeSession(response)
    channel._session = session  # type: ignore[assignment]
    channel._token = "SECRET-TOKEN"
    return session


def _message() -> OutboundMessage:
    return OutboundMessage(
        intent_id="inbox:1",
        text="Inbox: hi",
        url="https://wp.example.ts.net/inbox/1",
        button_label="Open inbox item",
    )


async def test_start_without_env_is_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("WAYPOINT_TEST_TG_TOKEN", raising=False)
    channel = _channel()
    health = await channel.start()
    assert health.available is False
    assert "WAYPOINT_TEST_TG_TOKEN" in (health.detail or "")
    # The (absent) token value is never exposed; only the variable name is.
    assert "SECRET" not in (health.detail or "")


async def test_send_builds_request(monkeypatch) -> None:
    channel = _channel(chat_ids=["12345"])
    session = _wire(channel, _FakeResponse(200, {"ok": True}))
    result = await channel.send(_message())
    assert result.status == "sent"
    url, body = session.calls[0]
    assert url == "https://api.telegram.org/botSECRET-TOKEN/sendMessage"
    assert body["chat_id"] == "12345"
    assert body["disable_web_page_preview"] is True
    keyboard = body["reply_markup"]["inline_keyboard"]
    assert keyboard == [[{"text": "Open inbox item", "url": _message().url}]]


async def test_send_to_multiple_chats(monkeypatch) -> None:
    channel = _channel(chat_ids=["1", "2", "3"])
    session = _wire(channel, _FakeResponse(200, {"ok": True}))
    result = await channel.send(_message())
    assert result.status == "sent"
    assert [body["chat_id"] for _, body in session.calls] == ["1", "2", "3"]


async def test_rate_limit_is_retryable_with_retry_after() -> None:
    channel = _channel()
    _wire(channel, _FakeResponse(429, {"parameters": {"retry_after": 30}}))
    result = await channel.send(_message())
    assert result.status == "retry"
    assert result.retry_after == 30.0


async def test_client_error_is_terminal() -> None:
    channel = _channel()
    _wire(channel, _FakeResponse(400, {"description": "chat not found"}))
    result = await channel.send(_message())
    assert result.status == "failed"
    assert result.http_status == 400


async def test_send_before_start_fails() -> None:
    channel = _channel()
    result = await channel.send(_message())
    assert result.status == "failed"
