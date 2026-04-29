import functools
import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from waypoint.schemas import (
    Backend,
    EventRecord,
    ScheduledSessionRecord,
    ScheduleStatus,
    SessionRecord,
    SessionStatus,
    SessionTransport,
)


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
                remote_cwd TEXT,
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
                remote_cwd TEXT,
                launch_target_id TEXT,
                title TEXT,
                args TEXT NOT NULL DEFAULT '[]',
                initial_prompt TEXT,
                scheduled_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                session_id TEXT,
                failure_reason TEXT
            );
            """)
        self._ensure_column("sessions", "transport", "TEXT NOT NULL DEFAULT 'tmux'")
        self._ensure_column("sessions", "thread_id", "TEXT")
        self._ensure_column("sessions", "remote_cwd", "TEXT")
        self._ensure_column("sessions", "launch_target_id", "TEXT")
        self._ensure_column("sessions", "pinned_at", "TEXT")
        self._ensure_column("sessions", "permission_mode", "TEXT")
        self.connection.commit()

    @_synchronized
    def close(self) -> None:
        self.connection.close()

    @_synchronized
    def create_session(self, session: SessionRecord) -> SessionRecord:
        self.connection.execute(
            """
            INSERT INTO sessions (
                id, backend, source, transport, title, cwd, remote_cwd, launch_target_id, repo_name, branch, status,
                created_at, updated_at, last_event_at, tmux_session, tmux_window,
                tmux_pane, thread_id, raw_log_path, structured_log_path, pid
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                session.backend,
                session.source,
                session.transport,
                session.title,
                session.cwd,
                session.remote_cwd,
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
        self, session_id: str, cursor: int | None = None
    ) -> list[EventRecord]:
        query = "SELECT * FROM events WHERE session_id = ?"
        params: list[Any] = [session_id]
        if cursor is not None:
            query += " AND id > ?"
            params.append(cursor)
        query += " ORDER BY sequence ASC, id ASC"
        rows = self.connection.execute(query, params).fetchall()
        return [self._event_from_row(row) for row in rows]

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
                id, backend, cwd, remote_cwd, launch_target_id, title, args,
                initial_prompt, scheduled_at, created_at, status, session_id,
                failure_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                schedule.id,
                schedule.backend,
                schedule.cwd,
                schedule.remote_cwd,
                schedule.launch_target_id,
                schedule.title,
                json.dumps(list(schedule.args)),
                schedule.initial_prompt,
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
        payload["transport"] = SessionTransport(
            payload.get("transport", SessionTransport.TMUX)
        )
        return SessionRecord.model_validate(payload)

    def _schedule_from_row(self, row: sqlite3.Row) -> ScheduledSessionRecord:
        payload = dict(row)
        for field_name in ("scheduled_at", "created_at"):
            payload[field_name] = datetime.fromisoformat(payload[field_name])
        payload["status"] = ScheduleStatus(payload.get("status", "pending"))
        payload["backend"] = Backend(payload["backend"])
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
        return value

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        rows = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row["name"] == column for row in rows):
            return
        self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
