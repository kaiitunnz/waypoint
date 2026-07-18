import pytest
from pydantic import ValidationError

from waypoint.notifications.contracts import IntentKind
from waypoint.settings import NotificationSettings, Settings

_ALL_KINDS: tuple[IntentKind, ...] = ("inbox", "plan_approval", "approval", "question")


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


def test_signals_default_all_true() -> None:
    signals = NotificationSettings().signals
    assert (signals.inbox, signals.plan, signals.permission, signals.question) == (
        True,
        True,
        True,
        True,
    )


def test_omitted_signals_block_allows_every_intent() -> None:
    settings = NotificationSettings()
    for kind in _ALL_KINDS:
        assert settings.allows_intent(kind) is True


@pytest.mark.parametrize(
    "key, intent_kind",
    [
        ("inbox", "inbox"),
        ("plan", "plan_approval"),
        ("permission", "approval"),
        ("question", "question"),
    ],
)
def test_each_signal_can_be_disabled(key: str, intent_kind: IntentKind) -> None:
    settings = NotificationSettings(signals={key: False})
    assert settings.allows_intent(intent_kind) is False
    # Disabling one signal leaves the others on.
    for other in _ALL_KINDS:
        if other != intent_kind:
            assert settings.allows_intent(other) is True


def test_unknown_signal_key_fails_validation() -> None:
    with pytest.raises(ValidationError):
        NotificationSettings(signals={"bogus": True})


def test_secret_never_a_yaml_literal() -> None:
    from waypoint.settings import TelegramChannelConfig

    # The token is named by env var; no field would hold the literal.
    assert "bot_token_env" in TelegramChannelConfig.model_fields
    assert "bot_token" not in TelegramChannelConfig.model_fields
