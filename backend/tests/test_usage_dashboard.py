from datetime import UTC, datetime, timedelta

from waypoint.schemas import (
    SessionRateLimitUsage,
    SessionRecord,
    UsageWindow,
)
from waypoint.usage_dashboard import account_bucket_for, build_dashboard


def _snapshot(
    *,
    source: str,
    notes: list[str],
    updated_at: datetime,
    windows: list[UsageWindow] | None = None,
) -> SessionRateLimitUsage:
    return SessionRateLimitUsage(
        source=source,
        updated_at=updated_at,
        windows=windows or [],
        notes=notes,
    )


def _session(
    *,
    sid: str,
    backend: str,
    snapshot: SessionRateLimitUsage | None,
    updated_at: datetime | None = None,
) -> SessionRecord:
    now = updated_at or datetime.now(UTC)
    return SessionRecord(
        id=sid,
        backend=backend,
        source="managed",
        title=sid,
        cwd="~/",
        status="running",
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=f"/tmp/{sid}.raw",
        structured_log_path=f"/tmp/{sid}.json",
        rate_limit_usage=snapshot,
    )


def test_account_bucket_claude_uses_org_and_tier() -> None:
    snap = _snapshot(
        source="claude_code",
        notes=["CLI OAuth", "org: Acme", "org tier: enterprise"],
        updated_at=datetime.now(UTC),
    )
    key, label = account_bucket_for(snap, session_id="s1")
    assert key == "claude_code:Acme"
    assert label == "Acme · enterprise"


def test_account_bucket_claude_without_tier_falls_back_to_org_only_label() -> None:
    snap = _snapshot(
        source="claude_code",
        notes=["CLI OAuth", "org: Acme"],
        updated_at=datetime.now(UTC),
    )
    _, label = account_bucket_for(snap, session_id="s1")
    assert label == "Acme"


def test_account_bucket_codex_uses_email_and_plan() -> None:
    snap = _snapshot(
        source="codex",
        notes=["CLI OAuth", "plan: education", "user@example.com"],
        updated_at=datetime.now(UTC),
    )
    key, label = account_bucket_for(snap, session_id="s1")
    assert key == "codex:user@example.com"
    assert label == "user@example.com · plan: education"


def test_account_bucket_no_account_info_falls_back_to_session_scope() -> None:
    claude = _snapshot(
        source="claude_code",
        notes=["CLI OAuth"],
        updated_at=datetime.now(UTC),
    )
    codex = _snapshot(
        source="codex",
        notes=["CLI OAuth"],
        updated_at=datetime.now(UTC),
    )
    assert account_bucket_for(claude, session_id="s1") == (
        "claude_code:session:s1",
        "Claude Code",
    )
    assert account_bucket_for(codex, session_id="s2") == (
        "codex:session:s2",
        "Codex",
    )


def test_build_dashboard_lists_freshest_session_first() -> None:
    # The refresh path probes ``session_ids[0]``; if the oldest-encountered
    # session is exited, ``force_refresh_rate_limit_usage`` would silently
    # no-op. Putting the freshest-snapshot session first keeps refresh
    # pointed at the session most likely to still have live adapter state.
    now = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    older = _snapshot(
        source="claude_code",
        notes=["org: Acme"],
        updated_at=now - timedelta(minutes=15),
    )
    newer = _snapshot(
        source="claude_code",
        notes=["org: Acme"],
        updated_at=now,
    )
    middle = _snapshot(
        source="claude_code",
        notes=["org: Acme"],
        updated_at=now - timedelta(minutes=5),
    )
    sessions = [
        _session(sid="s-old", backend="claude_code", snapshot=older),
        _session(sid="s-new", backend="claude_code", snapshot=newer),
        _session(sid="s-mid", backend="claude_code", snapshot=middle),
    ]
    dashboard = build_dashboard(sessions)
    assert len(dashboard.buckets) == 1
    assert dashboard.buckets[0].session_ids[0] == "s-new"
    assert set(dashboard.buckets[0].session_ids) == {"s-old", "s-new", "s-mid"}


def test_build_dashboard_groups_sessions_and_keeps_freshest_snapshot() -> None:
    now = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    older = _snapshot(
        source="claude_code",
        notes=["org: Acme", "org tier: enterprise"],
        updated_at=now - timedelta(minutes=10),
        windows=[
            UsageWindow(id="5h", label="5h", used_percent=20.0),
        ],
    )
    newer = _snapshot(
        source="claude_code",
        notes=["org: Acme", "org tier: enterprise"],
        updated_at=now,
        windows=[
            UsageWindow(id="5h", label="5h", used_percent=42.0),
        ],
    )
    sessions = [
        _session(sid="s-old", backend="claude_code", snapshot=older),
        _session(sid="s-new", backend="claude_code", snapshot=newer),
    ]
    dashboard = build_dashboard(sessions)
    assert len(dashboard.buckets) == 1
    bucket = dashboard.buckets[0]
    assert bucket.account_key == "claude_code:Acme"
    assert bucket.snapshot.updated_at == now
    assert bucket.snapshot.windows[0].used_percent == 42.0
    assert set(bucket.session_ids) == {"s-old", "s-new"}


def test_build_dashboard_skips_sessions_without_snapshot() -> None:
    sessions = [
        _session(sid="s1", backend="claude_code", snapshot=None),
    ]
    dashboard = build_dashboard(sessions)
    assert dashboard.buckets == []


def test_build_dashboard_separates_backends_and_accounts() -> None:
    now = datetime.now(UTC)
    claude = _snapshot(
        source="claude_code",
        notes=["org: Acme", "org tier: enterprise"],
        updated_at=now,
    )
    codex = _snapshot(
        source="codex",
        notes=["plan: pro", "user@example.com"],
        updated_at=now,
    )
    codex_other = _snapshot(
        source="codex",
        notes=["plan: pro", "other@example.com"],
        updated_at=now,
    )
    sessions = [
        _session(sid="s1", backend="claude_code", snapshot=claude),
        _session(sid="s2", backend="codex", snapshot=codex),
        _session(sid="s3", backend="codex", snapshot=codex_other),
    ]
    dashboard = build_dashboard(sessions)
    keys = {bucket.account_key for bucket in dashboard.buckets}
    assert keys == {
        "claude_code:Acme",
        "codex:user@example.com",
        "codex:other@example.com",
    }
