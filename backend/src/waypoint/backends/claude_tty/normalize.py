"""Transcript-record → canonical-event normalization for the claude_tty (Emulated) transport.

The Claude TUI writes a JSONL transcript to
``~/.claude/projects/<dashed-cwd>/<session-id>.jsonl``.  Each record wraps the
same ``message`` object consumed by the claude_code normalizer, so
``iter_content_blocks`` / ``stringify_tool_result`` are reused directly.

Key invariants (all verified against real transcripts in Phase 0):

- Records are processed in arrival order; same-``message.id`` records are
  interleaved with ``user`` tool_result records and must NOT be merged.
- Usage is counted only on the first record for each ``message.id``; subsequent
  records carry an identical copy that would inflate totals 2–3.6× without the
  guard.
- A synthesized result (SYSTEM_NOTE, status=IDLE) is emitted when the last
  assistant record in a turn has a terminal ``stop_reason`` (anything other than
  ``tool_use``) and contains no ``tool_use`` blocks.
- Injected harness user turns (``<task-notification>`` and context-window
  summaries beginning with "This session is being continued") are dropped.
"""

import json
import uuid
from typing import Any

from waypoint.backends.claude_code.normalize import (
    TASK_TOOL_NAMES,
    TaskListTracker,
    extract_created_task_id,
    format_task_snapshot,
    iter_content_blocks,
    stringify_tool_result,
)
from waypoint.backends.diff_preview import (
    build_preview,
    files_from_claude_tool_result,
)
from waypoint.schemas import EventKind, SessionStatus

FILE_EDIT_TOOL_NAMES: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit"})

# Allowlist of record types we handle; everything else is dropped silently.
# This is an allowlist rather than a denylist so undocumented TUI record types
# (e.g. the ``pr-link`` type observed in real transcripts) are dropped rather
# than mis-rendered.
HANDLED_RECORD_TYPES: frozenset[str] = frozenset({"assistant", "user"})


class NormalizedEvent:
    __slots__ = ("kind", "text", "metadata", "status")

    def __init__(
        self,
        kind: EventKind,
        text: str,
        metadata: dict[str, Any],
        status: SessionStatus,
    ) -> None:
        self.kind = kind
        self.text = text
        self.metadata = metadata
        self.status = status


class TranscriptNormalizer:
    """Stateful normalizer for a single session's JSONL transcript stream.

    Instantiate one per session and feed records in arrival order via
    ``process_record``.  Usage deduplication state is accumulated across calls.
    """

    def __init__(self) -> None:
        self._seen_message_ids: set[str] = set()
        self._task_tracker: TaskListTracker = TaskListTracker()
        self._pending_task_creates: dict[str, dict[str, Any]] = {}
        self._suppressed_result_tool_use_ids: set[str] = set()
        self._task_card_item_id: str | None = None
        # Message ids that have already emitted a synthesized "turn complete"
        # note. A turn's content can arrive as several same-id records (e.g. a
        # thinking record then a text record, both stamped end_turn); the note
        # must fire once, not once per record.
        self._result_emitted_ids: set[str] = set()
        # Set by the tailer right after it Esc-dismisses an AskUserQuestion
        # popup. The Esc forces the TUI to flush the tool_use record (and a
        # "user rejected" result) to the transcript; the latch tells the next
        # AskUserQuestion record to surface as an answerable card rather than a
        # plain tool_call, and to swallow the synthetic rejection.
        self._expect_dismissed_question: bool = False
        self._dismissed_question_ids: set[str] = set()
        # tool_use ids of file-edit calls, so the matching tool_result record
        # (which carries the applied diff in its toolUseResult) can attach a
        # diff preview. The TUI records the change on the result, not the call.
        self._file_edit_tool_use_ids: set[str] = set()

    def arm_question_dismissal(self) -> None:
        self._expect_dismissed_question = True

    def process_record(self, record: dict[str, Any]) -> list[NormalizedEvent]:
        rec_type = record.get("type")
        if rec_type == "assistant":
            return self._process_assistant(record)
        if rec_type == "user":
            return self._process_user(record)
        return []

    def _process_assistant(self, record: dict[str, Any]) -> list[NormalizedEvent]:
        message: dict[str, Any] = record.get("message") or {}
        message_id = str(message.get("id") or "")
        usage: dict[str, Any] = message.get("usage") or {}
        stop_reason = str(message.get("stop_reason") or "")

        first_seen = message_id not in self._seen_message_ids
        if message_id:
            self._seen_message_ids.add(message_id)

        events: list[NormalizedEvent] = []
        blocks = iter_content_blocks(message.get("content"))
        has_tool_use = False
        had_text = False
        had_thinking = False

        for block in blocks:
            bt = block.get("type")
            if bt == "text":
                text = str(block.get("text") or "")
                if text:
                    had_text = True
                    events.append(
                        NormalizedEvent(
                            kind=EventKind.AGENT_OUTPUT,
                            text=text,
                            metadata={
                                "method": "assistant.text",
                                "item_id": message_id,
                                "payload": block,
                                "status": SessionStatus.RUNNING,
                            },
                            status=SessionStatus.RUNNING,
                        )
                    )
            elif bt == "tool_use":
                has_tool_use = True
                tool_use_id = str(block.get("id") or "")
                tool_name = str(block.get("name") or "tool")
                if tool_name == "AskUserQuestion" and self._expect_dismissed_question:
                    self._expect_dismissed_question = False
                    if tool_use_id:
                        self._dismissed_question_ids.add(tool_use_id)
                    events.append(
                        NormalizedEvent(
                            kind=EventKind.TOOL_CALL,
                            text=f"{tool_name}\n"
                            f"{json.dumps(block.get('input') or {}, indent=2)}",
                            metadata={
                                "method": "assistant.tool_use",
                                "item_id": tool_use_id,
                                "tool_name": tool_name,
                                "tool_use_id": tool_use_id,
                                "payload": block,
                                "status": SessionStatus.WAITING_INPUT,
                            },
                            status=SessionStatus.WAITING_INPUT,
                        )
                    )
                    continue
                if tool_name in TASK_TOOL_NAMES:
                    tool_input: dict[str, Any] = block.get("input") or {}
                    if tool_name == "TaskCreate":
                        if tool_use_id:
                            self._pending_task_creates[tool_use_id] = tool_input
                    elif tool_name == "TaskUpdate":
                        task_id = str(tool_input.get("taskId") or "")
                        if tool_use_id:
                            self._suppressed_result_tool_use_ids.add(tool_use_id)
                        if task_id:
                            self._task_tracker.update(
                                task_id,
                                status=tool_input.get("status"),
                                content=tool_input.get("subject"),
                                active_form=tool_input.get("activeForm"),
                                description=tool_input.get("description"),
                            )
                            events.extend(self._emit_task_snapshot())
                    else:
                        # TaskGet / TaskList: suppress result, emit nothing
                        if tool_use_id:
                            self._suppressed_result_tool_use_ids.add(tool_use_id)
                    continue
                if tool_name in FILE_EDIT_TOOL_NAMES and tool_use_id:
                    self._file_edit_tool_use_ids.add(tool_use_id)
                input_text = json.dumps(block.get("input") or {}, indent=2)
                events.append(
                    NormalizedEvent(
                        kind=EventKind.TOOL_CALL,
                        text=f"{tool_name}\n{input_text}",
                        metadata={
                            "method": "assistant.tool_use",
                            "item_id": tool_use_id,
                            "tool_name": tool_name,
                            "tool_use_id": tool_use_id,
                            "payload": block,
                            "status": SessionStatus.RUNNING,
                        },
                        status=SessionStatus.RUNNING,
                    )
                )
            elif bt == "thinking":
                had_thinking = True
            # thinking blocks emit nothing; tracked only to defer the note below

        # Synthesize a result event when the turn is complete: stop_reason is
        # terminal (not "tool_use") and no tool_use blocks were in this record.
        # A turn's final message can split into a thinking record then a text
        # record, both stamped end_turn; defer off the thinking-only record so
        # the note lands after the visible text, and emit it at most once per
        # message id so the split does not double the note.
        already_noted = bool(message_id) and message_id in self._result_emitted_ids
        # Only an end_turn message splits its thinking and text across sibling
        # records, so the note can wait for the text. An abnormal termination
        # (e.g. max_tokens hit mid-thinking) has no text sibling coming, so its
        # note must fire on the thinking record or the turn never resolves.
        thinking_only = had_thinking and not had_text and stop_reason == "end_turn"
        if (
            stop_reason
            and stop_reason != "tool_use"
            and not has_tool_use
            and not already_noted
            and not thinking_only
        ):
            if message_id:
                self._result_emitted_ids.add(message_id)
            usage_payload: dict[str, Any] = usage if first_seen else {}
            output_tokens = int(usage.get("output_tokens") or 0)
            if stop_reason == "end_turn":
                result_text = (
                    f"Turn complete · {output_tokens} output tokens"
                    if output_tokens
                    else "Turn complete"
                )
            else:
                suffix = f" · {output_tokens} output tokens" if output_tokens else ""
                result_text = f"Turn complete ({stop_reason}){suffix}"
            events.append(
                NormalizedEvent(
                    kind=EventKind.SYSTEM_NOTE,
                    text=result_text,
                    metadata={
                        "method": "result",
                        "stop_reason": stop_reason,
                        "usage": usage_payload,
                        "payload": usage_payload,
                        "status": SessionStatus.IDLE,
                    },
                    status=SessionStatus.IDLE,
                )
            )

        return events

    def _process_user(self, record: dict[str, Any]) -> list[NormalizedEvent]:
        message: dict[str, Any] = record.get("message") or {}
        content = message.get("content")

        if _is_injected_turn(content):
            return []

        turn_aborted = _is_user_rejection(record)
        dismissed_question = False

        # Only tool_result blocks are surfaced from user records. A user/peer
        # turn's own text is already recorded at the input boundary
        # (runtime.handle_input → _record_user_event); re-emitting the
        # transcript's copy would duplicate the message in the event stream.
        events: list[NormalizedEvent] = []
        for block in iter_content_blocks(content):
            if block.get("type") != "tool_result":
                continue
            tool_use_id = str(block.get("tool_use_id") or "")
            if tool_use_id and tool_use_id in self._dismissed_question_ids:
                # The "user rejected" result the TUI writes when we Esc the
                # popup to surface it. Drop it so the question card stays
                # answerable, and skip the abort note below — the session
                # waits on the user, it has not ended the turn.
                self._dismissed_question_ids.discard(tool_use_id)
                dismissed_question = True
                continue
            if tool_use_id and tool_use_id in self._pending_task_creates:
                create_input = self._pending_task_creates.pop(tool_use_id)
                task_id = extract_created_task_id(block) or tool_use_id
                if self._task_tracker.is_empty:
                    self._task_card_item_id = None
                self._task_tracker.create(
                    task_id,
                    content=str(create_input.get("subject") or ""),
                    active_form=create_input.get("activeForm"),
                    description=create_input.get("description"),
                    status=create_input.get("status") or "pending",
                )
                events.extend(self._emit_task_snapshot())
                continue
            if tool_use_id and tool_use_id in self._suppressed_result_tool_use_ids:
                self._suppressed_result_tool_use_ids.discard(tool_use_id)
                continue
            text = stringify_tool_result(block.get("content"))
            is_error = bool(block.get("is_error"))
            metadata: dict[str, Any] = {
                "method": "user.tool_result",
                "item_id": tool_use_id,
                "tool_use_id": tool_use_id,
                "is_error": is_error,
                "payload": block,
                "status": SessionStatus.RUNNING,
            }
            if tool_use_id in self._file_edit_tool_use_ids:
                self._file_edit_tool_use_ids.discard(tool_use_id)
                if not is_error:
                    diff_preview = _diff_preview_from_tool_result(record)
                    if diff_preview is not None:
                        metadata["diff_preview"] = diff_preview
            events.append(
                NormalizedEvent(
                    kind=EventKind.TOOL_RESULT,
                    text=text,
                    metadata=metadata,
                    status=SessionStatus.RUNNING,
                )
            )

        # A declined tool ends the turn with no terminal-stop_reason record to
        # follow, so resolve the session to idle here or it stays stuck running.
        if turn_aborted and not dismissed_question:
            events.append(
                NormalizedEvent(
                    kind=EventKind.SYSTEM_NOTE,
                    text="Turn ended — tool use declined",
                    metadata={
                        "method": "result",
                        "stop_reason": "tool_rejected",
                        "status": SessionStatus.IDLE,
                    },
                    status=SessionStatus.IDLE,
                )
            )

        return events

    def _emit_task_snapshot(self) -> list[NormalizedEvent]:
        if self._task_card_item_id is None:
            self._task_card_item_id = uuid.uuid4().hex
        todos = self._task_tracker.snapshot()
        return [
            NormalizedEvent(
                kind=EventKind.TOOL_RESULT,
                text=format_task_snapshot(todos),
                metadata={
                    "method": "assistant.task_update",
                    "item_id": self._task_card_item_id,
                    "item_type": "todo_list",
                    "tool_name": "TodoWrite",
                    "payload": {"input": {"todos": todos}},
                    "status": SessionStatus.RUNNING,
                },
                status=SessionStatus.RUNNING,
            )
        ]


def _diff_preview_from_tool_result(record: dict[str, Any]) -> dict[str, Any] | None:
    """Build an applied-phase diff preview from an edit's ``toolUseResult``."""
    files = files_from_claude_tool_result(record.get("toolUseResult"))
    if not files:
        return None
    preview = build_preview("applied", files)
    return preview.model_dump(mode="json") if preview else None


def _is_injected_turn(content: Any) -> bool:
    """Return True for harness-injected user turns that must not surface as chat."""
    if isinstance(content, str):
        stripped = content.lstrip()
        return stripped.startswith("<task-notification>") or stripped.startswith(
            "This session is being continued"
        )
    return False


def _is_user_rejection(record: dict[str, Any]) -> bool:
    """Return True when the user declined the tool this record reports on.

    A declined tool aborts the TUI turn back to the ready prompt with no
    following assistant record, so the terminal-``stop_reason`` path never
    fires. The CLI tags the record with ``toolUseResult: "User rejected tool
    use"``, which we use to resolve the session to idle off the rejection.
    """
    result = record.get("toolUseResult")
    return isinstance(result, str) and result.strip().lower().startswith(
        "user rejected"
    )
