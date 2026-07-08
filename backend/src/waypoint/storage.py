import functools
import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from waypoint.perf import debug_timer
from waypoint.schemas import (
    BoardChannel,
    BoardEntry,
    EventKind,
    EventRecord,
    InboxApprovalAnswer,
    InboxApprovalBlock,
    InboxBlock,
    InboxBlockInput,
    InboxItem,
    InboxQuestionAnswer,
    InboxQuestionBlock,
    InboxReply,
    InboxReplyInput,
    InboxStatus,
    ScheduledMessageRecord,
    ScheduledMessageStatus,
    ScheduledSessionRecord,
    ScheduleStatus,
    SessionCommandInvocation,
    SessionInputItem,
    SessionPresetRecord,
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


_INBOX_BLOCKS_ADAPTER: TypeAdapter[list[InboxBlock]] = TypeAdapter(list[InboxBlock])


class InboxError(Exception):
    """Base for inbox block-submit errors the API maps to HTTP codes."""


class InboxBlockNotFoundError(InboxError):
    """The item exists but has no block with the given id (→ 404)."""


class InboxBlockTypeError(InboxError):
    """The submitted answer does not fit the target block's type (→ 422)."""


def _materialize_blocks(blocks: list[InboxBlockInput]) -> list[InboxBlock]:
    # Assign a server-side id to each authored block; answers/replies start null.
    return _INBOX_BLOCKS_ADAPTER.validate_python(
        [{**block.model_dump(mode="json"), "id": uuid.uuid4().hex} for block in blocks]
    )


def _recompute_inbox_status(blocks: list[InboxBlock]) -> InboxStatus:
    # Resolved iff there is at least one required interactive block AND all such
    # blocks are answered. The ``≥1 required`` guard defeats the vacuous-truth
    # trap: an item with no required interactive blocks (pure-FYI, or only
    # optional questions) must NOT resolve here — it resolves on read instead.
    required = [
        block
        for block in blocks
        if isinstance(block, (InboxQuestionBlock, InboxApprovalBlock))
        and block.required
    ]
    if not required:
        return InboxStatus.OPEN
    if all(block.answer is not None for block in required):
        return InboxStatus.RESOLVED
    return InboxStatus.OPEN


def _inbox_is_no_action(blocks: list[InboxBlock]) -> bool:
    # True when nothing required gates the item — the resolve-on-read path.
    return not any(
        isinstance(block, (InboxQuestionBlock, InboxApprovalBlock)) and block.required
        for block in blocks
    )


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
                resolved_model TEXT,
                effort TEXT,
                args TEXT NOT NULL DEFAULT '[]',
                config_overrides TEXT NOT NULL DEFAULT '[]',
                launch_env TEXT NOT NULL DEFAULT '{}',
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
                launch_env TEXT NOT NULL DEFAULT '{}',
                initial_prompt TEXT,
                permission_mode TEXT,
                scheduled_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                session_id TEXT,
                failure_reason TEXT
            );

            CREATE TABLE IF NOT EXISTS scheduled_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                submit INTEGER NOT NULL DEFAULT 1,
                command TEXT,
                items TEXT,
                attachments TEXT NOT NULL DEFAULT '[]',
                scheduled_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                failure_reason TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE INDEX IF NOT EXISTS idx_scheduled_messages_status
                ON scheduled_messages(status);

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

            -- Durable, human-facing inbox. Everything filtered/searched/sorted
            -- is a real column; the ordered content (with answers/replies) is a
            -- JSON blob under ``blocks``, updated in place via an atomic
            -- read-modify-write under the storage lock.
            CREATE TABLE IF NOT EXISTS inbox_items (
                id TEXT PRIMARY KEY,
                from_session_id TEXT NOT NULL,
                from_label TEXT,
                subject TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                read_at TEXT,
                version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                blocks TEXT NOT NULL DEFAULT '[]'
            );

            -- Reusable launch defaults applied at session/schedule request
            -- boundaries. ``spec`` is the JSON-serialized SessionPresetSpec.
            CREATE TABLE IF NOT EXISTS session_presets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                spec TEXT NOT NULL DEFAULT '{}',
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- At most one default preset per deployment; names are unique
            -- case-insensitively.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_session_presets_default
                ON session_presets(is_default)
                WHERE is_default = 1;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_session_presets_name_nocase
                ON session_presets(LOWER(name));
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
        self._ensure_column("sessions", "launch_env", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("sessions", "launch_mode", "TEXT NOT NULL DEFAULT 'auto'")
        self._ensure_column("sessions", "context_usage", "TEXT")
        self._ensure_column("sessions", "rate_limit_usage", "TEXT")
        self._ensure_column("sessions", "spawner_session_id", "TEXT")
        self._ensure_column("sessions", "worktree_path", "TEXT")
        self._ensure_column("sessions", "resolved_model", "TEXT")
        self._ensure_column("sessions", "tags", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("sessions", "preset_id", "TEXT")
        self._ensure_column("sessions", "preset_name", "TEXT")
        self._ensure_column("sessions", "account_profile_id", "TEXT")
        self._ensure_column("sessions", "account_profile_label", "TEXT")
        self._ensure_column(
            "scheduled_sessions", "config_overrides", "TEXT NOT NULL DEFAULT '[]'"
        )
        self._ensure_column(
            "scheduled_sessions", "launch_env", "TEXT NOT NULL DEFAULT '{}'"
        )
        self._ensure_column("scheduled_sessions", "preset_id", "TEXT")
        self._ensure_column("scheduled_sessions", "preset_name", "TEXT")
        self._ensure_column("scheduled_sessions", "account_profile_id", "TEXT")
        self._ensure_column("scheduled_sessions", "account_profile_label", "TEXT")
        # Additive migration for the inbox table on databases that predate it.
        # (No-ops on a fresh DB where the CREATE TABLE above already made the
        # complete table; only load-bearing for columns added in a later release.)
        self._ensure_column("inbox_items", "from_label", "TEXT")
        self._ensure_column("inbox_items", "read_at", "TEXT")
        self._ensure_column("inbox_items", "version", "INTEGER NOT NULL DEFAULT 0")
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
            CREATE INDEX IF NOT EXISTS idx_inbox_status
                ON inbox_items(status);
            CREATE INDEX IF NOT EXISTS idx_inbox_updated
                ON inbox_items(updated_at, id);
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
                resolved_model, effort, args, config_overrides, launch_env, context_usage,
                rate_limit_usage, tags, preset_id, preset_name,
                account_profile_id, account_profile_label
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                session.resolved_model,
                session.effort,
                json.dumps(list(session.args)),
                json.dumps(list(session.config_overrides)),
                json.dumps(session.launch_env),
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
                json.dumps(session.tags),
                session.preset_id,
                session.preset_name,
                session.account_profile_id,
                session.account_profile_label,
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
        fields.setdefault("updated_at", datetime.now(UTC))
        with debug_timer(log, "Storage.update_session", fields=sorted(fields)):
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
        merge: bool = False,
        unset: list[str] | None = None,
    ) -> BoardEntry | None:
        now = datetime.now(UTC)
        patch = metadata or {}
        unset = unset or []
        # Merge/unset both patch the current blob rather than replacing it, so
        # they read it first; the SELECT and UPDATE share this method's
        # ``@_synchronized`` lock, so the read-modify-write is atomic. ``unset``
        # implies patch semantics too — removing a key must not drop the others.
        if merge or unset:
            existing_row = self.connection.execute(
                "SELECT metadata FROM board_entries WHERE id = ? AND channel = ?",
                (entry_id, channel),
            ).fetchone()
            if existing_row is None:
                return None
            existing = json.loads(existing_row["metadata"] or "{}")
            final_meta = {**existing, **patch}
            for removed_key in unset:
                final_meta.pop(removed_key, None)
        else:
            final_meta = patch
        if text is None:
            cursor = self.connection.execute(
                "UPDATE board_entries SET metadata = ?, edited_at = ? "
                "WHERE id = ? AND channel = ?",
                (json.dumps(final_meta), now.isoformat(), entry_id, channel),
            )
        else:
            cursor = self.connection.execute(
                "UPDATE board_entries SET text = ?, metadata = ?, edited_at = ? "
                "WHERE id = ? AND channel = ?",
                (text, json.dumps(final_meta), now.isoformat(), entry_id, channel),
            )
        self.connection.commit()
        if not (cursor.rowcount or 0):
            return None
        row = self.connection.execute(
            "SELECT * FROM board_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        return self._board_entry_from_row(row) if row else None

    # ───────────────────────────── Inbox ─────────────────────────────

    @_synchronized
    def create_inbox_item(
        self,
        from_session_id: str,
        from_label: str | None,
        subject: str,
        blocks: list[InboxBlockInput],
    ) -> InboxItem:
        now = datetime.now(UTC)
        item = InboxItem(
            id=uuid.uuid4().hex,
            from_session_id=from_session_id,
            from_label=from_label,
            subject=subject,
            status=InboxStatus.OPEN,
            read_at=None,
            version=0,
            created_at=now,
            updated_at=now,
            blocks=_materialize_blocks(blocks),
        )
        self.connection.execute(
            """
            INSERT INTO inbox_items (
                id, from_session_id, from_label, subject, status, read_at,
                version, created_at, updated_at, blocks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.from_session_id,
                item.from_label,
                item.subject,
                item.status,
                None,
                item.version,
                now.isoformat(),
                now.isoformat(),
                self._dump_inbox_blocks(item.blocks),
            ),
        )
        self.connection.commit()
        return item

    @_synchronized
    def get_inbox_item(self, item_id: str) -> InboxItem | None:
        row = self.connection.execute(
            "SELECT * FROM inbox_items WHERE id = ?", (item_id,)
        ).fetchone()
        return self._inbox_item_from_row(row) if row else None

    @_synchronized
    def list_inbox_items(
        self,
        *,
        status: InboxStatus | None = None,
        query: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[InboxItem], bool, str | None]:
        # Keyset pagination over the ``(updated_at, id)`` sort. ``updated_at`` is
        # volatile (block-submit rewrites it), so a walk can skip/duplicate an
        # item answered mid-pagination — acceptable for a live load-more list
        # that also receives ``inbox_update`` events and self-heals on the client.
        sql = "SELECT * FROM inbox_items WHERE 1 = 1"
        params: list[Any] = []
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        if query:
            like = f"%{query}%"
            sql += " AND (subject LIKE ? OR from_label LIKE ?)"
            params.extend([like, like])
        if cursor:
            cursor_updated, _, cursor_id = cursor.partition("|")
            sql += " AND (updated_at < ? OR (updated_at = ? AND id < ?))"
            params.extend([cursor_updated, cursor_updated, cursor_id])
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(limit + 1)
        rows = self.connection.execute(sql, params).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [self._inbox_item_from_row(row) for row in rows]
        next_cursor: str | None = None
        if has_more and items:
            last = rows[-1]
            next_cursor = f"{last['updated_at']}|{last['id']}"
        return items, has_more, next_cursor

    @_synchronized
    def submit_inbox_block(
        self,
        item_id: str,
        block_id: str,
        *,
        answer: dict[str, Any] | None = None,
        reply: InboxReplyInput | None = None,
    ) -> tuple[InboxItem, bool] | None:
        # Atomic read-modify-write on the ``blocks`` JSON: SELECT → mutate the
        # one block (validated against its type) → recompute status (monotonic)
        # → bump version → UPDATE, all under this method's lock (mirrors
        # ``update_board_entry``). Returns ``(item, changed)`` — ``changed`` is
        # False for a no-op submit (neither answer nor reply), so the runtime
        # can skip the broadcast (mirrors ``mark_inbox_read``). Returns None if
        # the item is gone (→ 404); raises InboxBlockNotFoundError /
        # InboxBlockTypeError for block-level problems the API maps to 404 / 422.
        row = self.connection.execute(
            "SELECT * FROM inbox_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return None
        blocks = self._parse_inbox_blocks(row["blocks"])
        target = next((block for block in blocks if block.id == block_id), None)
        if target is None:
            raise InboxBlockNotFoundError(block_id)
        now = datetime.now(UTC)
        changed = False
        if answer is not None:
            self._apply_inbox_answer(target, answer, now)
            changed = True
        if reply is not None:
            target.reply = InboxReply(
                notes=reply.notes,
                attachments=list(reply.attachments),
                created_at=now,
            )
            changed = True
        if not changed:
            return self._inbox_item_from_row(row), False
        existing_status = InboxStatus(row["status"])
        # Monotonic: resolved is terminal — a later reply/optional answer must
        # never demote a read-resolved item back to open (RFC §7/§13).
        if (
            existing_status == InboxStatus.RESOLVED
            or _recompute_inbox_status(blocks) == InboxStatus.RESOLVED
        ):
            new_status = InboxStatus.RESOLVED
        else:
            new_status = existing_status
        new_version = int(row["version"]) + 1
        self.connection.execute(
            "UPDATE inbox_items SET status = ?, version = ?, updated_at = ?, "
            "blocks = ? WHERE id = ?",
            (
                new_status,
                new_version,
                now.isoformat(),
                self._dump_inbox_blocks(blocks),
                item_id,
            ),
        )
        self.connection.commit()
        updated = self.get_inbox_item(item_id)
        assert updated is not None
        return updated, True

    @_synchronized
    def mark_inbox_read(self, item_id: str) -> tuple[InboxItem, bool] | None:
        # Idempotent: the UI calls this on every open. A first read stamps
        # ``read_at``; a no-action item additionally resolves-on-read (a real
        # state change → bump version). A plain read of an interactive item sets
        # ``read_at`` only and never bumps version (RFC §7). Once ``read_at`` is
        # set this is a no-op. Returns ``(item, changed)`` — ``changed`` is False
        # for the no-op re-read so the runtime can skip a redundant broadcast; or
        # ``None`` when the item does not exist.
        row = self.connection.execute(
            "SELECT * FROM inbox_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return None
        if row["read_at"]:
            return self._inbox_item_from_row(row), False
        now = datetime.now(UTC)
        blocks = self._parse_inbox_blocks(row["blocks"])
        status = InboxStatus(row["status"])
        if status == InboxStatus.OPEN and _inbox_is_no_action(blocks):
            self.connection.execute(
                "UPDATE inbox_items SET read_at = ?, status = ?, version = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    now.isoformat(),
                    InboxStatus.RESOLVED,
                    int(row["version"]) + 1,
                    now.isoformat(),
                    item_id,
                ),
            )
        else:
            self.connection.execute(
                "UPDATE inbox_items SET read_at = ? WHERE id = ?",
                (now.isoformat(), item_id),
            )
        self.connection.commit()
        updated = self.get_inbox_item(item_id)
        assert updated is not None  # just updated it under the lock
        return updated, True

    @_synchronized
    def delete_inbox_item(self, item_id: str) -> bool:
        # Removes the inbox row only; reply/display attachments stay in their
        # session stores (RFC §13).
        cursor = self.connection.execute(
            "DELETE FROM inbox_items WHERE id = ?", (item_id,)
        )
        self.connection.commit()
        return (cursor.rowcount or 0) > 0

    @_synchronized
    def delete_inbox_items(self, item_ids: list[str]) -> list[str]:
        # Batch delete by id. Returns the subset that actually existed (so the
        # runtime fans a ``deleted`` broadcast only for real removals). Chunked
        # under SQLite's ~999 bound-variable limit; the whole batch is one lock
        # hold with a single trailing commit (all-or-nothing across chunks).
        unique_ids = list(dict.fromkeys(item_ids))
        deleted: list[str] = []
        for start in range(0, len(unique_ids), 500):
            chunk = unique_ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            present = [
                row["id"]
                for row in self.connection.execute(
                    f"SELECT id FROM inbox_items WHERE id IN ({placeholders})", chunk
                ).fetchall()
            ]
            if not present:
                continue
            self.connection.execute(
                f"DELETE FROM inbox_items WHERE id IN ({placeholders})", chunk
            )
            deleted.extend(present)
        if deleted:
            self.connection.commit()
        return deleted

    @_synchronized
    def delete_resolved_inbox_items(self) -> list[str]:
        # Empty-the-resolved-folder: removes every resolved item regardless of
        # pagination. SELECT ids first so the runtime can broadcast each removal.
        resolved = [
            row["id"]
            for row in self.connection.execute(
                "SELECT id FROM inbox_items WHERE status = ?",
                (InboxStatus.RESOLVED,),
            ).fetchall()
        ]
        if not resolved:
            return []
        self.connection.execute(
            "DELETE FROM inbox_items WHERE status = ?", (InboxStatus.RESOLVED,)
        )
        self.connection.commit()
        return resolved

    @_synchronized
    def unresolved_inbox_count(self) -> int:
        return int(
            self.connection.execute(
                "SELECT COUNT(*) AS n FROM inbox_items WHERE status = ?",
                (InboxStatus.OPEN,),
            ).fetchone()["n"]
        )

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
    def seed_events(
        self, session_id: str, events: list[EventRecord]
    ) -> list[EventRecord]:
        """Bulk-append externally-sourced events (e.g. imported thread history).

        Unlike ``clone_events`` (which copies another session's rows verbatim),
        the caller supplies freshly-built ``EventRecord``s whose ``sequence`` is
        ignored: sequences are (re)assigned ascending from
        ``MAX(sequence) + 1`` for the target so seeded history slots after any
        events already present. Each row is stamped with the canonical envelope
        ``version`` like ``append_event``, but the session's
        ``last_event_at``/``updated_at``/``status`` are updated once at the end
        rather than per row. Returns the persisted rows (with assigned ``id`` and
        ``sequence``) in order, so callers can mirror them to the structured log.
        """
        if not events:
            return []
        base = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS max_sequence FROM events WHERE session_id = ?",
                (session_id,),
            ).fetchone()["max_sequence"]
        )
        rows: list[tuple[str, str, str, str, str, int]] = []
        last_ts_iso: str | None = None
        last_status: str | None = None
        for offset, event in enumerate(events, start=1):
            metadata = event.metadata
            if "version" not in metadata:
                metadata = {**metadata, "version": 1}
            ts_iso = event.ts.isoformat()
            rows.append(
                (
                    session_id,
                    ts_iso,
                    event.kind,
                    event.text,
                    json.dumps(metadata),
                    base + offset,
                )
            )
            last_ts_iso = ts_iso
            status = metadata.get("status")
            if status is not None:
                last_status = status
        self.connection.executemany(
            """
            INSERT INTO events (session_id, ts, kind, text, metadata, sequence)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.connection.execute(
            """
            UPDATE sessions
            SET last_event_at = ?, updated_at = ?, status = COALESCE(?, status)
            WHERE id = ?
            """,
            (last_ts_iso, last_ts_iso, last_status, session_id),
        )
        self.connection.commit()
        persisted = self.connection.execute(
            """
            SELECT * FROM events
            WHERE session_id = ? AND sequence > ?
            ORDER BY sequence ASC, id ASC
            """,
            (session_id, base),
        ).fetchall()
        return [self._event_from_row(row) for row in persisted]

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
    def latest_todo_event(self, session_id: str) -> EventRecord | None:
        """Return the most recent todo/task event, or ``None``.

        Every todo event is a full snapshot of the current list, so the
        single latest one drives the frontend's task dock regardless of
        which transcript window the client has paginated into. The
        predicate mirrors :func:`waypoint.events.is_todo_list_event`
        (``item_type == "todo_list"`` or a ``TodoWrite`` tool name, with
        the ``default_api:`` prefix); running it in SQL avoids
        deserializing every row of a long, todo-less session on each load.
        """
        row = self.connection.execute(
            """
            SELECT * FROM events
            WHERE session_id = ?
              AND (json_extract(metadata, '$.item_type') = 'todo_list'
                   OR lower(json_extract(metadata, '$.tool_name'))
                      IN ('todowrite', 'default_api:todowrite'))
            ORDER BY sequence DESC, id DESC
            LIMIT 1
            """,
            [session_id],
        ).fetchone()
        return self._event_from_row(row) if row is not None else None

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
                config_overrides, launch_env, initial_prompt, permission_mode, model, effort, scheduled_at,
                created_at, status, session_id, failure_reason, preset_id, preset_name,
                account_profile_id, account_profile_label
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(schedule.launch_env),
                schedule.initial_prompt,
                schedule.permission_mode,
                schedule.model,
                schedule.effort,
                schedule.scheduled_at.isoformat(),
                schedule.created_at.isoformat(),
                schedule.status,
                schedule.session_id,
                schedule.failure_reason,
                schedule.preset_id,
                schedule.preset_name,
                schedule.account_profile_id,
                schedule.account_profile_label,
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

    # ── Session presets ──────────────────────────────────────────────────
    @_synchronized
    def create_session_preset(self, preset: SessionPresetRecord) -> SessionPresetRecord:
        self.connection.execute(
            """
            INSERT INTO session_presets (
                id, name, description, spec, is_default, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                preset.id,
                preset.name,
                preset.description,
                json.dumps(preset.spec.model_dump(mode="json")),
                1 if preset.is_default else 0,
                preset.created_at.isoformat(),
                preset.updated_at.isoformat(),
            ),
        )
        self.connection.commit()
        return preset

    @_synchronized
    def list_session_presets(self) -> list[SessionPresetRecord]:
        rows = self.connection.execute(
            "SELECT * FROM session_presets ORDER BY LOWER(name) ASC"
        ).fetchall()
        return [self._preset_from_row(row) for row in rows]

    @_synchronized
    def get_session_preset(self, preset_id: str) -> SessionPresetRecord | None:
        row = self.connection.execute(
            "SELECT * FROM session_presets WHERE id = ?", (preset_id,)
        ).fetchone()
        return self._preset_from_row(row) if row is not None else None

    @_synchronized
    def get_session_preset_by_name(self, name: str) -> SessionPresetRecord | None:
        row = self.connection.execute(
            "SELECT * FROM session_presets WHERE LOWER(name) = LOWER(?)", (name,)
        ).fetchone()
        return self._preset_from_row(row) if row is not None else None

    @_synchronized
    def get_default_session_preset(self) -> SessionPresetRecord | None:
        row = self.connection.execute(
            "SELECT * FROM session_presets WHERE is_default = 1"
        ).fetchone()
        return self._preset_from_row(row) if row is not None else None

    @_synchronized
    def update_session_preset(
        self, preset_id: str, **fields: Any
    ) -> SessionPresetRecord:
        # ``is_default`` is intentionally not updatable here — the default
        # invariant is managed exclusively by ``set_default_session_preset`` so
        # the partial unique index is never violated by a raw column write.
        fields.pop("is_default", None)
        fields.setdefault("updated_at", datetime.now(UTC))
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [self._serialize_field(value) for value in fields.values()]
        values.append(preset_id)
        if _SUPPORTS_RETURNING:
            row = self.connection.execute(
                f"UPDATE session_presets SET {assignments} WHERE id = ? RETURNING *",
                values,
            ).fetchone()
            self.connection.commit()
            if row is None:
                raise KeyError(preset_id)
            return self._preset_from_row(row)
        if self.get_session_preset(preset_id) is None:
            raise KeyError(preset_id)
        self.connection.execute(
            f"UPDATE session_presets SET {assignments} WHERE id = ?", values
        )
        self.connection.commit()
        updated = self.get_session_preset(preset_id)
        assert updated is not None
        return updated

    @_synchronized
    def delete_session_preset(self, preset_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM session_presets WHERE id = ?", (preset_id,)
        )
        self.connection.commit()
        return (cursor.rowcount or 0) > 0

    @_synchronized
    def set_default_session_preset(self, preset_id: str | None) -> None:
        # Clear the current default and (optionally) set a new one in one
        # transaction so the ``is_default = 1`` partial unique index always holds.
        ts = datetime.now(UTC).isoformat()
        self.connection.execute(
            "UPDATE session_presets SET is_default = 0, updated_at = ? "
            "WHERE is_default = 1",
            (ts,),
        )
        if preset_id is not None:
            cursor = self.connection.execute(
                "UPDATE session_presets SET is_default = 1, updated_at = ? "
                "WHERE id = ?",
                (ts, preset_id),
            )
            if (cursor.rowcount or 0) == 0:
                self.connection.rollback()
                raise KeyError(preset_id)
        self.connection.commit()

    def _preset_from_row(self, row: sqlite3.Row) -> SessionPresetRecord:
        payload = dict(row)
        for field_name in ("created_at", "updated_at"):
            payload[field_name] = datetime.fromisoformat(payload[field_name])
        payload["is_default"] = bool(payload.get("is_default", 0))
        raw_spec = payload.get("spec") or "{}"
        try:
            parsed_spec = json.loads(raw_spec)
        except json.JSONDecodeError:
            parsed_spec = {}
        payload["spec"] = parsed_spec if isinstance(parsed_spec, dict) else {}
        return SessionPresetRecord.model_validate(payload)

    @_synchronized
    def create_scheduled_message(
        self, record: ScheduledMessageRecord
    ) -> ScheduledMessageRecord:
        self.connection.execute(
            """
            INSERT INTO scheduled_messages (
                id, session_id, text, submit, command, items, attachments,
                scheduled_at, created_at, status, failure_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.session_id,
                record.text,
                1 if record.submit else 0,
                (
                    json.dumps(record.command.model_dump(mode="json"))
                    if record.command
                    else None
                ),
                (
                    json.dumps([item.model_dump(mode="json") for item in record.items])
                    if record.items
                    else None
                ),
                json.dumps(list(record.attachments)),
                record.scheduled_at.isoformat(),
                record.created_at.isoformat(),
                record.status,
                record.failure_reason,
            ),
        )
        self.connection.commit()
        return record

    @_synchronized
    def list_scheduled_messages(
        self,
        statuses: list[ScheduledMessageStatus] | None = None,
        session_id: str | None = None,
    ) -> list[ScheduledMessageRecord]:
        query = "SELECT * FROM scheduled_messages"
        params: list[Any] = []
        conditions: list[str] = []
        if statuses:
            placeholders = ",".join(["?"] * len(statuses))
            conditions.append(f"status IN ({placeholders})")
            params.extend(status.value for status in statuses)
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY scheduled_at ASC, created_at ASC"
        rows = self.connection.execute(query, params).fetchall()
        return [self._scheduled_message_from_row(row) for row in rows]

    @_synchronized
    def get_scheduled_message(self, message_id: str) -> ScheduledMessageRecord | None:
        row = self.connection.execute(
            "SELECT * FROM scheduled_messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            return None
        return self._scheduled_message_from_row(row)

    @_synchronized
    def update_scheduled_message(
        self, message_id: str, **fields: Any
    ) -> ScheduledMessageRecord:
        if not fields:
            current = self.get_scheduled_message(message_id)
            if current is None:
                raise KeyError(message_id)
            return current
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [self._serialize_field(value) for value in fields.values()]
        values.append(message_id)
        self.connection.execute(
            f"UPDATE scheduled_messages SET {assignments} WHERE id = ?", values
        )
        self.connection.commit()
        updated = self.get_scheduled_message(message_id)
        if updated is None:
            raise KeyError(message_id)
        return updated

    @_synchronized
    def delete_scheduled_message(self, message_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM scheduled_messages WHERE id = ?", (message_id,)
        )
        self.connection.commit()
        return (cursor.rowcount or 0) > 0

    @_synchronized
    def delete_scheduled_messages_by_session(self, session_id: str) -> int:
        cursor = self.connection.execute(
            "DELETE FROM scheduled_messages WHERE session_id = ?", (session_id,)
        )
        self.connection.commit()
        return cursor.rowcount or 0

    @_synchronized
    def delete_scheduled_messages_by_status(
        self,
        statuses: list[ScheduledMessageStatus],
        session_id: str | None = None,
    ) -> int:
        if not statuses:
            return 0
        placeholders = ",".join(["?"] * len(statuses))
        query = f"DELETE FROM scheduled_messages WHERE status IN ({placeholders})"
        params = [item.value for item in statuses]
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        cursor = self.connection.execute(query, params)
        self.connection.commit()
        return cursor.rowcount or 0

    @_synchronized
    def next_sequence(self, session_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS max_sequence FROM events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["max_sequence"]) + 1

    @_synchronized
    def db_stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ):
            table = row["name"]
            count = self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[
                0
            ]
            stats[table] = {"row_count": count}

        events_by_kind = {}
        for row in self.connection.execute(
            "SELECT kind, COUNT(*) as count FROM events GROUP BY kind"
        ):
            events_by_kind[row["kind"]] = row["count"]
        stats["events_by_kind"] = events_by_kind

        events_by_session = {}
        for row in self.connection.execute(
            "SELECT session_id, COUNT(*) as count FROM events GROUP BY session_id"
        ):
            events_by_session[row["session_id"]] = row["count"]
        stats["events_by_session"] = events_by_session

        db_size = (
            self.database_path.stat().st_size if self.database_path.exists() else 0
        )
        wal_path = self.database_path.parent / (self.database_path.name + "-wal")
        wal_size = wal_path.stat().st_size if wal_path.exists() else 0
        stats["fs_footprint"] = {
            "db_size_bytes": db_size,
            "wal_size_bytes": wal_size,
        }

        return stats

    @_synchronized
    def scan_orphan_session_dirs(self, sessions_dir: Path) -> list[Path]:
        orphans: list[Path] = []
        if not sessions_dir.exists():
            return orphans

        valid_ids = {
            row["id"] for row in self.connection.execute("SELECT id FROM sessions")
        }

        for session_dir in sessions_dir.iterdir():
            if session_dir.is_dir() and session_dir.name not in valid_ids:
                orphans.append(session_dir)
        return orphans

    def scan_structured_logs(self, sessions_dir: Path) -> list[Path]:
        """Per-session ``events.jsonl`` audit logs present on disk.

        These mirror the SQLite events table (the source of truth) and are
        write-only, so they are safe to delete to reclaim space.

        Filesystem-only — no ``@_synchronized`` because it never touches the
        connection (the decorator only serializes DB access).
        """
        logs: list[Path] = []
        if not sessions_dir.exists():
            return logs
        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            log_path = session_dir / "events.jsonl"
            if log_path.is_file():
                logs.append(log_path)
        return logs

    @_synchronized
    def delete_events_for(
        self,
        transports: list[str] | None = None,
        statuses: list[str] | None = None,
        older_than: datetime | None = None,
        dry_run: bool = False,
    ) -> int:
        action = "SELECT COUNT(*)" if dry_run else "DELETE"
        query = f"""
            {action} FROM events
            WHERE kind IN ('agent_output', 'raw_terminal_chunk')
        """
        params: list[Any] = []

        if transports or statuses or older_than:
            query += " AND session_id IN (SELECT id FROM sessions WHERE 1=1"
            if transports:
                placeholders = ",".join(["?"] * len(transports))
                query += f" AND transport IN ({placeholders})"
                params.extend(transports)
            if statuses:
                placeholders = ",".join(["?"] * len(statuses))
                query += f" AND status IN ({placeholders})"
                params.extend(statuses)
            if older_than:
                query += " AND last_event_at < ?"
                params.append(older_than.isoformat())
            query += ")"

        cursor = self.connection.execute(query, params)
        if dry_run:
            return cursor.fetchone()[0]
        self.connection.commit()
        return cursor.rowcount or 0

    @_synchronized
    def vacuum(self) -> None:
        # VACUUM cannot run inside a transaction; with isolation_level='' a
        # pending DML would have opened one, so commit first to be safe.
        self.connection.commit()
        self.connection.execute("VACUUM")
        self.connection.commit()

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
        raw_launch_env = payload.get("launch_env") or "{}"
        try:
            parsed_launch_env = json.loads(raw_launch_env)
        except json.JSONDecodeError:
            parsed_launch_env = {}
        payload["launch_env"] = (
            parsed_launch_env if isinstance(parsed_launch_env, dict) else {}
        )
        raw_tags = payload.get("tags") or "{}"
        try:
            parsed_tags = json.loads(raw_tags)
        except json.JSONDecodeError:
            parsed_tags = {}
        payload["tags"] = parsed_tags if isinstance(parsed_tags, dict) else {}
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
        raw_launch_env = payload.get("launch_env") or "{}"
        try:
            parsed_launch_env = json.loads(raw_launch_env)
        except json.JSONDecodeError:
            parsed_launch_env = {}
        payload["launch_env"] = (
            parsed_launch_env if isinstance(parsed_launch_env, dict) else {}
        )
        return ScheduledSessionRecord.model_validate(payload)

    def _scheduled_message_from_row(self, row: sqlite3.Row) -> ScheduledMessageRecord:
        payload = dict(row)
        for field_name in ("scheduled_at", "created_at"):
            payload[field_name] = datetime.fromisoformat(payload[field_name])
        payload["status"] = ScheduledMessageStatus(payload.get("status", "pending"))
        payload["submit"] = bool(payload.get("submit", 1))
        raw_command = payload.pop("command", None)
        if raw_command:
            try:
                cmd_data = json.loads(raw_command)
                payload["command"] = (
                    SessionCommandInvocation.model_validate(cmd_data)
                    if isinstance(cmd_data, dict)
                    else None
                )
            except (json.JSONDecodeError, ValidationError):
                payload["command"] = None
        else:
            payload["command"] = None
        raw_items = payload.pop("items", None)
        if raw_items:
            try:
                items_data = json.loads(raw_items)
                payload["items"] = (
                    [SessionInputItem.model_validate(i) for i in items_data]
                    if isinstance(items_data, list)
                    else None
                )
            except (json.JSONDecodeError, ValidationError):
                payload["items"] = None
        else:
            payload["items"] = None
        raw_attachments = payload.get("attachments") or "[]"
        try:
            parsed_attachments = json.loads(raw_attachments)
        except json.JSONDecodeError:
            parsed_attachments = []
        payload["attachments"] = (
            parsed_attachments if isinstance(parsed_attachments, list) else []
        )
        return ScheduledMessageRecord.model_validate(payload)

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

    def _dump_inbox_blocks(self, blocks: list[InboxBlock]) -> str:
        return json.dumps(_INBOX_BLOCKS_ADAPTER.dump_python(blocks, mode="json"))

    def _parse_inbox_blocks(self, raw: str | None) -> list[InboxBlock]:
        try:
            decoded = json.loads(raw or "[]")
        except json.JSONDecodeError:
            decoded = []
        if not isinstance(decoded, list):
            return []
        return _INBOX_BLOCKS_ADAPTER.validate_python(decoded)

    def _apply_inbox_answer(
        self, block: InboxBlock, answer: dict[str, Any], now: datetime
    ) -> None:
        # An ``answer`` is only valid on the matching interactive block type, and
        # must fit that block's declared options — the CLI/REST scripting surface
        # bypasses the UI's constraints, so an off-menu decision or an unknown /
        # over-count selection is a 422 here rather than silently recorded.
        try:
            if isinstance(block, InboxQuestionBlock):
                parsed_q = InboxQuestionAnswer.model_validate(answer)
                labels = {option.label for option in block.options}
                unknown = [
                    choice for choice in parsed_q.selected if choice not in labels
                ]
                if unknown:
                    raise InboxBlockTypeError(
                        f"unknown option(s): {', '.join(unknown)}"
                    )
                if not block.multi and len(parsed_q.selected) > 1:
                    raise InboxBlockTypeError(
                        "single-select question got multiple selections"
                    )
                if (
                    block.required
                    and not parsed_q.selected
                    and not (parsed_q.other or "").strip()
                ):
                    # A content-free answer must not satisfy a required gate.
                    raise InboxBlockTypeError(
                        "required question needs a selection or free-text answer"
                    )
                block.answer = parsed_q
            elif isinstance(block, InboxApprovalBlock):
                parsed_a = InboxApprovalAnswer.model_validate(answer)
                if parsed_a.decision not in set(block.options):
                    raise InboxBlockTypeError(f"unknown decision: {parsed_a.decision}")
                block.answer = parsed_a
            else:
                raise InboxBlockTypeError(f"answer not allowed on {block.type} block")
        except ValidationError as exc:
            raise InboxBlockTypeError(str(exc)) from exc
        block.answered_at = now

    def _inbox_item_from_row(self, row: sqlite3.Row) -> InboxItem:
        payload = dict(row)
        payload["created_at"] = datetime.fromisoformat(payload["created_at"])
        payload["updated_at"] = datetime.fromisoformat(payload["updated_at"])
        payload["read_at"] = (
            datetime.fromisoformat(payload["read_at"])
            if payload.get("read_at")
            else None
        )
        raw_blocks = payload.get("blocks") or "[]"
        try:
            decoded = json.loads(raw_blocks)
        except json.JSONDecodeError:
            decoded = []
        payload["blocks"] = decoded if isinstance(decoded, list) else []
        return InboxItem.model_validate(payload)

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
