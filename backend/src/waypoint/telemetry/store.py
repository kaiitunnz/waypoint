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
from collections.abc import Iterable, Sequence
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

# ``telemetry_meta`` key the latest NL-insight digest is stored under (CONTRACT-NL.md §4).
_NL_INSIGHT_META_KEY = "nl_insight"

# The daily-rollup primary key a fact contributes to: (day, backend, model,
# repo, source, transport, is_child). Recomputing one key rescans that key's
# day-bounded slice from scratch, so accumulating the affected keys across a
# batch and recomputing each once is byte-identical to recomputing per fact.
_RollupKey = tuple[str, str, str, str, str, str, int]


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


def _offset_modifier(minutes: int) -> str:
    """A SQLite date-modifier shifting a UTC instant to a host-tz wall clock.

    ``date`` / ``strftime`` need an explicit sign on the minutes modifier, so a
    negative (west-of-UTC) offset formats as ``-300 minutes``. A single fixed
    offset across a range differs from a per-instant tz resolution at a DST
    boundary — immaterial on this non-DST host.
    """
    return f"{minutes:+d} minutes"


def _host_offset_modifier() -> str:
    """``_offset_modifier`` for the host's current UTC offset.

    Uses the same tz source as ``_day_key`` (naive ``astimezone()``), so
    ``date(occurred_at, _host_offset_modifier())`` matches ``_day_key`` for a
    fixed offset.
    """
    offset = datetime.now().astimezone().utcoffset()
    minutes = round(offset.total_seconds() / 60) if offset is not None else 0
    return _offset_modifier(minutes)


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
                  account_label   TEXT,
                  profile_label   TEXT,
                  window_id       TEXT,
                  window_label    TEXT,
                  used_percent    REAL,
                  resets_at       TEXT,
                  PRIMARY KEY (kind, source, fact_id)
                );
                CREATE INDEX IF NOT EXISTS idx_tf_kind_time      ON telemetry_facts(kind, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_tf_session        ON telemetry_facts(session_id);
                CREATE INDEX IF NOT EXISTS idx_tf_time           ON telemetry_facts(occurred_at);

                -- ``idx_tf_kind_backend_time`` bought nothing measurable:
                -- backend is ~3-cardinality and every backend-filtered read
                -- also constrains kind+time, so the planner rides
                -- ``idx_tf_kind_time`` and filters backend as a residual at the
                -- same latency. Drop it (~4.5MB + a per-fact write) on boot.
                DROP INDEX IF EXISTS idx_tf_kind_backend_time;

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

                -- ``telemetry_rollup_session`` backed a since-removed
                -- ``active_denom`` metric that no reader consumed; drop it on
                -- every boot so existing on-disk databases shed it (no
                -- migration framework — this idempotent DROP is the shedder).
                DROP TABLE IF EXISTS telemetry_rollup_session;
                """)
            self._ensure_column("account_label", "TEXT")
            self._ensure_column("profile_label", "TEXT")
            self._conn.commit()

    def _ensure_column(self, column: str, ddl: str) -> None:
        rows = self._conn.execute("PRAGMA table_info(telemetry_facts)").fetchall()
        if any(row["name"] == column for row in rows):
            return
        self._conn.execute(f"ALTER TABLE telemetry_facts ADD COLUMN {column} {ddl}")

    # ── ingest ────────────────────────────────────────────────────────────

    def ingest_fact(
        self, fact: TelemetryFact, *, tags: dict[str, str] | None = None
    ) -> bool:
        with self._lock:
            keys = self._ingest_fact_locked(fact, tags or {})
            for key in keys or ():
                self._recompute_rollup_key(*key)
            self._conn.commit()
            return keys is not None

    def ingest_facts(
        self, facts: Iterable[tuple[TelemetryFact, dict[str, str]]]
    ) -> int:
        with self._lock:
            written = 0
            keys_to_recompute: set[_RollupKey] = set()
            for fact, tags in facts:
                keys = self._ingest_fact_locked(fact, tags)
                if keys is not None:
                    written += 1
                    keys_to_recompute |= keys
            # Coalesce: a same-day/same-dims batch collapses to one recompute per
            # key instead of one per fact, and recompute-from-scratch depends
            # only on the final table state, so the result is byte-identical.
            for key in keys_to_recompute:
                self._recompute_rollup_key(*key)
            self._conn.commit()
            return written

    def _ingest_fact_locked(
        self, fact: TelemetryFact, tags: dict[str, str]
    ) -> set[_RollupKey] | None:
        """Insert/replace one fact, returning the rollup keys the caller must
        recompute (the fact's new key plus its pre-revision key), or ``None``
        when the fact was a dedup/stale-revision no-op."""
        existing = self._conn.execute(
            """
            SELECT revision, occurred_at, backend, model_at_turn, repo_name,
                   src_source, transport, is_child
            FROM telemetry_facts WHERE kind = ? AND source = ? AND fact_id = ?
            """,
            (fact.kind, fact.source, fact.fact_id),
        ).fetchone()
        if existing is not None and existing["revision"] > fact.revision:
            return None
        if existing is not None and existing["revision"] == fact.revision:
            return None

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

        # A brand-new row has no prior tags to clear, so skip the DELETE unless
        # this is a revision (existing row) or the fact actually carries tags.
        if existing is not None or tags:
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

        keys_to_recompute: set[_RollupKey] = set()
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
        return keys_to_recompute

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
            self._conn.commit()
            return {"facts": facts_removed, "rollups": rollups_removed}

    def rebuild_rollups_from_facts(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM telemetry_daily_rollup")
            # DISTINCT on the host-tz day directly (not raw ``occurred_at``,
            # which is near-unique and defeats the DISTINCT — 54k rows for 155
            # keys). ``date(occurred_at, <offset>)`` matches ``_day_key`` for a
            # fixed offset (naive UTC substr would misbucket near midnight).
            modifier = _host_offset_modifier()
            rows = self._conn.execute(
                """
                SELECT DISTINCT date(occurred_at, ?) AS day, backend,
                       COALESCE(model_at_turn, '') AS model,
                       COALESCE(repo_name, '') AS repo_name, src_source, transport, is_child
                FROM telemetry_facts WHERE partial = 0
                """,
                (modifier,),
            ).fetchall()
            keys = {
                (
                    row["day"],
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

    def set_nl_insight(self, insight_json: str) -> None:
        """Persist the latest NL-insight digest (CONTRACT-NL.md §4).

        A single latest-digest slot (stored via ``telemetry_meta``), not a
        growing table — each successful generation overwrites the previous
        one. Not touched by ``prune()`` (there is nothing to age out of a
        single slot; the maintenance loop's own age-check against it is what
        decides whether to regenerate); cleared by the explicit
        ``DELETE /api/telemetry`` path instead (see ``clear_nl_insight``).
        """
        self.set_meta(_NL_INSIGHT_META_KEY, insight_json)

    def get_nl_insight(self) -> str | None:
        return self.get_meta(_NL_INSIGHT_META_KEY)

    def clear_nl_insight(self) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM telemetry_meta WHERE k = ?", (_NL_INSIGHT_META_KEY,)
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
            SELECT kind, turn_kind, transition
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
        lifecycle: dict[str, int] = {}

        for row in rows:
            kind = row["kind"]
            if kind == TelemetryFactKind.SESSION_LIFECYCLE:
                lifecycle[row["transition"]] = lifecycle.get(row["transition"], 0) + 1
            elif kind == TelemetryFactKind.TURN:
                if row["turn_kind"] == TurnKind.USER:
                    turns_user += 1
                elif row["turn_kind"] == TurnKind.AGENT:
                    turns_agent += 1
            elif kind == TelemetryFactKind.TOOL_CALL:
                tool_calls += 1

        metrics = {
            "turns_user": turns_user,
            "turns_agent": turns_agent,
            "tool_calls": tool_calls,
            "lifecycle": lifecycle,
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
        descending: bool = False,
        columns: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fact rows for ``kind``/``rng``/``flt``.

        ``columns`` narrows the projection for read-heavy callers (token fold,
        heatmap) that touch only a handful of the ~38 columns; the returned
        dicts then carry ONLY those keys, so a projection must list every
        column its caller reads. Defaults to the full row.
        """
        with self._lock:
            where, params = self._filter_clause(
                rng, flt, extra_kind=kind, partial=partial
            )
            projection = "*" if columns is None else ", ".join(columns)
            # Stable tiebreak (source, fact_id) after occurred_at so paginated
            # drilldown results (LIMIT/OFFSET) never reorder or repeat a row
            # across pages when several facts share the same instant. Drill-down
            # defaults to newest-first (``descending``) so the first page shows
            # the most recent, most relevant facts rather than the oldest.
            direction = "DESC" if descending else "ASC"
            sql = (
                f"SELECT {projection} FROM telemetry_facts WHERE {where} "
                f"ORDER BY occurred_at {direction}, source {direction}, "
                f"fact_id {direction}"
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

    def heatmap_counts(
        self, rng: TelemetryRange, flt: TelemetryFilter
    ) -> list[tuple[int, int, int]]:
        """``(dow, hour, count)`` buckets over TURN + TOOL_CALL facts in range.

        Buckets in SQL (≤168 rows) instead of materializing every fact and
        counting in Python. ``occurred_at`` is shifted by the range's host UTC
        offset so cells land on the host-tz wall clock the dashboard renders.
        SQLite ``strftime('%w')`` numbers Sunday=0..Saturday=6, but the
        ``ActivityHeatmapCell.dow`` contract is Monday=0 (Python
        ``datetime.weekday()``), hence the ``+ 6) % 7`` shift. A single offset
        across the range differs from a per-instant tz resolution at a DST
        boundary — immaterial on this non-DST host.
        """
        with self._lock:
            where, params = self._filter_clause(
                rng,
                flt,
                extra_kind=(TelemetryFactKind.TURN, TelemetryFactKind.TOOL_CALL),
                partial=False,
            )
            modifier = _offset_modifier(rng.utc_offset_minutes)
            rows = self._conn.execute(
                f"""
                SELECT (CAST(strftime('%w', occurred_at, ?) AS INTEGER) + 6) % 7 AS dow,
                       CAST(strftime('%H', occurred_at, ?) AS INTEGER) AS hour,
                       COUNT(*) AS n
                FROM telemetry_facts WHERE {where}
                GROUP BY dow, hour
                ORDER BY dow, hour
                """,
                [modifier, modifier, *params],
            ).fetchall()
            return [(int(r["dow"]), int(r["hour"]), int(r["n"])) for r in rows]

    def query_rollup(
        self, rng: TelemetryRange, flt: TelemetryFilter
    ) -> list[dict[str, Any]]:
        """Daily rollup rows matching ``rng``/``flt``.

        The rollup key is ``(day, backend, model, repo, source, transport,
        is_child)`` — it has no tag or parent-session dimension, so it cannot
        represent a ``flt.tags`` or ``flt.parent_session_id`` filter. Callers
        that need those must fall back to a fact scan instead of this method
        (``aggregate.session_counts_totals`` does); a ``model=`` filter also
        always zeroes lifecycle-derived counts here since lifecycle facts
        carry no ``model_at_turn`` and so always land in the ``""`` model
        bucket (lifecycle isn't model-attributable).
        """
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

    def clear_insight_dismissals(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM telemetry_insight_dismissal")
            self._conn.commit()

    def latest_lifecycle_transitions(self) -> dict[str, str]:
        """The most recent lifecycle transition per session (by ``occurred_at``).

        Seeds the ingester's in-memory transition dedup at startup so the first
        status event for a known session after a restart doesn't re-mint a
        transition fact already persisted (a fresh event sequence yields a new
        ``fact_id`` that dodges the store's PK dedup).
        """
        with self._lock:
            # One newest row per session via a window function (≤ session-count
            # rows) instead of scanning every lifecycle fact. The tiebreak makes
            # exact-timestamp ties resolve to a single deterministic row (a bare
            # MAX(...) join could return several tied rows); which tied
            # transition wins is immaterial to the dedup seed.
            rows = self._conn.execute(
                """
                SELECT session_id, transition FROM (
                    SELECT session_id, transition,
                           ROW_NUMBER() OVER (
                               PARTITION BY session_id
                               ORDER BY occurred_at DESC, source DESC, fact_id DESC
                           ) AS rn
                    FROM telemetry_facts
                    WHERE kind = ? AND transition IS NOT NULL
                )
                WHERE rn = 1
                """,
                (TelemetryFactKind.SESSION_LIFECYCLE,),
            ).fetchall()
            return {row["session_id"]: row["transition"] for row in rows}

    def _descendant_session_ids(self, root_id: str) -> set[str]:
        """Every transitive descendant of ``root_id`` (excluding ``root_id`` itself).

        Walks the ``spawner_session_id`` edges telemetry facts carry — mirrors
        ``aggregate._descendant_session_ids``'s BFS, fed from facts instead of
        live ``SessionRecord``s (a session with zero facts contributes nothing
        to a facts query anyway, so this is sufficient here).
        """
        rows = self._conn.execute(
            "SELECT DISTINCT session_id, spawner_session_id FROM telemetry_facts "
            "WHERE spawner_session_id IS NOT NULL"
        ).fetchall()
        children: dict[str, list[str]] = {}
        for row in rows:
            children.setdefault(row["spawner_session_id"], []).append(row["session_id"])
        found: set[str] = set()
        queue = list(children.get(root_id, []))
        while queue:
            current = queue.pop()
            if current in found:
                continue
            found.add(current)
            queue.extend(children.get(current, []))
        return found

    def _filter_clause(
        self,
        rng: TelemetryRange,
        flt: TelemetryFilter,
        *,
        extra_kind: TelemetryFactKind | Sequence[TelemetryFactKind],
        partial: bool,
    ) -> tuple[str, list[Any]]:
        kinds: Sequence[TelemetryFactKind] = (
            [extra_kind] if isinstance(extra_kind, TelemetryFactKind) else extra_kind
        )
        clauses = [
            f"kind IN ({', '.join('?' for _ in kinds)})",
            "occurred_at >= ?",
            "occurred_at < ?",
        ]
        params: list[Any] = [*kinds, _iso_utc(rng.start), _iso_utc(rng.end)]
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
                # The parent's own facts plus every transitive descendant's —
                # a direct-children-only join would silently drop grandchildren.
                descendant_ids = self._descendant_session_ids(flt.parent_session_id)
                ids = [flt.parent_session_id, *sorted(descendant_ids)]
                clauses.append(f"session_id IN ({', '.join('?' for _ in ids)})")
                params.extend(ids)
            else:
                # Excluding descendants means the parent's OWN facts only —
                # not "only its children" (that would be backwards).
                clauses.append("session_id = ?")
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
        "account_label": None,
        "profile_label": None,
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
        row["account_label"] = fact.account_label
        row["profile_label"] = fact.profile_label
        row["window_id"] = fact.window_id
        row["window_label"] = fact.window_label
        row["used_percent"] = fact.used_percent
        row["resets_at"] = _iso_utc(fact.resets_at) if fact.resets_at else None
    return row
