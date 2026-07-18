import pytest
from pydantic import ValidationError

from waypoint.settings import NotificationSettings, Settings


def test_disabled_by_default() -> None:
    settings = Settings()
    assert settings.notifications.enabled is False
    assert settings.notifications.channels == []
    assert settings.notifications.enabled_channels() == []


def test_enabled_requires_public_base_url() -> None:
    with pytest.raises(ValidationError):
        NotificationSettings(enabled=True)


def test_public_base_url_is_normalized() -> None:
    settings = NotificationSettings(
        enabled=True, public_base_url="https://waypoint.example.ts.net/"
    )
    assert settings.public_base_url == "https://waypoint.example.ts.net"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://waypoint.example.ts.net",
        "https://user:pw@waypoint.example.ts.net",
        "https://waypoint.example.ts.net/?token=x",
        "https://waypoint.example.ts.net/#frag",
        "https:///no-host",
    ],
)
def test_public_base_url_rejects_bad_forms(url: str) -> None:
    with pytest.raises(ValidationError):
        NotificationSettings(enabled=True, public_base_url=url)


def test_channel_dispatch_and_unknown_type() -> None:
    settings = NotificationSettings(
        enabled=True,
        public_base_url="https://wp.example.ts.net",
        channels=[
            {
                "id": "personal",
                "type": "telegram",
                "bot_token_env": "WAYPOINT_TELEGRAM_BOT_TOKEN",
                "chat_ids": ["123", "-100999"],
            }
        ],
    )
    assert settings.channels[0].chat_ids == ["123", "-100999"]
    with pytest.raises(ValidationError):
        NotificationSettings(
            enabled=True,
            public_base_url="https://wp.example.ts.net",
            channels=[{"id": "x", "type": "whatsapp", "bot_token_env": "Y"}],
        )


def test_duplicate_enabled_channel_ids_rejected() -> None:
    with pytest.raises(ValidationError):
        NotificationSettings(
            enabled=True,
            public_base_url="https://wp.example.ts.net",
            channels=[
                {"id": "dup", "type": "telegram", "bot_token_env": "A"},
                {"id": "dup", "type": "telegram", "bot_token_env": "B"},
            ],
        )


def test_secret_never_a_yaml_literal() -> None:
    from waypoint.settings import TelegramChannelConfig

    # The token is named by env var; no field would hold the literal.
    assert "bot_token_env" in TelegramChannelConfig.model_fields
    assert "bot_token" not in TelegramChannelConfig.model_fields
