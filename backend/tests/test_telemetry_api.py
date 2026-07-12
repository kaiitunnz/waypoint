"""Route-level tests for ``/api/telemetry/*`` over the real FastAPI app.

Covers the API contract pieces CONTRACT.md §4/§7 calls out explicitly: auth,
date/tz range boundaries, empty range, filter semantics (including limit-card
hiding), drill-down parameter validation, insight dismissal, and the
debounced ``telemetry_update`` WS envelope.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from starlette.testclient import TestClient

from waypoint.api import create_app
from waypoint.schemas import (
    SessionRecord,
    SessionSource,
    SessionStatus,
    TokenUsageInit,
    TokenUsageRecord,
)
from waypoint.settings import Settings
from waypoint.telemetry.facts import (
    ContextSnapshotFact,
    FactDimensions,
    LifecycleTransition,
    LimitSnapshotFact,
    SessionLifecycleFact,
    TurnFact,
    TurnKind,
)
from waypoint.telemetry.query import host_utc_offset_minutes


def _build(tmp_path: Path) -> tuple[Any, str]:
    settings = Settings(data_dir=tmp_path / "data", telemetry_enabled=True)
    app = create_app(settings)
    context = app.state.context
    token = context.tokens.issue().token
    return app, token


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _dims() -> FactDimensions:
    return FactDimensions.model_validate(
        {
            "backend": "codex",
            "repo_name": "waypoint",
            "source": SessionSource.MANAGED,
            "transport": "tmux",
            "spawner_session_id": None,
            "is_child": False,
        }
    )


def _seed_session(context: Any, session_id: str) -> None:
    now = datetime.now(UTC)
    context.storage.create_session(
        SessionRecord(
            id=session_id,
            backend="codex",
            source=SessionSource.MANAGED,
            transport="tmux",
            title=session_id,
            cwd="/tmp",
            repo_name="waypoint",
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path="/tmp/raw.log",
            structured_log_path="/tmp/events.jsonl",
        )
    )


async def test_requires_auth(tmp_path: Path) -> None:
    app, _ = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get("/api/telemetry/overview")
    assert resp.status_code == 401


async def test_overview_empty_instance_returns_zeros_not_error(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get("/api/telemetry/overview", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["tokens"]["totals"] == {}
    assert body["tokens"]["display_total"] == 0
    assert body["tokens"]["safe_total"] is True
    assert body["sessions"]["active_now"] == 0
    assert body["tool_calls"] == 0
    assert "start" in body["range"] and "end" in body["range"] and "tz" in body["range"]


async def test_range_echo_carries_numeric_utc_offset(tmp_path: Path) -> None:
    # The frontend can't use ``tz`` (a tzname() abbreviation) as a JS timeZone,
    # so the echo also carries a deterministic numeric offset (#10d).
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get(
            "/api/telemetry/overview", params={"preset": "7d"}, headers=_auth(token)
        )
    assert resp.status_code == 200
    rng = resp.json()["range"]
    assert isinstance(rng["utc_offset_minutes"], int)
    assert rng["utc_offset_minutes"] == host_utc_offset_minutes()


async def test_custom_range_requires_both_start_and_end(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get(
            "/api/telemetry/overview",
            params={"start": "2026-01-01"},
            headers=_auth(token),
        )
    assert resp.status_code == 400


async def test_unknown_preset_is_rejected(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get(
            "/api/telemetry/overview",
            params={"preset": "bogus"},
            headers=_auth(token),
        )
    assert resp.status_code == 400


async def test_preset_today_excludes_yesterdays_data(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    context = app.state.context
    _seed_session(context, "s1")
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    context.storage.telemetry.ingest_fact(
        SessionLifecycleFact(
            fact_id="s1:created:today",
            source="runtime",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            transition=LifecycleTransition.CREATED,
        )
    )
    context.storage.telemetry.ingest_fact(
        SessionLifecycleFact(
            fact_id="s1:created:yesterday",
            source="runtime",
            session_id="s1",
            occurred_at=yesterday,
            dims=_dims(),
            transition=LifecycleTransition.STARTING,
        )
    )
    async with _client(app) as client:
        resp = await client.get(
            "/api/telemetry/overview", params={"preset": "today"}, headers=_auth(token)
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sessions"]["created"] == 1


async def test_tokens_group_by_backend_splits_totals(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    context = app.state.context
    _seed_session(context, "s1")
    now = datetime.now(UTC)

    for backend, tokens in (("codex", 100), ("claude_code", 250)):
        context.storage.record_token_usage(
            "s1",
            TokenUsageRecord(
                record_id=f"turn-{backend}",
                source=backend,
                observed_at=now,
                totals={"input_tokens": tokens},
                display_total_tokens=tokens,
            ),
            init=TokenUsageInit(coverage="entire_waypoint_session", observed_from=now),
        )
        context.storage.telemetry.ingest_fact(
            TurnFact(
                fact_id=f"turn-{backend}",
                source=backend,
                session_id="s1",
                occurred_at=now,
                dims=FactDimensions.model_validate(
                    {
                        "backend": backend,
                        "repo_name": "waypoint",
                        "source": SessionSource.MANAGED,
                        "transport": "tmux",
                        "spawner_session_id": None,
                        "is_child": False,
                    }
                ),
                turn_kind=TurnKind.AGENT,
            )
        )

    async with _client(app) as client:
        resp = await client.get(
            "/api/telemetry/tokens",
            params={"group_by": "backend"},
            headers=_auth(token),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["group_by"] == "backend"
    by_key = {group["key"]: group for group in body["groups"]}
    assert by_key["codex"]["totals"] == {
        "fresh_input": 100,
        "cache_read": 0,
        "cache_write": 0,
        "output": 0,
        "reasoning": 0,
    }
    assert by_key["claude_code"]["totals"] == {
        "fresh_input": 250,
        "cache_read": 0,
        "cache_write": 0,
        "output": 0,
        "reasoning": 0,
    }


async def test_tokens_group_cached_read_tokens_excluded_from_display_total(
    tmp_path: Path,
) -> None:
    """A cache-read-heavy group's ``display_total`` is the small new-work
    number, and ``cached_read_tokens`` carries the cache-read volume
    standalone (#2, iteration 4)."""
    app, token = _build(tmp_path)
    context = app.state.context
    _seed_session(context, "s1")
    now = datetime.now(UTC)

    context.storage.record_token_usage(
        "s1",
        TokenUsageRecord(
            record_id="turn-1",
            source="claude_code",
            observed_at=now,
            totals={
                "input_tokens": 100,
                "cache_read_tokens": 579_000_000,
                "output_tokens": 50,
            },
        ),
        init=TokenUsageInit(coverage="entire_waypoint_session", observed_from=now),
    )
    context.storage.telemetry.ingest_fact(
        TurnFact(
            fact_id="turn-1",
            source="claude_code",
            session_id="s1",
            occurred_at=now,
            dims=FactDimensions.model_validate(
                {
                    "backend": "claude_code",
                    "repo_name": "waypoint",
                    "source": SessionSource.MANAGED,
                    "transport": "tmux",
                    "spawner_session_id": None,
                    "is_child": False,
                }
            ),
            turn_kind=TurnKind.AGENT,
        )
    )

    async with _client(app) as client:
        resp = await client.get(
            "/api/telemetry/tokens",
            params={"group_by": "backend"},
            headers=_auth(token),
        )
    assert resp.status_code == 200
    group = resp.json()["groups"][0]
    assert group["cached_read_tokens"] == 579_000_000
    assert group["display_total"] == 150
    assert group["totals"]["cache_read"] == 579_000_000


async def test_drilldown_requires_kind_query_param(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get("/api/telemetry/drilldown", headers=_auth(token))
    assert resp.status_code == 422


async def test_drilldown_defaults_page_size_to_20(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get(
            "/api/telemetry/drilldown",
            params={"kind": "tool_call"},
            headers=_auth(token),
        )
    assert resp.status_code == 200
    assert resp.json()["page_size"] == 20


async def test_settings_endpoint_shape(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get("/api/telemetry/settings", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["retention_days_facts"] == 90
    assert body["retention_months_rollups"] == 13
    assert body["external_export"] is False
    assert body["content_capture"] is False
    assert "privacy_statement" in body


async def test_limit_card_hidden_via_session_scoped_filter(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    context = app.state.context
    _seed_session(context, "s1")
    now = datetime.now(UTC)
    context.storage.telemetry.ingest_fact(
        LimitSnapshotFact(
            fact_id="limit-1",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            account_key="codex:acct",
            window_id="5h",
            used_percent=95.0,
        )
    )
    async with _client(app) as client:
        unscoped = await client.get("/api/telemetry/overview", headers=_auth(token))
        scoped = await client.get(
            "/api/telemetry/overview",
            params={"repo": "waypoint"},
            headers=_auth(token),
        )
    assert unscoped.json()["limit_card_hidden"] is False
    assert scoped.json()["limit_card_hidden"] is True
    assert scoped.json()["limit_card_hidden_reason"] is not None
    assert scoped.json()["alerts"]["limits"] == []


async def test_insight_dismiss_round_trip(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    context = app.state.context
    _seed_session(context, "s1")
    now = datetime.now(UTC)
    context.storage.telemetry.ingest_fact(
        ContextSnapshotFact(
            fact_id="ctx-1",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            used_tokens=9500,
            window_tokens=10000,
            occupancy_percent=95.0,
        )
    )
    async with _client(app) as client:
        before = await client.get("/api/telemetry/insights", headers=_auth(token))
        assert before.status_code == 200
        types_before = {i["type"] for i in before.json()["insights"]}
        assert "context_pressure" in types_before

        dismiss = await client.post(
            "/api/telemetry/insights/context_pressure/dismiss", headers=_auth(token)
        )
        assert dismiss.status_code == 200
        assert dismiss.json()["signature"] == "context_pressure"

        after = await client.get("/api/telemetry/insights", headers=_auth(token))
        types_after = {i["type"] for i in after.json()["insights"]}
        assert "context_pressure" not in types_after


async def test_nl_insight_endpoints_404_when_disabled(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        get_resp = await client.get("/api/telemetry/nl-insight", headers=_auth(token))
        post_resp = await client.post("/api/telemetry/nl-insight", headers=_auth(token))
    assert get_resp.status_code == 404
    assert post_resp.status_code == 404


async def test_nl_insight_post_generates_and_get_returns_it(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", telemetry_enabled=True)
    settings.telemetry_nl.enabled = True
    app = create_app(settings)
    context = app.state.context
    token = context.tokens.issue().token

    async def fake_run_oneshot(**_kwargs: Any) -> str:
        return json.dumps({"prose": "Quiet week.", "evidence": [], "confidence": "low"})

    context.runtime.run_oneshot = fake_run_oneshot

    async with _client(app) as client:
        post = await client.post("/api/telemetry/nl-insight", headers=_auth(token))
        assert post.status_code == 200
        assert post.json()["insight"]["prose"] == "Quiet week."

        get = await client.get("/api/telemetry/nl-insight", headers=_auth(token))
        assert get.status_code == 200
        body = get.json()
        assert body["available"] is True
        assert body["insight"]["prose"] == "Quiet week."


async def test_nl_insight_get_reports_unavailable_before_first_generation(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path / "data", telemetry_enabled=True)
    settings.telemetry_nl.enabled = True
    app = create_app(settings)
    context = app.state.context
    token = context.tokens.issue().token

    async with _client(app) as client:
        resp = await client.get("/api/telemetry/nl-insight", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["insight"] is None


async def test_nl_insight_post_returns_409_on_generation_failure(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path / "data", telemetry_enabled=True)
    settings.telemetry_nl.enabled = True
    app = create_app(settings)
    context = app.state.context
    token = context.tokens.issue().token

    async def fake_run_oneshot(**_kwargs: Any) -> None:
        return None

    context.runtime.run_oneshot = fake_run_oneshot

    async with _client(app) as client:
        resp = await client.post("/api/telemetry/nl-insight", headers=_auth(token))
    assert resp.status_code == 409


def test_delete_publishes_debounced_telemetry_update(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", telemetry_enabled=True)
    app = create_app(settings)
    context = app.state.context
    token = context.tokens.issue().token
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/sessions?token={token}") as ws:
            ws.receive_json()  # initial session_list_update hydration frame
            client.delete(
                "/api/telemetry", headers={"Authorization": f"Bearer {token}"}
            )
            update = ws.receive_json()
    assert update["type"] == "telemetry_update"
