import asyncio
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.backends.registry import get_registry
from waypoint.schemas import (
    ScheduleCreateRequest,
    ScheduledMessageCreateRequest,
    ScheduledMessageRecord,
    ScheduledMessageStatus,
    ScheduledSessionRecord,
    ScheduleStatus,
    SessionCreateRequest,
    SessionEnvelope,
    SessionInputRequest,
    SessionSource,
)

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.scheduler")

POLL_INTERVAL_SECONDS = 5.0


def validate_permission_mode_for_backend(backend: str, mode: str | None) -> str | None:
    """Resolve a user-supplied starting mode for a given backend.

    Returns the canonical mode string when accepted, ``None`` when the caller
    didn't pick one (so the runtime can pick its own default), and raises a
    400 HTTPException for unknown modes.
    """
    if mode is None or mode == "":
        return None
    registry = get_registry()
    if not registry.has_backend(backend):
        return None
    plugin = registry.get(backend)
    allowed = tuple(spec.id for spec in plugin.capabilities.permission_modes)
    if not allowed:
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

    def __init__(self, runtime: "SessionRuntime") -> None:
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
        # Validate launch target and transport up-front so the user gets
        # immediate feedback instead of a failure when the schedule fires.
        launch_target = None
        if request.launch_target_id:
            launch_target = self._runtime._resolve_launch_target(
                request.launch_target_id, request.backend
            )
        if request.transport is not None:
            self._runtime._validate_supported_transport(
                request.backend, request.transport
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
            launch_mode=request.launch_mode,
            transport=request.transport,
            title=request.title,
            args=list(request.args),
            config_overrides=list(request.config_overrides),
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

    def list_message_schedules(
        self, session_id: str | None = None
    ) -> list[ScheduledMessageRecord]:
        return self._runtime.storage.list_scheduled_messages(session_id=session_id)

    def create_message_schedule(
        self, session_id: str, request: ScheduledMessageCreateRequest
    ) -> ScheduledMessageRecord:
        session = self._runtime.storage.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"session {session_id} not found",
            )
        if (
            request.text == ""
            and request.command is None
            and not request.items
            and not request.attachments
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="message schedule must have text, command, items, or attachments",
            )
        scheduled_at = self._resolve_scheduled_at(request)
        now = datetime.now(UTC)
        if request.scheduled_at is not None and scheduled_at <= now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scheduled_at must be in the future",
            )
        record = ScheduledMessageRecord(
            id=self._generate_id(),
            session_id=session_id,
            text=request.text,
            submit=request.submit,
            command=request.command,
            items=request.items,
            attachments=list(request.attachments),
            scheduled_at=scheduled_at,
            created_at=now,
            status=ScheduledMessageStatus.PENDING,
        )
        self._runtime.storage.create_scheduled_message(record)
        self._wakeup.set()
        asyncio.create_task(self._publish_update())
        return record

    def cancel_message_schedule(self, message_id: str) -> ScheduledMessageRecord:
        existing = self._runtime.storage.get_scheduled_message(message_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="message schedule not found",
            )
        if existing.status == ScheduledMessageStatus.PENDING:
            updated = self._runtime.storage.update_scheduled_message(
                message_id, status=ScheduledMessageStatus.CANCELLED
            )
            asyncio.create_task(self._publish_update())
            return updated
        self._runtime.storage.delete_scheduled_message(message_id)
        asyncio.create_task(self._publish_update())
        return existing

    async def purge_session_messages(self, session_id: str) -> int:
        removed = self._runtime.storage.delete_scheduled_messages_by_session(session_id)
        if removed:
            await self._publish_update()
        return removed

    def clear_message_history(self, session_id: str | None = None) -> int:
        removed = self._runtime.storage.delete_scheduled_messages_by_status(
            [
                ScheduledMessageStatus.SENT,
                ScheduledMessageStatus.CANCELLED,
                ScheduledMessageStatus.FAILED,
            ],
            session_id=session_id,
        )
        if removed:
            asyncio.create_task(self._publish_update())
        return removed

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
        pending_msgs = self._runtime.storage.list_scheduled_messages(
            [ScheduledMessageStatus.PENDING]
        )
        if not pending and not pending_msgs:
            return 60.0
        all_times = [item.scheduled_at for item in pending]
        all_times.extend(item.scheduled_at for item in pending_msgs)
        soonest = min(all_times)
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
        pending_msgs = self._runtime.storage.list_scheduled_messages(
            [ScheduledMessageStatus.PENDING]
        )
        for msg in pending_msgs:
            if msg.scheduled_at > now:
                continue
            await self._fire_message(msg)

    async def _fire_message(self, record: ScheduledMessageRecord) -> None:
        try:
            session = self._runtime.storage.get_session(record.session_id)
            if session is None:
                self._runtime.storage.update_scheduled_message(
                    record.id,
                    status=ScheduledMessageStatus.FAILED,
                    failure_reason="session not found",
                )
                return
            await self._runtime.handle_input(
                record.session_id,
                SessionInputRequest(
                    text=record.text,
                    submit=record.submit,
                    command=record.command,
                    items=record.items,
                    attachments=(
                        list(record.attachments) if record.attachments else None
                    ),
                ),
            )
            self._runtime.storage.update_scheduled_message(
                record.id, status=ScheduledMessageStatus.SENT
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "scheduled message fire failed",
                extra={"msg_id": record.id},
            )
            self._runtime.storage.update_scheduled_message(
                record.id,
                status=ScheduledMessageStatus.FAILED,
                failure_reason=str(exc),
            )
        await self._publish_update()

    async def _fire(self, schedule: ScheduledSessionRecord) -> None:
        try:
            session = await self._runtime.create_session(
                SessionCreateRequest(
                    backend=schedule.backend,
                    cwd=schedule.cwd,
                    launch_target_id=schedule.launch_target_id,
                    launch_mode=schedule.launch_mode,
                    transport=schedule.transport,
                    title=schedule.title,
                    args=schedule.args,
                    config_overrides=schedule.config_overrides,
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
                    ],
                    "message_schedules": [
                        item.model_dump(mode="json")
                        for item in self.list_message_schedules()
                    ],
                },
            )
        )

    @staticmethod
    def _resolve_permission_mode(request: ScheduleCreateRequest) -> str | None:
        return validate_permission_mode_for_backend(
            request.backend, request.permission_mode
        )

    @staticmethod
    def _resolve_scheduled_at(
        request: ScheduleCreateRequest | ScheduledMessageCreateRequest,
    ) -> datetime:
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
