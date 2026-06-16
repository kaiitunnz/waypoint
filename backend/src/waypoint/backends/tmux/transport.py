from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.attachments import ResolvedAttachment, append_attachment_paths
from waypoint.backends.approvals import is_approve_decision
from waypoint.backends.base import PaneSubmitConfirming
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.schemas import (
    SessionInputRequest,
    SessionRecord,
    SessionSource,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


class TmuxTransport(TransportAdapter):
    is_structured = False
    supports_resume = True

    def __init__(self, runtime: SessionRuntime) -> None:
        self._runtime = runtime

    @property
    def adapter(self):
        return self._runtime.tmux

    @staticmethod
    def _target(session: SessionRecord) -> str:
        state = session.transport_state
        return state.get("tmux_pane") or state.get("tmux_session") or session.id

    async def send_input(
        self,
        session: SessionRecord,
        text: str,
        attachments: list[ResolvedAttachment] | None = None,
    ) -> None:
        # A raw terminal can't carry binary, so attachments degrade to their
        # host paths appended to the message; the inner CLI reads them itself.
        payload = append_attachment_paths(text, attachments or [])
        target = self._target(session)
        # Resolve the *agent* plugin (by backend), not plugin_for(session),
        # which is transport-keyed and returns this TmuxPlugin for the generic
        # tmux transport — the agent (Claude/Codex/OpenCode) owns the
        # composer-confirmation knowledge.
        plugin = self._runtime.registry.get(session.backend)
        confirmer = plugin if isinstance(plugin, PaneSubmitConfirming) else None
        try:
            if confirmer is None:
                await self.adapter.send_input(target, payload, True)
                return
            # Some wrapped TUIs absorb the submit Enter while still ingesting
            # the paste (the Claude TUI does this loading an image pasted by
            # path), leaving the message typed but unsent. Paste without
            # submitting, then send Enter and confirm the composer cleared,
            # retrying the keystroke if it was swallowed.
            await self.adapter.send_input(target, payload, submit=False)
            await self._submit_confirmed(target, confirmer, payload)
        except TmuxError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    async def _submit_confirmed(
        self,
        target: str,
        confirmer: PaneSubmitConfirming,
        sent_text: str,
        *,
        attempts: int = 8,
        poll_seconds: float = 0.4,
    ) -> None:
        # Re-send Enter until the composer reports cleared. Stop on the first
        # confirmed submit so a landed keystroke is never followed by a stray
        # one (which could hit a started turn or a dialog). Bounded; if the TUI
        # never confirms, leave the input rather than spamming keystrokes.
        for _ in range(attempts):
            await self.adapter.submit(target)
            await asyncio.sleep(poll_seconds)
            if confirmer.confirm_pane_submit(
                await self.adapter.capture_snapshot(target), sent_text
            ):
                return

    async def interrupt(self, session: SessionRecord) -> None:
        await self.adapter.interrupt(self._target(session))

    async def resume(self, session: SessionRecord) -> None:
        await self.adapter.resume(self._target(session))

    async def terminate(self, session: SessionRecord) -> None:
        target = self._target(session)
        with suppress(TmuxError):
            await self.adapter.stop_pipe(target)
        tmux_session = session.transport_state.get("tmux_session")
        if session.source == SessionSource.MANAGED and tmux_session:
            with suppress(TmuxError):
                await self.adapter.kill_session(tmux_session)
        monitor = self._runtime.monitor_tasks.pop(session.id, None)
        if monitor is not None:
            monitor.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await monitor

    async def respond_to_approval(
        self,
        session: SessionRecord,
        decision: str,
        text: str | None,
        approval_id: str | None = None,
    ) -> bool:
        mapped = "y" if is_approve_decision(decision) else "n"
        reply = text or mapped
        await self._runtime.handle_input(
            session.id, SessionInputRequest(text=reply, submit=True)
        )
        await self._runtime._record_system_event(
            session.id, f"Approval response sent: {mapped}"
        )
        return True

    def has_pending_approval(self, session: SessionRecord) -> bool:
        return False
