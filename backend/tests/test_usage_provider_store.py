"""UsageProviderStore: durability, reconciliation, identity key, digests."""

import os
import stat
from datetime import UTC, datetime
from pathlib import Path

from waypoint.schemas import (
    ProviderRateLimitUsage,
    ProviderUsageSnapshot,
    UsageWindow,
)
from waypoint.storage import Storage


def _snapshot(
    provider_id: str, account_key: str, email: str, pct: float
) -> ProviderUsageSnapshot:
    now = datetime.now(UTC)
    return ProviderUsageSnapshot(
        provider_id=provider_id,
        provider_type="lumid",
        account_key=account_key,
        account_label=email,
        snapshot=ProviderRateLimitUsage(
            source_id="lumid",
            updated_at=now,
            windows=[UsageWindow(id="lumid-five-hour", label="5h", used_percent=pct)],
        ),
        observed_at=now,
        last_success_at=now,
    )


def test_durable_load_roundtrip(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    store = storage.usage_providers
    key = store.account_key_digest("lumid", "a@x.com")
    cred = store.credential_digest("tok_a")
    store.upsert_snapshot(_snapshot("lumid", key, "a@x.com", 10.0), [cred])

    reopened = Storage(tmp_path / "db.sqlite").usage_providers
    loaded = reopened.load_snapshots("lumid")
    assert len(loaded) == 1
    assert loaded[0].account_label == "a@x.com"


def test_latest_replaces(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    store = storage.usage_providers
    key = store.account_key_digest("lumid", "a@x.com")
    cred = store.credential_digest("tok_a")
    store.upsert_snapshot(_snapshot("lumid", key, "a@x.com", 10.0), [cred])
    store.upsert_snapshot(_snapshot("lumid", key, "a@x.com", 42.0), [cred])
    loaded = store.load_snapshots("lumid")
    assert len(loaded) == 1
    assert loaded[0].snapshot.windows[0].used_percent == 42.0


def test_reconcile_removes_orphaned_account(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    store = storage.usage_providers
    key_a = store.account_key_digest("lumid", "a@x.com")
    key_b = store.account_key_digest("lumid", "b@x.com")
    cred_a = store.credential_digest("tok_a")
    cred_b = store.credential_digest("tok_b")
    store.upsert_snapshot(_snapshot("lumid", key_a, "a@x.com", 10.0), [cred_a])
    store.upsert_snapshot(_snapshot("lumid", key_b, "b@x.com", 20.0), [cred_b])
    # tok_b left the config.
    store.reconcile("lumid", {cred_a})
    loaded = {s.account_label for s in store.load_snapshots("lumid")}
    assert loaded == {"a@x.com"}


def test_reconcile_keeps_shared_account(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    store = storage.usage_providers
    key = store.account_key_digest("lumid", "a@x.com")
    cred_a = store.credential_digest("tok_a")
    cred_b = store.credential_digest("tok_b")
    store.upsert_snapshot(_snapshot("lumid", key, "a@x.com", 10.0), [cred_a, cred_b])
    # Only tok_a removed; tok_b still observes the account.
    store.reconcile("lumid", {cred_b})
    assert len(store.load_snapshots("lumid")) == 1


def test_remove_provider(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    store = storage.usage_providers
    key = store.account_key_digest("lumid", "a@x.com")
    cred = store.credential_digest("tok_a")
    store.upsert_snapshot(_snapshot("lumid", key, "a@x.com", 10.0), [cred])
    store.remove_provider("lumid")
    assert store.load_snapshots("lumid") == []
    assert store.list_provider_ids() == []


def test_identity_key_mode_0600_and_reuse(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    store = storage.usage_providers
    d1 = store.account_key_digest("lumid", "a@x.com")
    key_file = tmp_path / "usage_provider_identity.key"
    assert key_file.exists()
    mode = stat.S_IMODE(os.stat(key_file).st_mode)
    assert mode == 0o600
    # Same input -> stable digest (key reused).
    d2 = store.account_key_digest("lumid", "a@x.com")
    assert d1 == d2
    assert d1.startswith("hmac:v1:")
    # Different email -> different digest.
    assert store.account_key_digest("lumid", "b@x.com") != d1


def test_credential_digest_is_not_reversible_token(tmp_path: Path) -> None:
    store = Storage(tmp_path / "db.sqlite").usage_providers
    digest = store.credential_digest("lm_pat_live_secret")
    assert "lm_pat_live_secret" not in digest
    assert len(digest) == 64  # sha256 hex
