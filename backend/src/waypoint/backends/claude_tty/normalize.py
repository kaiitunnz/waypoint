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
- A manual ``/compact`` produces only a ``compact_boundary`` system record and
  an injected continuation summary, with no terminal assistant record, so a
  synthesized result (SYSTEM_NOTE, status=IDLE) is emitted off the boundary to
  resolve the turn. Auto-compaction fires mid-turn and is left alone.
- A builtin local slash command (e.g. ``/compact`` with nothing to compact,
  ``/status``) prints a ``local_command`` system record and runs no model turn,
  so it too is resolved to IDLE off that record with its stdout as the note.
"""

import json
import re
import uuid
from typing import Any

from waypoint.backends.claude_code.adapter import _apply_plan_edit, _is_plan_file_path
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
        # Plan mode writes the plan to ~/.claude/plans/<slug>.md before raising
        # the ExitPlanMode dialog (which is withheld from the transcript while it
        # blocks). The dialog has no plan text on the wire, so the tailer reads
        # the body captured here when it surfaces the plan-approval card.
        self.last_plan_path: str | None = None
        self.last_plan_content: str | None = None

    def arm_question_dismissal(self) -> None:
        self._expect_dismissed_question = True

    def process_record(self, record: dict[str, Any]) -> list[NormalizedEvent]:
        rec_type = record.get("type")
        if rec_type == "assistant":
            return self._process_assistant(record)
        if rec_type == "user":
            return self._process_user(record)
        if rec_type == "system":
            return self._process_system(record)
        # Allowlist dispatch: undocumented TUI record types (e.g. the ``pr-link``
        # type seen in real transcripts) are dropped rather than mis-rendered.
        return []

    def _process_system(self, record: dict[str, Any]) -> list[NormalizedEvent]:
        subtype = record.get("subtype")
        if subtype == "compact_boundary":
            return self._compact_boundary_event(record)
        if subtype == "local_command":
            return self._local_command_event(record)
        return []

    def _compact_boundary_event(self, record: dict[str, Any]) -> list[NormalizedEvent]:
        # A manual /compact is a turn whose only output is the compaction
        # boundary and an injected continuation summary, both otherwise dropped.
        # Without a terminal assistant record the session never resolves, so
        # synthesize the result note here. Auto-compaction fires mid-turn and the
        # surrounding turn ends with its own end_turn, so it needs no note.
        meta: dict[str, Any] = record.get("compactMetadata") or {}
        if meta.get("trigger") != "manual":
            return []
        pre_tokens = meta.get("preTokens")
        text = (
            f"Context compacted · {pre_tokens} tokens"
            if pre_tokens
            else "Context compacted"
        )
        return [self._result_note(text, "compact")]

    def _local_command_event(self, record: dict[str, Any]) -> list[NormalizedEvent]:
        # A builtin slash command that runs locally (e.g. /compact when there is
        # nothing to compact, /status) prints to stdout and returns without a
        # model turn. handle_input already flipped the session to RUNNING on
        # send, and nothing else follows, so resolve it here and surface the
        # command's output as the note. Commands that DO run a turn expand into
        # a user prompt rather than emitting this subtype, so resolving to idle
        # here is safe; a stray case would self-correct on the next assistant
        # record (which re-asserts RUNNING).
        text = _strip_local_command_output(record.get("content")) or "Command complete"
        return [self._result_note(text, "local_command")]

    def _result_note(self, text: str, stop_reason: str) -> NormalizedEvent:
        return NormalizedEvent(
            kind=EventKind.SYSTEM_NOTE,
            text=text,
            metadata={
                "method": "result",
                "stop_reason": stop_reason,
                "status": SessionStatus.IDLE,
            },
            status=SessionStatus.IDLE,
        )

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
                if tool_name in FILE_EDIT_TOOL_NAMES:
                    self._maybe_capture_plan(tool_name, block.get("input") or {})
                    if tool_use_id:
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

    def _maybe_capture_plan(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        """Track the plan body when a file-edit targets ~/.claude/plans/.

        Mirrors the claude_code adapter: capture the written content (or apply an
        Edit/MultiEdit patch to the running copy) so the body is in hand from the
        transcript, never read off disk — which would fail for remote sessions.
        """
        path = str(tool_input.get("file_path") or "")
        if not _is_plan_file_path(path):
            return
        self.last_plan_path = path
        if tool_name == "Write":
            content = tool_input.get("content")
            if isinstance(content, str):
                self.last_plan_content = content
        elif isinstance(self.last_plan_content, str):
            self.last_plan_content = _apply_plan_edit(
                self.last_plan_content, tool_name, tool_input
            )

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


_LOCAL_COMMAND_OUTPUT_RE = re.compile(
    r"<local-command-(?:stdout|stderr)>(.*?)</local-command-(?:stdout|stderr)>",
    re.DOTALL,
)
_LOCAL_COMMAND_MAX_LEN = 500


def _strip_local_command_output(content: Any) -> str:
    """Pull the human-readable text out of a local_command record's content."""
    if not isinstance(content, str):
        return ""
    parts = _LOCAL_COMMAND_OUTPUT_RE.findall(content)
    text = "\n".join(parts).strip() if parts else content.strip()
    if len(text) > _LOCAL_COMMAND_MAX_LEN:
        text = text[: _LOCAL_COMMAND_MAX_LEN - 1].rstrip() + "…"
    return text


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
