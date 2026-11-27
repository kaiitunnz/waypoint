"""Fake Claude transcripts, main and subagent, on disk for claude_tty tests."""

import json
from pathlib import Path
from typing import Any

from waypoint.backends.claude_tty.byte_source import (
    LocalTranscriptByteSource,
    SubagentTranscript,
    Tail,
    transcript_path,
)

THREAD = "0f8fad5b-d9cb-469f-a165-70867728950e"
CWD = "/tmp/probe/repo"


def use(
    tool_use_id: str, name: str, tool_input: dict[str, Any], msg: str = "msg-1"
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "cwd": CWD,
        "message": {
            "id": msg,
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": name,
                    "input": tool_input,
                }
            ],
        },
    }


def result(tool_use_id: str) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}
            ]
        },
    }


def prompt(text: str, **extra: Any) -> dict[str, Any]:
    return {"type": "user", "message": {"content": text}, **extra}


def jsonl(*records: dict[str, Any]) -> bytes:
    return b"".join(json.dumps(record).encode() + b"\n" for record in records)


def session_tree(tmp_path: Path) -> tuple[LocalTranscriptByteSource, Path]:
    config_dir = str(tmp_path / "config")
    main = transcript_path(CWD, THREAD, config_dir)
    main.parent.mkdir(parents=True)
    main.write_bytes(b"")
    return LocalTranscriptByteSource(CWD, THREAD, config_dir), main


def append(path: Path, *records: dict[str, Any]) -> None:
    with path.open("ab") as fh:
        fh.write(jsonl(*records))


def write_subagent(
    main: Path,
    agent_id: str,
    agent_type: str,
    records: bytes,
    folder: str = "",
    meta: dict[str, Any] | None = None,
) -> Path:
    root = main.with_suffix("") / "subagents" / folder
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"agent-{agent_id}.jsonl"
    path.write_bytes(records)
    (root / f"agent-{agent_id}.meta.json").write_text(
        json.dumps({"agentType": agent_type, **(meta or {})})
    )
    return path


class NoTranscriptTree:
    """For a fake source whose session has no files around its transcript."""

    def read_transcript_tail(self, limit: int) -> Tail | None:
        return None

    def list_subagents(self) -> list[SubagentTranscript] | None:
        return None

    def read_tail(self, path: str, limit: int) -> Tail | None:
        return None
