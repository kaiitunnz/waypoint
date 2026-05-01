from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.claude_cli import CLAUDE_PERMISSION_MODES
from waypoint.schemas import (
    Backend,
    ScheduleCreateRequest,
    ScheduledSessionRecord,
    ScheduleStatus,
    SessionCreateRequest,
    SessionEnvelope,
    SessionInputRequest,
    SessionSource,
)
from waypoint.transports.codex import CODEX_PERMISSION_PRESETS

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.scheduler")

POLL_INTERVAL_SECONDS = 5.0


def validate_permission_mode_for_backend(
    backend: str, mode: str | None
) -> str | None:
    """Resolve a user-supplied starting mode for a given backend.

    Returns the canonical mode string when accepted, ``None`` when the caller
    didn't pick one (so the runtime can pick its own default), and raises a
    400 HTTPException for unknown modes.
    """
    if mode is None or mode == "":
        return None
    allowed: tuple[str, ...]
    if backend == Backend.CLAUDE_CODE:
        allowed = CLAUDE_PERMISSION_MODES
    elif backend == Backend.CODEX:
        allowed = tuple(CODEX_PERMISSION_PRESETS)
    else:
        return None
    if mode not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"unsupported {backend} permission mode: {mode}; "
                f"expected one of {', '.join(allowed)}"
            ),
        )
    return mode


class Scheduler:
    """Polls scheduled session entries and launches them when they come due."""

    def __init__(self, runtime: SessionRuntime) -> None:
        self._runtime = runtime
        self._task: asyncio.Task[None] | None = None
        self._wakeup = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def list_schedules(self) -> list[ScheduledSessionRecord]:
        return self._runtime.storage.list_schedules()

    def create_schedule(self, request: ScheduleCreateRequest) -> ScheduledSessionRecord:
        scheduled_at = self._resolve_scheduled_at(request)
        permission_mode = self._resolve_permission_mode(request)
        # Validate launch target up-front so the user gets immediate feedback.
        launch_target = None
        if request.launch_target_id:
            launch_target = self._runtime._resolve_launch_target(
                request.launch_target_id, request.backend
            )
        cwd = request.cwd
        if launch_target is not None and not cwd:
            cwd = launch_target.default_cwd
        now = datetime.now(UTC)
        record = ScheduledSessionRecord(
            id=self._generate_id(),
            backend=request.backend,
            cwd=cwd,
            launch_target_id=request.launch_target_id,
            title=request.title,
            args=list(request.args),
            initial_prompt=request.initial_prompt,
            permission_mode=permission_mode,
            model=request.model or None,
            effort=request.effort or None,
            scheduled_at=scheduled_at,
            created_at=now,
            status=ScheduleStatus.PENDING,
        )
        self._runtime.storage.create_schedule(record)
        self._wakeup.set()
        asyncio.create_task(self._publish_update())
        return record

    def cancel_schedule(self, schedule_id: str) -> ScheduledSessionRecord:
        existing = self._runtime.storage.get_schedule(schedule_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found"
            )
        if existing.status == ScheduleStatus.PENDING:
            updated = self._runtime.storage.update_schedule(
                schedule_id, status=ScheduleStatus.CANCELLED
            )
            asyncio.create_task(self._publish_update())
            return updated
        # Already terminal — drop the row so the user can clear it from the
        # list. Returning the pre-delete record keeps the API symmetric.
        self._runtime.storage.delete_schedule(schedule_id)
        asyncio.create_task(self._publish_update())
        return existing

    def clear_history(self) -> int:
        removed = self._runtime.storage.delete_schedules_by_status(
            [
                ScheduleStatus.LAUNCHED,
                ScheduleStatus.CANCELLED,
                ScheduleStatus.FAILED,
            ]
        )
        if removed:
            asyncio.create_task(self._publish_update())
        return removed

    async def _loop(self) -> None:
        try:
            while True:
                await self._fire_due_schedules()
                next_wait = self._compute_wait_seconds()
                self._wakeup.clear()
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=next_wait)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("scheduler loop crashed")

    def _compute_wait_seconds(self) -> float:
        pending = self._runtime.storage.list_schedules([ScheduleStatus.PENDING])
        if not pending:
            return 60.0
        soonest = min(item.scheduled_at for item in pending)
        delta = (soonest - datetime.now(UTC)).total_seconds()
        if delta <= 0:
            return 0.5
        return min(delta, POLL_INTERVAL_SECONDS)

    async def _fire_due_schedules(self) -> None:
        now = datetime.now(UTC)
        pending = self._runtime.storage.list_schedules([ScheduleStatus.PENDING])
        for schedule in pending:
            if schedule.scheduled_at > now:
                continue
            await self._fire(schedule)

    async def _fire(self, schedule: ScheduledSessionRecord) -> None:
        try:
            session = await self._runtime.create_session(
                SessionCreateRequest(
                    backend=schedule.backend,
                    cwd=schedule.cwd,
                    launch_target_id=schedule.launch_target_id,
                    title=schedule.title,
                    args=schedule.args,
                    source_mode=SessionSource.MANAGED,
                    permission_mode=schedule.permission_mode,
                    model=schedule.model,
                    effort=schedule.effort,
                )
            )
            self._runtime.storage.update_schedule(
                schedule.id,
                status=ScheduleStatus.LAUNCHED,
                session_id=session.id,
            )
            if schedule.initial_prompt:
                # Brief grace window before the first input; some backends are
                # not yet idle the same tick they finish boot.
                await asyncio.sleep(0.1)
                await self._runtime.handle_input(
                    session.id,
                    SessionInputRequest(text=schedule.initial_prompt, submit=True),
                )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "scheduled session launch failed",
                extra={"schedule_id": schedule.id},
            )
            self._runtime.storage.update_schedule(
                schedule.id,
                status=ScheduleStatus.FAILED,
                failure_reason=str(exc),
            )
        await self._publish_update()

    async def _publish_update(self) -> None:
        await self._runtime.broadcast.publish(
            SessionEnvelope(
                type="schedule_list_update",
                payload={
                    "schedules": [
                        item.model_dump(mode="json") for item in self.list_schedules()
                    ]
                },
            )
        )

    @staticmethod
    def _resolve_permission_mode(request: ScheduleCreateRequest) -> str | None:
        return validate_permission_mode_for_backend(
            request.backend, request.permission_mode
        )

    @staticmethod
    def _resolve_scheduled_at(request: ScheduleCreateRequest) -> datetime:
        if request.scheduled_at is not None:
            scheduled_at = request.scheduled_at
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=UTC)
            return scheduled_at.astimezone(UTC)
        if request.delay_seconds is not None:
            if request.delay_seconds < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="delay_seconds must be non-negative",
                )
            return datetime.now(UTC) + timedelta(seconds=request.delay_seconds)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="schedule must provide scheduled_at or delay_seconds",
        )

    @staticmethod
    def _generate_id() -> str:
        return f"sched-{secrets.token_hex(4)}"
