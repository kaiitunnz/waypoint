import functools
import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from waypoint.schemas import (
    EventKind,
    EventRecord,
    ScheduledSessionRecord,
    ScheduleStatus,
    SessionRecord,
    SessionStatus,
)

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


_LEGACY_STATE_KEYS: tuple[str, ...] = (
    "thread_id",
    "tmux_session",
    "tmux_window",
    "tmux_pane",
    "pid",
)


def _legacy_state_from_row(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a `transport_state` dict from the legacy per-transport columns.

    Used when reading rows written before the JSON column existed; lets the
    plugin layer treat `session.transport_state` as the canonical source
    while the legacy columns are still being populated by older writers.
    """
    state: dict[str, Any] = {}
    for key in _LEGACY_STATE_KEYS:
        value = payload.get(key)
        if value is not None and value != "":
            state[key] = value
    return state


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
                repo_name TEXT,
                branch TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_event_at TEXT NOT NULL,
                tmux_session TEXT,
                tmux_window TEXT,
                tmux_pane TEXT,
                thread_id TEXT,
                raw_log_path TEXT NOT NULL,
                structured_log_path TEXT NOT NULL,
                pid INTEGER
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
                title TEXT,
                args TEXT NOT NULL DEFAULT '[]',
                initial_prompt TEXT,
                permission_mode TEXT,
                scheduled_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                session_id TEXT,
                failure_reason TEXT
            );
            """)
        self._ensure_column("sessions", "transport", "TEXT NOT NULL DEFAULT 'tmux'")
        self._ensure_column("sessions", "thread_id", "TEXT")
        self._ensure_column("sessions", "launch_target_id", "TEXT")
        self._ensure_column("sessions", "pinned_at", "TEXT")
        self._ensure_column("sessions", "permission_mode", "TEXT")
        self._ensure_column("sessions", "model", "TEXT")
        self._ensure_column("sessions", "effort", "TEXT")
        # Per-plugin opaque session state. Each plugin decides what to put
        # here (Codex thread id, Claude session uuid, tmux pane targets, ...);
        # `_session_from_row` reconstructs it from the legacy columns when
        # the JSON blob is empty so older rows keep round-tripping.
        self._ensure_column("sessions", "transport_state", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("scheduled_sessions", "permission_mode", "TEXT")
        self._ensure_column("scheduled_sessions", "model", "TEXT")
        self._ensure_column("scheduled_sessions", "effort", "TEXT")
        self.connection.commit()

    @_synchronized
    def close(self) -> None:
        self.connection.close()

    @_synchronized
    def create_session(self, session: SessionRecord) -> SessionRecord:
        self.connection.execute(
            """
            INSERT INTO sessions (
                id, backend, source, transport, title, cwd, launch_target_id, repo_name, branch, status,
                created_at, updated_at, last_event_at, tmux_session, tmux_window,
                tmux_pane, thread_id, raw_log_path, structured_log_path, pid,
                pinned_at, permission_mode, model, effort, transport_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                session.backend,
                session.source,
                session.transport,
                session.title,
                session.cwd,
                session.launch_target_id,
                session.repo_name,
                session.branch,
                session.status,
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
                session.last_event_at.isoformat(),
                session.tmux_session,
                session.tmux_window,
                session.tmux_pane,
                session.thread_id,
                session.raw_log_path,
                session.structured_log_path,
                session.pid,
                session.pinned_at.isoformat() if session.pinned_at else None,
                session.permission_mode,
                session.model,
                session.effort,
                json.dumps(session.transport_state),
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
        current = self.get_session(session_id)
        if current is None:
            raise KeyError(session_id)
        fields.setdefault("updated_at", datetime.now(UTC))
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [self._serialize_field(value) for value in fields.values()]
        values.append(session_id)
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
    def append_event(self, event: EventRecord) -> EventRecord:
        # Stamp every persisted event with the canonical envelope version
        # so older transcripts replay safely under newer readers (the
        # frontend's `parseEvent` looks at `metadata.version` to decide
        # which schema to apply).
        if "version" not in event.metadata:
            event = event.model_copy(
                update={"metadata": {**event.metadata, "version": 1}}
            )
        cursor = self.connection.execute(
            """
            INSERT INTO events (session_id, ts, kind, text, metadata, sequence)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.session_id,
                event.ts.isoformat(),
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
                event.ts.isoformat(),
                event.ts.isoformat(),
                event.metadata.get("status"),
                event.session_id,
            ),
        )
        self.connection.commit()
        last_id = cursor.lastrowid
        if last_id is None:
            raise RuntimeError("sqlite did not assign a row id for the inserted event")
        return event.model_copy(update={"id": int(last_id)})

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
                id, backend, cwd, launch_target_id, title, args,
                initial_prompt, permission_mode, model, effort, scheduled_at, created_at, status,
                session_id, failure_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                schedule.id,
                schedule.backend,
                schedule.cwd,
                schedule.launch_target_id,
                schedule.title,
                json.dumps(list(schedule.args)),
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
        # Legacy rows written before the transport column existed default to
        # the tmux fallback — that's what every pre-Step-6 attached session
        # was running.
        payload["transport"] = payload.get("transport") or "tmux"
        # Prefer the JSON blob; fall back to legacy columns for rows
        # written before the column was introduced. Plugin code reads
        # `transport_state` exclusively after Step 6.
        raw_state = payload.pop("transport_state", None)
        parsed_state: dict[str, Any] = {}
        if raw_state:
            try:
                decoded = json.loads(raw_state)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                parsed_state = decoded
        if not parsed_state:
            parsed_state = _legacy_state_from_row(payload)
        payload["transport_state"] = parsed_state
        return SessionRecord.model_validate(payload)

    def _schedule_from_row(self, row: sqlite3.Row) -> ScheduledSessionRecord:
        payload = dict(row)
        for field_name in ("scheduled_at", "created_at"):
            payload[field_name] = datetime.fromisoformat(payload[field_name])
        payload["status"] = ScheduleStatus(payload.get("status", "pending"))
        raw_args = payload.get("args") or "[]"
        try:
            parsed_args = json.loads(raw_args)
        except json.JSONDecodeError:
            parsed_args = []
        payload["args"] = parsed_args if isinstance(parsed_args, list) else []
        return ScheduledSessionRecord.model_validate(payload)

    def _event_from_row(self, row: sqlite3.Row) -> EventRecord:
        payload = dict(row)
        payload["ts"] = datetime.fromisoformat(payload["ts"])
        payload["metadata"] = json.loads(payload["metadata"])
        return EventRecord.model_validate(payload)

    def _serialize_field(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return json.dumps(value)
        return value

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        rows = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row["name"] == column for row in rows):
            return
        self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
