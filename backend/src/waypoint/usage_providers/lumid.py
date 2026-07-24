"""The official Lumid usage provider.

All Lumid-specific HTTP, bearer authentication, endpoint paths, and response
parsing live here; nothing outside this module references Lumid. The provider
identifies each configured PAT via ``/api/v1/user`` and publishes only that
owner's matching ``/api/v1/admin/claude-user-usage`` row.

Secrets never leave process memory: PATs are read from the environment at
refresh, held only for the request, and never logged, persisted, or returned.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from waypoint.schemas import (
    ProviderErrorState,
    ProviderRateLimitUsage,
    ProviderRefreshResult,
    ProviderUsageMetadata,
    ProviderUsageSnapshot,
    ProviderUsageStatus,
    UsageWindow,
)
from waypoint.usage_providers.store import UsageProviderStore

log = logging.getLogger(__name__)

_BASE_URL = "https://lum.id"
_USER_PATH = "/api/v1/user"
_USAGE_PATH = "/api/v1/admin/claude-user-usage"
_FIVE_HOUR_MINUTES = 300
_SEVEN_DAY_MINUTES = 7 * 24 * 60
_MAX_CONCURRENCY = 4


# ── Lumid response envelopes (extra ignored) ──


class _UserData(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: str


class _UserEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ret_code: int
    data: _UserData | None = None


class _UsageRow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: str
    five_hour_tokens: int
    seven_day_tokens: int
    five_hour_pct: float
    seven_day_pct: float
    requests_7d: int | None = None
    last_ts: datetime | None = None
    five_hour_resets_at: datetime | None = None
    seven_day_resets_at: datetime | None = None


class _UsageData(BaseModel):
    model_config = ConfigDict(extra="ignore")
    five_hour_tokens: int  # common cap
    seven_day_tokens: int  # common cap
    users: list[_UsageRow] = []


class _UsageEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ret_code: int
    data: _UsageData | None = None


@dataclass
class _CredResult:
    credential_digest: str
    email: str | None = None
    snapshot: ProviderUsageSnapshot | None = None
    error: ProviderErrorState | None = None


@dataclass
class _Status:
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    result_counts: dict[str, int] = field(default_factory=dict)
    error_counts: dict[str, int] = field(default_factory=dict)


class LumidUsageProvider:
    type = "lumid"

    def __init__(
        self,
        *,
        provider_id: str,
        label: str,
        token_env: str,
        store: UsageProviderStore,
        http_timeout: float,
        refresh_interval_seconds: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.id = provider_id
        self.label = label
        self._token_env = token_env
        self._store = store
        self._http_timeout = http_timeout
        self.refresh_interval_seconds = refresh_interval_seconds
        self._transport = transport
        self._snapshots: dict[str, ProviderUsageSnapshot] = {}
        self._status = _Status()
        self._client: httpx.AsyncClient | None = None

    def load_durable(self) -> None:
        self._snapshots = {
            snap.account_key: snap for snap in self._store.load_snapshots(self.id)
        }

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=_BASE_URL,
                timeout=self._http_timeout,
                follow_redirects=False,
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def buckets(self) -> list[ProviderUsageSnapshot]:
        return list(self._snapshots.values())

    def status(self) -> ProviderUsageStatus:
        return ProviderUsageStatus(
            provider_id=self.id,
            provider_type=self.type,
            provider_label=self.label,
            enabled=True,
            last_attempt_at=self._status.last_attempt_at,
            last_success_at=self._status.last_success_at,
            stale=self._is_stale(),
            result_counts=dict(self._status.result_counts),
            error_counts=dict(self._status.error_counts),
        )

    def _is_stale(self) -> bool:
        if self._status.last_success_at is None:
            return bool(self._snapshots)
        threshold = max(2 * self.refresh_interval_seconds, 600)
        age = (datetime.now(UTC) - self._status.last_success_at).total_seconds()
        return age > threshold

    def _parse_tokens(self) -> list[str]:
        raw = os.environ.get(self._token_env, "")
        seen: set[str] = set()
        tokens: list[str] = []
        for part in raw.split(","):
            token = part.strip()
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)
        return tokens

    async def refresh(self, *, force: bool) -> ProviderRefreshResult:
        now = datetime.now(UTC)
        self._status.last_attempt_at = now
        tokens = self._parse_tokens()
        if not tokens:
            self._status.result_counts = {"missing_token": 1}
            self._status.error_counts = {"missing_token": 1}
            return ProviderRefreshResult(
                provider_id=self.id,
                error_count=1,
                last_attempt_at=now,
                last_success_at=self._status.last_success_at,
                errors=["missing_token"],
            )

        semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

        async def guarded(token: str) -> _CredResult:
            async with semaphore:
                return await self._fetch_one(token, now)

        results = await asyncio.gather(*(guarded(token) for token in tokens))
        return self._reconcile_and_summarize(results, now)

    async def _fetch_one(self, token: str, observed_at: datetime) -> _CredResult:
        credential_digest = self._store.credential_digest(token)
        client = await self._get_client()
        headers = {"Authorization": f"Bearer {token}"}

        try:
            user_resp = await client.get(_USER_PATH, headers=headers)
        except httpx.HTTPError:
            return _CredResult(credential_digest, error="network")
        if user_resp.status_code in (401, 403):
            return _CredResult(credential_digest, error="identity_failed")
        email = self._parse_email(user_resp)
        if email is None:
            return _CredResult(credential_digest, error="identity_failed")

        try:
            usage_resp = await client.get(_USAGE_PATH, headers=headers)
        except httpx.HTTPError:
            return _CredResult(credential_digest, email=email, error="network")
        if usage_resp.status_code in (401, 403):
            return _CredResult(
                credential_digest, email=email, error="permission_denied"
            )
        usage_data = self._parse_usage(usage_resp)
        if usage_data is None:
            return _CredResult(
                credential_digest, email=email, error="usage_unavailable"
            )

        row = _select_row(usage_data.users, email)
        if row is None:
            return _CredResult(
                credential_digest, email=email, error="no_matching_usage"
            )

        snapshot = self._build_snapshot(email, row, usage_data, observed_at)
        return _CredResult(credential_digest, email=email, snapshot=snapshot)

    def _parse_email(self, resp: httpx.Response) -> str | None:
        try:
            envelope = _UserEnvelope.model_validate(resp.json())
        except (ValueError, ValidationError):
            return None
        if envelope.ret_code != 0 or envelope.data is None:
            return None
        email = envelope.data.email.strip().lower()
        return email or None

    def _parse_usage(self, resp: httpx.Response) -> _UsageData | None:
        try:
            envelope = _UsageEnvelope.model_validate(resp.json())
        except (ValueError, ValidationError):
            return None
        if envelope.ret_code != 0 or envelope.data is None:
            return None
        return envelope.data

    def _build_snapshot(
        self,
        email: str,
        row: _UsageRow,
        usage: _UsageData,
        observed_at: datetime,
    ) -> ProviderUsageSnapshot:
        windows = [
            UsageWindow(
                id="lumid-five-hour",
                label="5h",
                used_percent=row.five_hour_pct,
                used_tokens=row.five_hour_tokens,
                limit_tokens=usage.five_hour_tokens,
                remaining_tokens=max(usage.five_hour_tokens - row.five_hour_tokens, 0),
                window_minutes=_FIVE_HOUR_MINUTES,
                resets_at=row.five_hour_resets_at,
            ),
            UsageWindow(
                id="lumid-seven-day",
                label="7d",
                used_percent=row.seven_day_pct,
                used_tokens=row.seven_day_tokens,
                limit_tokens=usage.seven_day_tokens,
                remaining_tokens=max(usage.seven_day_tokens - row.seven_day_tokens, 0),
                window_minutes=_SEVEN_DAY_MINUTES,
                resets_at=row.seven_day_resets_at,
            ),
        ]
        account_key = self._store.account_key_digest(self.id, email)
        return ProviderUsageSnapshot(
            provider_id=self.id,
            provider_type=self.type,
            account_key=account_key,
            account_label=email,
            snapshot=ProviderRateLimitUsage(
                source_id=self.type,
                updated_at=observed_at,
                windows=windows,
            ),
            metadata=ProviderUsageMetadata(
                requests_7d=row.requests_7d,
                last_ts=row.last_ts,
            ),
            observed_at=observed_at,
            last_success_at=observed_at,
        )

    def _reconcile_and_summarize(
        self, results: list[_CredResult], now: datetime
    ) -> ProviderRefreshResult:
        result_counts: dict[str, int] = {}
        error_counts: dict[str, int] = {}
        errors: list[ProviderErrorState] = []
        ok_count = 0

        # Merge successful snapshots by account, keeping the freshest, and track
        # which credentials observed each account for reconciliation.
        fresh: dict[str, ProviderUsageSnapshot] = {}
        creds_by_account: dict[str, list[str]] = {}
        active_credentials: set[str] = set()

        for res in results:
            active_credentials.add(res.credential_digest)
            if res.snapshot is not None:
                ok_count += 1
                result_counts["success"] = result_counts.get("success", 0) + 1
                key = res.snapshot.account_key
                creds_by_account.setdefault(key, []).append(res.credential_digest)
                existing = fresh.get(key)
                if existing is None or res.snapshot.observed_at > existing.observed_at:
                    fresh[key] = res.snapshot
            elif res.error is not None:
                error_counts[res.error] = error_counts.get(res.error, 0) + 1
                errors.append(res.error)

        for key, snapshot in fresh.items():
            self._store.upsert_snapshot(snapshot, creds_by_account[key])
        self._store.reconcile(self.id, active_credentials)
        self.load_durable()

        self._status.result_counts = result_counts | error_counts
        self._status.error_counts = error_counts
        if ok_count:
            self._status.last_success_at = now

        return ProviderRefreshResult(
            provider_id=self.id,
            ok_count=ok_count,
            error_count=len(errors),
            last_attempt_at=now,
            last_success_at=self._status.last_success_at,
            errors=errors,
        )


def _select_row(rows: list[_UsageRow], email: str) -> _UsageRow | None:
    for row in rows:
        if row.email.strip().lower() == email:
            return row
    return None
