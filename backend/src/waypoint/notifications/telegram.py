"""Telegram outbound channel.

Delivers via the Bot API ``sendMessage`` with a URL-only inline keyboard, so a
click opens Waypoint's own authenticated UI and the channel stays one-way. The
bot token is read once from an environment variable at startup and travels only
inside the HTTPS request URL — never persisted, logged, or returned by status.
"""

import logging
import os
from urllib.parse import urlsplit

import aiohttp

from waypoint.notifications.contracts import (
    ChannelCapabilities,
    ChannelHealth,
    DeliveryResult,
    OutboundMessage,
)

log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
# Telegram signals a rate-limit with HTTP 429; 5xx and network errors are also
# retryable. Everything else (400/401/403 — bad token, chat never started the
# bot, malformed request) is terminal for this row.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _valid_button_url(url: str) -> bool:
    """Whether Telegram will accept ``url`` as an inline-keyboard button URL.

    Telegram rejects button URLs without a real host (e.g. an internal alias
    like ``http://h0:8797`` — "Wrong HTTP URL"). A dotted hostname is a good
    proxy for a public origin; anything else falls back to a link in the
    message text, which Telegram accepts for any origin and auto-links.
    """
    parts = urlsplit(url)
    return (
        parts.scheme in ("http", "https")
        and parts.hostname is not None
        and "." in parts.hostname
    )


class TelegramChannel:
    def __init__(
        self,
        *,
        channel_id: str,
        bot_token_env: str,
        chat_ids: list[str],
        http_timeout_seconds: float,
    ) -> None:
        self.id = channel_id
        self.capabilities = ChannelCapabilities(supports_inbound=False)
        self._bot_token_env = bot_token_env
        self._chat_ids = list(chat_ids)
        self._timeout = aiohttp.ClientTimeout(total=http_timeout_seconds)
        self._token: str | None = None
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> ChannelHealth:
        token = os.environ.get(self._bot_token_env, "").strip()
        if not token:
            return ChannelHealth(
                channel_id=self.id,
                available=False,
                detail=f"token environment variable {self._bot_token_env} is unset",
            )
        if not self._chat_ids:
            return ChannelHealth(
                channel_id=self.id,
                available=False,
                detail="no chat ids configured",
            )
        self._token = token
        self._session = aiohttp.ClientSession(timeout=self._timeout)
        return ChannelHealth(channel_id=self.id, available=True)

    async def stop(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._token = None

    async def send(self, message: OutboundMessage) -> DeliveryResult:
        if self._session is None or self._token is None:
            return DeliveryResult(status="failed", error="channel not started")
        url = f"{_API_BASE}/bot{self._token}/sendMessage"
        # An inline URL button is nicer, but Telegram rejects a button whose URL
        # is not a valid public HTTP(S) URL — which would fail the whole send.
        # For such origins, put the deep link in the text (Telegram auto-links
        # it) so delivery succeeds regardless of the configured origin.
        if _valid_button_url(message.url):
            text = message.text
            reply_markup: dict[str, object] | None = {
                "inline_keyboard": [
                    [{"text": message.button_label, "url": message.url}]
                ]
            }
        else:
            text = f"{message.text}\n\n{message.button_label}: {message.url}"
            reply_markup = None
        # Deliver to every configured chat id. A partial failure requeues the
        # whole row (at-least-once): already-delivered chats may see a duplicate
        # on retry, which is the documented trade-off for durable delivery.
        result: DeliveryResult = DeliveryResult(status="sent")
        for chat_id in self._chat_ids:
            body: dict[str, object] = {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            }
            if reply_markup is not None:
                body["reply_markup"] = reply_markup
            outcome = await self._send_one(url, body)
            if outcome.status == "sent":
                continue
            # Prefer surfacing a retry over a terminal failure so a single bad
            # chat id does not permanently drop delivery to the healthy ones.
            if outcome.status == "retry" or result.status != "retry":
                result = outcome
            if outcome.status == "retry":
                return result
        return result

    async def _send_one(self, url: str, body: dict[str, object]) -> DeliveryResult:
        assert self._session is not None
        try:
            async with self._session.post(url, json=body) as response:
                status = response.status
                if status == 200:
                    return DeliveryResult(status="sent", http_status=200)
                retry_after = await self._retry_after(response)
                retryable = status in _RETRYABLE_STATUS
                log.warning(
                    "telegram send failed",
                    extra={
                        "channel_id": self.id,
                        "http_status": status,
                        "retryable": retryable,
                    },
                )
                return DeliveryResult(
                    status="retry" if retryable else "failed",
                    retry_after=retry_after,
                    http_status=status,
                    error=f"telegram http {status}",
                )
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.warning(
                "telegram send error",
                extra={"channel_id": self.id, "error": type(exc).__name__},
            )
            return DeliveryResult(status="retry", error=type(exc).__name__)

    @staticmethod
    async def _retry_after(response: aiohttp.ClientResponse) -> float | None:
        try:
            payload = await response.json()
        except (aiohttp.ClientError, ValueError):
            return None
        if isinstance(payload, dict):
            parameters = payload.get("parameters")
            if isinstance(parameters, dict):
                value = parameters.get("retry_after")
                if isinstance(value, (int, float)):
                    return float(value)
        return None
