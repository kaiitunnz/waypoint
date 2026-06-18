import functools
import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from waypoint.perf import debug_timer
from waypoint.schemas import (
    BoardChannel,
    BoardEntry,
    EventKind,
    EventRecord,
    ScheduledSessionRecord,
    ScheduleStatus,
    SessionRecord,
    SessionStatus,
)

log = logging.getLogger("waypoint.storage")

# ``UPDATE ... RETURNING`` landed in SQLite 3.35 (2021). When present it lets
# ``update_session`` fetch the post-update row in the same statement, avoiding
# the extra existence-check and re-read round-trips on a hot path.
_SUPPORTS_RETURNING = sqlite3.sqlite_version_info >= (3, 35, 0)

# Event kinds that the chat view always renders as their own bubble. These
# drive the page-size budget so a page of N reliably surfaces ~N visible
# entries even when the backend pads the transcript with bookkeeping
# events (Codex's item/started + item/completed system_notes for
# userMessage/reasoning, "session restored" notes after a backend
# restart, etc.). Non-anchor events ride along inside the same page
# without consuming budget.
_ANCHOR_KINDS: frozenset[EventKind] = frozenset(
    {
        EventKind.USER_INPUT,
        EventKind.AGENT_OUTPUT,
        EventKind.TOOL_CALL,
        EventKind.TOOL_RESULT,
        EventKind.APPROVAL_REQUEST,
    }
)

# Hard cap on raw events returned by ``list_events_by_message_count``. The
# anchor-budget walk would otherwise scan arbitrarily far when a session
# has no anchor events at all (tmux raw_terminal_chunk stream, history
# of just system_notes after a restart, etc.) since nothing increments
# the message counter. Capping here keeps the page payload bounded; the
# caller still gets ``has_more=True`` and can paginate further.
_MAX_EVENTS_PER_PAGE: int = 2000


def _is_message_anchor(event: EventRecord) -> bool:
    return event.kind in _ANCHOR_KINDS


def _anchor_key(event: EventRecord) -> tuple[str, Any]:
    """Group key for the anchor walk.

    Mirrors the frontend's coalesce rules:
    - ``agent_output`` events sharing ``item_id`` collapse into one bubble
      (``mergeEvents`` matches by kind+item_id).
    - ``tool_call`` and ``tool_result`` events sharing ``item_id`` render
      as one tool pair (``buildTranscriptItems``), so they share a key.
    - Everything else is its own message — a per-event ``sequence`` makes
      the key unique.
    """
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    item_id = metadata.get("item_id")
    if isinstance(item_id, str) and item_id:
        if event.kind == EventKind.AGENT_OUTPUT:
            return ("agent", item_id)
        if event.kind in (EventKind.TOOL_CALL, EventKind.TOOL_RESULT):
            return ("tool", item_id)
    return ("solo", event.sequence)


def _synchronized[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Serialize access to ``self.connection`` across threads.

    sqlite3 connections opened with ``check_same_thread=False`` may be shared,
    but writes / interleaved cursor operations must still be serialized by the
    user — otherwise concurrent threadpool requests trip
    ``sqlite3.InterfaceError: bad parameter or other API misuse``.
    """

    @functools.wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        self = args[0]
        with self._lock:  # type: ignore[attr-defined]
            return method(*args, **kwargs)

    return wrapper


class Storage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        # WAL drops the full fsync that the default rollback journal does
        # on every commit — the streaming event path commits per event, so
        # that fsync otherwise serializes the whole asyncio loop.
        # synchronous NORMAL is safe under WAL (a crash can lose only the
        # last un-checkpointed commits, never corrupt the db). This single
        # connection is serialized by ``_synchronized``, so WAL's
        # concurrent-reader benefit doesn't apply today; busy_timeout is
        # defensive in case that ever changes.
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self._init_db()

    def _init_db(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                backend TEXT NOT NULL,
                source TEXT NOT NULL,
                transport TEXT NOT NULL DEFAULT 'tmux',
                title TEXT NOT NULL,
                cwd TEXT NOT NULL,
                launch_target_id TEXT,
                launch_mode TEXT NOT NULL DEFAULT 'auto',
                repo_name TEXT,
                branch TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_event_at TEXT NOT NULL,
                raw_log_path TEXT NOT NULL,
                structured_log_path TEXT NOT NULL,
                transport_state TEXT NOT NULL DEFAULT '{}',
                pinned_at TEXT,
                permission_mode TEXT,
                model TEXT,
                effort TEXT,
                args TEXT NOT NULL DEFAULT '[]',
                config_overrides TEXT NOT NULL DEFAULT '[]',
                context_usage TEXT,
                rate_limit_usage TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                metadata TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            -- Serves both the per-event next_sequence MAX(sequence) lookup
            -- and the per-session paginated event reads, which would
            -- otherwise scan the whole events table.
            CREATE INDEX IF NOT EXISTS idx_events_session_seq
                ON events(session_id, sequence);

            CREATE TABLE IF NOT EXISTS auth_tokens (
                token TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scheduled_sessions (
                id TEXT PRIMARY KEY,
                backend TEXT NOT NULL,
                cwd TEXT NOT NULL,
                launch_target_id TEXT,
                launch_mode TEXT NOT NULL DEFAULT 'auto',
                transport TEXT,
                title TEXT,
                args TEXT NOT NULL DEFAULT '[]',
                config_overrides TEXT NOT NULL DEFAULT '[]',
                initial_prompt TEXT,
                permission_mode TEXT,
                scheduled_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                session_id TEXT,
                failure_reason TEXT
            );

            CREATE TABLE IF NOT EXISTS board_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                author_session_id TEXT,
                author_label TEXT,
                key TEXT,
                text TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                edited_at TEXT,
                UNIQUE(channel, key)
            );

            CREATE INDEX IF NOT EXISTS idx_board_channel
                ON board_entries(channel, id);

            -- Channels exist independently of their entries so a cleared
            -- channel survives with zero posts; a channel is gone only when
            -- explicitly deleted.
            CREATE TABLE IF NOT EXISTS board_channels (
                channel TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            """)
        # Register any channels that predate the board_channels table.
        self.connection.execute(
            "INSERT OR IGNORE INTO board_channels (channel, created_at) "
            "SELECT channel, MIN(created_at) FROM board_entries GROUP BY channel"
        )
        self._ensure_column("board_entries", "edited_at", "TEXT")
        self._ensure_column("board_entries", "author_label", "TEXT")
        self._ensure_column("scheduled_sessions", "permission_mode", "TEXT")
        self._ensure_column("scheduled_sessions", "model", "TEXT")
        self._ensure_column("scheduled_sessions", "effort", "TEXT")
        self._ensure_column(
            "scheduled_sessions", "launch_mode", "TEXT NOT NULL DEFAULT 'auto'"
        )
        self._ensure_column("scheduled_sessions", "transport", "TEXT")
        self._ensure_column("sessions", "args", "TEXT NOT NULL DEFAULT '[]'")
        self._ensure_column(
            "sessions", "config_overrides", "TEXT NOT NULL DEFAULT '[]'"
        )
        self._ensure_column("sessions", "launch_mode", "TEXT NOT NULL DEFAULT 'auto'")
        self._ensure_column("sessions", "context_usage", "TEXT")
        self._ensure_column("sessions", "rate_limit_usage", "TEXT")
        self._ensure_column("sessions", "spawner_session_id", "TEXT")
        self._ensure_column("sessions", "worktree_path", "TEXT")
        self._ensure_column(
            "scheduled_sessions", "config_overrides", "TEXT NOT NULL DEFAULT '[]'"
        )
        # Indexes for columns filtered on by the runtime/scheduler. Created
        # after the ALTER TABLE block above so ``spawner_session_id`` exists on
        # databases that predate it.
        self.connection.executescript("""
            CREATE INDEX IF NOT EXISTS idx_sessions_spawner
                ON sessions(spawner_session_id);
            CREATE INDEX IF NOT EXISTS idx_board_author
                ON board_entries(author_session_id);
            CREATE INDEX IF NOT EXISTS idx_scheduled_status
                ON scheduled_sessions(status);
            """)
        self.connection.commit()

    @_synchronized
    def close(self) -> None:
        self.connection.close()

    @_synchronized
    def create_session(self, session: SessionRecord) -> SessionRecord:
        self.connection.execute(
            """
            INSERT INTO sessions (
                id, backend, source, transport, title, cwd, launch_target_id,
                launch_mode, repo_name, branch, status, created_at, updated_at,
                last_event_at, raw_log_path, structured_log_path, transport_state,
                pinned_at, spawner_session_id, worktree_path, permission_mode, model,
                effort, args, config_overrides, context_usage, rate_limit_usage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                session.backend,
                session.source,
                session.transport,
                session.title,
                session.cwd,
                session.launch_target_id,
                session.launch_mode,
                session.repo_name,
                session.branch,
                session.status,
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
                session.last_event_at.isoformat(),
                session.raw_log_path,
                session.structured_log_path,
                json.dumps(session.transport_state),
                session.pinned_at.isoformat() if session.pinned_at else None,
                session.spawner_session_id,
                session.worktree_path,
                session.permission_mode,
                session.model,
                session.effort,
                json.dumps(list(session.args)),
                json.dumps(list(session.config_overrides)),
                (
                    json.dumps(session.context_usage.model_dump(mode="json"))
                    if session.context_usage is not None
                    else None
                ),
                (
                    json.dumps(session.rate_limit_usage.model_dump(mode="json"))
                    if session.rate_limit_usage is not None
                    else None
                ),
            ),
        )
        self.connection.commit()
        return session

    @_synchronized
    def list_sessions(self) -> list[SessionRecord]:
        rows = self.connection.execute(
            "SELECT * FROM sessions ORDER BY last_event_at DESC, created_at DESC"
        ).fetchall()
        return [self._session_from_row(row) for row in rows]

    @_synchronized
    def get_session(self, session_id: str) -> SessionRecord | None:
        row = self.connection.execute(
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return self._session_from_row(row)

    @_synchronized
    def update_session(self, session_id: str, **fields: Any) -> SessionRecord:
        with debug_timer(log, "Storage.update_session", fields=sorted(fields)):
            fields.setdefault("updated_at", datetime.now(UTC))
            assignments = ", ".join(f"{name} = ?" for name in fields)
            values = [self._serialize_field(value) for value in fields.values()]
            values.append(session_id)
            if _SUPPORTS_RETURNING:
                row = self.connection.execute(
                    f"UPDATE sessions SET {assignments} WHERE id = ? RETURNING *",
                    values,
                ).fetchone()
                self.connection.commit()
                if row is None:
                    raise KeyError(session_id)
                return self._session_from_row(row)
            if self.get_session(session_id) is None:
                raise KeyError(session_id)
            self.connection.execute(
                f"UPDATE sessions SET {assignments} WHERE id = ?",
                values,
            )
            self.connection.commit()
            updated = self.get_session(session_id)
            assert updated is not None
            return updated

    @_synchronized
    def delete_session(self, session_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM events WHERE session_id = ?",
            (session_id,),
        )
        events_deleted = cursor.rowcount or 0
        cursor = self.connection.execute(
            "DELETE FROM sessions WHERE id = ?",
            (session_id,),
        )
        sessions_deleted = cursor.rowcount or 0
        self.connection.commit()
        return sessions_deleted > 0 or events_deleted > 0

    @_synchronized
    def add_board_entry(
        self,
        channel: str,
        text: str,
        *,
        key: str | None = None,
        author_session_id: str | None = None,
        author_label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BoardEntry:
        now = datetime.now(UTC)
        meta = metadata or {}
        meta_json = json.dumps(meta)
        self.connection.execute(
            "INSERT OR IGNORE INTO board_channels (channel, created_at) VALUES (?, ?)",
            (channel, now.isoformat()),
        )
        if key is None:
            cursor = self.connection.execute(
                """
                INSERT INTO board_entries
                    (channel, author_session_id, author_label, key, text, metadata, created_at)
                VALUES (?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    channel,
                    author_session_id,
                    author_label,
                    text,
                    meta_json,
                    now.isoformat(),
                ),
            )
            entry_id = int(cursor.lastrowid or 0)
        else:
            # Latest post for a ``(channel, key)`` cell overwrites in place,
            # keeping the original row id so key reads stay stable.
            self.connection.execute(
                """
                INSERT INTO board_entries
                    (channel, author_session_id, author_label, key, text, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, key) DO UPDATE SET
                    author_session_id = excluded.author_session_id,
                    author_label = excluded.author_label,
                    text = excluded.text,
                    metadata = excluded.metadata,
                    created_at = excluded.created_at
                """,
                (
                    channel,
                    author_session_id,
                    author_label,
                    key,
                    text,
                    meta_json,
                    now.isoformat(),
                ),
            )
            row = self.connection.execute(
                "SELECT id FROM board_entries WHERE channel = ? AND key = ?",
                (channel, key),
            ).fetchone()
            entry_id = int(row["id"])
        self.connection.commit()
        return BoardEntry(
            id=entry_id,
            channel=channel,
            author_session_id=author_session_id,
            author_label=author_label,
            key=key,
            text=text,
            metadata=meta,
            created_at=now,
        )

    @_synchronized
    def list_board_entries(
        self,
        channel: str,
        *,
        since: int | None = None,
        key: str | None = None,
    ) -> list[BoardEntry]:
        query = "SELECT * FROM board_entries WHERE channel = ?"
        params: list[Any] = [channel]
        if key is not None:
            query += " AND key = ?"
            params.append(key)
        if since is not None:
            query += " AND id > ?"
            params.append(since)
        query += " ORDER BY id ASC"
        rows = self.connection.execute(query, params).fetchall()
        return [self._board_entry_from_row(row) for row in rows]

    @_synchronized
    def read_board_channel(
        self,
        channel: str,
        *,
        log_limit: int | None = None,
        before: int | None = None,
    ) -> tuple[list[BoardEntry], int]:
        """Read a page of a channel: keyed cells plus a window of the append-log.

        Returns ``(entries, log_total)`` where ``entries`` is cells followed by
        log rows in ascending id order, and ``log_total`` is the full append-log
        count. The newest ``log_limit`` log rows are returned; pass ``before`` (a
        log id) to page further back. Cells are always returned in full on the
        first page (``before`` unset) so every cell's latest value stays visible;
        older pages carry log rows only.
        """
        cells: list[BoardEntry] = []
        if before is None:
            cell_rows = self.connection.execute(
                "SELECT * FROM board_entries WHERE channel = ? AND key IS NOT NULL "
                "ORDER BY id ASC",
                (channel,),
            ).fetchall()
            cells = [self._board_entry_from_row(row) for row in cell_rows]
        log_total = int(
            self.connection.execute(
                "SELECT COUNT(*) AS n FROM board_entries "
                "WHERE channel = ? AND key IS NULL",
                (channel,),
            ).fetchone()["n"]
        )
        log_query = "SELECT * FROM board_entries WHERE channel = ? AND key IS NULL"
        params: list[Any] = [channel]
        if before is not None:
            log_query += " AND id < ?"
            params.append(before)
        log_query += " ORDER BY id DESC"
        if log_limit is not None:
            log_query += " LIMIT ?"
            params.append(log_limit)
        log_rows = self.connection.execute(log_query, params).fetchall()
        log = [self._board_entry_from_row(row) for row in reversed(log_rows)]
        return cells + log, log_total

    @_synchronized
    def list_board_channels(self) -> list[BoardChannel]:
        # Driven by the registry so cleared (empty) channels still appear; an
        # empty channel sorts by its own creation time.
        rows = self.connection.execute("""
            SELECT c.channel AS channel,
                   COUNT(e.id) AS entry_count,
                   COALESCE(MAX(e.created_at), c.created_at) AS last_created_at
            FROM board_channels c
            LEFT JOIN board_entries e ON e.channel = c.channel
            GROUP BY c.channel
            ORDER BY last_created_at DESC
            """).fetchall()
        return [
            BoardChannel(
                channel=row["channel"],
                entry_count=int(row["entry_count"]),
                last_created_at=datetime.fromisoformat(row["last_created_at"]),
            )
            for row in rows
        ]

    @_synchronized
    def clear_board_channel(self, channel: str, keep_last: int | None = None) -> int:
        # Remove the posts but keep the channel registered so it survives empty.
        # With keep_last, retain the N most-recent keyless log posts; cells are
        # always deleted regardless.
        if keep_last is not None and keep_last > 0:
            cutoff_row = self.connection.execute(
                """
                SELECT id FROM board_entries
                WHERE channel = ? AND key IS NULL
                ORDER BY id DESC
                LIMIT 1 OFFSET ?
                """,
                (channel, keep_last - 1),
            ).fetchone()
            if cutoff_row is not None:
                cursor = self.connection.execute(
                    "DELETE FROM board_entries WHERE channel = ? AND "
                    "(key IS NOT NULL OR id < ?)",
                    (channel, cutoff_row["id"]),
                )
            else:
                # Fewer than keep_last log posts exist; only drop cells.
                cursor = self.connection.execute(
                    "DELETE FROM board_entries WHERE channel = ? AND key IS NOT NULL",
                    (channel,),
                )
        else:
            cursor = self.connection.execute(
                "DELETE FROM board_entries WHERE channel = ?",
                (channel,),
            )
        self.connection.commit()
        return cursor.rowcount or 0

    @_synchronized
    def delete_board_channel(self, channel: str) -> int:
        cursor = self.connection.execute(
            "DELETE FROM board_entries WHERE channel = ?",
            (channel,),
        )
        removed = cursor.rowcount or 0
        self.connection.execute(
            "DELETE FROM board_channels WHERE channel = ?",
            (channel,),
        )
        self.connection.commit()
        return removed

    @_synchronized
    def delete_board_entry(self, channel: str, entry_id: int) -> bool:
        # Scoped to the channel so a wrong channel is a clean no-op; works for
        # both keyless log posts and keyed cells.
        cursor = self.connection.execute(
            "DELETE FROM board_entries WHERE id = ? AND channel = ?",
            (entry_id, channel),
        )
        self.connection.commit()
        return (cursor.rowcount or 0) > 0

    @_synchronized
    def update_board_entry(
        self,
        channel: str,
        entry_id: int,
        text: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> BoardEntry | None:
        now = datetime.now(UTC)
        if text is None:
            cursor = self.connection.execute(
                "UPDATE board_entries SET metadata = ?, edited_at = ? "
                "WHERE id = ? AND channel = ?",
                (json.dumps(metadata or {}), now.isoformat(), entry_id, channel),
            )
        else:
            cursor = self.connection.execute(
                "UPDATE board_entries SET text = ?, metadata = ?, edited_at = ? "
                "WHERE id = ? AND channel = ?",
                (text, json.dumps(metadata or {}), now.isoformat(), entry_id, channel),
            )
        self.connection.commit()
        if not (cursor.rowcount or 0):
            return None
        row = self.connection.execute(
            "SELECT * FROM board_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        return self._board_entry_from_row(row) if row else None

    @_synchronized
    def prune_board_for_session(self, session_id: str) -> int:
        # Only keyed cells are pruned; keyless log posts are durable history
        # and survive a session delete (GC by channel via clear/delete).
        cursor = self.connection.execute(
            "DELETE FROM board_entries WHERE author_session_id = ? AND key IS NOT NULL",
            (session_id,),
        )
        self.connection.commit()
        return cursor.rowcount or 0

    @_synchronized
    def append_event(self, event: EventRecord) -> EventRecord:
        # Stamp every persisted event with the canonical envelope version
        # so older transcripts replay safely under newer readers (the
        # frontend's `parseEvent` looks at `metadata.version` to decide
        # which schema to apply).
        if "version" not in event.metadata:
            event = event.model_copy(
                update={"metadata": {**event.metadata, "version": 1}}
            )
        with debug_timer(log, "Storage.append_event", session=event.session_id):
            ts_iso = event.ts.isoformat()
            cursor = self.connection.execute(
                """
                INSERT INTO events (session_id, ts, kind, text, metadata, sequence)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.session_id,
                    ts_iso,
                    event.kind,
                    event.text,
                    json.dumps(event.metadata),
                    event.sequence,
                ),
            )
            self.connection.execute(
                """
                UPDATE sessions
                SET last_event_at = ?, updated_at = ?, status = COALESCE(?, status)
                WHERE id = ?
                """,
                (
                    ts_iso,
                    ts_iso,
                    event.metadata.get("status"),
                    event.session_id,
                ),
            )
            self.connection.commit()
            last_id = cursor.lastrowid
            if last_id is None:
                raise RuntimeError(
                    "sqlite did not assign a row id for the inserted event"
                )
            return event.model_copy(update={"id": int(last_id)})

    @_synchronized
    def clone_events(self, source_session_id: str, target_session_id: str) -> int:
        """Bulk-copy all events from source to target, reassigning session_id.

        Returns the number of rows inserted.
        """
        self.connection.execute(
            """
            INSERT INTO events (session_id, ts, kind, text, metadata, sequence)
            SELECT ?, ts, kind, text, metadata, sequence
            FROM events
            WHERE session_id = ?
            ORDER BY sequence ASC, id ASC
            """,
            (target_session_id, source_session_id),
        )
        self.connection.commit()
        return self.connection.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ?", (target_session_id,)
        ).fetchone()[0]

    @_synchronized
    def list_events(
        self,
        session_id: str,
        cursor: int | None = None,
    ) -> list[EventRecord]:
        """Read events for a session in ascending order.

        - ``cursor`` (id-after): only events with ``id > cursor``. Used by
          reconnection / catch-up paths that already know the last
          observed event.
        - No cursor: the entire transcript.

        Bounded windows for the chat view go through
        :meth:`list_events_by_message_count` instead — that paginator
        respects logical-message boundaries so a page of N reliably
        surfaces N visible chat entries regardless of how chatty the
        backend is at the raw-event level.
        """
        query = "SELECT * FROM events WHERE session_id = ?"
        params: list[Any] = [session_id]
        if cursor is not None:
            query += " AND id > ?"
            params.append(cursor)
        query += " ORDER BY sequence ASC, id ASC"
        rows = self.connection.execute(query, params).fetchall()
        return [self._event_from_row(row) for row in rows]

    @_synchronized
    def list_events_by_message_count(
        self,
        session_id: str,
        *,
        message_limit: int,
        before_sequence: int | None = None,
    ) -> list[EventRecord]:
        """Return enough events to span ``message_limit`` logical chat
        messages, plus any non-anchor events sandwiched in or trailing
        the latest anchor.

        Walks events backward via SQLite cursor. Only anchor kinds
        (user/agent/tool/approval, see ``_ANCHOR_KINDS``) consume budget
        — bookkeeping ``system_note`` / ``status_update`` events ride
        along free so the page count tracks visible bubbles, not raw
        chattiness. Stops as soon as the ``(message_limit + 1)``-th
        anchor boundary is seen.

        Returns events in ascending order so the caller can splice them
        directly into the chat view.
        """
        query = "SELECT * FROM events WHERE session_id = ?"
        params: list[Any] = [session_id]
        if before_sequence is not None:
            query += " AND sequence < ?"
            params.append(before_sequence)
        query += " ORDER BY sequence DESC, id DESC"

        # Count *distinct* anchor keys, not contiguous runs: Codex
        # interleaves events for concurrent tool calls so the same
        # item_id can appear as multiple non-contiguous groups in
        # sequence order, but the frontend coalesces them into one
        # bubble. Counting distinct keys keeps the page-size budget
        # aligned with the visible bubble count.
        #
        # The event-count safety cap only kicks in (a) before any anchor
        # has been seen — the anchorless tmux/system-note case the cap
        # exists for — or (b) when crossing into a *new* anchor that
        # would push us past the cap. We never break in the middle of
        # collecting a single anchor's events; otherwise a Codex reply
        # streamed as 2500 deltas would arrive truncated and the user
        # would have to "Load older" to reconstruct one bubble.
        collected: list[EventRecord] = []
        seen_anchors: set[tuple[str, Any]] = set()
        current_anchor: tuple[str, Any] | None = None
        message_count = 0
        for row in self.connection.execute(query, params):
            event = self._event_from_row(row)
            if _is_message_anchor(event):
                next_key = _anchor_key(event)
                if next_key != current_anchor:
                    if next_key not in seen_anchors:
                        if message_count >= message_limit:
                            break
                        if len(collected) >= _MAX_EVENTS_PER_PAGE:
                            break
                        message_count += 1
                        seen_anchors.add(next_key)
                    current_anchor = next_key
            collected.append(event)
            if current_anchor is None and len(collected) >= _MAX_EVENTS_PER_PAGE:
                break
        collected.reverse()
        return collected

    @_synchronized
    def has_events_before_sequence(self, session_id: str, before_sequence: int) -> bool:
        """Cheap ``has_more`` probe for the chat paginator."""
        row = self.connection.execute(
            "SELECT 1 FROM events WHERE session_id = ? AND sequence < ? LIMIT 1",
            [session_id, before_sequence],
        ).fetchone()
        return row is not None

    @_synchronized
    def insert_token(self, token: str, expires_at: datetime) -> None:
        now = datetime.now(UTC)
        self.connection.execute(
            "INSERT OR REPLACE INTO auth_tokens (token, expires_at, created_at) VALUES (?, ?, ?)",
            (token, expires_at.isoformat(), now.isoformat()),
        )
        self.connection.commit()

    @_synchronized
    def get_token_expiry(self, token: str) -> datetime | None:
        row = self.connection.execute(
            "SELECT expires_at FROM auth_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["expires_at"])

    @_synchronized
    def refresh_token_expiry(self, token: str, expires_at: datetime) -> None:
        self.connection.execute(
            "UPDATE auth_tokens SET expires_at = ? WHERE token = ?",
            (expires_at.isoformat(), token),
        )
        self.connection.commit()

    @_synchronized
    def delete_token(self, token: str) -> None:
        self.connection.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))
        self.connection.commit()

    @_synchronized
    def purge_expired_tokens(self, now: datetime) -> int:
        cursor = self.connection.execute(
            "DELETE FROM auth_tokens WHERE expires_at < ?",
            (now.isoformat(),),
        )
        self.connection.commit()
        return cursor.rowcount or 0

    @_synchronized
    def create_schedule(
        self, schedule: ScheduledSessionRecord
    ) -> ScheduledSessionRecord:
        self.connection.execute(
            """
            INSERT INTO scheduled_sessions (
                id, backend, cwd, launch_target_id, launch_mode, transport, title, args,
                config_overrides, initial_prompt, permission_mode, model, effort, scheduled_at,
                created_at, status, session_id, failure_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                schedule.id,
                schedule.backend,
                schedule.cwd,
                schedule.launch_target_id,
                schedule.launch_mode,
                schedule.transport,
                schedule.title,
                json.dumps(list(schedule.args)),
                json.dumps(list(schedule.config_overrides)),
                schedule.initial_prompt,
                schedule.permission_mode,
                schedule.model,
                schedule.effort,
                schedule.scheduled_at.isoformat(),
                schedule.created_at.isoformat(),
                schedule.status,
                schedule.session_id,
                schedule.failure_reason,
            ),
        )
        self.connection.commit()
        return schedule

    @_synchronized
    def list_schedules(
        self, statuses: list[ScheduleStatus] | None = None
    ) -> list[ScheduledSessionRecord]:
        query = "SELECT * FROM scheduled_sessions"
        params: list[Any] = []
        if statuses:
            placeholders = ",".join(["?"] * len(statuses))
            query += f" WHERE status IN ({placeholders})"
            params.extend(status.value for status in statuses)
        query += " ORDER BY scheduled_at ASC, created_at ASC"
        rows = self.connection.execute(query, params).fetchall()
        return [self._schedule_from_row(row) for row in rows]

    @_synchronized
    def get_schedule(self, schedule_id: str) -> ScheduledSessionRecord | None:
        row = self.connection.execute(
            "SELECT * FROM scheduled_sessions WHERE id = ?", (schedule_id,)
        ).fetchone()
        if row is None:
            return None
        return self._schedule_from_row(row)

    @_synchronized
    def update_schedule(
        self, schedule_id: str, **fields: Any
    ) -> ScheduledSessionRecord:
        if not fields:
            current = self.get_schedule(schedule_id)
            if current is None:
                raise KeyError(schedule_id)
            return current
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [self._serialize_field(value) for value in fields.values()]
        values.append(schedule_id)
        self.connection.execute(
            f"UPDATE scheduled_sessions SET {assignments} WHERE id = ?", values
        )
        self.connection.commit()
        updated = self.get_schedule(schedule_id)
        if updated is None:
            raise KeyError(schedule_id)
        return updated

    @_synchronized
    def delete_schedule(self, schedule_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM scheduled_sessions WHERE id = ?", (schedule_id,)
        )
        self.connection.commit()
        return (cursor.rowcount or 0) > 0

    @_synchronized
    def delete_schedules_by_status(self, statuses: list[ScheduleStatus]) -> int:
        if not statuses:
            return 0
        placeholders = ",".join(["?"] * len(statuses))
        cursor = self.connection.execute(
            f"DELETE FROM scheduled_sessions WHERE status IN ({placeholders})",
            [item.value for item in statuses],
        )
        self.connection.commit()
        return cursor.rowcount or 0

    @_synchronized
    def next_sequence(self, session_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS max_sequence FROM events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["max_sequence"]) + 1

    def _session_from_row(self, row: sqlite3.Row) -> SessionRecord:
        payload = dict(row)
        for field_name in ("created_at", "updated_at", "last_event_at"):
            payload[field_name] = datetime.fromisoformat(payload[field_name])
        raw_pinned_at = payload.get("pinned_at")
        payload["pinned_at"] = (
            datetime.fromisoformat(raw_pinned_at) if raw_pinned_at else None
        )
        payload["status"] = SessionStatus(payload["status"])
        payload["launch_mode"] = payload.get("launch_mode") or "auto"
        raw_state = payload.pop("transport_state", None) or "{}"
        try:
            decoded = json.loads(raw_state)
        except json.JSONDecodeError:
            decoded = {}
        payload["transport_state"] = decoded if isinstance(decoded, dict) else {}
        raw_args = payload.get("args") or "[]"
        try:
            parsed_args = json.loads(raw_args)
        except json.JSONDecodeError:
            parsed_args = []
        payload["args"] = parsed_args if isinstance(parsed_args, list) else []
        raw_overrides = payload.get("config_overrides") or "[]"
        try:
            parsed_overrides = json.loads(raw_overrides)
        except json.JSONDecodeError:
            parsed_overrides = []
        payload["config_overrides"] = (
            parsed_overrides if isinstance(parsed_overrides, list) else []
        )
        raw_context_usage = payload.get("context_usage")
        if raw_context_usage:
            try:
                parsed_context_usage = json.loads(raw_context_usage)
            except json.JSONDecodeError:
                parsed_context_usage = None
            if isinstance(parsed_context_usage, dict):
                payload["context_usage"] = parsed_context_usage
            else:
                payload["context_usage"] = None
        else:
            payload["context_usage"] = None
        raw_rate_limit_usage = payload.get("rate_limit_usage")
        if raw_rate_limit_usage:
            try:
                parsed_rate_limit_usage = json.loads(raw_rate_limit_usage)
            except json.JSONDecodeError:
                parsed_rate_limit_usage = None
            if isinstance(parsed_rate_limit_usage, dict):
                payload["rate_limit_usage"] = parsed_rate_limit_usage
            else:
                payload["rate_limit_usage"] = None
        else:
            payload["rate_limit_usage"] = None
        return SessionRecord.model_validate(payload)

    def _schedule_from_row(self, row: sqlite3.Row) -> ScheduledSessionRecord:
        payload = dict(row)
        for field_name in ("scheduled_at", "created_at"):
            payload[field_name] = datetime.fromisoformat(payload[field_name])
        payload["status"] = ScheduleStatus(payload.get("status", "pending"))
        payload["launch_mode"] = payload.get("launch_mode") or "auto"
        raw_args = payload.get("args") or "[]"
        try:
            parsed_args = json.loads(raw_args)
        except json.JSONDecodeError:
            parsed_args = []
        payload["args"] = parsed_args if isinstance(parsed_args, list) else []
        raw_overrides = payload.get("config_overrides") or "[]"
        try:
            parsed_overrides = json.loads(raw_overrides)
        except json.JSONDecodeError:
            parsed_overrides = []
        payload["config_overrides"] = (
            parsed_overrides if isinstance(parsed_overrides, list) else []
        )
        return ScheduledSessionRecord.model_validate(payload)

    def _event_from_row(self, row: sqlite3.Row) -> EventRecord:
        payload = dict(row)
        payload["ts"] = datetime.fromisoformat(payload["ts"])
        payload["metadata"] = json.loads(payload["metadata"])
        return EventRecord.model_validate(payload)

    def _board_entry_from_row(self, row: sqlite3.Row) -> BoardEntry:
        payload = dict(row)
        payload["created_at"] = datetime.fromisoformat(payload["created_at"])
        if payload.get("edited_at"):
            payload["edited_at"] = datetime.fromisoformat(payload["edited_at"])
        raw_metadata = payload.get("metadata") or "{}"
        try:
            decoded = json.loads(raw_metadata)
        except json.JSONDecodeError:
            decoded = {}
        payload["metadata"] = decoded if isinstance(decoded, dict) else {}
        return BoardEntry.model_validate(payload)

    def _serialize_field(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, BaseModel):
            return json.dumps(value.model_dump(mode="json"))
        if isinstance(value, dict):
            return json.dumps(value)
        return value

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        rows = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row["name"] == column for row in rows):
            return
        self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
