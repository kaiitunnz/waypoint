"""Whether a plugin can drive a session. A plugin that fails to wire, or that no
longer loads, leaves its sessions visibly unavailable."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

from waypoint.backends import BackendPlugin
from waypoint.schemas import SessionRecord, SessionStatus

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.plugin_availability")

# ``transport_state`` key of a marked session: the status to return to, since a
# pane restore writes none, and the reason already noted.
_MARK_KEY = "unavailable_mark"


class PluginAvailability:
    def __init__(self, runtime: "SessionRuntime") -> None:
        self._runtime = runtime
        # Plugin id -> why it cannot drive sessions.
        self.failures: dict[str, str] = {}

    @contextmanager
    def guard(self, plugin: BackendPlugin, stage: str) -> Iterator[None]:
        """Run one of ``plugin``'s wiring steps; a failure makes it unavailable."""
        try:
            yield
        except Exception as exc:
            log.exception(
                "backend plugin %s failed in %s; its sessions are unavailable",
                plugin.id,
                stage,
            )
            self.failures[plugin.id] = f"failed in {stage}: {exc}"

    def reason(self, session: SessionRecord) -> str | None:
        """Why no plugin can drive ``session``, or ``None`` when one can."""
        try:
            driver = self._lookup(session)
        except KeyError:
            return (
                f"no backend plugin provides {session.backend} "
                f"over {session.transport}"
            )
        if not self.failures:
            return None
        return self._failure(driver.id, session.backend)

    def driver(self, session: SessionRecord) -> BackendPlugin | None:
        """The plugin that drives ``session``, unless it is unavailable."""
        return None if self.reason(session) else self._lookup(session)

    def require(self, session: SessionRecord) -> None:
        if reason := self.reason(session):
            raise _unavailable(f"session is unavailable: {reason}")

    def require_plugins(self, *plugin_ids: str) -> None:
        if failure := self._failure(*plugin_ids):
            raise _unavailable(failure)

    async def reconcile(self, session: SessionRecord) -> SessionRecord | None:
        """At boot, mark a session no plugin can drive and return ``None``.
        Otherwise return its record, with the status a marked session had."""
        reason = self.reason(session)
        if reason is None:
            return self._unmark(session)
        if session.status != SessionStatus.EXITED:
            try:
                self._lookup(session)
            except KeyError:
                log.exception(
                    "no backend plugin for session %s (backend %s, transport %s)",
                    session.id,
                    session.backend,
                    session.transport,
                )
            await self._mark(session, reason)
        return None

    def _lookup(self, session: SessionRecord) -> BackendPlugin:
        driver = self._runtime.registry.plugin_for(session)
        self._runtime.registry.get(session.backend)
        return driver

    def _failure(self, *plugin_ids: str) -> str | None:
        for plugin_id in plugin_ids:
            if failure := self.failures.get(plugin_id):
                return f"backend plugin {plugin_id} {failure}"
        return None

    async def _mark(self, session: SessionRecord, reason: str) -> None:
        mark = session.transport_state.get(_MARK_KEY)
        if (
            mark is not None
            and mark["reason"] == reason
            and session.status == SessionStatus.ERROR
        ):
            return
        self._runtime.storage.update_session(
            session.id,
            transport_state={
                **session.transport_state,
                _MARK_KEY: {
                    "status": mark["status"] if mark else session.status,
                    "reason": reason,
                },
            },
        )
        async with self._runtime._keep_read(session.id):
            await self._runtime._record_system_event(
                session.id,
                f"Session unavailable: {reason}",
                status=SessionStatus.ERROR,
            )

    def _unmark(self, session: SessionRecord) -> SessionRecord:
        mark = session.transport_state.get(_MARK_KEY)
        if mark is None:
            return session
        fields: dict[str, Any] = {
            "transport_state": {
                key: value
                for key, value in session.transport_state.items()
                if key != _MARK_KEY
            }
        }
        # A status set since the mark, such as by a terminate, stands.
        if session.status == SessionStatus.ERROR:
            fields["status"] = SessionStatus(mark["status"])
        return self._runtime.storage.update_session(session.id, **fields)


def _unavailable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
