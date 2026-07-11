"""Generic derivation from normalized signals into telemetry facts (CONTRACT.md §3).

``TelemetryIngester`` consumes already-normalized ``EventRecord``s, session-field
updates, and per-turn token-ledger records — it never branches on backend id.
Each ``derive_from_*`` call is a fast, synchronous, error-swallowing enqueue
(never raises into a turn); a runtime-owned background task drains the queue
in small batches so a burst of events never holds the sqlite writer for long.

The runtime (not this module) is responsible for constructing one
``TelemetryIngester`` per process, calling the ``derive_from_*`` methods from
its event/session-update/token-ledger seams, starting/stopping the drain task
alongside its own lifecycle, and invoking ``backfill()`` once at boot.
"""

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from waypoint.backends.approvals import is_approve_decision
from waypoint.schemas import (
    EventKind,
    EventRecord,
    SessionContextUsage,
    SessionRateLimitUsage,
    SessionRecord,
    SessionStatus,
    TokenUsageRecord,
)
from waypoint.storage import Storage
from waypoint.telemetry.facts import (
    ApprovalDecision,
    ContextSnapshotFact,
    FactDimensions,
    FactSource,
    LifecycleTransition,
    LimitSnapshotFact,
    SessionLifecycleFact,
    TelemetryFact,
    ToolCallFact,
    ToolOutcome,
    TurnFact,
    TurnKind,
)
from waypoint.usage_dashboard import _PluginRegistry, resolve_account

log = logging.getLogger("waypoint.telemetry.ingest")

_STATUS_TO_TRANSITION: dict[str, LifecycleTransition] = {
    SessionStatus.STARTING: LifecycleTransition.STARTING,
    SessionStatus.RUNNING: LifecycleTransition.RUNNING,
    SessionStatus.IDLE: LifecycleTransition.IDLE,
    SessionStatus.WAITING_INPUT: LifecycleTransition.WAITING,
    SessionStatus.INTERRUPTED: LifecycleTransition.INTERRUPTED,
    SessionStatus.EXITED: LifecycleTransition.EXITED,
    SessionStatus.ERROR: LifecycleTransition.ERROR,
}

# The exact text ``SessionRuntime.approve()`` records via ``_record_system_event``
# (a generic, backend-neutral runtime path, not a per-plugin one) — the only
# place a decision on an ``APPROVAL_REQUEST`` currently surfaces at all.
_APPROVAL_DECISION_PREFIX = "Approval response sent: "

# ``ContextSnapshotFact`` is rate-limited to one per session per minute bucket
# (CONTRACT.md §3); this is the strftime pattern for that bucket.
_MINUTE_BUCKET_FORMAT = "%Y%m%dT%H%M"


def _dims_for_session(session: SessionRecord) -> FactDimensions:
    repo_name = (
        os.path.basename(session.repo_name.rstrip("/")) if session.repo_name else None
    )
    return FactDimensions(
        backend=session.backend,
        repo_name=repo_name or None,
        source=session.source,
        transport=session.transport,
        spawner_session_id=session.spawner_session_id,
        is_child=session.spawner_session_id is not None,
    )


def _tool_use_id(metadata: Mapping[str, Any]) -> str | None:
    value = metadata.get("tool_use_id") or metadata.get("item_id")
    return value if isinstance(value, str) and value else None


def _tool_name(metadata: Mapping[str, Any]) -> str | None:
    value = metadata.get("tool_name")
    return value if isinstance(value, str) and value else None


def _approval_id(metadata: Mapping[str, Any]) -> str | None:
    value = metadata.get("approval_id")
    return value if isinstance(value, str) and value else None


def _approval_decision_from_event(
    event: EventRecord,
) -> tuple[str, ApprovalDecision] | None:
    approval_id = _approval_id(event.metadata)
    if approval_id is None or not event.text.startswith(_APPROVAL_DECISION_PREFIX):
        return None
    word = event.text[len(_APPROVAL_DECISION_PREFIX) :].strip()
    # An unrecognized word is treated as a decline, never skipped — skipping
    # would strand the fact at REQUESTED forever even though a decision was
    # actually made.
    decision = (
        ApprovalDecision.APPROVED
        if is_approve_decision(word)
        else ApprovalDecision.DECLINED
    )
    return approval_id, decision


def _epoch_ms(value: datetime) -> int:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return int(aware.timestamp() * 1000)


# Length of the pseudonymous digest suffix on a telemetry ``account_key``
# (e.g. ``acct_1a2b3c4d``) — short enough to stay a label, long enough that
# collisions across the handful of accounts one Waypoint instance sees are
# not a practical concern.
_ACCOUNT_KEY_DIGEST_LENGTH = 8


def _pseudonymize_account_key(raw_account_key: str) -> str:
    """A stable, non-reversible grouping key for a raw account identity (FR-9).

    ``raw_account_key`` (e.g. ``codex:noppanat@u.nus.edu``, ``claude_code:lumid``)
    must never reach the store or the API verbatim; only this digest does.
    Stable across process restarts (plain SHA-256, no per-process salt) so the
    same account always groups under the same ``account_key``.
    """
    digest = hashlib.sha256(raw_account_key.encode("utf-8")).hexdigest()
    return f"acct_{digest[:_ACCOUNT_KEY_DIGEST_LENGTH]}"


class TelemetryIngester:
    """Enqueue-and-drain seam between normalized signals and ``TelemetryStore``."""

    def __init__(
        self,
        storage: Storage,
        registry: _PluginRegistry | None = None,
        *,
        batch_size: int = 200,
        drain_debounce_seconds: float = 1.0,
    ) -> None:
        self._storage = storage
        self._store = storage.telemetry
        # Backend-plugin lookup for resolving a rate-limit snapshot's account
        # when the session has no verified probe (``resolve_account``). Only
        # ``None`` in tests that don't exercise that fallback; the runtime
        # always supplies its real registry.
        self._registry = registry
        self._batch_size = batch_size
        self._drain_debounce_seconds = drain_debounce_seconds
        self._queue: list[tuple[TelemetryFact, dict[str, str]]] = []
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        # Transient cross-event correlation, keyed by (session_id, id). Small
        # and self-cleaning (each entry is popped once its counterpart event
        # arrives); never persisted.
        self._pending_tool_calls: dict[tuple[str, str], tuple[datetime, str | None]] = (
            {}
        )
        self._pending_approvals: dict[tuple[str, str], str | None] = {}
        # Nearly every event stamps ``metadata["status"]`` (STATUS_UPDATE is
        # never actually emitted; see the note below), so without this a
        # single turn's worth of same-status events would each mint their own
        # lifecycle fact. Only a genuine transition is worth recording.
        self._last_transition: dict[str, LifecycleTransition] = {}

    # ── enqueue (fast path; called inline from runtime signal points) ──────

    def derive_from_session_created(self, session: SessionRecord) -> None:
        try:
            self._enqueue(
                SessionLifecycleFact(
                    fact_id=f"{session.id}:created",
                    source=FactSource.RUNTIME,
                    session_id=session.id,
                    occurred_at=session.created_at,
                    dims=_dims_for_session(session),
                    transition=LifecycleTransition.CREATED,
                ),
                session.tags,
            )
        except Exception:
            log.debug(
                "telemetry derivation failed for session-created",
                extra={"session_id": session.id},
                exc_info=True,
            )

    def derive_from_event(self, session: SessionRecord, event: EventRecord) -> None:
        try:
            self._derive_from_event(session, event)
        except Exception:
            log.debug(
                "telemetry derivation failed for event",
                extra={"session_id": session.id, "kind": event.kind},
                exc_info=True,
            )

    def derive_from_token_record(
        self, session: SessionRecord, record: TokenUsageRecord
    ) -> None:
        try:
            self._derive_from_token_record(session, record)
        except Exception:
            log.debug(
                "telemetry derivation failed for token record",
                extra={"session_id": session.id},
                exc_info=True,
            )

    def derive_from_session_update(
        self, session: SessionRecord, updates: Mapping[str, Any]
    ) -> None:
        try:
            self._derive_from_session_update(session, updates)
        except Exception:
            log.debug(
                "telemetry derivation failed for session update",
                extra={"session_id": session.id, "fields": sorted(updates)},
                exc_info=True,
            )

    # ── derivation ──────────────────────────────────────────────────────────

    def _derive_from_event(self, session: SessionRecord, event: EventRecord) -> None:
        dims = _dims_for_session(session)
        tags = session.tags

        # ``status`` may be a ``SessionStatus`` member or its plain string
        # value (both hash/compare equal since ``SessionStatus`` is a
        # ``StrEnum``, so one lookup handles either).
        status = event.metadata.get("status")
        transition = (
            _STATUS_TO_TRANSITION.get(status) if isinstance(status, str) else None
        )
        if (
            transition is not None
            and self._last_transition.get(session.id) != transition
        ):
            self._last_transition[session.id] = transition
            self._enqueue(
                SessionLifecycleFact(
                    fact_id=f"{session.id}:{event.sequence}",
                    source=FactSource.RUNTIME,
                    session_id=session.id,
                    occurred_at=event.ts,
                    dims=dims,
                    transition=transition,
                ),
                tags,
            )

        if event.kind == EventKind.USER_INPUT:
            self._enqueue(
                TurnFact(
                    fact_id=f"{session.id}:{event.sequence}",
                    source=session.backend,
                    session_id=session.id,
                    occurred_at=event.ts,
                    dims=dims,
                    turn_kind=TurnKind.USER,
                    model_at_turn=session.resolved_model,
                ),
                tags,
            )
        elif event.kind == EventKind.TOOL_CALL:
            tool_use_id = _tool_use_id(event.metadata)
            if tool_use_id is not None:
                tool_name = _tool_name(event.metadata)
                self._pending_tool_calls[(session.id, tool_use_id)] = (
                    event.ts,
                    tool_name,
                )
                self._enqueue(
                    ToolCallFact(
                        fact_id=tool_use_id,
                        source=session.backend,
                        session_id=session.id,
                        occurred_at=event.ts,
                        dims=dims,
                        tool_name=tool_name or "unknown",
                        outcome=ToolOutcome.UNKNOWN,
                    ),
                    tags,
                )
        elif event.kind == EventKind.TOOL_RESULT:
            tool_use_id = _tool_use_id(event.metadata)
            if tool_use_id is not None:
                pending = self._pending_tool_calls.pop((session.id, tool_use_id), None)
                call_ts, call_tool_name = pending if pending else (None, None)
                duration_ms = (
                    max(0, int((event.ts - call_ts).total_seconds() * 1000))
                    if call_ts is not None
                    else None
                )
                is_error = event.metadata.get("is_error")
                outcome = ToolOutcome.UNKNOWN
                if isinstance(is_error, bool):
                    outcome = ToolOutcome.FAILED if is_error else ToolOutcome.SUCCEEDED
                # The tool_result event carries no tool_name (only the paired
                # tool_call does), and this revision-1 fact overwrites the
                # revision-0 one, so carry the name forward from the pending
                # call — otherwise every resolved tool collapses to "unknown".
                self._enqueue(
                    ToolCallFact(
                        fact_id=tool_use_id,
                        source=session.backend,
                        session_id=session.id,
                        occurred_at=event.ts,
                        revision=1,
                        dims=dims,
                        tool_name=_tool_name(event.metadata)
                        or call_tool_name
                        or "unknown",
                        outcome=outcome,
                        duration_ms=duration_ms,
                    ),
                    tags,
                )
        elif event.kind == EventKind.APPROVAL_REQUEST:
            approval_id = _approval_id(event.metadata)
            if approval_id is not None:
                tool_name = _tool_name(event.metadata)
                self._pending_approvals[(session.id, approval_id)] = tool_name
                self._enqueue(
                    ToolCallFact(
                        fact_id=approval_id,
                        source=session.backend,
                        session_id=session.id,
                        occurred_at=event.ts,
                        dims=dims,
                        tool_name=tool_name or "unknown",
                        outcome=ToolOutcome.UNKNOWN,
                        approval_decision=ApprovalDecision.REQUESTED,
                    ),
                    tags,
                )

        decision = _approval_decision_from_event(event)
        if decision is not None:
            approval_id, mapped = decision
            tool_name = self._pending_approvals.pop((session.id, approval_id), None)
            self._enqueue(
                ToolCallFact(
                    fact_id=approval_id,
                    source=session.backend,
                    session_id=session.id,
                    occurred_at=event.ts,
                    revision=1,
                    dims=dims,
                    tool_name=tool_name or "unknown",
                    outcome=ToolOutcome.UNKNOWN,
                    approval_decision=mapped,
                ),
                tags,
            )

    def _derive_from_token_record(
        self, session: SessionRecord, record: TokenUsageRecord
    ) -> None:
        if not record.record_id:
            return
        # Best-effort fallback when the plugin can't resolve a precise
        # per-turn model: the session's last-known resolved model, never a
        # guess (CONTRACT.md §3 backfill note; applies live too).
        model_at_turn = record.model or session.resolved_model
        self._enqueue(
            TurnFact(
                fact_id=record.record_id,
                source=record.source,
                session_id=session.id,
                occurred_at=record.observed_at,
                # A corrected record keeps the same identity but should still
                # win over the earlier sighting, so the rollup (which joins
                # back to the live ledger row) gets recomputed for it.
                revision=_epoch_ms(record.observed_at),
                dims=_dims_for_session(session),
                turn_kind=TurnKind.AGENT,
                model_at_turn=model_at_turn,
                effort_at_turn=record.effort,
            ),
            session.tags,
        )

    def _derive_from_session_update(
        self, session: SessionRecord, updates: Mapping[str, Any]
    ) -> None:
        dims = _dims_for_session(session)
        tags = session.tags

        context_usage = updates.get("context_usage")
        if isinstance(context_usage, SessionContextUsage):
            occupancy = None
            if context_usage.context_window_tokens:
                occupancy = (
                    context_usage.used_tokens
                    / context_usage.context_window_tokens
                    * 100
                )
            bucket = context_usage.updated_at.strftime(_MINUTE_BUCKET_FORMAT)
            self._enqueue(
                ContextSnapshotFact(
                    fact_id=f"{session.id}:{bucket}",
                    source=context_usage.source,
                    session_id=session.id,
                    occurred_at=context_usage.updated_at,
                    revision=_epoch_ms(context_usage.updated_at),
                    dims=dims,
                    used_tokens=context_usage.used_tokens,
                    window_tokens=context_usage.context_window_tokens,
                    occupancy_percent=occupancy,
                ),
                tags,
            )

        rate_limit_usage = updates.get("rate_limit_usage")
        if isinstance(rate_limit_usage, SessionRateLimitUsage):
            # Provider limits are account-scoped (FR-6). Without a resolvable
            # account the snapshot can't be attributed to one, so skip it
            # rather than mint a per-session pseudo-account that fragments
            # the account-scoped limit view into one row per session. Prefers
            # the session's verified probe, falling back to the plugin's own
            # ``rate_limit_account`` (e.g. CC/profile-less-Codex org/email
            # notes) so sessions that never ran a verified-account probe
            # still surface (root cause of the "only Codex shows" bug).
            resolved = resolve_account(
                rate_limit_usage,
                registry=self._registry,
                verified_account_key=session.verified_account_key,
                verified_account_label=session.verified_account_label,
            )
            if resolved is None:
                return
            raw_account_key, account_label = resolved
            # FR-9: only the pseudonymous digest is persisted as account_key;
            # the raw identity never reaches the store or the API.
            account_key = _pseudonymize_account_key(raw_account_key)
            for window in rate_limit_usage.windows:
                self._enqueue(
                    LimitSnapshotFact(
                        fact_id=(
                            f"{account_key}:{window.id}:"
                            f"{rate_limit_usage.updated_at.isoformat()}"
                        ),
                        source=rate_limit_usage.source,
                        session_id=session.id,
                        occurred_at=rate_limit_usage.updated_at,
                        dims=dims,
                        account_key=account_key,
                        account_label=account_label,
                        window_id=window.id,
                        window_label=window.label,
                        used_percent=window.used_percent,
                        resets_at=window.resets_at,
                    ),
                    tags,
                )

    def _enqueue(self, fact: TelemetryFact, tags: Mapping[str, str]) -> None:
        self._queue.append((fact, dict(tags)))
        self._wake.set()

    # ── drain (runtime-owned background task) ──────────────────────────────

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._drain_loop(), name="telemetry-ingest-drain"
            )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        # Best-effort final flush so nothing enqueued right before shutdown
        # is silently dropped.
        self._drain_available()

    async def _drain_loop(self) -> None:
        while True:
            await self._wake.wait()
            await asyncio.sleep(self._drain_debounce_seconds)
            self._wake.clear()
            await self._drain_available_yielding()

    async def _drain_available_yielding(self) -> None:
        while self._queue:
            batch, self._queue = (
                self._queue[: self._batch_size],
                self._queue[self._batch_size :],
            )
            self._ingest_batch(batch)
            await asyncio.sleep(0)

    def _drain_available(self) -> None:
        while self._queue:
            batch, self._queue = (
                self._queue[: self._batch_size],
                self._queue[self._batch_size :],
            )
            self._ingest_batch(batch)

    def _ingest_batch(self, batch: list[tuple[TelemetryFact, dict[str, str]]]) -> None:
        try:
            self._store.ingest_facts(batch)
        except Exception:
            log.debug("telemetry drain batch failed", exc_info=True)

    # ── backfill (one-shot, off the hot path) ───────────────────────────────

    async def backfill(self) -> None:
        """One guarded pass deriving facts from existing data, then a rollup rebuild.

        Guarded by ``telemetry_meta['backfill_done']`` so it runs at most once
        ever per database; safe to call unconditionally at boot.
        """
        if self._store.get_meta("backfill_done") == "true":
            return
        for session in self._storage.list_sessions():
            self.derive_from_session_created(session)
            for event in self._storage.list_events(session.id):
                self.derive_from_event(session, event)
            for record in self._ledger_records(session.id):
                self.derive_from_token_record(session, record)
            if session.context_usage is not None:
                self.derive_from_session_update(
                    session, {"context_usage": session.context_usage}
                )
            if session.rate_limit_usage is not None:
                self.derive_from_session_update(
                    session, {"rate_limit_usage": session.rate_limit_usage}
                )
            await self._drain_available_yielding()
        self._store.rebuild_rollups_from_facts()
        now = datetime.now(UTC).isoformat()
        self._store.set_meta("backfill_through", now)
        self._store.set_meta("backfill_done", "true")

    def _ledger_records(self, session_id: str) -> list[TokenUsageRecord]:
        rows = self._storage.connection.execute(
            """
            SELECT source, record_id, observed_at, usage_json
            FROM session_token_usage_records
            WHERE session_id = ? ORDER BY observed_at ASC
            """,
            (session_id,),
        ).fetchall()
        records: list[TokenUsageRecord] = []
        for row in rows:
            try:
                usage = json.loads(row["usage_json"])
            except json.JSONDecodeError:
                continue
            if not isinstance(usage, dict):
                continue
            records.append(
                TokenUsageRecord(
                    record_id=row["record_id"],
                    source=row["source"],
                    observed_at=datetime.fromisoformat(row["observed_at"]),
                    totals=usage.get("totals") or {},
                    display_total_tokens=usage.get("display_total_tokens"),
                    model=usage.get("model"),
                    effort=usage.get("effort"),
                )
            )
        return records
