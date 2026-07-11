"""SQLite-backed storage for the telemetry fact contract (CONTRACT.md §1).

Shares the single connection + lock ``Storage`` owns (WAL is single-writer;
this module never opens a second connection). Every public method acquires
the shared lock, mirroring ``Storage``'s ``_synchronized`` decorator.

Rollup maintenance is implemented as recompute-on-write rather than a hand
-rolled incremental delta: ``_recompute_rollup_key`` recomputes one rollup
row from scratch from ``telemetry_facts`` (a query bounded to one
day/backend/model/repo/source/transport/is_child key, cheap at personal-
instance scale) and both ``ingest_fact`` and ``rebuild_rollups_from_facts``
call it. This makes delta-update and full-rebuild the same code path by
construction, instead of two independently-maintained implementations that
could drift apart.
"""

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from waypoint.telemetry.facts import (
    ContextSnapshotFact,
    LimitSnapshotFact,
    SessionLifecycleFact,
    TelemetryFact,
    TelemetryFactKind,
    TelemetryFilter,
    TelemetryRange,
    ToolCallFact,
    TurnFact,
    TurnKind,
)

# Filter terms for ``TelemetryFilter.tags`` are ``key:value`` strings joined
# against the ``telemetry_fact_tag`` side table.
_TAG_FILTER_SEPARATOR = ":"


def _iso_utc(value: datetime) -> str:
    """Normalize to a UTC ISO8601 string so lexical and chronological order agree."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _day_key(occurred_at: datetime) -> str:
    """The host-tz calendar day an instant falls on (CONTRACT.md §1c: "day in host tz").

    Uses the process's local system timezone via naive ``astimezone()`` — this
    deployment has no separate configured display timezone yet.
    """
    aware = (
        occurred_at
        if occurred_at.tzinfo is not None
        else occurred_at.replace(tzinfo=UTC)
    )
    return aware.astimezone().date().isoformat()


def _day_bounds_utc(day: str) -> tuple[str, str]:
    """The [start, end) UTC instants spanning a host-tz calendar day."""
    day_date = date.fromisoformat(day)
    start_local = datetime.combine(day_date, datetime.min.time())
    start_utc = start_local.astimezone(UTC)
    end_utc = (start_local + timedelta(days=1)).astimezone(UTC)
    return start_utc.isoformat(), end_utc.isoformat()


def _parse_tag_term(term: str) -> tuple[str, str] | None:
    """Parse a ``key:value`` filter term; ``None`` when it doesn't fit that shape."""
    if _TAG_FILTER_SEPARATOR not in term:
        return None
    key, _, value = term.partition(_TAG_FILTER_SEPARATOR)
    if not key:
        return None
    return key, value


class TelemetryStore:
    """Owns the ``telemetry_*`` tables on the shared ``Storage`` connection."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS telemetry_facts (
                  kind            TEXT NOT NULL,
                  source          TEXT NOT NULL,
                  fact_id         TEXT NOT NULL,
                  revision        INTEGER NOT NULL DEFAULT 0,
                  partial         INTEGER NOT NULL DEFAULT 0,
                  session_id      TEXT NOT NULL,
                  occurred_at     TEXT NOT NULL,
                  recorded_at     TEXT NOT NULL,
                  schema_version  INTEGER NOT NULL DEFAULT 1,
                  backend         TEXT NOT NULL,
                  repo_name       TEXT,
                  src_source      TEXT NOT NULL,
                  transport       TEXT NOT NULL,
                  spawner_session_id TEXT,
                  is_child        INTEGER NOT NULL DEFAULT 0,
                  transition      TEXT,
                  turn_kind       TEXT,
                  model_at_turn   TEXT,
                  effort_at_turn  TEXT,
                  tool_name       TEXT,
                  tool_category   TEXT,
                  outcome         TEXT,
                  duration_ms     INTEGER,
                  approval_decision TEXT,
                  used_tokens     INTEGER,
                  window_tokens   INTEGER,
                  occupancy_percent REAL,
                  account_key     TEXT,
                  window_id       TEXT,
                  window_label    TEXT,
                  used_percent    REAL,
                  resets_at       TEXT,
                  PRIMARY KEY (kind, source, fact_id)
                );
                CREATE INDEX IF NOT EXISTS idx_tf_kind_time      ON telemetry_facts(kind, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_tf_kind_backend_time ON telemetry_facts(kind, backend, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_tf_session        ON telemetry_facts(session_id);
                CREATE INDEX IF NOT EXISTS idx_tf_time           ON telemetry_facts(occurred_at);

                CREATE TABLE IF NOT EXISTS telemetry_fact_tag (
                  kind TEXT NOT NULL, source TEXT NOT NULL, fact_id TEXT NOT NULL,
                  key TEXT NOT NULL, value TEXT NOT NULL,
                  PRIMARY KEY (kind, source, fact_id, key)
                );
                CREATE INDEX IF NOT EXISTS idx_tft_kv ON telemetry_fact_tag(key, value);

                CREATE TABLE IF NOT EXISTS telemetry_daily_rollup (
                  day TEXT NOT NULL,
                  backend TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
                  repo_name TEXT NOT NULL DEFAULT '', src_source TEXT NOT NULL DEFAULT '',
                  transport TEXT NOT NULL DEFAULT '', is_child INTEGER NOT NULL DEFAULT 0,
                  metrics_json TEXT NOT NULL,
                  PRIMARY KEY (day, backend, model, repo_name, src_source, transport, is_child)
                );

                CREATE TABLE IF NOT EXISTS telemetry_insight_dismissal (
                  signature TEXT NOT NULL, range_key TEXT NOT NULL, dismissed_at TEXT NOT NULL,
                  PRIMARY KEY (signature, range_key)
                );
                CREATE TABLE IF NOT EXISTS telemetry_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);

                -- Internal bookkeeping (not part of the frozen contract's public
                -- tables): dedupes which sessions contributed to a rollup key so
                -- ``active_denom`` is an exact distinct count rather than an
                -- estimate, and survives partial recomputation.
                CREATE TABLE IF NOT EXISTS telemetry_rollup_session (
                  day TEXT NOT NULL, backend TEXT NOT NULL, model TEXT NOT NULL,
                  repo_name TEXT NOT NULL, src_source TEXT NOT NULL,
                  transport TEXT NOT NULL, is_child INTEGER NOT NULL,
                  session_id TEXT NOT NULL,
                  PRIMARY KEY (day, backend, model, repo_name, src_source, transport, is_child, session_id)
                );
                """)
            self._conn.commit()

    # ── ingest ────────────────────────────────────────────────────────────

    def ingest_fact(
        self, fact: TelemetryFact, *, tags: dict[str, str] | None = None
    ) -> bool:
        with self._lock:
            wrote = self._ingest_fact_locked(fact, tags or {})
            self._conn.commit()
            return wrote

    def ingest_facts(
        self, facts: Iterable[tuple[TelemetryFact, dict[str, str]]]
    ) -> int:
        with self._lock:
            written = sum(
                1 for fact, tags in facts if self._ingest_fact_locked(fact, tags)
            )
            self._conn.commit()
            return written

    def _ingest_fact_locked(self, fact: TelemetryFact, tags: dict[str, str]) -> bool:
        existing = self._conn.execute(
            """
            SELECT revision, occurred_at, backend, model_at_turn, repo_name,
                   src_source, transport, is_child
            FROM telemetry_facts WHERE kind = ? AND source = ? AND fact_id = ?
            """,
            (fact.kind, fact.source, fact.fact_id),
        ).fetchone()
        if existing is not None and existing["revision"] > fact.revision:
            return False
        if existing is not None and existing["revision"] == fact.revision:
            return False

        row = _row_from_fact(fact)
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        assignments = ", ".join(
            f"{c} = excluded.{c}"
            for c in columns
            if c not in ("kind", "source", "fact_id")
        )
        self._conn.execute(
            f"""
            INSERT INTO telemetry_facts ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(kind, source, fact_id) DO UPDATE SET {assignments}
            """,
            [row[c] for c in columns],
        )

        self._conn.execute(
            "DELETE FROM telemetry_fact_tag WHERE kind = ? AND source = ? AND fact_id = ?",
            (fact.kind, fact.source, fact.fact_id),
        )
        for key, value in tags.items():
            self._conn.execute(
                """
                INSERT INTO telemetry_fact_tag (kind, source, fact_id, key, value)
                VALUES (?, ?, ?, ?, ?)
                """,
                (fact.kind, fact.source, fact.fact_id, key, value),
            )

        keys_to_recompute: set[tuple[str, str, str, str, str, str, int]] = set()
        if existing is not None:
            keys_to_recompute.add(
                (
                    _day_key(datetime.fromisoformat(existing["occurred_at"])),
                    existing["backend"],
                    existing["model_at_turn"] or "",
                    existing["repo_name"] or "",
                    existing["src_source"],
                    existing["transport"],
                    int(existing["is_child"]),
                )
            )
        keys_to_recompute.add(
            (
                _day_key(fact.occurred_at),
                fact.dims.backend,
                row["model_at_turn"] or "",
                fact.dims.repo_name or "",
                fact.dims.source,
                fact.dims.transport,
                int(fact.dims.is_child),
            )
        )
        for rollup_key in keys_to_recompute:
            self._recompute_rollup_key(*rollup_key)
        return True

    # ── maintenance ───────────────────────────────────────────────────────

    def prune(
        self, *, facts_before: datetime, rollups_before: datetime
    ) -> dict[str, int]:
        with self._lock:
            facts_cutoff = _iso_utc(facts_before)
            fact_ids = self._conn.execute(
                "SELECT kind, source, fact_id FROM telemetry_facts WHERE occurred_at < ?",
                (facts_cutoff,),
            ).fetchall()
            for row in fact_ids:
                self._conn.execute(
                    "DELETE FROM telemetry_fact_tag WHERE kind = ? AND source = ? AND fact_id = ?",
                    (row["kind"], row["source"], row["fact_id"]),
                )
            cursor = self._conn.execute(
                "DELETE FROM telemetry_facts WHERE occurred_at < ?", (facts_cutoff,)
            )
            facts_removed = cursor.rowcount or 0

            rollups_cutoff = _day_key(rollups_before)
            cursor = self._conn.execute(
                "DELETE FROM telemetry_daily_rollup WHERE day < ?", (rollups_cutoff,)
            )
            rollups_removed = cursor.rowcount or 0
            self._conn.execute(
                "DELETE FROM telemetry_rollup_session WHERE day < ?", (rollups_cutoff,)
            )
            self._conn.commit()
            return {"facts": facts_removed, "rollups": rollups_removed}

    def rebuild_rollups_from_facts(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM telemetry_daily_rollup")
            self._conn.execute("DELETE FROM telemetry_rollup_session")
            rows = self._conn.execute("""
                SELECT DISTINCT backend, COALESCE(model_at_turn, '') AS model,
                       COALESCE(repo_name, '') AS repo_name, src_source, transport, is_child,
                       occurred_at
                FROM telemetry_facts WHERE partial = 0
                """).fetchall()
            keys = {
                (
                    _day_key(datetime.fromisoformat(row["occurred_at"])),
                    row["backend"],
                    row["model"],
                    row["repo_name"],
                    row["src_source"],
                    row["transport"],
                    int(row["is_child"]),
                )
                for row in rows
            }
            for key in keys:
                self._recompute_rollup_key(*key)
            self._conn.commit()

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT kind, source, fact_id, occurred_at, backend,
                       COALESCE(model_at_turn, '') AS model, COALESCE(repo_name, '') AS repo_name,
                       src_source, transport, is_child
                FROM telemetry_facts WHERE session_id = ?
                """,
                (session_id,),
            ).fetchall()
            affected_keys = {
                (
                    _day_key(datetime.fromisoformat(row["occurred_at"])),
                    row["backend"],
                    row["model"],
                    row["repo_name"],
                    row["src_source"],
                    row["transport"],
                    int(row["is_child"]),
                )
                for row in rows
            }
            for row in rows:
                self._conn.execute(
                    "DELETE FROM telemetry_fact_tag WHERE kind = ? AND source = ? AND fact_id = ?",
                    (row["kind"], row["source"], row["fact_id"]),
                )
            self._conn.execute(
                "DELETE FROM telemetry_facts WHERE session_id = ?", (session_id,)
            )
            for key in affected_keys:
                self._recompute_rollup_key(*key)
            self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT v FROM telemetry_meta WHERE k = ?", (key,)
            ).fetchone()
            return row["v"] if row is not None else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO telemetry_meta (k, v) VALUES (?, ?)
                ON CONFLICT(k) DO UPDATE SET v = excluded.v
                """,
                (key, value),
            )
            self._conn.commit()

    # ── rollup recompute ──────────────────────────────────────────────────

    def _recompute_rollup_key(
        self,
        day: str,
        backend: str,
        model: str,
        repo_name: str,
        src_source: str,
        transport: str,
        is_child: int,
    ) -> None:
        start_utc, end_utc = _day_bounds_utc(day)
        rows = self._conn.execute(
            """
            SELECT kind, turn_kind, transition, tool_name, outcome, session_id, source, fact_id
            FROM telemetry_facts
            WHERE backend = ? AND COALESCE(model_at_turn, '') = ? AND COALESCE(repo_name, '') = ?
              AND src_source = ? AND transport = ? AND is_child = ? AND partial = 0
              AND occurred_at >= ? AND occurred_at < ?
            """,
            (
                backend,
                model,
                repo_name,
                src_source,
                transport,
                is_child,
                start_utc,
                end_utc,
            ),
        ).fetchall()

        self._conn.execute(
            """
            DELETE FROM telemetry_rollup_session
            WHERE day = ? AND backend = ? AND model = ? AND repo_name = ?
              AND src_source = ? AND transport = ? AND is_child = ?
            """,
            (day, backend, model, repo_name, src_source, transport, is_child),
        )
        if not rows:
            self._conn.execute(
                """
                DELETE FROM telemetry_daily_rollup
                WHERE day = ? AND backend = ? AND model = ? AND repo_name = ?
                  AND src_source = ? AND transport = ? AND is_child = ?
                """,
                (day, backend, model, repo_name, src_source, transport, is_child),
            )
            return

        turns_user = 0
        turns_agent = 0
        tool_calls = 0
        tool_outcomes: dict[str, int] = {}
        lifecycle: dict[str, int] = {}
        tokens: dict[str, int] = {}
        display_totals: list[int | None] = []
        session_ids: set[str] = set()

        for row in rows:
            session_ids.add(row["session_id"])
            kind = row["kind"]
            if kind == TelemetryFactKind.SESSION_LIFECYCLE:
                lifecycle[row["transition"]] = lifecycle.get(row["transition"], 0) + 1
            elif kind == TelemetryFactKind.TURN:
                if row["turn_kind"] == TurnKind.USER:
                    turns_user += 1
                elif row["turn_kind"] == TurnKind.AGENT:
                    turns_agent += 1
                    ledger = self._agent_turn_tokens(
                        row["session_id"], row["source"], row["fact_id"]
                    )
                    if ledger is None:
                        display_totals.append(None)
                    else:
                        totals, display_total = ledger
                        for category, amount in totals.items():
                            tokens[category] = tokens.get(category, 0) + amount
                        display_totals.append(display_total)
            elif kind == TelemetryFactKind.TOOL_CALL:
                tool_calls += 1
                tool_outcomes[row["outcome"]] = tool_outcomes.get(row["outcome"], 0) + 1

        for session_id in session_ids:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO telemetry_rollup_session
                    (day, backend, model, repo_name, src_source, transport, is_child, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day,
                    backend,
                    model,
                    repo_name,
                    src_source,
                    transport,
                    is_child,
                    session_id,
                ),
            )

        display_total = (
            sum(d for d in display_totals if d is not None)
            if display_totals and all(d is not None for d in display_totals)
            else None
        )
        metrics = {
            "tokens": tokens,
            "display_total": display_total,
            "turns_user": turns_user,
            "turns_agent": turns_agent,
            "tool_calls": tool_calls,
            "tool_outcomes": tool_outcomes,
            "lifecycle": lifecycle,
            "active_denom": len(session_ids),
        }
        self._conn.execute(
            """
            INSERT INTO telemetry_daily_rollup
                (day, backend, model, repo_name, src_source, transport, is_child, metrics_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day, backend, model, repo_name, src_source, transport, is_child)
            DO UPDATE SET metrics_json = excluded.metrics_json
            """,
            (
                day,
                backend,
                model,
                repo_name,
                src_source,
                transport,
                is_child,
                json.dumps(metrics),
            ),
        )

    def _agent_turn_tokens(
        self, session_id: str, source: str, fact_id: str
    ) -> tuple[dict[str, int], int | None] | None:
        """Look up the per-turn token ledger row an AGENT ``TurnFact`` derives from.

        The ledger (``session_token_usage_records``) is the source of truth for
        token amounts (plan §2.1: "reuse the existing ledger... do not
        duplicate it"); facts carry no token columns of their own, so the
        rollup's token bucket joins back to it by the shared ``record_id``.
        """
        row = self._conn.execute(
            """
            SELECT usage_json FROM session_token_usage_records
            WHERE session_id = ? AND source = ? AND record_id = ?
            """,
            (session_id, source, fact_id),
        ).fetchone()
        if row is None:
            return None
        try:
            usage = json.loads(row["usage_json"])
        except json.JSONDecodeError:
            return None
        if not isinstance(usage, dict):
            return None
        raw_totals = usage.get("totals") or {}
        totals = {
            str(k): int(v) for k, v in raw_totals.items() if isinstance(v, int | float)
        }
        display_total = usage.get("display_total_tokens")
        return totals, display_total if isinstance(display_total, int) else None

    # ── queries ───────────────────────────────────────────────────────────

    def query_facts(
        self,
        kind: TelemetryFactKind,
        rng: TelemetryRange,
        flt: TelemetryFilter,
        *,
        limit: int | None = None,
        offset: int | None = None,
        partial: bool = False,
    ) -> list[dict[str, Any]]:
        with self._lock:
            where, params = self._filter_clause(
                rng, flt, extra_kind=kind, partial=partial
            )
            sql = (
                f"SELECT * FROM telemetry_facts WHERE {where} ORDER BY occurred_at ASC"
            )
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
                if offset is not None:
                    sql += " OFFSET ?"
                    params.append(offset)
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def count_facts(
        self, kind: TelemetryFactKind, rng: TelemetryRange, flt: TelemetryFilter
    ) -> int:
        with self._lock:
            where, params = self._filter_clause(
                rng, flt, extra_kind=kind, partial=False
            )
            row = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM telemetry_facts WHERE {where}", params
            ).fetchone()
            return int(row["n"])

    def query_rollup(
        self, rng: TelemetryRange, flt: TelemetryFilter
    ) -> list[dict[str, Any]]:
        with self._lock:
            end_day = _day_key(rng.end)
            # ``end`` is an exclusive instant; only exclude its calendar day when
            # it falls exactly on that day's local midnight (nothing from it is
            # in range), otherwise the day is partially covered and stays in.
            end_is_boundary = _day_bounds_utc(end_day)[0] == _iso_utc(rng.end)
            clauses = ["day >= ?", "day < ?" if end_is_boundary else "day <= ?"]
            params: list[Any] = [_day_key(rng.start), end_day]
            if flt.backends:
                clauses.append(f"backend IN ({', '.join('?' for _ in flt.backends)})")
                params.extend(flt.backends)
            if flt.models:
                clauses.append(f"model IN ({', '.join('?' for _ in flt.models)})")
                params.extend(flt.models)
            if flt.repos:
                clauses.append(f"repo_name IN ({', '.join('?' for _ in flt.repos)})")
                params.extend(flt.repos)
            if flt.sources:
                clauses.append(f"src_source IN ({', '.join('?' for _ in flt.sources)})")
                params.extend(flt.sources)
            if flt.transports:
                clauses.append(
                    f"transport IN ({', '.join('?' for _ in flt.transports)})"
                )
                params.extend(flt.transports)
            if flt.parent_scope == "top_level":
                clauses.append("is_child = 0")
            elif flt.parent_scope == "children":
                clauses.append("is_child = 1")
            where = " AND ".join(clauses)
            rows = self._conn.execute(
                f"SELECT * FROM telemetry_daily_rollup WHERE {where} ORDER BY day ASC",
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def dismiss_insight(self, signature: str, range_key: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO telemetry_insight_dismissal (signature, range_key, dismissed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(signature, range_key) DO UPDATE SET dismissed_at = excluded.dismissed_at
                """,
                (signature, range_key, _iso_utc(datetime.now(UTC))),
            )
            self._conn.commit()

    def dismissed_insights(self, range_key: str) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT signature FROM telemetry_insight_dismissal WHERE range_key = ?",
                (range_key,),
            ).fetchall()
            return {row["signature"] for row in rows}

    def _filter_clause(
        self,
        rng: TelemetryRange,
        flt: TelemetryFilter,
        *,
        extra_kind: TelemetryFactKind,
        partial: bool,
    ) -> tuple[str, list[Any]]:
        clauses = ["kind = ?", "occurred_at >= ?", "occurred_at < ?"]
        params: list[Any] = [extra_kind, _iso_utc(rng.start), _iso_utc(rng.end)]
        if not partial:
            clauses.append("partial = 0")
        if flt.backends:
            clauses.append(f"backend IN ({', '.join('?' for _ in flt.backends)})")
            params.extend(flt.backends)
        if flt.models:
            clauses.append(f"model_at_turn IN ({', '.join('?' for _ in flt.models)})")
            params.extend(flt.models)
        if flt.repos:
            clauses.append(f"repo_name IN ({', '.join('?' for _ in flt.repos)})")
            params.extend(flt.repos)
        if flt.sources:
            clauses.append(f"src_source IN ({', '.join('?' for _ in flt.sources)})")
            params.extend(flt.sources)
        if flt.transports:
            clauses.append(f"transport IN ({', '.join('?' for _ in flt.transports)})")
            params.extend(flt.transports)
        if flt.parent_scope == "top_level":
            clauses.append("is_child = 0")
        elif flt.parent_scope == "children":
            clauses.append("is_child = 1")
        if flt.parent_session_id:
            if flt.include_descendants:
                clauses.append("(spawner_session_id = ? OR session_id = ?)")
                params.extend([flt.parent_session_id, flt.parent_session_id])
            else:
                clauses.append("spawner_session_id = ?")
                params.append(flt.parent_session_id)
        if flt.tags:
            tag_terms = [
                term for term in (_parse_tag_term(t) for t in flt.tags) if term
            ]
            for key, value in tag_terms:
                clauses.append("""
                    (kind, source, fact_id) IN (
                        SELECT kind, source, fact_id FROM telemetry_fact_tag
                        WHERE key = ? AND value = ?
                    )
                    """)
                params.extend([key, value])
        return " AND ".join(clauses), params


def _row_from_fact(fact: TelemetryFact) -> dict[str, Any]:
    """Project a typed ``TelemetryFact`` onto the flat ``telemetry_facts`` row."""
    row: dict[str, Any] = {
        "kind": fact.kind,
        "source": fact.source,
        "fact_id": fact.fact_id,
        "revision": fact.revision,
        "partial": int(fact.partial),
        "session_id": fact.session_id,
        "occurred_at": _iso_utc(fact.occurred_at),
        "recorded_at": _iso_utc(datetime.now(UTC)),
        "schema_version": fact.schema_version,
        "backend": fact.dims.backend,
        "repo_name": fact.dims.repo_name,
        "src_source": fact.dims.source,
        "transport": fact.dims.transport,
        "spawner_session_id": fact.dims.spawner_session_id,
        "is_child": int(fact.dims.is_child),
        "transition": None,
        "turn_kind": None,
        "model_at_turn": None,
        "effort_at_turn": None,
        "tool_name": None,
        "tool_category": None,
        "outcome": None,
        "duration_ms": None,
        "approval_decision": None,
        "used_tokens": None,
        "window_tokens": None,
        "occupancy_percent": None,
        "account_key": None,
        "window_id": None,
        "window_label": None,
        "used_percent": None,
        "resets_at": None,
    }
    if isinstance(fact, SessionLifecycleFact):
        row["transition"] = fact.transition
    elif isinstance(fact, TurnFact):
        row["turn_kind"] = fact.turn_kind
        row["model_at_turn"] = fact.model_at_turn
        row["effort_at_turn"] = fact.effort_at_turn
    elif isinstance(fact, ToolCallFact):
        row["tool_name"] = fact.tool_name
        row["tool_category"] = fact.tool_category
        row["outcome"] = fact.outcome
        row["duration_ms"] = fact.duration_ms
        row["model_at_turn"] = fact.model_at_turn
        row["approval_decision"] = fact.approval_decision
    elif isinstance(fact, ContextSnapshotFact):
        row["used_tokens"] = fact.used_tokens
        row["window_tokens"] = fact.window_tokens
        row["occupancy_percent"] = fact.occupancy_percent
    elif isinstance(fact, LimitSnapshotFact):
        row["account_key"] = fact.account_key
        row["window_id"] = fact.window_id
        row["window_label"] = fact.window_label
        row["used_percent"] = fact.used_percent
        row["resets_at"] = _iso_utc(fact.resets_at) if fact.resets_at else None
    return row
