"""Tests for ``TelemetryIngester`` (CONTRACT.md §3/§7).

Covers derivation correctness per signal (session-created, status-carrying
events, user/agent turns, tool call+result pairing, approval request+decision,
context/limit snapshots), dedup on event replay, ``backfill()`` idempotency,
and privacy (no raw text/paths ever land in a fact/tag column).
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from waypoint.backends import BackendRegistry, get_registry, reset_registry_for_tests
from waypoint.schemas import (
    EventKind,
    EventRecord,
    SessionContextUsage,
    SessionRateLimitUsage,
    SessionRecord,
    SessionSource,
    SessionStatus,
    TokenUsageInit,
    TokenUsageRecord,
    UsageWindow,
)
from waypoint.storage import Storage
from waypoint.telemetry.ingest import TelemetryIngester


@pytest.fixture
def registry() -> BackendRegistry:
    reset_registry_for_tests()
    return get_registry()


def _make_session(
    storage: Storage,
    session_id: str,
    *,
    repo_path: str | None = "/home/user/projects/waypoint",
    resolved_model: str | None = "gpt-5-codex",
    spawner_session_id: str | None = None,
    tags: dict[str, str] | None = None,
) -> SessionRecord:
    now = datetime.now(UTC)
    session = SessionRecord(
        id=session_id,
        backend="codex",
        source=SessionSource.MANAGED,
        transport="tmux",
        title="t",
        cwd="/home/user/projects/waypoint",
        repo_name=repo_path,
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/home/user/.waypoint/sessions/s1/raw.log",
        structured_log_path="/home/user/.waypoint/sessions/s1/events.jsonl",
        resolved_model=resolved_model,
        spawner_session_id=spawner_session_id,
        tags=tags or {},
    )
    storage.create_session(session)
    return session


def _facts(storage: Storage, session_id: str | None = None) -> list[dict]:
    if session_id is None:
        rows = storage.connection.execute("SELECT * FROM telemetry_facts").fetchall()
    else:
        rows = storage.connection.execute(
            "SELECT * FROM telemetry_facts WHERE session_id = ?", (session_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def test_session_created_derives_lifecycle_fact(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    ingester = TelemetryIngester(storage)

    ingester.derive_from_session_created(session)
    ingester._drain_available()

    rows = _facts(storage, "s1")
    assert len(rows) == 1
    assert rows[0]["kind"] == "session_lifecycle"
    assert rows[0]["transition"] == "created"
    assert rows[0]["backend"] == "codex"
    assert rows[0]["repo_name"] == "waypoint"
    assert rows[0]["is_child"] == 0


def test_child_session_is_stamped_is_child(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "child", spawner_session_id="parent")
    ingester = TelemetryIngester(storage)

    ingester.derive_from_session_created(session)
    ingester._drain_available()

    row = _facts(storage, "child")[0]
    assert row["is_child"] == 1
    assert row["spawner_session_id"] == "parent"


def test_repo_name_basename_handles_trailing_slash(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1", repo_path="/home/user/projects/waypoint/")
    ingester = TelemetryIngester(storage)

    ingester.derive_from_session_created(session)
    ingester._drain_available()

    row = _facts(storage, "s1")[0]
    assert row["repo_name"] == "waypoint"


def test_status_metadata_on_event_derives_lifecycle_fact_and_dedups_replay(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    ingester = TelemetryIngester(storage)
    now = datetime.now(UTC)

    event = EventRecord(
        session_id="s1",
        ts=now,
        kind=EventKind.SYSTEM_NOTE,
        text="Turn ended",
        metadata={"status": SessionStatus.IDLE},
        sequence=7,
    )
    ingester.derive_from_event(session, event)
    ingester.derive_from_event(session, event)  # replay of the exact same event
    ingester._drain_available()

    rows = [r for r in _facts(storage, "s1") if r["kind"] == "session_lifecycle"]
    assert len(rows) == 1
    assert rows[0]["transition"] == "idle"
    assert rows[0]["fact_id"] == "s1:7"


def test_many_same_status_events_in_one_turn_collapse_to_one_lifecycle_fact(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    ingester = TelemetryIngester(storage)
    now = datetime.now(UTC)

    # STATUS_UPDATE is never actually emitted; nearly every event of a turn
    # (tool calls, agent output, ...) stamps metadata["status"] with the
    # current status instead, so a single turn easily carries 30 RUNNING
    # events. Only the first should mint a lifecycle fact.
    for sequence in range(1, 31):
        ingester.derive_from_event(
            session,
            EventRecord(
                session_id="s1",
                ts=now,
                kind=EventKind.TOOL_CALL,
                text="Read\n{...}",
                metadata={
                    "tool_use_id": f"tool-{sequence}",
                    "tool_name": "Read",
                    "status": SessionStatus.RUNNING,
                },
                sequence=sequence,
            ),
        )
    ingester._drain_available()

    rows = [r for r in _facts(storage, "s1") if r["kind"] == "session_lifecycle"]
    assert len(rows) == 1
    assert rows[0]["transition"] == "running"


def test_status_change_after_dedup_still_emits(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    ingester = TelemetryIngester(storage)
    now = datetime.now(UTC)

    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.SYSTEM_NOTE,
            text="a",
            metadata={"status": SessionStatus.RUNNING},
            sequence=1,
        ),
    )
    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.SYSTEM_NOTE,
            text="b",
            metadata={"status": SessionStatus.RUNNING},
            sequence=2,
        ),
    )
    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.SYSTEM_NOTE,
            text="c",
            metadata={"status": SessionStatus.IDLE},
            sequence=3,
        ),
    )
    ingester._drain_available()

    rows = [r for r in _facts(storage, "s1") if r["kind"] == "session_lifecycle"]
    transitions = sorted(r["transition"] for r in rows)
    assert transitions == ["idle", "running"]


def test_user_input_event_derives_turn_fact_with_resolved_model(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1", resolved_model="claude-sonnet-5")
    ingester = TelemetryIngester(storage)
    now = datetime.now(UTC)

    event = EventRecord(
        session_id="s1",
        ts=now,
        kind=EventKind.USER_INPUT,
        text="do the thing",
        metadata={},
        sequence=1,
    )
    ingester.derive_from_event(session, event)
    ingester._drain_available()

    rows = [r for r in _facts(storage, "s1") if r["kind"] == "turn"]
    assert len(rows) == 1
    assert rows[0]["turn_kind"] == "user"
    assert rows[0]["model_at_turn"] == "claude-sonnet-5"


def test_tool_call_then_result_merges_into_one_fact_with_outcome_and_duration(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    ingester = TelemetryIngester(storage)
    call_ts = datetime.now(UTC)
    result_ts = call_ts + timedelta(milliseconds=250)

    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=call_ts,
            kind=EventKind.TOOL_CALL,
            text="Read\n{...}",
            metadata={"tool_use_id": "tool-1", "tool_name": "Read"},
            sequence=1,
        ),
    )
    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=result_ts,
            kind=EventKind.TOOL_RESULT,
            text="<file contents>",
            metadata={"tool_use_id": "tool-1", "tool_name": "Read", "is_error": False},
            sequence=2,
        ),
    )
    ingester._drain_available()

    rows = [r for r in _facts(storage, "s1") if r["kind"] == "tool_call"]
    assert len(rows) == 1
    assert rows[0]["fact_id"] == "tool-1"
    assert rows[0]["tool_name"] == "Read"
    assert rows[0]["outcome"] == "succeeded"
    assert rows[0]["duration_ms"] == 250
    assert rows[0]["revision"] == 1


def test_tool_result_is_error_maps_to_failed(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    ingester = TelemetryIngester(storage)
    now = datetime.now(UTC)

    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.TOOL_CALL,
            text="Bash\n{...}",
            metadata={"tool_use_id": "tool-2", "tool_name": "Bash"},
            sequence=1,
        ),
    )
    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.TOOL_RESULT,
            text="error output",
            metadata={"tool_use_id": "tool-2", "tool_name": "Bash", "is_error": True},
            sequence=2,
        ),
    )
    ingester._drain_available()

    row = [r for r in _facts(storage, "s1") if r["kind"] == "tool_call"][0]
    assert row["outcome"] == "failed"


def test_duplicate_tool_result_event_is_deduped(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    ingester = TelemetryIngester(storage)
    now = datetime.now(UTC)
    result_event = EventRecord(
        session_id="s1",
        ts=now,
        kind=EventKind.TOOL_RESULT,
        text="ok",
        metadata={"tool_use_id": "tool-3", "tool_name": "Read", "is_error": False},
        sequence=2,
    )

    ingester.derive_from_event(session, result_event)
    ingester.derive_from_event(session, result_event)  # replay
    ingester._drain_available()

    rows = storage.connection.execute(
        "SELECT COUNT(*) AS n FROM telemetry_facts WHERE kind = 'tool_call' AND fact_id = 'tool-3'"
    ).fetchone()
    assert rows["n"] == 1


def test_approval_request_then_decision_updates_fact(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    ingester = TelemetryIngester(storage)
    now = datetime.now(UTC)

    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.APPROVAL_REQUEST,
            text="Allow Bash to run `rm -rf /secret/path`?",
            metadata={"approval_id": "appr-1", "tool_name": "Bash"},
            sequence=1,
        ),
    )
    ingester._drain_available()
    requested = [r for r in _facts(storage, "s1") if r["kind"] == "tool_call"][0]
    assert requested["approval_decision"] == "requested"
    assert requested["tool_name"] == "Bash"

    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now + timedelta(seconds=1),
            kind=EventKind.SYSTEM_NOTE,
            text="Approval response sent: accept",
            metadata={"approval_id": "appr-1", "status": SessionStatus.RUNNING},
            sequence=2,
        ),
    )
    ingester._drain_available()

    rows = [r for r in _facts(storage, "s1") if r["kind"] == "tool_call"]
    assert len(rows) == 1
    assert rows[0]["approval_decision"] == "approved"
    assert rows[0]["tool_name"] == "Bash"  # carried over from the request

    # The same event also carried a status, so a lifecycle fact derives too.
    lifecycle = [r for r in _facts(storage, "s1") if r["kind"] == "session_lifecycle"]
    assert len(lifecycle) == 1
    assert lifecycle[0]["transition"] == "running"


def test_approval_decline_maps_to_declined(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    ingester = TelemetryIngester(storage)
    now = datetime.now(UTC)

    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.APPROVAL_REQUEST,
            text="Allow?",
            metadata={"approval_id": "appr-2", "tool_name": "Write"},
            sequence=1,
        ),
    )
    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now + timedelta(seconds=1),
            kind=EventKind.SYSTEM_NOTE,
            text="Approval response sent: decline",
            metadata={"approval_id": "appr-2"},
            sequence=2,
        ),
    )
    ingester._drain_available()

    row = [r for r in _facts(storage, "s1") if r["kind"] == "tool_call"][0]
    assert row["approval_decision"] == "declined"


def test_approval_shared_vocabulary_words_map_to_approved(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    ingester = TelemetryIngester(storage)
    now = datetime.now(UTC)

    for word in ["allow", "acceptalways", "yes"]:
        session_id = f"s-{word}"
        session = _make_session(storage, session_id)
        approval_id = f"appr-{word}"
        ingester.derive_from_event(
            session,
            EventRecord(
                session_id=session_id,
                ts=now,
                kind=EventKind.APPROVAL_REQUEST,
                text="Allow?",
                metadata={"approval_id": approval_id, "tool_name": "Bash"},
                sequence=1,
            ),
        )
        ingester.derive_from_event(
            session,
            EventRecord(
                session_id=session_id,
                ts=now + timedelta(seconds=1),
                kind=EventKind.SYSTEM_NOTE,
                text=f"Approval response sent: {word}",
                metadata={"approval_id": approval_id},
                sequence=2,
            ),
        )
        ingester._drain_available()

        row = [r for r in _facts(storage, session_id) if r["kind"] == "tool_call"][0]
        assert row["approval_decision"] == "approved", word


def test_approval_unrecognized_word_maps_to_declined_not_stranded(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    ingester = TelemetryIngester(storage)
    now = datetime.now(UTC)

    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.APPROVAL_REQUEST,
            text="Allow?",
            metadata={"approval_id": "appr-3", "tool_name": "Write"},
            sequence=1,
        ),
    )
    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now + timedelta(seconds=1),
            kind=EventKind.SYSTEM_NOTE,
            text="Approval response sent: some-unrecognized-word",
            metadata={"approval_id": "appr-3"},
            sequence=2,
        ),
    )
    ingester._drain_available()

    row = [r for r in _facts(storage, "s1") if r["kind"] == "tool_call"][0]
    assert row["approval_decision"] == "declined"


def test_token_record_derives_agent_turn_fact(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    ingester = TelemetryIngester(storage)
    now = datetime.now(UTC)

    storage.record_token_usage(
        "s1",
        TokenUsageRecord(
            record_id="turn-1",
            source="codex",
            observed_at=now,
            totals={"input_tokens": 10},
            model="gpt-5-codex",
            effort="high",
        ),
        init=TokenUsageInit(coverage="entire_waypoint_session", observed_from=now),
    )
    ingester.derive_from_token_record(
        session,
        TokenUsageRecord(
            record_id="turn-1",
            source="codex",
            observed_at=now,
            totals={"input_tokens": 10},
            model="gpt-5-codex",
            effort="high",
        ),
    )
    ingester._drain_available()

    rows = [r for r in _facts(storage, "s1") if r["kind"] == "turn"]
    assert len(rows) == 1
    assert rows[0]["fact_id"] == "turn-1"
    assert rows[0]["turn_kind"] == "agent"
    assert rows[0]["model_at_turn"] == "gpt-5-codex"
    assert rows[0]["effort_at_turn"] == "high"
    assert rows[0]["source"] == "codex"


def test_token_record_without_model_falls_back_to_resolved_model(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1", resolved_model="gpt-5-codex")
    ingester = TelemetryIngester(storage)
    now = datetime.now(UTC)

    ingester.derive_from_token_record(
        session,
        TokenUsageRecord(
            record_id="turn-2", source="codex", observed_at=now, totals={}
        ),
    )
    ingester._drain_available()

    row = [r for r in _facts(storage, "s1") if r["kind"] == "turn"][0]
    assert row["model_at_turn"] == "gpt-5-codex"


def test_context_usage_update_is_rate_limited_to_one_per_minute(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    ingester = TelemetryIngester(storage)
    t0 = datetime.now(UTC).replace(second=1, microsecond=0)

    ingester.derive_from_session_update(
        session,
        {
            "context_usage": SessionContextUsage(
                used_tokens=1000,
                context_window_tokens=10000,
                updated_at=t0,
                source="codex",
            )
        },
    )
    ingester.derive_from_session_update(
        session,
        {
            "context_usage": SessionContextUsage(
                used_tokens=2000,
                context_window_tokens=10000,
                updated_at=t0 + timedelta(seconds=10),
                source="codex",
            )
        },
    )
    ingester._drain_available()

    rows = [r for r in _facts(storage, "s1") if r["kind"] == "context_snapshot"]
    assert len(rows) == 1
    assert rows[0]["used_tokens"] == 2000
    assert rows[0]["occupancy_percent"] == 20.0


def test_rate_limit_usage_derives_one_fact_per_window(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1").model_copy(
        update={"verified_account_key": "acct-abc"}
    )
    ingester = TelemetryIngester(storage)
    now = datetime.now(UTC)

    ingester.derive_from_session_update(
        session,
        {
            "rate_limit_usage": SessionRateLimitUsage(
                source="codex",
                updated_at=now,
                windows=[
                    UsageWindow(id="5h", label="5 hour", used_percent=42.0),
                    UsageWindow(id="7d", label="weekly", used_percent=10.0),
                ],
            )
        },
    )
    ingester._drain_available()

    rows = [r for r in _facts(storage, "s1") if r["kind"] == "limit_snapshot"]
    assert len(rows) == 2
    window_ids = {r["window_id"] for r in rows}
    assert window_ids == {"5h", "7d"}
    # Keyed to the verified account, so windows aggregate across the account's
    # sessions rather than fragmenting per session — but the persisted key is
    # a pseudonymous digest, never the raw verified account key (FR-9).
    account_keys = {r["account_key"] for r in rows}
    assert len(account_keys) == 1
    account_key = next(iter(account_keys))
    assert account_key != "acct-abc"
    assert account_key.startswith("acct_")
    assert all(r["account_label"] == "acct-abc" for r in rows)


def test_rate_limit_usage_carries_profile_label_for_profiled_session(
    tmp_path: Path,
) -> None:
    """A profiled session's limit facts carry the user-chosen local profile
    name — FR-9-safe and always shown, unlike the OAuth-derived account_label."""
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1").model_copy(
        update={
            "verified_account_key": "acct-abc",
            "account_profile_id": "nus",
            "account_profile_label": "nus",
        }
    )
    ingester = TelemetryIngester(storage)

    ingester.derive_from_session_update(
        session,
        {
            "rate_limit_usage": SessionRateLimitUsage(
                source="codex",
                updated_at=datetime.now(UTC),
                windows=[UsageWindow(id="5h", label="5 hour", used_percent=42.0)],
            )
        },
    )
    ingester._drain_available()

    rows = [r for r in _facts(storage, "s1") if r["kind"] == "limit_snapshot"]
    assert len(rows) == 1
    assert rows[0]["profile_label"] == "nus"


def test_rate_limit_usage_profile_label_defaults_for_no_profile_session(
    tmp_path: Path,
) -> None:
    """A session with no account profile gets the humanized "Default" label,
    not a raw pseudonym or ``None``."""
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1").model_copy(
        update={"verified_account_key": "acct-abc"}
    )
    ingester = TelemetryIngester(storage)

    ingester.derive_from_session_update(
        session,
        {
            "rate_limit_usage": SessionRateLimitUsage(
                source="codex",
                updated_at=datetime.now(UTC),
                windows=[UsageWindow(id="5h", label="5 hour", used_percent=42.0)],
            )
        },
    )
    ingester._drain_available()

    rows = [r for r in _facts(storage, "s1") if r["kind"] == "limit_snapshot"]
    assert len(rows) == 1
    assert rows[0]["profile_label"] == "Default"


def test_rate_limit_usage_without_verified_account_derives_nothing(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")  # no verified_account_key
    ingester = TelemetryIngester(storage)
    ingester.derive_from_session_update(
        session,
        {
            "rate_limit_usage": SessionRateLimitUsage(
                source="codex",
                updated_at=datetime.now(UTC),
                windows=[UsageWindow(id="5h", label="5 hour", used_percent=42.0)],
            )
        },
    )
    ingester._drain_available()
    rows = [r for r in _facts(storage, "s1") if r["kind"] == "limit_snapshot"]
    assert rows == []


def test_rate_limit_usage_flows_for_claude_code_without_verified_account(
    tmp_path: Path, registry: BackendRegistry
) -> None:
    """Root-cause regression: a claude_code session carrying rate_limit_usage
    plus org/tier notes but no verified_account_key (never ran a
    verified-account probe) now yields limit facts, resolved via the plugin's
    own ``rate_limit_account`` instead of being silently dropped."""
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1").model_copy(update={"backend": "claude_code"})
    ingester = TelemetryIngester(storage, registry)

    ingester.derive_from_session_update(
        session,
        {
            "rate_limit_usage": SessionRateLimitUsage(
                source="claude_code",
                updated_at=datetime.now(UTC),
                notes=["CLI OAuth", "org: Acme", "org tier: enterprise"],
                windows=[UsageWindow(id="5h", label="5 hour", used_percent=42.0)],
            )
        },
    )
    ingester._drain_available()

    rows = [r for r in _facts(storage, "s1") if r["kind"] == "limit_snapshot"]
    assert len(rows) == 1
    assert rows[0]["account_key"].startswith("acct_")
    assert "Acme" not in rows[0]["account_key"]
    assert rows[0]["account_label"] == "Acme · enterprise"


def test_rate_limit_usage_flows_for_profile_less_codex(
    tmp_path: Path, registry: BackendRegistry
) -> None:
    """A codex session with no launched account profile (and so no verified
    probe) still surfaces its rate-limit windows, via the plugin's email/plan
    notes fallback."""
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")  # backend="codex", no verified key
    ingester = TelemetryIngester(storage, registry)

    ingester.derive_from_session_update(
        session,
        {
            "rate_limit_usage": SessionRateLimitUsage(
                source="codex",
                updated_at=datetime.now(UTC),
                notes=["noppanat@u.nus.edu", "plan: pro"],
                windows=[UsageWindow(id="5h", label="5 hour", used_percent=10.0)],
            )
        },
    )
    ingester._drain_available()

    rows = [r for r in _facts(storage, "s1") if r["kind"] == "limit_snapshot"]
    assert len(rows) == 1
    assert rows[0]["account_key"].startswith("acct_")
    assert "noppanat" not in rows[0]["account_key"]
    assert rows[0]["account_label"] == "noppanat@u.nus.edu · plan: pro"


def test_account_key_is_never_the_raw_email_or_org(
    tmp_path: Path, registry: BackendRegistry
) -> None:
    """FR-9: whichever resolution path fires, the persisted ``account_key``
    is always a digest — the raw identity never reaches the store."""
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    ingester = TelemetryIngester(storage, registry)

    ingester.derive_from_session_update(
        session,
        {
            "rate_limit_usage": SessionRateLimitUsage(
                source="codex",
                updated_at=datetime.now(UTC),
                notes=["secret.user@example.com"],
                windows=[UsageWindow(id="5h", label="5 hour", used_percent=10.0)],
            )
        },
    )
    ingester._drain_available()

    rows = [r for r in _facts(storage, "s1") if r["kind"] == "limit_snapshot"]
    assert len(rows) == 1
    assert "secret.user@example.com" not in rows[0]["account_key"]
    assert rows[0]["account_key"].startswith("acct_")
    # The label is still the human identity (gated behind the API's
    # telemetry_local_labels setting, not at ingest/storage time).
    assert rows[0]["account_label"] == "secret.user@example.com"


def test_pseudonymized_account_key_is_stable_across_sessions(
    tmp_path: Path, registry: BackendRegistry
) -> None:
    """The same raw account always digests to the same account_key, so two
    sessions on the same account still group together."""
    storage = Storage(tmp_path / "db.sqlite")
    ingester = TelemetryIngester(storage, registry)
    session_a = _make_session(storage, "s1")
    session_b = _make_session(storage, "s2")

    for session in (session_a, session_b):
        ingester.derive_from_session_update(
            session,
            {
                "rate_limit_usage": SessionRateLimitUsage(
                    source="codex",
                    updated_at=datetime.now(UTC),
                    notes=["shared@example.com"],
                    windows=[UsageWindow(id="5h", label="5 hour", used_percent=1.0)],
                )
            },
        )
    ingester._drain_available()

    keys = {r["account_key"] for r in _facts(storage) if r["kind"] == "limit_snapshot"}
    assert len(keys) == 1


def test_backfill_derives_facts_and_is_idempotent(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    storage.append_event(
        EventRecord(
            session_id="s1",
            ts=session.created_at,
            kind=EventKind.USER_INPUT,
            text="hello",
            sequence=1,
        )
    )
    storage.record_token_usage(
        "s1",
        TokenUsageRecord(
            record_id="turn-1",
            source="codex",
            observed_at=session.created_at,
            totals={"input_tokens": 5},
            model="gpt-5-codex",
        ),
        init=TokenUsageInit(
            coverage="entire_waypoint_session", observed_from=session.created_at
        ),
    )

    ingester = TelemetryIngester(storage)
    asyncio.run(ingester.backfill())

    rows = _facts(storage, "s1")
    kinds = {r["kind"] for r in rows}
    assert "session_lifecycle" in kinds
    assert "turn" in kinds
    assert storage.telemetry.get_meta("backfill_done") == "true"

    # A second call is a no-op guarded by telemetry_meta, not a re-derive.
    count_before = storage.connection.execute(
        "SELECT COUNT(*) AS n FROM telemetry_facts"
    ).fetchone()["n"]
    asyncio.run(ingester.backfill())
    count_after = storage.connection.execute(
        "SELECT COUNT(*) AS n FROM telemetry_facts"
    ).fetchone()["n"]
    assert count_after == count_before


def test_privacy_no_raw_text_or_paths_persisted(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(
        storage,
        "s1",
        repo_path="/home/user/projects/waypoint",
    )
    ingester = TelemetryIngester(storage)
    now = datetime.now(UTC)
    secret_path = "/home/user/.ssh/id_rsa"
    secret_text = f"cat {secret_path}\nSUPER_SECRET_TOKEN=abc123"

    ingester.derive_from_session_created(session)
    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.USER_INPUT,
            text=secret_text,
            metadata={},
            sequence=1,
        ),
    )
    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.TOOL_CALL,
            text=f'Bash\n{{"command": "cat {secret_path}"}}',
            metadata={
                "tool_use_id": "tool-9",
                "tool_name": "Bash",
                "payload": {"input": {"command": f"cat {secret_path}"}},
            },
            sequence=2,
        ),
    )
    ingester.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.TOOL_RESULT,
            text=secret_text,
            metadata={"tool_use_id": "tool-9", "tool_name": "Bash", "is_error": False},
            sequence=3,
        ),
    )
    ingester._drain_available()

    fact_rows = storage.connection.execute("SELECT * FROM telemetry_facts").fetchall()
    tag_rows = storage.connection.execute("SELECT * FROM telemetry_fact_tag").fetchall()
    serialized = json.dumps(
        [dict(r) for r in fact_rows] + [dict(r) for r in tag_rows], default=str
    )
    assert secret_path not in serialized
    assert "SUPER_SECRET_TOKEN" not in serialized
    assert session.cwd not in serialized
    assert session.raw_log_path not in serialized

    repo_row = next(r for r in fact_rows if r["kind"] == "session_lifecycle")
    assert repo_row["repo_name"] == "waypoint"  # basename only, never the full path
    tool_row = next(r for r in fact_rows if r["kind"] == "tool_call")
    assert tool_row["tool_name"] == "Bash"  # bare name only


def test_start_stop_drains_queued_facts(tmp_path: Path) -> None:
    async def _run() -> None:
        storage = Storage(tmp_path / "db.sqlite")
        session = _make_session(storage, "s1")
        ingester = TelemetryIngester(storage, drain_debounce_seconds=0.01)
        await ingester.start()
        ingester.derive_from_session_created(session)
        await asyncio.sleep(0.2)
        rows = _facts(storage, "s1")
        assert len(rows) == 1
        await ingester.stop()

    asyncio.run(_run())


def test_on_persisted_fires_only_after_the_batch_is_drained(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    # Record the persisted fact count at the moment the callback fires, to
    # prove the dirty signal (broadcast trigger, #10a) can only reflect
    # already-stored facts — never a still-queued enqueue.
    persisted_counts: list[int] = []
    ingester = TelemetryIngester(
        storage,
        on_persisted=lambda: persisted_counts.append(len(_facts(storage, "s1"))),
    )

    ingester.derive_from_session_created(session)
    assert persisted_counts == []  # enqueue alone must not signal
    assert _facts(storage, "s1") == []

    ingester._drain_available()
    assert persisted_counts == [1]  # signalled once, after the fact was stored


def test_seed_last_transitions_dedups_across_restart(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    session = _make_session(storage, "s1")
    now = datetime.now(UTC)

    first = TelemetryIngester(storage)
    first.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.SYSTEM_NOTE,
            text="a",
            metadata={"status": SessionStatus.IDLE},
            sequence=5,
        ),
    )
    first._drain_available()
    before = [r for r in _facts(storage, "s1") if r["kind"] == "session_lifecycle"]
    assert len(before) == 1

    # A fresh ingester models a process restart: without seeding, the next
    # IDLE event (new sequence → new fact_id) would dodge the store's PK dedup
    # and re-mint a duplicate transition (#10b).
    second = TelemetryIngester(storage)
    second._seed_last_transitions()
    second.derive_from_event(
        session,
        EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.SYSTEM_NOTE,
            text="b",
            metadata={"status": SessionStatus.IDLE},
            sequence=9,
        ),
    )
    second._drain_available()

    after = [r for r in _facts(storage, "s1") if r["kind"] == "session_lifecycle"]
    assert len(after) == 1
    assert after[0]["fact_id"] == "s1:5"
