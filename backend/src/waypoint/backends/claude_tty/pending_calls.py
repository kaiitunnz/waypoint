"""Which tool calls are waiting on a Claude TUI session's permission dialogs.

The TUI has no structured approval channel, so a dialog read off the pane is
matched against calls recorded elsewhere. A :class:`CallSource` supplies those
candidates; :class:`TranscriptCalls` reads them from the session's main and
subagent transcripts.
"""

import json
from dataclasses import dataclass
from typing import Any, Protocol

from waypoint.backends.claude_code.normalize import iter_content_blocks
from waypoint.backends.claude_tty.byte_source import Tail, TranscriptTreeReader

# A pending call's own record has to fit in the window.
_CANDIDATE_WINDOW = 4 * 1024 * 1024
# A result or a newer message is at the end, so a shorter window shows it.
_RESOLVED_WINDOW = 1024 * 1024


@dataclass(frozen=True)
class PendingCall:
    """A tool call with no result yet."""

    tool_use_id: str
    name: str
    input: dict[str, Any]
    cwd: str | None = None
    # The transcript it was read from; None for the main one.
    transcript: str | None = None


class CallSource(Protocol):
    def candidates(self, agent: str | None) -> list[PendingCall]:
        """Pending calls a dialog may be asking about, oldest first.

        ``agent`` is the subagent a dialog names, None for the main thread.
        Empty when they can't all be read, since a partial set could make one
        call look like the only one. Never raises.
        """
        ...

    def is_resolved(self, call: PendingCall) -> bool:
        """Whether ``call`` has a result by now. Never raises."""
        ...


def _records(tail: Tail) -> list[dict[str, Any]]:
    lines = tail.data.split(b"\n")
    if tail.truncated:
        lines = lines[1:]  # cut mid-record
    records = []
    for line in lines:
        # An unterminated last line is still being written.
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _message(record: dict[str, Any]) -> dict[str, Any]:
    message = record.get("message")
    return message if isinstance(message, dict) else {}


def pending_calls(tail: Tail, transcript: str | None = None) -> list[PendingCall]:
    """Unresolved tool calls of the transcript's latest assistant message.

    Every call gets a result before the next message, so an earlier message's
    unresolved call will never run. A new prompt or an interrupt ends them all.
    """
    message_id: str | None = None
    calls: dict[str, PendingCall] = {}
    for record in _records(tail):
        kind = record.get("type")
        if kind not in ("assistant", "user"):
            continue
        message = _message(record)
        blocks = iter_content_blocks(message.get("content"))
        if kind == "assistant":
            if (current := str(message.get("id") or "")) != message_id:
                message_id = current
                calls = {}
            for block in blocks:
                if block.get("type") != "tool_use" or not block.get("id"):
                    continue
                tool_input = block.get("input")
                cwd = record.get("cwd")
                calls[str(block["id"])] = PendingCall(
                    tool_use_id=str(block["id"]),
                    name=str(block.get("name") or ""),
                    input=tool_input if isinstance(tool_input, dict) else {},
                    cwd=cwd if isinstance(cwd, str) else None,
                    transcript=transcript,
                )
            continue
        results = [b for b in blocks if b.get("type") == "tool_result"]
        for block in results:
            calls.pop(str(block.get("tool_use_id") or ""), None)
        if not results and not record.get("isMeta"):
            calls = {}
    return list(calls.values())


class TranscriptCalls:
    """Candidates from the session's transcripts."""

    def __init__(self, reader: TranscriptTreeReader) -> None:
        self._reader = reader

    def candidates(self, agent: str | None) -> list[PendingCall]:
        if agent is None:
            tail = self._reader.read_transcript_tail(_CANDIDATE_WINDOW)
            return [] if tail is None else pending_calls(tail)
        if (subagents := self._reader.list_subagents()) is None:
            return []
        calls: list[PendingCall] = []
        for subagent in subagents:
            if subagent.finished or agent not in (subagent.agent_type, subagent.name):
                continue
            tail = self._reader.read_tail(subagent.path, _CANDIDATE_WINDOW)
            if tail is None:
                return []
            calls += pending_calls(tail, subagent.path)
        return calls

    def is_resolved(self, call: PendingCall) -> bool:
        """The call has a result, or the transcript has moved to a later
        message."""
        tail = (
            self._reader.read_transcript_tail(_RESOLVED_WINDOW)
            if call.transcript is None
            else self._reader.read_tail(call.transcript, _RESOLVED_WINDOW)
        )
        return tail is not None and call.tool_use_id not in {
            pending.tool_use_id for pending in pending_calls(tail)
        }
