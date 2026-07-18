"""Channel-config → channel-instance construction.

Maps each enabled config entry to its :class:`NotificationChannel`
implementation by ``type``. Adding a new channel registers one factory here; no
notification producer changes.
"""

from waypoint.notifications.contracts import NotificationChannel
from waypoint.notifications.telegram import TelegramChannel
from waypoint.settings import NotificationSettings, TelegramChannelConfig


def build_channels(settings: NotificationSettings) -> list[NotificationChannel]:
    channels: list[NotificationChannel] = []
    for config in settings.enabled_channels():
        if isinstance(config, TelegramChannelConfig):
            channels.append(
                TelegramChannel(
                    channel_id=config.id,
                    bot_token_env=config.bot_token_env,
                    chat_ids=config.chat_ids,
                    http_timeout_seconds=settings.http_timeout_seconds,
                )
            )
    return channels
