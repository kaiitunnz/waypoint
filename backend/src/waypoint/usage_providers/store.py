"""Durable latest-snapshot store + versioned identity key for usage providers.

Shares the one :class:`~waypoint.storage.Storage` sqlite connection and lock,
mirroring :class:`~waypoint.telemetry.store.TelemetryStore`. It persists only
normalized snapshots, provider health, and non-reversible HMAC digests — never a
raw PAT, authorization header, reversible token, or raw upstream body (NFR1).

Identity key: a random 256-bit key minted once into a mode-0600 file under the
data directory. ``account_key_digest`` is ``hmac:v<n>:<hex>`` over
``provider_id + "\\0" + normalized_email``; ``credential_digest`` is an HMAC over
the raw PAT, held only to reconcile which configured credentials still observe an
account. Removing the key file starts a new version; old rows are replaced after
a successful sweep.
"""

import hashlib
import hmac
import secrets
import sqlite3
import threading
from pathlib import Path

from waypoint.schemas import ProviderUsageSnapshot

_IDENTITY_KEY_FILENAME = "usage_provider_identity.key"
_KEY_VERSION = 1


class UsageProviderStore:
    """Owns the ``usage_provider_*`` tables and the identity key file."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        lock: threading.RLock,
        data_dir: Path,
    ) -> None:
        self._conn = conn
        self._lock = lock
        self._data_dir = data_dir
        self._identity_key: bytes | None = None

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS usage_provider_snapshots (
                  provider_id         TEXT NOT NULL,
                  account_key_digest  TEXT NOT NULL,
                  snapshot_json       TEXT NOT NULL,
                  observed_at         TEXT NOT NULL,
                  last_success_at     TEXT NOT NULL,
                  PRIMARY KEY (provider_id, account_key_digest)
                );

                -- Which configured credentials currently observe an account.
                -- Holds only HMAC digests; never a PAT. Used to reconcile /
                -- expire accounts when a credential leaves the config.
                CREATE TABLE IF NOT EXISTS usage_provider_credentials (
                  provider_id         TEXT NOT NULL,
                  account_key_digest  TEXT NOT NULL,
                  credential_digest   TEXT NOT NULL,
                  PRIMARY KEY (provider_id, account_key_digest, credential_digest)
                );
                """)
            self._conn.commit()

    # ── identity key + digests ──

    def _identity_key_path(self) -> Path:
        return self._data_dir / _IDENTITY_KEY_FILENAME

    def _load_identity_key(self) -> bytes:
        if self._identity_key is not None:
            return self._identity_key
        path = self._identity_key_path()
        if path.exists():
            self._identity_key = path.read_bytes()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            key = secrets.token_bytes(32)
            path.write_bytes(key)
            path.chmod(0o600)
            self._identity_key = key
        return self._identity_key

    def account_key_digest(self, provider_id: str, normalized_email: str) -> str:
        key = self._load_identity_key()
        message = f"{provider_id}\0{normalized_email}".encode()
        digest = hmac.new(key, message, hashlib.sha256).hexdigest()
        return f"hmac:v{_KEY_VERSION}:{digest}"

    def credential_digest(self, token: str) -> str:
        key = self._load_identity_key()
        return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()

    # ── snapshot persistence ──

    def upsert_snapshot(
        self, snapshot: ProviderUsageSnapshot, credential_digests: list[str]
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO usage_provider_snapshots
                  (provider_id, account_key_digest, snapshot_json,
                   observed_at, last_success_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider_id, account_key_digest) DO UPDATE SET
                  snapshot_json=excluded.snapshot_json,
                  observed_at=excluded.observed_at,
                  last_success_at=excluded.last_success_at
                """,
                (
                    snapshot.provider_id,
                    snapshot.account_key,
                    snapshot.model_dump_json(),
                    snapshot.observed_at.isoformat(),
                    snapshot.last_success_at.isoformat(),
                ),
            )
            for cred in credential_digests:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO usage_provider_credentials
                      (provider_id, account_key_digest, credential_digest)
                    VALUES (?, ?, ?)
                    """,
                    (snapshot.provider_id, snapshot.account_key, cred),
                )
            self._conn.commit()

    def load_snapshots(self, provider_id: str) -> list[ProviderUsageSnapshot]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT snapshot_json FROM usage_provider_snapshots WHERE provider_id = ?",
                (provider_id,),
            ).fetchall()
        return [
            ProviderUsageSnapshot.model_validate_json(r["snapshot_json"]) for r in rows
        ]

    def reconcile(self, provider_id: str, active_credential_digests: set[str]) -> None:
        """Drop credential associations whose credential left the config, then
        delete any account with no remaining configured credential."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT account_key_digest, credential_digest "
                "FROM usage_provider_credentials WHERE provider_id = ?",
                (provider_id,),
            ).fetchall()
            for row in rows:
                if row["credential_digest"] not in active_credential_digests:
                    self._conn.execute(
                        "DELETE FROM usage_provider_credentials "
                        "WHERE provider_id = ? AND account_key_digest = ? "
                        "AND credential_digest = ?",
                        (
                            provider_id,
                            row["account_key_digest"],
                            row["credential_digest"],
                        ),
                    )
            self._conn.execute(
                """
                DELETE FROM usage_provider_snapshots
                WHERE provider_id = ?
                  AND account_key_digest NOT IN (
                    SELECT account_key_digest FROM usage_provider_credentials
                    WHERE provider_id = ?
                  )
                """,
                (provider_id, provider_id),
            )
            self._conn.commit()

    def remove_provider(self, provider_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM usage_provider_snapshots WHERE provider_id = ?",
                (provider_id,),
            )
            self._conn.execute(
                "DELETE FROM usage_provider_credentials WHERE provider_id = ?",
                (provider_id,),
            )
            self._conn.commit()

    def list_provider_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT provider_id FROM usage_provider_snapshots"
            ).fetchall()
        return [r["provider_id"] for r in rows]
