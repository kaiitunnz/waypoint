"""Ephemeral, read-only ``/btw`` side-questions for the Claude agent.

A side-question is answered from the current conversation with **no tools**, is
**never written to the transcript**, runs **non-blocking** beside any in-flight
turn, is **session-scoped**, and **survives a backend restart while pending**.
It leaves **no native Claude thread behind** once resolved.

Mechanism (swappable behind this module's public functions): a one-shot
``claude -p "<question>" --resume <thread> --fork-session --session-id <E>
--output-format json`` with tools disabled. ``--fork-session`` copies the live
thread into ``<E>.jsonl`` and never mutates the original. The forked thread is
retained while the aside's card is open (so it can be promoted into a real
session) and deleted the moment its record leaves the session.

Cleanup invariant — the load-bearing guarantee:
    A durable record (an entry in
    ``SessionRecord.transport_state["pending_side_questions"]``) and its forked
    thread ``<E>.jsonl`` are born and die together. ``<E>.jsonl`` is deleted
    **iff** its record leaves ``pending_side_questions`` *without being adopted
    by a fork* — i.e. on dismiss, fork-handoff is the one exception (the new
    session owns the thread), session-delete, a restart re-issue (the stale
    ``<E>``), or attempt-cap failure. Deletion targets the exact, globally
    unique ``<E>.jsonl`` filename, so it can never touch another session's fork.

Concurrency: every read-modify-write of ``pending_side_questions`` runs under a
per-session lock, reading the record list **fresh inside the lock**, so
simultaneous asks, a completion landing during the recovery sweep, or two client
windows can never clobber the list. Records are keyed by side-question id;
different sessions and different ids are fully independent.

This module owns the mechanism and the durable state; ``plugin.py`` owns the
wiring (``/btw`` interception, the runtime-dispatched fork/dismiss methods, and
scheduling :func:`recover_pending_side_questions` from ``setup``).
"""

from pathlib import Path
from typing import TYPE_CHECKING

from waypoint.schemas import SessionRecord

if TYPE_CHECKING:
    from waypoint.backends.claude_code.plugin import ClaudeCodePlugin
    from waypoint.runtime import SessionRuntime


async def start_side_question(
    runtime: "SessionRuntime",
    plugin: "ClaudeCodePlugin",
    session: SessionRecord,
    question: str,
) -> None:
    """Begin a side-question: persist a ``pending`` record, broadcast it, and
    launch the background fork-query.

    Non-blocking — returns once the record is persisted and the task is
    scheduled; the answer is delivered later via the ``side_question``
    broadcast. On an empty conversation (nothing to read) the record resolves
    straight to ``error`` with a "send a message first" note and no fork is
    spawned. Registered against ``plugin`` so the task can be cancelled on
    shutdown.
    """
    raise NotImplementedError


async def fork_aside(
    runtime: "SessionRuntime",
    plugin: "ClaudeCodePlugin",
    session: SessionRecord,
    side_question_id: str,
    *,
    new_session_id: str,
    title: str,
    raw_log: Path,
    structured_log: Path,
) -> SessionRecord:
    """Adopt the aside's forked thread as a new managed Claude session.

    Hands off ``fork_thread_id`` (does **not** delete it), drops the record,
    and broadcasts the removal. Raises if the side-question is unknown or has no
    retained thread.
    """
    raise NotImplementedError


async def dismiss_aside(
    runtime: "SessionRuntime",
    plugin: "ClaudeCodePlugin",
    session: SessionRecord,
    side_question_id: str,
) -> None:
    """Resolve a side-question: drop its record, delete its forked thread, and
    broadcast the removal. No-op if the id is unknown."""
    raise NotImplementedError


async def recover_pending_side_questions(
    runtime: "SessionRuntime",
    plugin: "ClaudeCodePlugin",
) -> None:
    """Post-restart sweep over every Claude-agent session's pending records.

    ``pending`` records had their one-shot killed by the restart: delete the
    orphaned ``<E>``, re-issue with a fresh ``<E>`` (bump ``attempts``; on cap,
    mark ``error`` and drop), set ``resumed=True``, and re-broadcast.
    ``answered`` records keep their on-disk thread (it survived the restart) and
    are simply re-broadcast so the overlay reappears. Records on dead/exited
    sessions are cleaned up (thread deleted, record dropped) so nothing leaks.
    """
    raise NotImplementedError
