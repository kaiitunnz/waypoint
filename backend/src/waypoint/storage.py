import functools
import json
import logging
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
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
    ManagerConfig,
    ManagerTicket,
    ManagerTicketState,
    ScheduledMessageRecord,
    ScheduledMessageStatus,
    ScheduledSessionRecord,
    ScheduleStatus,
    SessionCommandInvocation,
    SessionInputItem,
    SessionPresetRecord,
    SessionRecord,
    SessionStatus,
    SessionTokenUsage,
    TokenUsageInit,
    TokenUsageRecord,
    WakeSubscription,
)
from waypoint.telemetry.store import TelemetryStore
from waypoint.usage_providers.store import UsageProviderStore

log = logging.getLogger("waypoint.storage")

# The default compiled-templates layout `manager init` writes under a repo, used
# to recover the legacy manager's repo binding at migration time.
_MANAGER_TEMPLATES_SUFFIX = "/.waypoint/manager/templates"


def _repo_dir_from_templates_dir(payload: dict[str, Any]) -> str | None:
    """Recover a legacy manager's ``repo_dir`` from its compiled ``templates_dir``.

    The legacy config never persisted ``repo_dir``, but `manager init` compiled
    templates to ``<repo_dir>/.waypoint/manager/templates`` by default, so the repo
    is the ancestor of that path. Returns None when the templates dir is absent or
    was customized off the default layout (the manager then migrates unbound and is
    adopted on the next init).
    """
    render_context = payload.get("render_context")
    if not isinstance(render_context, dict):
        return None
    templates_dir = render_context.get("templates_dir")
    if isinstance(templates_dir, str) and templates_dir.endswith(
        _MANAGER_TEMPLATES_SUFFIX
    ):
        return templates_dir[: -len(_MANAGER_TEMPLATES_SUFFIX)]
    return None


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


class ManagerTicketConflict(Exception):
    """A version-checked manager-ticket update lost the CAS race (→ 409)."""


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
        self.telemetry = TelemetryStore(self.connection, self._lock)
        self.usage_providers = UsageProviderStore(
            self.connection, self._lock, self.database_path.parent
        )
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
                session_token_usage TEXT,
                rate_limit_usage TEXT
            );

            CREATE TABLE IF NOT EXISTS session_token_usage_records (
                session_id TEXT NOT NULL,
                source TEXT NOT NULL,
                record_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                usage_json TEXT NOT NULL,
                PRIMARY KEY (session_id, source, record_id)
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

            -- Durable notification outbox. One row per (channel, logical
            -- request); UNIQUE(channel_id, dedupe_key) makes repeated adapter
            -- events / retries idempotent. Holds no secrets — the rendered
            -- intent only, never a bot token.
            CREATE TABLE IF NOT EXISTS notification_deliveries (
                id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                intent_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                lease_until TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                sent_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(channel_id, dedupe_key)
            );

            CREATE INDEX IF NOT EXISTS idx_notification_deliveries_ready
                ON notification_deliveries(status, next_attempt_at);

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
                failure_reason TEXT,
                cron TEXT,
                timezone TEXT,
                last_run_at TEXT,
                last_run_status TEXT,
                last_failure_reason TEXT
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
                cron TEXT,
                timezone TEXT,
                last_run_at TEXT,
                last_run_status TEXT,
                last_failure_reason TEXT,
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

            -- A session's standing wake subscriptions. ``channel_globs``/``kinds``
            -- are JSON lists; ``wake_on_inbox`` is a 0/1 flag. Read fresh on every
            -- board/inbox mutation, so no in-memory re-registration on boot.
            CREATE TABLE IF NOT EXISTS wake_subscriptions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                channel_globs TEXT NOT NULL DEFAULT '[]',
                kinds TEXT NOT NULL DEFAULT '[]',
                wake_on_inbox INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            -- Waypoint Manager state machine. Filterable columns
            -- (state/priority/scale) + timestamps are denormalized for
            -- querying/ordering; the whole ticket is the JSON ``payload`` blob,
            -- updated in place via a version-checked read-modify-write.
            CREATE TABLE IF NOT EXISTS manager_tickets (
                id TEXT PRIMARY KEY,
                manager_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'p2',
                state TEXT NOT NULL DEFAULT 'intake',
                scale TEXT,
                version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}'
            );

            -- One row per initialized manager (one manager per repository). The
            -- whole ManagerConfig is the JSON ``payload``; ``project``/``repo_dir``/
            -- ``owner_session_id`` are denormalized for cross-manager queries
            -- (enumeration, repo/owner lookup). ``repo_dir`` is UNIQUE (NULL when
            -- unknown, e.g. a migrated legacy manager awaiting re-init).
            CREATE TABLE IF NOT EXISTS manager_config (
                id TEXT PRIMARY KEY,
                project TEXT NOT NULL DEFAULT '',
                repo_dir TEXT UNIQUE,
                owner_session_id TEXT,
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT ''
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
        self._ensure_column("sessions", "launch_env", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("sessions", "launch_mode", "TEXT NOT NULL DEFAULT 'auto'")
        self._ensure_column("sessions", "context_usage", "TEXT")
        token_usage_column_is_new = not self._has_column(
            "sessions", "session_token_usage"
        )
        self._ensure_column("sessions", "session_token_usage", "TEXT")
        self._ensure_column("sessions", "rate_limit_usage", "TEXT")
        self._ensure_column("sessions", "spawner_session_id", "TEXT")
        self._ensure_column("sessions", "worktree_path", "TEXT")
        self._ensure_column("sessions", "resolved_model", "TEXT")
        self._ensure_column("sessions", "tags", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("sessions", "preset_id", "TEXT")
        self._ensure_column("sessions", "preset_name", "TEXT")
        self._ensure_column("sessions", "account_profile_id", "TEXT")
        self._ensure_column("sessions", "account_profile_label", "TEXT")
        self._ensure_column("sessions", "verified_account_key", "TEXT")
        self._ensure_column("sessions", "verified_account_label", "TEXT")
        self._ensure_column("sessions", "verified_account_probed_at", "TEXT")
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
        # Recurrence (ticket 1076): additive, null means one-time.
        for _sched_table in ("scheduled_sessions", "scheduled_messages"):
            self._ensure_column(_sched_table, "cron", "TEXT")
            self._ensure_column(_sched_table, "timezone", "TEXT")
            self._ensure_column(_sched_table, "last_run_at", "TEXT")
            self._ensure_column(_sched_table, "last_run_status", "TEXT")
            self._ensure_column(_sched_table, "last_failure_reason", "TEXT")
        # Additive migration for the inbox table on databases that predate it.
        # (No-ops on a fresh DB where the CREATE TABLE above already made the
        # complete table; only load-bearing for columns added in a later release.)
        self._ensure_column("inbox_items", "from_label", "TEXT")
        self._ensure_column("inbox_items", "read_at", "TEXT")
        self._ensure_column("inbox_items", "version", "INTEGER NOT NULL DEFAULT 0")
        # Multi-manager migration: partition tickets by manager, then rebuild the
        # old single-row ``manager_config`` (pinned ``id = 1``) into the per-manager
        # shape and adopt the one legacy manager under a minted id.
        self._ensure_column("manager_tickets", "manager_id", "TEXT NOT NULL DEFAULT ''")
        self._migrate_manager_config_to_multi()
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
            CREATE INDEX IF NOT EXISTS idx_sessions_status
                ON sessions(status);
            CREATE INDEX IF NOT EXISTS idx_sessions_last_event
                ON sessions(last_event_at);
            CREATE INDEX IF NOT EXISTS idx_wake_subs_session
                ON wake_subscriptions(session_id);
            CREATE INDEX IF NOT EXISTS idx_manager_tickets_manager
                ON manager_tickets(manager_id, state);
            CREATE INDEX IF NOT EXISTS idx_manager_tickets_state
                ON manager_tickets(state);
            CREATE INDEX IF NOT EXISTS idx_manager_tickets_priority
                ON manager_tickets(priority);
            """)
        self.telemetry.init_schema()
        self.usage_providers.init_schema()
        if token_usage_column_is_new:
            self._mark_sessions_pretracked()
        self.connection.commit()

    def _has_column(self, table: str, column: str) -> bool:
        rows = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == column for row in rows)

    def _mark_sessions_pretracked(self) -> None:
        """Flag sessions that predate the ledger so their coverage reports
        "tracked since", not the whole session.

        Runs once, guarded by the column having just been added, so sessions
        created after the migration are never marked. The runtime's
        coverage-init reads the ``transport_state`` marker.
        """
        rows = self.connection.execute(
            "SELECT id, transport_state FROM sessions"
        ).fetchall()
        for row in rows:
            try:
                state = json.loads(row["transport_state"] or "{}")
            except json.JSONDecodeError:
                state = {}
            if not isinstance(state, dict):
                state = {}
            state["pretracked_tokens"] = True
            self.connection.execute(
                "UPDATE sessions SET transport_state = ? WHERE id = ?",
                (json.dumps(state), row["id"]),
            )

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
        # No FK cascade is enforced (PRAGMA foreign_keys is off), so prune the
        # per-turn token ledger explicitly in the same synchronized transaction.
        self.connection.execute(
            "DELETE FROM session_token_usage_records WHERE session_id = ?",
            (session_id,),
        )
        self.connection.execute(
            "DELETE FROM wake_subscriptions WHERE session_id = ?",
            (session_id,),
        )
        self.telemetry.delete_session(session_id)
        cursor = self.connection.execute(
            "DELETE FROM sessions WHERE id = ?",
            (session_id,),
        )
        sessions_deleted = cursor.rowcount or 0
        self.connection.commit()
        return sessions_deleted > 0 or events_deleted > 0

    @_synchronized
    def record_token_usage(
        self,
        session_id: str,
        record: TokenUsageRecord,
        *,
        init: TokenUsageInit,
    ) -> SessionTokenUsage:
        """Upsert one per-turn ledger row and delta-update the session aggregate.

        Idempotent under ``(session_id, source, record_id)``: a duplicate or a
        replay re-upserts the same row for a net-zero delta; a revised turn
        replaces it. ``init`` seeds coverage/observed_from on the first record
        only. One indexed read + one upsert + a bounded update, never a scan.
        """
        row = self.connection.execute(
            """
            SELECT usage_json FROM session_token_usage_records
            WHERE session_id = ? AND source = ? AND record_id = ?
            """,
            (session_id, record.source, record.record_id),
        ).fetchone()
        prior = None
        if row is not None:
            try:
                prior_dict = json.loads(row["usage_json"])
            except json.JSONDecodeError:
                prior_dict = None
            if isinstance(prior_dict, dict):
                prior = prior_dict

        self.connection.execute(
            """
            INSERT INTO session_token_usage_records
                (session_id, source, record_id, observed_at, usage_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id, source, record_id) DO UPDATE SET
                observed_at = excluded.observed_at,
                usage_json = excluded.usage_json
            """,
            (
                session_id,
                record.source,
                record.record_id,
                record.observed_at.isoformat(),
                json.dumps(
                    {
                        "totals": record.totals,
                        "display_total_tokens": record.display_total_tokens,
                        "model": record.model,
                        "effort": record.effort,
                    }
                ),
            ),
        )

        blob = self._load_token_usage_blob(session_id)
        if blob is None:
            blob = {
                "source": record.source,
                "tracked_turns": 0,
                "totals": {},
                "display_total_tokens": None,
                "display_total_sum": 0,
                "display_total_count": 0,
                "observed_from": init.observed_from.isoformat(),
                "complete_through": record.observed_at.isoformat(),
                "backfilled_through": None,
                "coverage": init.coverage,
                "coverage_note": init.coverage_note,
                "updated_at": record.observed_at.isoformat(),
            }

        self._apply_token_usage_delta(blob, record, prior)
        self._store_token_usage_blob(session_id, blob)
        self.connection.commit()
        return SessionTokenUsage.model_validate(blob)

    @staticmethod
    def _apply_token_usage_delta(
        blob: dict[str, Any],
        record: TokenUsageRecord,
        prior: dict[str, Any] | None,
    ) -> None:
        """Fold one record into ``blob`` in place (net-zero if it re-adds ``prior``)."""
        prior_totals: dict[str, int] = (prior or {}).get("totals", {}) or {}
        totals: dict[str, int] = dict(blob.get("totals", {}))
        for cat in set(record.totals) | set(prior_totals):
            net = int(record.totals.get(cat, 0)) - int(prior_totals.get(cat, 0))
            if net:
                totals[cat] = totals.get(cat, 0) + net
        # Drop categories that have netted back to zero so the UI never shows an
        # empty category; missing keys stay absent per the vocabulary rules.
        blob["totals"] = {k: v for k, v in totals.items() if v}

        is_new = prior is None
        if is_new:
            blob["tracked_turns"] = int(blob.get("tracked_turns", 0)) + 1

        prior_display = None if is_new else (prior or {}).get("display_total_tokens")
        new_display = record.display_total_tokens
        blob["display_total_sum"] = (
            int(blob.get("display_total_sum", 0))
            + (int(new_display) if new_display is not None else 0)
            - (int(prior_display) if prior_display is not None else 0)
        )
        blob["display_total_count"] = (
            int(blob.get("display_total_count", 0))
            + (1 if new_display is not None else 0)
            - (1 if prior_display is not None else 0)
        )
        tracked = int(blob["tracked_turns"])
        # Derived, non-destructive: a lone missing display_total yields None
        # without discarding the accumulated sum, so a later correction restores it.
        blob["display_total_tokens"] = (
            blob["display_total_sum"]
            if tracked > 0 and blob["display_total_count"] == tracked
            else None
        )

        observed = record.observed_at.isoformat()
        blob["complete_through"] = max(blob["complete_through"], observed)
        # Never regress on an out-of-order correction for an older turn.
        blob["updated_at"] = max(blob["updated_at"], observed)

    def _load_token_usage_blob(self, session_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT session_token_usage FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None or not row["session_token_usage"]:
            return None
        try:
            blob = json.loads(row["session_token_usage"])
        except json.JSONDecodeError:
            return None
        return blob if isinstance(blob, dict) else None

    def _store_token_usage_blob(self, session_id: str, blob: dict[str, Any]) -> None:
        self.connection.execute(
            "UPDATE sessions SET session_token_usage = ? WHERE id = ?",
            (json.dumps(blob), session_id),
        )

    @_synchronized
    def token_usage_records_for_sessions(
        self, session_ids: Iterable[str]
    ) -> list[dict[str, Any]]:
        """Raw per-turn ledger rows for a set of sessions (telemetry token join).

        Holds the shared connection lock so telemetry shapers can run under
        ``asyncio.to_thread`` without the cross-thread ``sqlite3`` misuse a bare
        ``connection.execute`` from the worker thread would risk.
        """
        ids = list(session_ids)
        if not ids:
            return []
        placeholders = ", ".join("?" for _ in ids)
        rows = self.connection.execute(
            "SELECT session_id, source, record_id, usage_json "
            f"FROM session_token_usage_records WHERE session_id IN ({placeholders})",
            ids,
        ).fetchall()
        return [dict(row) for row in rows]

    @_synchronized
    def rebuild_aggregate_from_ledger(
        self, session_id: str
    ) -> SessionTokenUsage | None:
        """Recompute the aggregate from the full ledger, preserving coverage.

        Off the hot path. Reconciles a materialized aggregate that drifted from
        its ledger and is the seam for verified native-history backfill. Returns
        ``None`` when the session has no ledger rows and no prior aggregate.
        """
        existing = self._load_token_usage_blob(session_id)
        rows = self.connection.execute(
            """
            SELECT source, observed_at, usage_json FROM session_token_usage_records
            WHERE session_id = ? ORDER BY observed_at ASC
            """,
            (session_id,),
        ).fetchall()
        if not rows:
            return (
                SessionTokenUsage.model_validate(existing)
                if existing is not None
                else None
            )
        blob: dict[str, Any] | None = None
        for row in rows:
            try:
                usage = json.loads(row["usage_json"])
            except json.JSONDecodeError:
                continue
            observed = datetime.fromisoformat(row["observed_at"])
            record = TokenUsageRecord(
                record_id="",
                source=row["source"],
                observed_at=observed,
                totals=usage.get("totals", {}) or {},
                display_total_tokens=usage.get("display_total_tokens"),
                model=usage.get("model"),
                effort=usage.get("effort"),
            )
            if blob is None:
                blob = {
                    "source": row["source"],
                    "tracked_turns": 0,
                    "totals": {},
                    "display_total_tokens": None,
                    "display_total_sum": 0,
                    "display_total_count": 0,
                    "observed_from": (existing or {}).get(
                        "observed_from", observed.isoformat()
                    ),
                    "complete_through": observed.isoformat(),
                    "backfilled_through": (existing or {}).get("backfilled_through"),
                    "coverage": (existing or {}).get("coverage", "tracked_since"),
                    "coverage_note": (existing or {}).get("coverage_note"),
                    "updated_at": observed.isoformat(),
                }
            self._apply_token_usage_delta(blob, record, None)
        assert blob is not None
        self._store_token_usage_blob(session_id, blob)
        self.connection.commit()
        return SessionTokenUsage.model_validate(blob)

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
    def create_inbox_item_with_notifications(
        self,
        *,
        from_session_id: str,
        from_label: str | None,
        subject: str,
        blocks: list[InboxBlockInput],
        make_deliveries: Callable[[InboxItem], list[tuple[str, str, str]]],
    ) -> InboxItem:
        """Create an inbox item and its per-channel outbox rows atomically.

        ``make_deliveries`` receives the persisted item (with its assigned id)
        and returns ``(channel_id, dedupe_key, intent_json)`` rows, so the
        source record and its notifications commit in one transaction — an item
        is never durable with its notification silently unqueued.
        """
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
        self._insert_delivery_rows(make_deliveries(item), now)
        self.connection.commit()
        return item

    # ── Notification outbox ──

    def _insert_delivery_rows(
        self, rows: list[tuple[str, str, str]], now: datetime
    ) -> None:
        """Insert queued outbox rows without committing (caller owns the txn)."""
        now_iso = now.isoformat()
        for channel_id, dedupe_key, intent_json in rows:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO notification_deliveries (
                    id, channel_id, dedupe_key, intent_json, status, attempts,
                    next_attempt_at, lease_until, last_error, created_at,
                    sent_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', 0, ?, NULL, NULL, ?, NULL, ?)
                """,
                (
                    uuid.uuid4().hex,
                    channel_id,
                    dedupe_key,
                    intent_json,
                    now_iso,
                    now_iso,
                    now_iso,
                ),
            )

    @_synchronized
    def claim_due_deliveries(
        self, *, now: datetime, limit: int, lease_seconds: float
    ) -> list[dict[str, Any]]:
        now_iso = now.isoformat()
        lease_iso = (now + timedelta(seconds=lease_seconds)).isoformat()
        rows = self.connection.execute(
            """
            SELECT id, channel_id, dedupe_key, intent_json, status, attempts
            FROM notification_deliveries
            WHERE status = 'queued' AND next_attempt_at <= ?
            ORDER BY next_attempt_at ASC
            LIMIT ?
            """,
            (now_iso, limit),
        ).fetchall()
        claimed: list[dict[str, Any]] = []
        for row in rows:
            self.connection.execute(
                """
                UPDATE notification_deliveries
                SET status = 'sending', lease_until = ?, updated_at = ?
                WHERE id = ?
                """,
                (lease_iso, now_iso, row["id"]),
            )
            claimed.append(
                {
                    "id": row["id"],
                    "channel_id": row["channel_id"],
                    "dedupe_key": row["dedupe_key"],
                    "intent_json": row["intent_json"],
                    "status": "sending",
                    "attempts": row["attempts"],
                }
            )
        if claimed:
            self.connection.commit()
        return claimed

    @_synchronized
    def mark_delivery_sent(self, delivery_id: str, *, sent_at: datetime) -> None:
        now_iso = datetime.now(UTC).isoformat()
        self.connection.execute(
            """
            UPDATE notification_deliveries
            SET status = 'sent', sent_at = ?, lease_until = NULL, updated_at = ?
            WHERE id = ?
            """,
            (sent_at.isoformat(), now_iso, delivery_id),
        )
        self.connection.commit()

    @_synchronized
    def requeue_delivery(
        self,
        delivery_id: str,
        *,
        next_attempt_at: datetime,
        attempts: int,
        last_error: str | None,
    ) -> None:
        now_iso = datetime.now(UTC).isoformat()
        self.connection.execute(
            """
            UPDATE notification_deliveries
            SET status = 'queued', next_attempt_at = ?, attempts = ?,
                last_error = ?, lease_until = NULL, updated_at = ?
            WHERE id = ?
            """,
            (next_attempt_at.isoformat(), attempts, last_error, now_iso, delivery_id),
        )
        self.connection.commit()

    @_synchronized
    def fail_delivery(
        self, delivery_id: str, *, attempts: int, last_error: str | None
    ) -> None:
        now_iso = datetime.now(UTC).isoformat()
        self.connection.execute(
            """
            UPDATE notification_deliveries
            SET status = 'failed', attempts = ?, last_error = ?,
                lease_until = NULL, updated_at = ?
            WHERE id = ?
            """,
            (attempts, last_error, now_iso, delivery_id),
        )
        self.connection.commit()

    @_synchronized
    def mark_delivery_suppressed(self, delivery_id: str, reason: str) -> None:
        """Retire a claimed row as terminally ``suppressed`` without a send.

        Used by the worker's pre-send race guard: a row queued before its
        session became visibly open (or before a policy change) is recorded with
        a compact, content-free reason and never retried.
        """
        now_iso = datetime.now(UTC).isoformat()
        self.connection.execute(
            """
            UPDATE notification_deliveries
            SET status = 'suppressed', last_error = ?, lease_until = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (reason, now_iso, delivery_id),
        )
        self.connection.commit()

    @_synchronized
    def recover_stale_deliveries(self, now: datetime) -> int:
        """Return in-flight ``sending`` rows to the queue.

        Called at startup: a fresh process owns no in-flight sends, so every
        ``sending`` row is a crash remnant and is requeued (at-least-once).
        """
        now_iso = now.isoformat()
        cursor = self.connection.execute(
            """
            UPDATE notification_deliveries
            SET status = 'queued', lease_until = NULL, updated_at = ?
            WHERE status = 'sending'
            """,
            (now_iso,),
        )
        self.connection.commit()
        return cursor.rowcount

    @_synchronized
    def count_deliveries_by_status(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS n FROM notification_deliveries GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    @_synchronized
    def delete_old_deliveries(self, cutoff: datetime) -> int:
        cursor = self.connection.execute(
            """
            DELETE FROM notification_deliveries
            WHERE status IN ('sent', 'failed', 'suppressed') AND created_at < ?
            """,
            (cutoff.isoformat(),),
        )
        self.connection.commit()
        return cursor.rowcount

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
        with debug_timer(log, "Storage.append_event", session=event.session_id):
            persisted = self._insert_event(event)
            self.connection.commit()
            return persisted

    @_synchronized
    def append_event_with_notifications(
        self, event: EventRecord, deliveries: list[tuple[str, str, str]]
    ) -> EventRecord:
        """Persist an event and its per-channel outbox rows in one transaction.

        A ``UNIQUE(channel_id, dedupe_key)`` conflict is ignored, so a replayed
        adapter event never queues a duplicate notification.
        """
        with debug_timer(
            log, "Storage.append_event_with_notifications", session=event.session_id
        ):
            persisted = self._insert_event(event)
            self._insert_delivery_rows(deliveries, event.ts)
            self.connection.commit()
            return persisted

    def _insert_event(self, event: EventRecord) -> EventRecord:
        # Stamp every persisted event with the canonical envelope version
        # so older transcripts replay safely under newer readers (the
        # frontend's `parseEvent` looks at `metadata.version` to decide
        # which schema to apply). Does not commit; the caller owns the txn.
        if "version" not in event.metadata:
            event = event.model_copy(
                update={"metadata": {**event.metadata, "version": 1}}
            )
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
        last_id = cursor.lastrowid
        if last_id is None:
            raise RuntimeError("sqlite did not assign a row id for the inserted event")
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
                account_profile_id, account_profile_label,
                cron, timezone, last_run_at, last_run_status, last_failure_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                schedule.cron,
                schedule.timezone,
                schedule.last_run_at.isoformat() if schedule.last_run_at else None,
                schedule.last_run_status,
                schedule.last_failure_reason,
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
    def claim_recurring_schedule(
        self,
        schedule_id: str,
        expected_scheduled_at: datetime,
        next_scheduled_at: datetime,
    ) -> ScheduledSessionRecord | None:
        """Atomically advance a due recurring session schedule to its next run.

        Conditional on the row still being ``pending`` at the expected due time,
        so a second poll or a restarted scheduler cannot claim the same
        occurrence twice. Returns the advanced record, or ``None`` when another
        pass already claimed or cancelled it.
        """
        cursor = self.connection.execute(
            "UPDATE scheduled_sessions SET scheduled_at = ? "
            "WHERE id = ? AND status = 'pending' AND scheduled_at = ?",
            (
                next_scheduled_at.isoformat(),
                schedule_id,
                expected_scheduled_at.isoformat(),
            ),
        )
        self.connection.commit()
        if (cursor.rowcount or 0) == 0:
            return None
        return self.get_schedule(schedule_id)

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
    def create_wake_subscription(self, sub: WakeSubscription) -> WakeSubscription:
        self.connection.execute(
            """
            INSERT INTO wake_subscriptions (
                id, session_id, channel_globs, kinds, wake_on_inbox, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                sub.id,
                sub.session_id,
                json.dumps(list(sub.channel_globs)),
                json.dumps(list(sub.kinds)),
                1 if sub.wake_on_inbox else 0,
                sub.created_at.isoformat(),
            ),
        )
        self.connection.commit()
        return sub

    @_synchronized
    def list_wake_subscriptions(self) -> list[WakeSubscription]:
        rows = self.connection.execute(
            "SELECT * FROM wake_subscriptions ORDER BY created_at ASC"
        ).fetchall()
        return [self._wake_subscription_from_row(row) for row in rows]

    @_synchronized
    def list_wake_subscriptions_for_session(
        self, session_id: str
    ) -> list[WakeSubscription]:
        rows = self.connection.execute(
            "SELECT * FROM wake_subscriptions WHERE session_id = ? "
            "ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return [self._wake_subscription_from_row(row) for row in rows]

    @_synchronized
    def delete_wake_subscription(self, sub_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM wake_subscriptions WHERE id = ?", (sub_id,)
        )
        self.connection.commit()
        return (cursor.rowcount or 0) > 0

    @_synchronized
    def delete_wake_subscriptions_for_session(self, session_id: str) -> int:
        cursor = self.connection.execute(
            "DELETE FROM wake_subscriptions WHERE session_id = ?", (session_id,)
        )
        self.connection.commit()
        return cursor.rowcount or 0

    def _wake_subscription_from_row(self, row: sqlite3.Row) -> WakeSubscription:
        payload = dict(row)
        payload["created_at"] = datetime.fromisoformat(payload["created_at"])
        payload["wake_on_inbox"] = bool(payload.get("wake_on_inbox", 0))
        for field_name in ("channel_globs", "kinds"):
            raw = payload.get(field_name) or "[]"
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = []
            payload[field_name] = parsed if isinstance(parsed, list) else []
        return WakeSubscription.model_validate(payload)

    @_synchronized
    def create_manager_ticket(self, ticket: ManagerTicket) -> ManagerTicket:
        self.connection.execute(
            """
            INSERT INTO manager_tickets (
                id, manager_id, title, priority, state, scale, version,
                created_at, updated_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._manager_ticket_columns(ticket),
        )
        self.connection.commit()
        return ticket

    @_synchronized
    def get_manager_ticket(
        self, ticket_id: str, *, manager_id: str | None = None
    ) -> ManagerTicket | None:
        sql = "SELECT * FROM manager_tickets WHERE id = ?"
        params: list[Any] = [ticket_id]
        if manager_id is not None:
            sql += " AND manager_id = ?"
            params.append(manager_id)
        row = self.connection.execute(sql, params).fetchone()
        return self._manager_ticket_from_row(row) if row is not None else None

    @_synchronized
    def list_manager_tickets(
        self,
        *,
        manager_id: str | None = None,
        states: list[ManagerTicketState] | None = None,
    ) -> list[ManagerTicket]:
        sql = "SELECT * FROM manager_tickets"
        params: list[Any] = []
        conditions: list[str] = []
        if manager_id is not None:
            conditions.append("manager_id = ?")
            params.append(manager_id)
        if states:
            placeholders = ",".join("?" for _ in states)
            conditions.append(f"state IN ({placeholders})")
            params.extend(str(state) for state in states)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at ASC, id ASC"
        rows = self.connection.execute(sql, params).fetchall()
        return [self._manager_ticket_from_row(row) for row in rows]

    @_synchronized
    def update_manager_ticket(self, ticket: ManagerTicket) -> ManagerTicket:
        # Version-checked read-modify-write: the caller passes the ticket it read
        # (``ticket.version`` is the expected current version); the row is bumped
        # to ``version + 1`` only when it still matches. A mismatch (concurrent
        # write or a vanished row) raises ManagerTicketConflict, mapped to 409.
        expected = ticket.version
        bumped = ticket.model_copy(update={"version": expected + 1})
        columns = self._manager_ticket_columns(bumped)
        # Scope the write by manager_id as well as the version CAS: callers already
        # fetch scoped, so this is defense-in-depth against a cross-manager write.
        cursor = self.connection.execute(
            """
            UPDATE manager_tickets SET
                manager_id = ?, title = ?, priority = ?, state = ?, scale = ?,
                version = ?, created_at = ?, updated_at = ?, payload = ?
            WHERE id = ? AND version = ? AND manager_id = ?
            """,
            (*columns[1:], ticket.id, expected, ticket.manager_id),
        )
        if (cursor.rowcount or 0) == 0:
            self.connection.rollback()
            raise ManagerTicketConflict(ticket.id)
        self.connection.commit()
        return bumped

    @_synchronized
    def delete_manager_ticket(
        self, ticket_id: str, *, manager_id: str | None = None
    ) -> bool:
        sql = "DELETE FROM manager_tickets WHERE id = ?"
        params: list[Any] = [ticket_id]
        if manager_id is not None:
            sql += " AND manager_id = ?"
            params.append(manager_id)
        cursor = self.connection.execute(sql, params)
        self.connection.commit()
        return (cursor.rowcount or 0) > 0

    @_synchronized
    def clear_manager_tickets(self, manager_id: str | None = None) -> int:
        sql = "DELETE FROM manager_tickets"
        params: list[Any] = []
        if manager_id is not None:
            sql += " WHERE manager_id = ?"
            params.append(manager_id)
        cursor = self.connection.execute(sql, params)
        self.connection.commit()
        return cursor.rowcount or 0

    @_synchronized
    def clear_manager_config(self, manager_id: str) -> None:
        self.connection.execute(
            "DELETE FROM manager_config WHERE id = ?", (manager_id,)
        )
        self.connection.commit()

    @_synchronized
    def get_manager_config(self, manager_id: str) -> ManagerConfig | None:
        row = self.connection.execute(
            "SELECT payload FROM manager_config WHERE id = ?", (manager_id,)
        ).fetchone()
        return self._manager_config_from_row(row)

    @_synchronized
    def get_manager_config_by_repo(self, repo_dir: str) -> ManagerConfig | None:
        if not repo_dir:
            return None
        row = self.connection.execute(
            "SELECT payload FROM manager_config WHERE repo_dir = ?", (repo_dir,)
        ).fetchone()
        return self._manager_config_from_row(row)

    @_synchronized
    def get_manager_config_by_owner(self, session_id: str) -> list[ManagerConfig]:
        rows = self.connection.execute(
            "SELECT payload FROM manager_config WHERE owner_session_id = ?",
            (session_id,),
        ).fetchall()
        configs = [self._manager_config_from_row(row) for row in rows]
        return [c for c in configs if c is not None]

    @_synchronized
    def list_manager_configs(self) -> list[ManagerConfig]:
        rows = self.connection.execute(
            "SELECT payload FROM manager_config ORDER BY created_at ASC, id ASC"
        ).fetchall()
        configs = [self._manager_config_from_row(row) for row in rows]
        return [c for c in configs if c is not None]

    @_synchronized
    def set_manager_config(self, config: ManagerConfig) -> ManagerConfig:
        self.connection.execute(
            """
            INSERT INTO manager_config
                (id, project, repo_dir, owner_session_id, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                project = excluded.project,
                repo_dir = excluded.repo_dir,
                owner_session_id = excluded.owner_session_id,
                payload = excluded.payload
            """,
            (
                config.id,
                config.project,
                config.repo_dir or None,
                config.owner_session_id,
                json.dumps(config.model_dump(mode="json")),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.connection.commit()
        return config

    @staticmethod
    def _manager_config_from_row(row: sqlite3.Row | None) -> ManagerConfig | None:
        if row is None:
            return None
        try:
            parsed = json.loads(row["payload"])
        except json.JSONDecodeError:
            return None
        return ManagerConfig.model_validate(parsed if isinstance(parsed, dict) else {})

    @staticmethod
    def _manager_ticket_columns(ticket: ManagerTicket) -> tuple[Any, ...]:
        return (
            ticket.id,
            ticket.manager_id,
            ticket.title,
            ticket.priority,
            str(ticket.state),
            str(ticket.scale) if ticket.scale is not None else None,
            ticket.version,
            ticket.created_at.isoformat(),
            ticket.updated_at.isoformat(),
            json.dumps(ticket.model_dump(mode="json")),
        )

    def _manager_ticket_from_row(self, row: sqlite3.Row) -> ManagerTicket:
        raw = row["payload"] or "{}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        # The column is authoritative for manager_id (a migrated legacy ticket's
        # payload predates the field; the backfilled column carries the id).
        if row["manager_id"]:
            parsed["manager_id"] = row["manager_id"]
        return ManagerTicket.model_validate(parsed)

    @_synchronized
    def create_scheduled_message(
        self, record: ScheduledMessageRecord
    ) -> ScheduledMessageRecord:
        self.connection.execute(
            """
            INSERT INTO scheduled_messages (
                id, session_id, text, submit, command, items, attachments,
                scheduled_at, created_at, status, failure_reason,
                cron, timezone, last_run_at, last_run_status, last_failure_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                record.cron,
                record.timezone,
                record.last_run_at.isoformat() if record.last_run_at else None,
                record.last_run_status,
                record.last_failure_reason,
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
    def claim_recurring_message(
        self,
        message_id: str,
        expected_scheduled_at: datetime,
        next_scheduled_at: datetime,
    ) -> ScheduledMessageRecord | None:
        """Atomically advance a due recurring message schedule to its next run.

        See :meth:`claim_recurring_schedule`.
        """
        cursor = self.connection.execute(
            "UPDATE scheduled_messages SET scheduled_at = ? "
            "WHERE id = ? AND status = 'pending' AND scheduled_at = ?",
            (
                next_scheduled_at.isoformat(),
                message_id,
                expected_scheduled_at.isoformat(),
            ),
        )
        self.connection.commit()
        if (cursor.rowcount or 0) == 0:
            return None
        return self.get_scheduled_message(message_id)

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
        raw_verified_probed_at = payload.get("verified_account_probed_at")
        payload["verified_account_probed_at"] = (
            datetime.fromisoformat(raw_verified_probed_at)
            if raw_verified_probed_at
            else None
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
        raw_token_usage = payload.get("session_token_usage")
        if raw_token_usage:
            try:
                parsed_token_usage = json.loads(raw_token_usage)
            except json.JSONDecodeError:
                parsed_token_usage = None
            # The stored blob carries private ``display_total_*`` bookkeeping
            # keys the ledger delta path maintains; model_validate drops them
            # (extra="ignore") and reads the materialized ``display_total_tokens``.
            if isinstance(parsed_token_usage, dict):
                payload["session_token_usage"] = parsed_token_usage
            else:
                payload["session_token_usage"] = None
        else:
            payload["session_token_usage"] = None
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
        if payload.get("last_run_at"):
            payload["last_run_at"] = datetime.fromisoformat(payload["last_run_at"])
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
        if payload.get("last_run_at"):
            payload["last_run_at"] = datetime.fromisoformat(payload["last_run_at"])
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
        if isinstance(value, (dict, list)):
            # JSON TEXT columns (launch_env/tags dicts, args/config_overrides
            # lists) round-trip through json; sqlite can't bind them directly.
            return json.dumps(value)
        return value

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        rows = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row["name"] == column for row in rows):
            return
        self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _migrate_manager_config_to_multi(self) -> None:
        """Rebuild the legacy single-row ``manager_config`` (``id = 1``) into the
        per-manager shape and adopt its one manager under a minted id.

        No-op on a fresh DB (the ``CREATE TABLE`` already made the new shape) and
        on an already-migrated DB (the ``repo_dir`` column marks it done). The
        legacy row never persisted ``repo_dir`` directly, so it is derived from the
        compiled ``templates_dir`` (default ``<repo>/.waypoint/manager/templates``);
        when that fails the migrated manager keeps a NULL ``repo_dir`` and the next
        ``manager init`` adopts it as the lone unbound manager.
        """
        if self._has_column("manager_config", "repo_dir"):
            return
        old = self.connection.execute(
            "SELECT payload FROM manager_config WHERE id = 1"
        ).fetchone()
        try:
            # A SAVEPOINT nests inside _init_db's in-progress transaction, so the
            # whole rebuild is atomic: a mid-rebuild failure rolls back to here
            # rather than leaving a half-migrated schema. The final commit is
            # _init_db's.
            self.connection.execute("SAVEPOINT manager_migration")
            self.connection.execute(
                "ALTER TABLE manager_config RENAME TO manager_config_legacy"
            )
            self.connection.execute("""
                CREATE TABLE manager_config (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL DEFAULT '',
                    repo_dir TEXT UNIQUE,
                    owner_session_id TEXT,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT ''
                )
                """)
            if old is not None:
                try:
                    payload = json.loads(old["payload"] or "{}")
                except json.JSONDecodeError:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                new_id = f"mgr-{secrets.token_hex(4)}"
                payload["id"] = new_id
                repo_dir = _repo_dir_from_templates_dir(payload)
                payload["repo_dir"] = repo_dir or ""
                self.connection.execute(
                    """
                    INSERT INTO manager_config
                        (id, project, repo_dir, owner_session_id, payload, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id,
                        str(payload.get("project") or ""),
                        repo_dir or None,
                        payload.get("owner_session_id"),
                        json.dumps(payload),
                        datetime.now(UTC).isoformat(),
                    ),
                )
                self.connection.execute(
                    "UPDATE manager_tickets SET manager_id = ? "
                    "WHERE manager_id IS NULL OR manager_id = ''",
                    (new_id,),
                )
            self.connection.execute("DROP TABLE manager_config_legacy")
            self.connection.execute("RELEASE manager_migration")
        except Exception:
            self.connection.execute("ROLLBACK TO manager_migration")
            self.connection.execute("RELEASE manager_migration")
            raise
