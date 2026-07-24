"""Lumid usage provider: identity+usage selection, error states, multi-PAT."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from waypoint.storage import Storage
from waypoint.usage_providers.lumid import LumidUsageProvider

pytestmark = pytest.mark.asyncio

_EMAIL = "noppanat.wad@gmail.com"

Handler = Callable[[httpx.Request], httpx.Response]


def _user_ok(email: str = _EMAIL) -> dict[str, Any]:
    return {"ret_code": 0, "message": "ok", "data": {"email": email, "id": "x"}}


def _usage_ok(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ret_code": 0,
        "message": "ok",
        "data": {
            "five_hour_tokens": 2_000_000,
            "seven_day_tokens": 20_000_000,
            "users": rows,
        },
    }


def _row(
    email: str = _EMAIL, five: int = 1_240_000, seven: int = 1_350_000
) -> dict[str, Any]:
    return {
        "email": email,
        "five_hour_tokens": five,
        "seven_day_tokens": seven,
        "five_hour_pct": five / 2_000_000 * 100,
        "seven_day_pct": seven / 20_000_000 * 100,
        "requests_7d": 355,
        "last_ts": "2026-07-24T12:33:46.107Z",
    }


def _routes(
    user: dict[str, Any] | int,
    usage: dict[str, Any] | int,
) -> Handler:
    """A MockTransport handler. Int values become that HTTP status with an empty
    body; dicts become 200 with the JSON body."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/user":
            if isinstance(user, int):
                return httpx.Response(user, json={"ret_code": 1})
            return httpx.Response(200, json=user)
        if request.url.path == "/api/v1/admin/claude-user-usage":
            if isinstance(usage, int):
                return httpx.Response(usage, json={"ret_code": 1})
            return httpx.Response(200, json=usage)
        return httpx.Response(404)

    return handler


def _provider(
    storage: Storage,
    handler: Handler,
    *,
    provider_id: str = "lumid",
) -> LumidUsageProvider:
    return LumidUsageProvider(
        provider_id=provider_id,
        label="Lumid",
        token_env="LUMID_TEST_TOKENS",
        store=storage.usage_providers,
        http_timeout=5.0,
        refresh_interval_seconds=300,
        transport=httpx.MockTransport(handler),
    )


def _storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "db.sqlite")


async def test_success_selects_matching_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LUMID_TEST_TOKENS", "tok_a")
    storage = _storage(tmp_path)
    provider = _provider(storage, _routes(_user_ok(), _usage_ok([_row()])))
    result = await provider.refresh(force=True)
    assert result.ok_count == 1
    buckets = provider.buckets()
    assert len(buckets) == 1
    snap = buckets[0]
    assert snap.account_label == _EMAIL
    windows = {w.id: w for w in snap.snapshot.windows}
    assert windows["lumid-five-hour"].used_tokens == 1_240_000
    assert windows["lumid-five-hour"].limit_tokens == 2_000_000
    assert windows["lumid-seven-day"].limit_tokens == 20_000_000
    assert snap.metadata.requests_7d == 355
    await provider.aclose()


async def test_case_insensitive_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LUMID_TEST_TOKENS", "tok_a")
    storage = _storage(tmp_path)
    provider = _provider(
        storage,
        _routes(_user_ok("NOPPANAT.WAD@GMAIL.COM"), _usage_ok([_row(email=_EMAIL)])),
    )
    result = await provider.refresh(force=True)
    assert result.ok_count == 1
    assert provider.buckets()[0].account_label == _EMAIL
    await provider.aclose()


async def test_only_matching_row_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LUMID_TEST_TOKENS", "tok_a")
    storage = _storage(tmp_path)
    rows = [
        _row(email="other@x.com", five=9),
        _row(),
        _row(email="third@x.com", five=7),
    ]
    provider = _provider(storage, _routes(_user_ok(), _usage_ok(rows)))
    await provider.refresh(force=True)
    buckets = provider.buckets()
    assert len(buckets) == 1
    assert buckets[0].account_label == _EMAIL
    await provider.aclose()


async def test_zero_usage_row_is_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LUMID_TEST_TOKENS", "tok_a")
    storage = _storage(tmp_path)
    provider = _provider(
        storage, _routes(_user_ok(), _usage_ok([_row(five=0, seven=0)]))
    )
    result = await provider.refresh(force=True)
    assert result.ok_count == 1
    windows = {w.id: w for w in provider.buckets()[0].snapshot.windows}
    assert windows["lumid-five-hour"].used_tokens == 0
    await provider.aclose()


async def test_no_matching_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUMID_TEST_TOKENS", "tok_a")
    storage = _storage(tmp_path)
    provider = _provider(
        storage, _routes(_user_ok(), _usage_ok([_row(email="other@x.com")]))
    )
    result = await provider.refresh(force=True)
    assert result.ok_count == 0
    assert "no_matching_usage" in result.errors
    assert provider.buckets() == []
    await provider.aclose()


async def test_missing_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LUMID_TEST_TOKENS", raising=False)
    storage = _storage(tmp_path)
    provider = _provider(storage, _routes(_user_ok(), _usage_ok([_row()])))
    result = await provider.refresh(force=True)
    assert result.errors == ["missing_token"]
    assert provider.status().error_counts.get("missing_token") == 1
    await provider.aclose()


async def test_identity_401(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUMID_TEST_TOKENS", "tok_a")
    storage = _storage(tmp_path)
    provider = _provider(storage, _routes(401, _usage_ok([_row()])))
    result = await provider.refresh(force=True)
    assert "identity_failed" in result.errors
    assert provider.buckets() == []
    await provider.aclose()


async def test_usage_403_is_permission_denied_keeps_last_good(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LUMID_TEST_TOKENS", "tok_a")
    storage = _storage(tmp_path)
    good = _provider(storage, _routes(_user_ok(), _usage_ok([_row()])))
    await good.refresh(force=True)
    await good.aclose()
    # A new refresh where usage returns 403 must keep the last-good snapshot.
    denied = _provider(storage, _routes(_user_ok(), 403))
    denied.load_durable()
    result = await denied.refresh(force=True)
    assert "permission_denied" in result.errors
    assert len(denied.buckets()) == 1  # last-good preserved
    await denied.aclose()


async def test_ret_code_error_is_usage_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LUMID_TEST_TOKENS", "tok_a")
    storage = _storage(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/user":
            return httpx.Response(200, json=_user_ok())
        return httpx.Response(200, json={"ret_code": 5, "message": "boom"})

    provider = _provider(storage, handler)
    result = await provider.refresh(force=True)
    assert "usage_unavailable" in result.errors
    await provider.aclose()


async def test_malformed_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LUMID_TEST_TOKENS", "tok_a")
    storage = _storage(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/user":
            return httpx.Response(200, json=_user_ok())
        return httpx.Response(200, text="not json")

    provider = _provider(storage, handler)
    result = await provider.refresh(force=True)
    assert "usage_unavailable" in result.errors
    await provider.aclose()


async def test_network_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUMID_TEST_TOKENS", "tok_a")
    storage = _storage(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    provider = _provider(storage, handler)
    result = await provider.refresh(force=True)
    assert "network" in result.errors
    await provider.aclose()


async def test_multi_pat_same_owner_deduped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LUMID_TEST_TOKENS", "tok_a, tok_b , tok_a")
    storage = _storage(tmp_path)
    provider = _provider(storage, _routes(_user_ok(), _usage_ok([_row()])))
    result = await provider.refresh(force=True)
    # tok_a deduped: two distinct tokens, both same owner -> one bucket.
    assert result.ok_count == 2
    assert len(provider.buckets()) == 1
    await provider.aclose()


async def test_multi_pat_distinct_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LUMID_TEST_TOKENS", "tok_a,tok_b")
    storage = _storage(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        email = _EMAIL if token == "tok_a" else "second@x.com"
        if request.url.path == "/api/v1/user":
            return httpx.Response(200, json=_user_ok(email))
        return httpx.Response(200, json=_usage_ok([_row(), _row(email="second@x.com")]))

    provider = _provider(storage, handler)
    result = await provider.refresh(force=True)
    assert result.ok_count == 2
    labels = {b.account_label for b in provider.buckets()}
    assert labels == {_EMAIL, "second@x.com"}
    await provider.aclose()


async def test_one_pat_fails_other_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LUMID_TEST_TOKENS", "good,bad")
    storage = _storage(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        if token == "bad" and request.url.path == "/api/v1/user":
            return httpx.Response(401)
        if request.url.path == "/api/v1/user":
            return httpx.Response(200, json=_user_ok())
        return httpx.Response(200, json=_usage_ok([_row()]))

    provider = _provider(storage, handler)
    result = await provider.refresh(force=True)
    assert result.ok_count == 1
    assert "identity_failed" in result.errors
    assert len(provider.buckets()) == 1
    await provider.aclose()


async def test_removed_pat_account_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = _storage(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        email = _EMAIL if token == "tok_a" else "second@x.com"
        if request.url.path == "/api/v1/user":
            return httpx.Response(200, json=_user_ok(email))
        return httpx.Response(200, json=_usage_ok([_row(), _row(email="second@x.com")]))

    monkeypatch.setenv("LUMID_TEST_TOKENS", "tok_a,tok_b")
    provider = _provider(storage, handler)
    await provider.refresh(force=True)
    assert len(provider.buckets()) == 2
    # Drop tok_b; second@x.com's account has no remaining configured credential.
    monkeypatch.setenv("LUMID_TEST_TOKENS", "tok_a")
    await provider.refresh(force=True)
    labels = {b.account_label for b in provider.buckets()}
    assert labels == {_EMAIL}
    await provider.aclose()


async def test_no_secret_leak_in_store_or_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "lm_pat_live_SENTINEL_SECRET_9f2c"
    monkeypatch.setenv("LUMID_TEST_TOKENS", sentinel)
    storage = _storage(tmp_path)
    provider = _provider(storage, _routes(_user_ok(), _usage_ok([_row()])))
    result = await provider.refresh(force=True)
    # Not in the refresh result / status.
    assert sentinel not in result.model_dump_json()
    assert sentinel not in json.dumps(provider.status().model_dump(mode="json"))
    await provider.aclose()
    # Not anywhere in the sqlite database bytes.
    db_bytes = (tmp_path / "db.sqlite").read_bytes()
    assert sentinel.encode() not in db_bytes
    # Not in the identity key file (it's a random key, but assert regardless).
    key_file = tmp_path / "usage_provider_identity.key"
    assert sentinel.encode() not in key_file.read_bytes()
