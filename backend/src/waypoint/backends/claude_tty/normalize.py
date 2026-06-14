"""Transcript-record → canonical-event normalization for the claude_tty backend.

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
from waypoint.schemas import EventKind, SessionStatus

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

        for block in blocks:
            bt = block.get("type")
            if bt == "text":
                text = str(block.get("text") or "")
                if text:
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
            # thinking blocks: intentionally skipped

        # Synthesize a result event when the turn is complete: stop_reason is
        # terminal (not "tool_use") and no tool_use blocks were in this record.
        if stop_reason and stop_reason != "tool_use" and not has_tool_use:
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

        # Only tool_result blocks are surfaced from user records. A user/peer
        # turn's own text is already recorded at the input boundary
        # (runtime.handle_input → _record_user_event); re-emitting the
        # transcript's copy would duplicate the message in the event stream.
        events: list[NormalizedEvent] = []
        for block in iter_content_blocks(content):
            if block.get("type") != "tool_result":
                continue
            tool_use_id = str(block.get("tool_use_id") or "")
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
            events.append(
                NormalizedEvent(
                    kind=EventKind.TOOL_RESULT,
                    text=text,
                    metadata={
                        "method": "user.tool_result",
                        "item_id": tool_use_id,
                        "tool_use_id": tool_use_id,
                        "is_error": is_error,
                        "payload": block,
                        "status": SessionStatus.RUNNING,
                    },
                    status=SessionStatus.RUNNING,
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


def _is_injected_turn(content: Any) -> bool:
    """Return True for harness-injected user turns that must not surface as chat."""
    if isinstance(content, str):
        stripped = content.lstrip()
        return stripped.startswith("<task-notification>") or stripped.startswith(
            "This session is being continued"
        )
    return False
