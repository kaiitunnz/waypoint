import re
from dataclasses import dataclass
from datetime import UTC, datetime

from waypoint.schemas import EventKind, EventRecord, SessionStatus

ANSI_CSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ANSI_OSC_PATTERN = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)", re.DOTALL)
ANSI_SINGLE_PATTERN = re.compile(r"\x1b[@-_]")
APPROVAL_PATTERNS = (
    re.compile(r"\bapprove\b", re.IGNORECASE),
    re.compile(r"\ballow\b", re.IGNORECASE),
    re.compile(r"\by\/n\b", re.IGNORECASE),
)
TOOL_CALL_PATTERNS = (
    re.compile(r"\brunning\b", re.IGNORECASE),
    re.compile(r"\bexecuting\b", re.IGNORECASE),
    re.compile(r"\btool\b", re.IGNORECASE),
)
TOOL_RESULT_PATTERNS = (
    re.compile(r"\bcompleted\b", re.IGNORECASE),
    re.compile(r"\bexited with code\b", re.IGNORECASE),
)
WAITING_INPUT_PATTERNS = (
    re.compile(r"\?$"),
    re.compile(r"\bcontinue\b", re.IGNORECASE),
    re.compile(r"\brespond\b", re.IGNORECASE),
)


@dataclass
class NormalizedChunk:
    events: list[EventRecord]
    status: SessionStatus
    confidence: str


class TerminalNormalizer:
    def clean(self, text: str) -> str:
        text = ANSI_OSC_PATTERN.sub("", text)
        text = ANSI_CSI_PATTERN.sub("", text)
        text = ANSI_SINGLE_PATTERN.sub("", text)
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def normalize(
        self, session_id: str, text: str, start_sequence: int
    ) -> NormalizedChunk:
        cleaned = self.clean(text)
        events: list[EventRecord] = []
        status = SessionStatus.RUNNING
        confidence = "heuristic"
        sequence = start_sequence
        for block in self._blocks(cleaned):
            kind = EventKind.AGENT_OUTPUT
            metadata: dict[str, str] = {"confidence": confidence}
            status = SessionStatus.RUNNING
            if self._matches(APPROVAL_PATTERNS, block):
                kind = EventKind.APPROVAL_REQUEST
                status = SessionStatus.WAITING_INPUT
                metadata["status"] = SessionStatus.WAITING_INPUT
            elif self._matches(TOOL_CALL_PATTERNS, block):
                kind = EventKind.TOOL_CALL
                metadata["status"] = SessionStatus.RUNNING
            elif self._matches(TOOL_RESULT_PATTERNS, block):
                kind = EventKind.TOOL_RESULT
                metadata["status"] = SessionStatus.IDLE
                status = SessionStatus.IDLE
            elif self._matches(WAITING_INPUT_PATTERNS, block):
                metadata["status"] = SessionStatus.WAITING_INPUT
                status = SessionStatus.WAITING_INPUT
            else:
                metadata["status"] = SessionStatus.RUNNING
            events.append(
                EventRecord(
                    session_id=session_id,
                    ts=datetime.now(UTC),
                    kind=kind,
                    text=block,
                    metadata=metadata,
                    sequence=sequence,
                )
            )
            sequence += 1
        if not events and cleaned.strip():
            events.append(
                EventRecord(
                    session_id=session_id,
                    ts=datetime.now(UTC),
                    kind=EventKind.RAW_TERMINAL_CHUNK,
                    text=cleaned,
                    metadata={"confidence": "raw", "status": SessionStatus.RUNNING},
                    sequence=sequence,
                )
            )
        if events:
            last_status = events[-1].metadata.get("status")
            if last_status:
                status = SessionStatus(last_status)
        return NormalizedChunk(events=events, status=status, confidence=confidence)

    def _blocks(self, text: str) -> list[str]:
        stripped = text.strip()
        if not stripped:
            return []
        blocks = [part.strip() for part in stripped.split("\n\n") if part.strip()]
        return blocks or [stripped]

    def _matches(self, patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
        return any(pattern.search(text) for pattern in patterns)
