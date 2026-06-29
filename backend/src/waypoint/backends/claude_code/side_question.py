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

import asyncio
import json
import logging
import shlex
import shutil
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.launch_targets import (
    SshLaunchTargetConfig,
    _resolve_local_binary,
    quote_remote_path,
)
from waypoint.schemas import (
    EventKind,
    SessionEnvelope,
    SessionRecord,
    SessionSource,
    SessionStatus,
    SideQuestion,
    SideQuestionStatus,
)

if TYPE_CHECKING:
    from waypoint.backends.claude_code.plugin import ClaudeCodePlugin
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.claude_code.side_question")

MAX_ATTEMPTS = 3
_ONE_SHOT_TIMEOUT = 120.0

# Per-session asyncio locks for transport_state["pending_side_questions"] mutations.
_session_locks: dict[str, asyncio.Lock] = {}

# Live background tasks kept in a set to prevent GC before completion.
_background_tasks: set[asyncio.Task[None]] = set()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _lock_for(session_id: str) -> asyncio.Lock:
    lock = _session_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[session_id] = lock
    return lock


def _read_side_questions(session: SessionRecord) -> list[SideQuestion]:
    raw = session.transport_state.get("pending_side_questions")
    if not isinstance(raw, list):
        return []
    out: list[SideQuestion] = []
    for item in raw:
        if isinstance(item, dict):
            try:
                out.append(SideQuestion.model_validate(item))
            except Exception:  # noqa: BLE001
                pass
    return out


def _write_side_questions(
    runtime: "SessionRuntime",
    session_id: str,
    questions: list[SideQuestion],
) -> SessionRecord:
    session = runtime.storage.get_session(session_id)
    if session is None:
        raise KeyError(session_id)
    state = {
        **session.transport_state,
        "pending_side_questions": [q.model_dump(mode="json") for q in questions],
    }
    return runtime.storage.update_session(session_id, transport_state=state)


async def _broadcast_upsert(
    runtime: "SessionRuntime",
    session_id: str,
    sq: SideQuestion,
) -> None:
    await runtime.broadcast.publish(
        SessionEnvelope(
            type="side_question",
            payload={"side_question": sq.model_dump(mode="json")},
        ),
        session_id=session_id,
    )


async def _broadcast_remove(
    runtime: "SessionRuntime",
    session_id: str,
    sqid: str,
) -> None:
    await runtime.broadcast.publish(
        SessionEnvelope(
            type="side_question",
            payload={"removed_id": sqid},
        ),
        session_id=session_id,
    )


def _delete_fork_file_local(fork_thread_id: str) -> None:
    """Delete ``<fork_thread_id>.jsonl`` from ``~/.claude/projects/``."""
    projects = Path.home() / ".claude" / "projects"
    if not projects.is_dir():
        return
    for p in projects.glob(f"*/{fork_thread_id}.jsonl"):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            log.warning("could not delete fork thread file %s", p)
        return  # UUID is unique; stop after the first match


async def _delete_fork_file(
    fork_thread_id: str,
    launch_target_id: str | None,
    runtime: "SessionRuntime",
) -> None:
    """Delete the forked thread file locally or via SSH."""
    if launch_target_id is None:
        await asyncio.to_thread(_delete_fork_file_local, fork_thread_id)
        return
    launch_target = runtime._find_launch_target(launch_target_id)
    if launch_target is None:
        return
    needle = shlex.quote(f"{fork_thread_id}.jsonl")
    # ssh_capture runs the command in the remote shell; glob and find both work.
    await launch_target.ssh_capture(
        f"find $HOME/.claude/projects -maxdepth 2 -name {needle}"
        f" -exec rm -f {{}} \\; 2>/dev/null || true"
    )


def _parse_one_shot_output(raw: bytes) -> str:
    """Parse ``--output-format json`` stdout and return the result text."""
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        data: dict = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude output is not valid JSON: {text[:200]!r}") from exc
    if data.get("is_error"):
        raise RuntimeError(f"claude error: {data.get('subtype', 'unknown')}")
    result = data.get("result")
    if not isinstance(result, str):
        raise RuntimeError(f"claude result missing or not a string: {text[:200]!r}")
    return result


async def _run_one_shot_local(
    question: str,
    thread_id: str,
    fork_id: str,
    cwd: str,
) -> str:
    """Run a one-shot fork-query locally and return the answer text."""
    binary = shutil.which("claude")
    if binary is None:
        raise RuntimeError("claude binary not found on PATH")
    args = [
        binary,
        "-p",
        question,
        "--resume",
        thread_id,
        "--fork-session",
        "--session-id",
        fork_id,
        "--output-format",
        "json",
        "--tools",
        "",
    ]
    cwd_path = Path(cwd).expanduser()
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_ONE_SHOT_TIMEOUT
        )
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError("side-question one-shot timed out") from None
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited with rc={proc.returncode}")
    return _parse_one_shot_output(stdout)


async def _run_one_shot_remote(
    question: str,
    thread_id: str,
    fork_id: str,
    cwd: str,
    launch_target: SshLaunchTargetConfig,
    claude_bin: str,
) -> str:
    """Run a one-shot fork-query on a remote SSH host and return the answer."""
    claude_args = [
        claude_bin,
        "-p",
        question,
        "--resume",
        thread_id,
        "--fork-session",
        "--session-id",
        fork_id,
        "--output-format",
        "json",
        "--tools",
        "",
    ]
    remote_parts = [
        f"cd {quote_remote_path(cwd)}",
        "&&",
        "exec",
        shlex.join(claude_args),
    ]
    remote_cmd = " ".join(remote_parts)
    wrapped = launch_target.wrap_remote_command(remote_cmd)
    ssh_args = [
        _resolve_local_binary(launch_target.ssh_bin),
        *launch_target.ssh_args,
        launch_target.ssh_destination,
        wrapped,
    ]
    proc = await asyncio.create_subprocess_exec(
        *ssh_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_ONE_SHOT_TIMEOUT
        )
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError("side-question one-shot timed out (remote)") from None
    if proc.returncode != 0:
        raise RuntimeError(f"claude (remote) exited with rc={proc.returncode}")
    return _parse_one_shot_output(stdout)


async def _mark_error(
    runtime: "SessionRuntime",
    session_id: str,
    sqid: str,
    error_msg: str,
) -> None:
    """Update a side-question to ``error`` status and broadcast the change."""
    updated: SideQuestion | None = None
    async with _lock_for(session_id):
        fresh = runtime.storage.get_session(session_id)
        if fresh is None:
            return
        questions = _read_side_questions(fresh)
        sq = next((q for q in questions if q.id == sqid), None)
        if sq is None:
            return
        updated = sq.model_copy(
            update={
                "status": SideQuestionStatus.ERROR,
                "error": error_msg,
                "fork_thread_id": None,
            }
        )
        questions = [updated if q.id == sqid else q for q in questions]
        _write_side_questions(runtime, session_id, questions)
    if updated is not None:
        await _broadcast_upsert(runtime, session_id, updated)


def _schedule_bg_task(
    runtime: "SessionRuntime",
    plugin: "ClaudeCodePlugin",
    session_id: str,
    sqid: str,
) -> None:
    task = asyncio.create_task(
        _run_side_question_bg(runtime, plugin, session_id, sqid),
        name=f"side-question-{sqid}",
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _run_side_question_bg(
    runtime: "SessionRuntime",
    plugin: "ClaudeCodePlugin",
    session_id: str,
    sqid: str,
) -> None:
    """Background worker: run the one-shot and update the side-question record."""
    try:
        session = runtime.storage.get_session(session_id)
        if session is None:
            return

        thread_id = session.transport_state.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            await _mark_error(
                runtime, session_id, sqid, "Session has no conversation thread."
            )
            return

        launch_target_id = session.launch_target_id
        launch_target = (
            runtime._find_launch_target(launch_target_id) if launch_target_id else None
        )
        fork_id = plugin.generate_session_id()

        # Read the question and persist the fork id on the record under one lock,
        # BEFORE launching the one-shot. ``--fork-session`` creates
        # ``<fork_id>.jsonl`` on disk; recording the id first means a restart
        # mid-flight can find that orphan via the record so recovery deletes it,
        # rather than leaking it — the record and its fork file stay
        # born-and-die-together as the cleanup invariant requires.
        question: str
        async with _lock_for(session_id):
            fresh = runtime.storage.get_session(session_id)
            if fresh is None:
                return
            qs = _read_side_questions(fresh)
            sq_now = next((q for q in qs if q.id == sqid), None)
            if sq_now is None or sq_now.status != SideQuestionStatus.PENDING:
                return  # dismissed or already resolved before we started
            question = sq_now.question
            qs = [
                (
                    sq_now.model_copy(update={"fork_thread_id": fork_id})
                    if q.id == sqid
                    else q
                )
                for q in qs
            ]
            _write_side_questions(runtime, session_id, qs)

        try:
            if launch_target is None:
                answer = await _run_one_shot_local(
                    question, thread_id, fork_id, session.cwd
                )
            else:
                claude_bin = plugin.remote_executable(launch_target) or "claude"
                answer = await _run_one_shot_remote(
                    question,
                    thread_id,
                    fork_id,
                    session.cwd,
                    launch_target,
                    claude_bin,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "side-question one-shot failed: %s",
                exc,
                extra={"session_id": session_id, "sqid": sqid},
            )
            # The one-shot may have created <E>.jsonl before failing; clean it up.
            await _delete_fork_file(fork_id, launch_target_id, runtime)
            await _mark_error(runtime, session_id, sqid, str(exc))
            return

        # Persist the answer under the per-session lock.
        fork_file_to_cleanup: str | None = None
        updated: SideQuestion | None = None

        async with _lock_for(session_id):
            fresh = runtime.storage.get_session(session_id)
            if fresh is None:
                # Session deleted while running; abandon, clean up the fork file.
                fork_file_to_cleanup = fork_id
            else:
                qs = _read_side_questions(fresh)
                sq = next((q for q in qs if q.id == sqid), None)
                if sq is None:
                    # Dismissed while running; clean up the fork file.
                    fork_file_to_cleanup = fork_id
                else:
                    updated = sq.model_copy(
                        update={
                            "status": SideQuestionStatus.ANSWERED,
                            "answer": answer,
                            "fork_thread_id": fork_id,
                        }
                    )
                    qs = [updated if q.id == sqid else q for q in qs]
                    _write_side_questions(runtime, session_id, qs)

        if fork_file_to_cleanup is not None:
            await _delete_fork_file(fork_file_to_cleanup, launch_target_id, runtime)
            return

        if updated is not None:
            await _broadcast_upsert(runtime, session_id, updated)

    except Exception:  # noqa: BLE001
        log.exception(
            "side-question bg task crashed",
            extra={"session_id": session_id, "sqid": sqid},
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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
    thread_id = session.transport_state.get("thread_id")
    now = datetime.now(UTC)

    if not isinstance(thread_id, str) or not thread_id:
        sq = SideQuestion(
            id=plugin.generate_session_id(),
            question=question,
            status=SideQuestionStatus.ERROR,
            error="Send a message first — there is no conversation to read.",
            attempts=1,
            created_at=now,
        )
        async with _lock_for(session.id):
            fresh = runtime.storage.get_session(session.id)
            if fresh is None:
                return
            qs = _read_side_questions(fresh)
            qs.append(sq)
            _write_side_questions(runtime, session.id, qs)
        await _broadcast_upsert(runtime, session.id, sq)
        return

    sq = SideQuestion(
        id=plugin.generate_session_id(),
        question=question,
        status=SideQuestionStatus.PENDING,
        attempts=1,
        created_at=now,
    )
    async with _lock_for(session.id):
        fresh = runtime.storage.get_session(session.id)
        if fresh is None:
            return
        qs = _read_side_questions(fresh)
        qs.append(sq)
        _write_side_questions(runtime, session.id, qs)
    await _broadcast_upsert(runtime, session.id, sq)
    _schedule_bg_task(runtime, plugin, session.id, sq.id)


async def claim_fork_thread(
    runtime: "SessionRuntime",
    session: SessionRecord,
    side_question_id: str,
) -> SideQuestion:
    """Hand an answered aside's forked thread off to a new owner.

    Drops the record **without** deleting ``<E>.jsonl`` (the new session adopts
    it — the one exception to the cleanup invariant) and broadcasts the removal.
    Returns the claimed record (its ``fork_thread_id`` is guaranteed set). Raises
    if the id is unknown or the aside has no retained thread yet.
    """
    async with _lock_for(session.id):
        fresh = runtime.storage.get_session(session.id)
        if fresh is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="session not found"
            )
        qs = _read_side_questions(fresh)
        sq = next((q for q in qs if q.id == side_question_id), None)
        if sq is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="side question not found",
            )
        if sq.status != SideQuestionStatus.ANSWERED or not sq.fork_thread_id:
            # A pending record now also carries a fork id (so a mid-flight restart
            # can clean it up), so presence of the id is no longer enough — only an
            # answered aside owns a complete, idle thread that is safe to adopt.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="side question has no retained thread; wait for an answer first",
            )
        qs = [q for q in qs if q.id != side_question_id]
        _write_side_questions(runtime, session.id, qs)

    await _broadcast_remove(runtime, session.id, side_question_id)
    return sq


async def fork_aside(
    runtime: "SessionRuntime",
    session: SessionRecord,
    side_question_id: str,
    *,
    new_session_id: str,
    transport_id: str,
    title: str,
    raw_log: Path,
    structured_log: Path,
    bring_up: Callable[[SessionRecord, str], Awaitable[None]],
) -> SessionRecord:
    """Promote an answered aside into a managed session on ``transport_id``.

    Claims the aside's forked thread (see :func:`claim_fork_thread`), persists a
    new session record that resumes it, seeds the transcript, then hands the
    launch to ``bring_up`` — the transport-specific step (a structured adapter
    restore for ``claude_cli``, a resumed tmux pane for ``claude_tty``).

    Transcript seeding is length-independent: the parent's events are bulk-cloned
    and the aside's question/answer are injected from the (durable) record, rather
    than re-reading and re-emitting the whole forked thread. Transport-agnostic on
    purpose — it never touches the structured adapter — so it works for any
    transport that can resume a thread.
    """
    sq = await claim_fork_thread(runtime, session, side_question_id)
    fork_thread_id = sq.fork_thread_id or ""

    # Everything after the claim runs under one rollback: record creation, event
    # cloning, transcript seeding, and launch can each fail, and any failure must
    # restore the source card and re-home or delete the handed-off fork thread —
    # never leave the aside swallowed or ``<E>.jsonl`` orphaned.
    created = False
    try:
        now = datetime.now(UTC)
        raw_log.touch(exist_ok=True)
        new_session = SessionRecord(
            id=new_session_id,
            backend=session.backend,
            source=SessionSource.MANAGED,
            transport=transport_id,
            title=title,
            cwd=session.cwd,
            launch_target_id=session.launch_target_id,
            repo_name=session.repo_name,
            branch=session.branch,
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"thread_id": fork_thread_id},
            permission_mode=session.permission_mode,
            model=session.model,
            effort=session.effort,
            args=session.args,
            config_overrides=session.config_overrides,
        )
        runtime.storage.create_session(new_session)
        created = True
        runtime.storage.clone_events(session.id, new_session_id)
        await _ingest_aside_qa(runtime, new_session_id, sq)
        await bring_up(new_session, fork_thread_id)
    except Exception as exc:  # noqa: BLE001
        # ``claim_fork_thread`` only hands the thread off — it never deletes
        # ``<E>.jsonl`` — so a restored record still owns its fork file. If the
        # source session vanished mid-promotion, no record can own the fork
        # again, so delete it to keep the record-and-fork-file invariant.
        if created:
            runtime.storage.delete_session(new_session_id)
        restored = await _restore_claimed_aside(runtime, session.id, sq)
        if not restored and fork_thread_id:
            await _delete_fork_file(fork_thread_id, session.launch_target_id, runtime)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"failed to promote side question: {exc}",
        ) from exc

    return runtime.get_session(new_session_id)


async def _restore_claimed_aside(
    runtime: "SessionRuntime", session_id: str, sq: SideQuestion
) -> bool:
    """Re-insert an aside claimed by a fork that then failed to launch, and
    re-broadcast it so the dismissed card returns.

    Returns ``True`` if the record now lives on the source session (restored, or
    already re-added), ``False`` if the source session is gone — in which case
    the caller owns cleaning up the orphaned fork thread."""
    async with _lock_for(session_id):
        fresh = runtime.storage.get_session(session_id)
        if fresh is None:
            return False
        qs = _read_side_questions(fresh)
        if any(q.id == sq.id for q in qs):
            return True
        qs.append(sq)
        _write_side_questions(runtime, session_id, qs)
    await _broadcast_upsert(runtime, session_id, sq)
    return True


async def _ingest_aside_qa(
    runtime: "SessionRuntime", session_id: str, sq: SideQuestion
) -> None:
    """Append the aside's question and answer to the promoted session.

    The forked thread already holds these turns, but the new session's event DB
    only carries the cloned parent transcript. Injecting the stored text appends
    them after the parent without re-reading the thread — O(1) regardless of how
    long the conversation is.
    """
    await runtime._record_user_event(
        session_id, sq.question, True, status=SessionStatus.IDLE
    )
    if sq.answer:
        await runtime._emit_adapter_event(
            session_id,
            EventKind.AGENT_OUTPUT,
            sq.answer,
            {"method": "assistant.text"},
            SessionStatus.IDLE,
        )


async def dismiss_aside(
    runtime: "SessionRuntime",
    plugin: "ClaudeCodePlugin",
    session: SessionRecord,
    side_question_id: str,
) -> None:
    """Resolve a side-question: drop its record, delete its forked thread, and
    broadcast the removal. No-op if the id is unknown."""
    fork_thread_id: str | None = None

    async with _lock_for(session.id):
        fresh = runtime.storage.get_session(session.id)
        if fresh is None:
            return
        qs = _read_side_questions(fresh)
        sq = next((q for q in qs if q.id == side_question_id), None)
        if sq is None:
            return  # no-op
        fork_thread_id = sq.fork_thread_id
        qs = [q for q in qs if q.id != side_question_id]
        _write_side_questions(runtime, session.id, qs)

    if fork_thread_id:
        await _delete_fork_file(fork_thread_id, session.launch_target_id, runtime)
    await _broadcast_remove(runtime, session.id, side_question_id)


async def recover_pending_side_questions(
    runtime: "SessionRuntime",
    plugin: "ClaudeCodePlugin",
    *,
    backend_ids: set[str] | None = None,
) -> None:
    """Post-restart sweep over every Claude-agent session's pending records.

    Sweeps sessions whose ``backend`` is in ``backend_ids`` (default: just
    ``plugin.id``). The legacy ``claude_tty`` alias backend drives the same
    one-shot mechanism, so it schedules this with ``backend_ids={"claude_tty"}``
    to cover its own rows — which ``plugin.id``-only filtering would skip.

    ``pending`` records had their one-shot killed by the restart: delete the
    orphaned ``<E>``, re-issue with a fresh ``<E>`` (bump ``attempts``; on cap,
    mark ``error`` and drop), set ``resumed=True``, and re-broadcast.
    ``answered`` records keep their on-disk thread (it survived the restart) and
    are simply re-broadcast so the overlay reappears. Records on dead/exited
    sessions are cleaned up (thread deleted, record dropped) so nothing leaks.
    """
    target_ids = backend_ids if backend_ids is not None else {plugin.id}
    for session in runtime.storage.list_sessions():
        if session.backend not in target_ids:
            continue
        questions = _read_side_questions(session)
        if not questions:
            continue

        # Dead/exited sessions: clean up all fork files and drop all records.
        if session.status in (SessionStatus.EXITED, SessionStatus.ERROR):
            for sq in questions:
                if sq.fork_thread_id:
                    await _delete_fork_file(
                        sq.fork_thread_id, session.launch_target_id, runtime
                    )
            async with _lock_for(session.id):
                fresh = runtime.storage.get_session(session.id)
                if fresh is not None:
                    state = {
                        **fresh.transport_state,
                        "pending_side_questions": [],
                    }
                    runtime.storage.update_session(session.id, transport_state=state)
            continue

        for sq in questions:
            if sq.status == SideQuestionStatus.ANSWERED:
                # Thread survived the restart.  Re-read under lock to get the
                # current state before broadcasting (dismiss may have raced us).
                sq_current: SideQuestion | None = None
                async with _lock_for(session.id):
                    fresh = runtime.storage.get_session(session.id)
                    if fresh is None:
                        continue
                    qs_fresh = _read_side_questions(fresh)
                    sq_current = next((q for q in qs_fresh if q.id == sq.id), None)
                if (
                    sq_current is not None
                    and sq_current.status == SideQuestionStatus.ANSWERED
                ):
                    await _broadcast_upsert(runtime, session.id, sq_current)

            elif sq.status == SideQuestionStatus.PENDING:
                # Re-read the record list FRESH inside the lock so a completion
                # that landed during recovery (between the stale snapshot and
                # the lock) is never clobbered and its fork file never orphaned.
                stale_fork_id: str | None = None
                to_broadcast: SideQuestion | None = None
                schedule_task = False

                async with _lock_for(session.id):
                    fresh = runtime.storage.get_session(session.id)
                    if fresh is None:
                        continue
                    qs_fresh = _read_side_questions(fresh)
                    sq_fresh = next((q for q in qs_fresh if q.id == sq.id), None)
                    if (
                        sq_fresh is None
                        or sq_fresh.status != SideQuestionStatus.PENDING
                    ):
                        # Completed or dismissed while we were iterating — leave it alone.
                        continue
                    stale_fork_id = sq_fresh.fork_thread_id
                    if sq_fresh.attempts >= MAX_ATTEMPTS:
                        to_broadcast = sq_fresh.model_copy(
                            update={
                                "status": SideQuestionStatus.ERROR,
                                "error": "Max attempts reached after backend restart.",
                                "fork_thread_id": None,
                            }
                        )
                    else:
                        to_broadcast = sq_fresh.model_copy(
                            update={
                                "attempts": sq_fresh.attempts + 1,
                                "resumed": True,
                                "fork_thread_id": None,
                            }
                        )
                        schedule_task = True
                    qs_fresh = [to_broadcast if q.id == sq.id else q for q in qs_fresh]
                    _write_side_questions(runtime, session.id, qs_fresh)

                # Async I/O and task scheduling outside the lock.
                if stale_fork_id:
                    await _delete_fork_file(
                        stale_fork_id, session.launch_target_id, runtime
                    )
                if to_broadcast is not None:
                    await _broadcast_upsert(runtime, session.id, to_broadcast)
                if schedule_task:
                    _schedule_bg_task(runtime, plugin, session.id, sq.id)

            # ERROR records: leave as-is; user can dismiss them.


async def delete_session_side_questions(
    runtime: "SessionRuntime",
    plugin: "ClaudeCodePlugin",
    session: SessionRecord,
) -> None:
    """Clean up all side-question fork files when a session is being deleted.

    Reads fork ids under the per-session lock, preferring the **fresh** storage
    state so a fork-promotion that concurrently claimed an aside (and now owns
    its ``<E>.jsonl``) is honored — its fork file is not deleted out from under
    the promoted session. Falls back to the passed ``session`` snapshot only when
    the storage row is already gone. Clears ``pending_side_questions`` and then
    deletes every retained ``<E>.jsonl``. Runs before the session row is removed
    (see ``SessionRuntime.delete``).
    """
    async with _lock_for(session.id):
        fresh = runtime.storage.get_session(session.id)
        source = fresh if fresh is not None else session
        questions = _read_side_questions(source)
        if not questions:
            return
        fork_ids = [sq.fork_thread_id for sq in questions if sq.fork_thread_id]
        if fresh is not None:
            state = {**fresh.transport_state, "pending_side_questions": []}
            runtime.storage.update_session(session.id, transport_state=state)

    for fork_thread_id in fork_ids:
        await _delete_fork_file(fork_thread_id, session.launch_target_id, runtime)
