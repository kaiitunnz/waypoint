from datetime import UTC, datetime, timedelta

from waypoint.auth import TokenStore
from waypoint.settings import Settings
from waypoint.storage import Storage


def make_store(tmp_path) -> TokenStore:
    settings = Settings(data_dir=tmp_path / "data", token_ttl_seconds=60 * 60 * 24)
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    return TokenStore(settings, storage)


def test_validate_refreshes_when_more_than_half_consumed(tmp_path) -> None:
    store = make_store(tmp_path)
    issued = store.issue()
    # Backdate the token so well over half the TTL has elapsed.
    aged = datetime.now(UTC) - timedelta(hours=18)
    store.storage.refresh_token_expiry(issued.token, aged + timedelta(hours=24))
    before = store.storage.get_token_expiry(issued.token)
    assert before is not None

    assert store.validate(issued.token) is True

    after = store.storage.get_token_expiry(issued.token)
    assert after is not None
    assert after > before


def test_validate_does_not_refresh_when_freshly_issued(tmp_path) -> None:
    store = make_store(tmp_path)
    issued = store.issue()
    before = store.storage.get_token_expiry(issued.token)

    assert store.validate(issued.token) is True

    after = store.storage.get_token_expiry(issued.token)
    assert after == before


def test_validate_rejects_expired_token(tmp_path) -> None:
    store = make_store(tmp_path)
    issued = store.issue()
    store.storage.refresh_token_expiry(
        issued.token, datetime.now(UTC) - timedelta(seconds=1)
    )

    assert store.validate(issued.token) is False
    assert store.storage.get_token_expiry(issued.token) is None
