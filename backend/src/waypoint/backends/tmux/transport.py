from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.attachments import ResolvedAttachment, append_attachment_paths
from waypoint.backends.approvals import is_approve_decision
from waypoint.backends.base import PaneSubmitConfirming
from waypoint.backends.registry import get_registry
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.schemas import (
    SessionInputRequest,
    SessionRecord,
    SessionSource,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.tmux")

# Bounds for the pre-restart settle wait (flush_before_restart): how long to
# wait overall and how often to poll, and how many consecutive unchanged
# polls count as "settled". Best-effort only — see flush_before_restart.
_FLUSH_SETTLE_TIMEOUT_SECONDS = 8.0
_FLUSH_SETTLE_POLL_SECONDS = 0.3
_FLUSH_SETTLE_STABLE_TICKS = 2


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
            # A reattach/restart relaunches the pane, and the wrapped TUI is
            # still booting when this fires — pasting before the composer exists
            # drops the keystrokes. Wait for it to draw first. This also refuses
            # to send while a modal dialog is open, so the message is never
            # pasted (and Enter'd) into an approval/trust prompt.
            await self._await_pane_ready(target, confirmer)
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

    async def _await_pane_ready(
        self,
        target: str,
        confirmer: PaneSubmitConfirming,
        *,
        attempts: int = 20,
        poll_seconds: float = 0.3,
    ) -> None:
        # Poll until the composer is drawn so the paste lands in it rather than
        # the boot screen. Checked before sleeping so an already-ready pane (the
        # common case — sending to a live session) costs one snapshot and no
        # delay. Bounded; if the TUI never reports ready, fall through and send
        # anyway rather than block the request indefinitely.
        for _ in range(attempts):
            snapshot = await self.adapter.capture_snapshot(target)
            # Never paste/Enter into a modal dialog — that would select an
            # option (e.g. approve a tool or accept a trust prompt). Surface it
            # so the caller responds to the dialog instead of bulldozing it.
            if confirmer.pane_shows_blocking_dialog(snapshot):
                raise TmuxError(
                    "the pane has an open dialog; respond to it before sending"
                )
            if confirmer.pane_ready_for_input(snapshot):
                return
            await asyncio.sleep(poll_seconds)

    async def _submit_confirmed(
        self,
        target: str,
        confirmer: PaneSubmitConfirming,
        sent_text: str,
        *,
        attempts: int = 30,
        poll_seconds: float = 0.4,
    ) -> None:
        # Re-send Enter until the composer reports cleared. Submit before
        # confirming so at least one Enter always lands (a snapshot taken right
        # after the paste can transiently read as empty before the text renders,
        # which would otherwise end the loop having sent nothing). Stop on the
        # first confirmed submit so a landed keystroke is never followed by a
        # stray one. Before each Enter, bail if a modal dialog is on screen — the
        # message already submitted and opened one (or one raced in), and an
        # Enter would select an option (e.g. approve a tool). Bounded; if the TUI
        # never confirms, leave the input rather than spamming keystrokes.
        #
        # The budget (attempts × poll) must outlast the wrapped TUI's paste
        # ingestion: the Claude TUI converts pasted image paths into ``[Image
        # #N]`` chips asynchronously and *drops* the submit Enter for the whole
        # window (the keystrokes are discarded, not queued), which on a
        # cold-booting session with several large images can run well past the
        # first few seconds. A budget too short here gives up mid-ingestion and
        # strands the message in the composer until the next send re-triggers it
        # — the original "first message with attachments never sends" bug. ~12s
        # comfortably covers observed ingestion; the loop still exits the instant
        # the composer clears, so the common (fast) case is unchanged.
        for _ in range(attempts):
            if confirmer.pane_shows_blocking_dialog(
                await self.adapter.capture_snapshot(target)
            ):
                return
            await self.adapter.submit(target)
            await asyncio.sleep(poll_seconds)
            if confirmer.confirm_pane_submit(
                await self.adapter.capture_snapshot(target), sent_text
            ):
                return

    async def interrupt(self, session: SessionRecord) -> None:
        await self.adapter.interrupt(self._target(session))

    async def flush_before_restart(self, session: SessionRecord) -> None:
        """Best-effort wait for the just-interrupted turn to settle before
        the account-profile switch tears down this session's pane.

        A scraped pane has no structured turn-end event, so this polls the
        strongest available proxy until it stops changing for
        ``_FLUSH_SETTLE_STABLE_TICKS`` consecutive polls: the wrapped agent's
        native transcript artifact's mtime, when one is already on disk (the
        artifact is exactly what the resume needs, so waiting on it directly
        beats a generic idle guess). Falls back to the pane's captured
        content length when no artifact exists yet — a not-yet-persisted
        first turn has no thread id to look up — or the session is remote (an
        artifact stat there is an SSH round trip per poll tick; the tmux pane
        itself is always local, even for a remote session — it's a local pane
        running ``ssh ... <agent CLI>`` — so capturing it never leaves this
        host).

        Bounded by a timeout; on timeout this logs and returns rather than
        raising. Fail-before-destroy is enforced by the transcript step
        (``ensure_thread_available``) later in the switch, not here — a pane
        that never settles must not block or abort the switch, since the
        switch already tolerates resuming a partially-written thread.
        """
        target = self._target(session)
        artifact_path = self._settle_artifact_path(session)

        async def _read_signal() -> float | int | None:
            if artifact_path is not None:
                try:
                    return artifact_path.stat().st_mtime
                except OSError:
                    return None
            try:
                snapshot = await self.adapter.capture_snapshot(target)
            except TmuxError:
                return None
            return len(snapshot)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + _FLUSH_SETTLE_TIMEOUT_SECONDS
        previous: float | int | None = None
        stable_ticks = 0
        while True:
            current = await _read_signal()
            if previous is not None and current == previous:
                stable_ticks += 1
                if stable_ticks >= _FLUSH_SETTLE_STABLE_TICKS:
                    return
            else:
                stable_ticks = 0
            previous = current
            if loop.time() >= deadline:
                log.info(
                    "tmux flush-before-restart settle timed out; proceeding",
                    extra={"session_id": session.id},
                )
                return
            await asyncio.sleep(_FLUSH_SETTLE_POLL_SECONDS)

    def _settle_artifact_path(self, session: SessionRecord) -> Path | None:
        """The wrapped agent's on-disk transcript artifact to watch for the
        settle signal, or ``None`` when there isn't one to watch yet (falls
        back to pane-idle) — see ``flush_before_restart``.
        """
        if session.launch_target_id is not None:
            return None
        registry = get_registry()
        if not registry.has_backend(session.backend):
            return None
        agent = registry.get(session.backend)
        config_dir_key = agent.capabilities.config_dir_env_var
        config_dir = session.launch_env.get(config_dir_key) if config_dir_key else None
        artifacts = agent.native_thread_artifacts(session, config_dir)
        return artifacts[0] if artifacts else None

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
