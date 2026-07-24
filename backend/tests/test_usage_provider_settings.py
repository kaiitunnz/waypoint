import pytest
from pydantic import ValidationError

from waypoint.settings import LumidUsageProviderConfig, Settings


def test_disabled_by_default() -> None:
    settings = Settings()
    assert settings.usage_providers == []
    assert settings.enabled_usage_providers() == []


def test_lumid_dispatch_and_defaults() -> None:
    settings = Settings(
        usage_providers=[{"id": "lumid", "type": "lumid", "token_env": "LUMID_TOKENS"}]
    )
    provider = settings.usage_providers[0]
    assert isinstance(provider, LumidUsageProviderConfig)
    assert provider.enabled is True
    assert provider.label == "Lumid"
    assert provider.refresh_interval_seconds == 300
    assert settings.enabled_usage_providers() == [provider]


def test_type_defaults_to_lumid() -> None:
    settings = Settings(usage_providers=[{"id": "lumid", "token_env": "LUMID_TOKENS"}])
    assert settings.usage_providers[0].type == "lumid"


def test_unknown_provider_type_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(usage_providers=[{"id": "x", "type": "webhook", "token_env": "TOK"}])


def test_missing_token_env_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(usage_providers=[{"id": "lumid", "type": "lumid"}])


@pytest.mark.parametrize("bad_env", ["1BAD", "has space", "with-dash", ""])
def test_invalid_token_env_name_rejected(bad_env: str) -> None:
    with pytest.raises(ValidationError):
        LumidUsageProviderConfig(id="lumid", token_env=bad_env)


@pytest.mark.parametrize("interval", [59, 3601, 0, -1])
def test_interval_out_of_bounds_rejected(interval: int) -> None:
    with pytest.raises(ValidationError):
        LumidUsageProviderConfig(
            id="lumid", token_env="LUMID_TOKENS", refresh_interval_seconds=interval
        )


def test_interval_bounds_accepted() -> None:
    for interval in (60, 3600):
        cfg = LumidUsageProviderConfig(
            id="lumid", token_env="LUMID_TOKENS", refresh_interval_seconds=interval
        )
        assert cfg.refresh_interval_seconds == interval


def test_duplicate_enabled_ids_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            usage_providers=[
                {"id": "dup", "token_env": "A"},
                {"id": "dup", "token_env": "B"},
            ]
        )


def test_duplicate_ids_allowed_when_one_disabled() -> None:
    settings = Settings(
        usage_providers=[
            {"id": "dup", "token_env": "A"},
            {"id": "dup", "token_env": "B", "enabled": False},
        ]
    )
    assert len(settings.enabled_usage_providers()) == 1


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        LumidUsageProviderConfig.model_validate(
            {"id": "lumid", "token_env": "TOK", "base_url": "https://evil"}
        )


def test_secret_never_a_yaml_literal() -> None:
    assert "token_env" in LumidUsageProviderConfig.model_fields
    assert "token" not in LumidUsageProviderConfig.model_fields
    assert "api_key" not in LumidUsageProviderConfig.model_fields


def test_bad_provider_id_rejected() -> None:
    with pytest.raises(ValidationError):
        LumidUsageProviderConfig(id="has space", token_env="TOK")
