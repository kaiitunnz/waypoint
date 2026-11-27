"""Which tool calls are waiting on a Claude TUI session's permission dialogs.

The TUI has no structured approval channel, so a dialog read off the pane is
matched against calls recorded elsewhere. A :class:`CallSource` supplies those
candidates; :class:`TranscriptCalls` reads them from the session's main and
subagent transcripts.
"""

import json
import threading
from dataclasses import dataclass, replace
from typing import Any, Protocol

from waypoint.backends.claude_code.normalize import iter_content_blocks
from waypoint.backends.claude_tty.byte_source import TranscriptTreeReader

# What is first read of a transcript. A pending call's own record has to fit in it.
_FIRST_WINDOW = 4 * 1024 * 1024
# The most read to catch a followed transcript up; further behind, its end is
# read afresh. Large enough for any single record, such as an image result.
_GAP_LIMIT = 16 * 1024 * 1024


@dataclass(frozen=True)
class PendingCall:
    """A tool call with no result yet."""

    tool_use_id: str
    name: str
    input: dict[str, Any]
    cwd: str | None = None
    # The transcript it was read from; None for the main one.
    transcript: str | None = None
    # The session host's home directory, which a dialog prints as ``~``.
    home: str | None = None


class CallSource(Protocol):
    """Where a dialog's call is looked up. Neither method raises.

    The matcher relies on two things of the calls a source returns. Their
    ``transcript`` groups calls that prompt in submission order: of identical
    calls of one transcript the oldest is named. Their ``home`` is set wherever a
    dialog may print the call's path under ``~``.
    """

    def candidates(self, agent: str | None) -> list[PendingCall]:
        """Pending calls a dialog may be asking about, oldest first.

        ``agent`` is the subagent a dialog names, None for the main thread.
        Empty when they can't all be read, since a partial set could make one
        call look like the only one.
        """
        ...

    def is_resolved(self, call: PendingCall) -> bool:
        """Whether ``call`` has a result by now."""
        ...


def _records(data: bytes) -> list[dict[str, Any]]:
    records = []
    for line in data.split(b"\n"):
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


class _Unresolved:
    """The unresolved tool calls of a transcript's latest assistant message, from
    its records fed in order.

    Every call gets a result before the next message, so an earlier message's
    unresolved call will never run. A new prompt or an interrupt ends them all.
    """

    def __init__(self, transcript: str | None) -> None:
        self._transcript = transcript
        self._message_id: str | None = None
        self._calls: dict[str, PendingCall] = {}

    @property
    def calls(self) -> list[PendingCall]:
        return list(self._calls.values())

    def feed(self, data: bytes) -> None:
        for record in _records(data):
            kind = record.get("type")
            if kind not in ("assistant", "user"):
                continue
            message = _message(record)
            blocks = iter_content_blocks(message.get("content"))
            if kind == "assistant":
                current = str(message.get("id") or "")
                if current != self._message_id:
                    self._message_id = current
                    self._calls = {}
                for block in blocks:
                    tool_use_id = str(block.get("id") or "")
                    if block.get("type") != "tool_use" or not tool_use_id:
                        continue
                    tool_input = block.get("input")
                    cwd = record.get("cwd")
                    self._calls[tool_use_id] = PendingCall(
                        tool_use_id=tool_use_id,
                        name=str(block.get("name") or ""),
                        input=tool_input if isinstance(tool_input, dict) else {},
                        cwd=cwd if isinstance(cwd, str) else None,
                        transcript=self._transcript,
                    )
                continue
            results = [b for b in blocks if b.get("type") == "tool_result"]
            for block in results:
                self._calls.pop(str(block.get("tool_use_id") or ""), None)
            if not results and not record.get("isMeta"):
                self._calls = {}


class _Followed:
    """One transcript's unresolved calls, kept current by reading only what was
    appended to it."""

    def __init__(self, transcript: str | None) -> None:
        self._transcript = transcript
        self._unresolved = _Unresolved(transcript)
        self._identity: tuple[int, int] | None = None
        # The end of the last whole record read.
        self._offset = 0
        # The listed size and modification time last read at, for a listed file.
        self.version: tuple[int, int] | None = None

    @property
    def calls(self) -> list[PendingCall]:
        return self._unresolved.calls

    def read(self, reader: TranscriptTreeReader, path: str) -> bool:
        """Take in what was appended. False when the file can't be read."""
        if self._identity is not None:
            if (chunk := reader.read_range(path, self._offset, _GAP_LIMIT)) is None:
                return False
            if (
                chunk.identity == self._identity
                and self._offset <= chunk.size <= self._offset + len(chunk.data)
            ):
                self._take(chunk.data)
                return True
        return self.restart(reader, path)

    def restart(self, reader: TranscriptTreeReader, path: str) -> bool:
        """Read the file's end afresh: on first sight, and when it was replaced
        or has grown too far to catch up."""
        if (size := reader.file_size(path)) is None:
            return False
        start = max(0, size - _FIRST_WINDOW)
        if (chunk := reader.read_range(path, start, _FIRST_WINDOW)) is None:
            return False
        # A window starting past the head begins mid-record.
        skip = (chunk.data.find(b"\n") + 1 or len(chunk.data)) if start else 0
        self._unresolved = _Unresolved(self._transcript)
        self._identity = chunk.identity
        self._offset = start + skip
        self._take(chunk.data[skip:])
        return True

    def _take(self, data: bytes) -> None:
        # An unterminated last line is still being written: it is read again.
        whole = data[: data.rfind(b"\n") + 1]
        self._unresolved.feed(whole)
        self._offset += len(whole)


class TranscriptCalls:
    """Candidates from the session's transcripts."""

    def __init__(self, reader: TranscriptTreeReader) -> None:
        self._reader = reader
        self._main = _Followed(None)
        # By path. A session can hold hundreds of subagent transcripts.
        self._subagents: dict[str, _Followed] = {}
        # A poll and a reply's check can look at once, each from its own thread.
        self._lock = threading.Lock()

    def candidates(self, agent: str | None) -> list[PendingCall]:
        with self._lock:
            calls = self._pending(agent)
        if not calls:
            return []
        home = self._reader.home_dir()
        return [replace(call, home=home) for call in calls]

    def _pending(self, agent: str | None) -> list[PendingCall]:
        if agent is None:
            path = self._reader.transcript_file()
            if path is None or not self._main.read(self._reader, path):
                return []
            return self._main.calls
        if (subagents := self._reader.list_subagents()) is None:
            return []
        calls: list[PendingCall] = []
        for subagent in subagents:
            if subagent.finished or agent not in (subagent.agent_type, subagent.name):
                continue
            followed = self._follow(subagent.path)
            if followed.version != subagent.version:
                # A transcript only grows; one that changed otherwise was rewritten.
                rewritten = (
                    followed.version is not None
                    and subagent.version[0] <= followed.version[0]
                )
                read = followed.restart if rewritten else followed.read
                if not read(self._reader, subagent.path):
                    return []
                followed.version = subagent.version
            calls += followed.calls
        return calls

    def _follow(self, path: str) -> _Followed:
        if (followed := self._subagents.get(path)) is None:
            followed = self._subagents[path] = _Followed(path)
        return followed

    def is_resolved(self, call: PendingCall) -> bool:
        """The call has a result, or the transcript has moved past it."""
        with self._lock:
            if call.transcript is None:
                followed, path = self._main, self._reader.transcript_file()
            else:
                followed, path = self._follow(call.transcript), call.transcript
            if path is None or not followed.read(self._reader, path):
                return False
            return call.tool_use_id not in {c.tool_use_id for c in followed.calls}
