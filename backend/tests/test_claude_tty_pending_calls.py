"""Reading a claude_tty session's pending tool calls off its transcripts."""

import json
from pathlib import Path
from typing import Any

from waypoint.backends.claude_tty.byte_source import (
    LocalTranscriptByteSource,
    RemoteClaudeTranscriptByteSource,
    Tail,
    transcript_path,
)
from waypoint.backends.claude_tty.pending_calls import (
    TranscriptCalls,
    pending_calls,
)
from waypoint.backends.transcript_fs_remote import RemoteMissing, RemoteRead

THREAD = "0f8fad5b-d9cb-469f-a165-70867728950e"
CWD = "/work/repo"


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


def ids(data: bytes, *, truncated: bool = False) -> list[str]:
    return [c.tool_use_id for c in pending_calls(Tail(data, truncated=truncated))]


def test_calls_without_a_result_are_pending() -> None:
    data = jsonl(
        use("a", "Bash", {"command": "ls"}),
        use("b", "Bash", {"command": "pwd"}),
        result("a"),
    )
    assert ids(data) == ["b"]
    (call,) = pending_calls(Tail(data))
    assert (call.name, call.input, call.cwd, call.transcript) == (
        "Bash",
        {"command": "pwd"},
        CWD,
        None,
    )


def test_only_the_latest_message_can_be_pending() -> None:
    data = jsonl(
        use("a", "Bash", {"command": "ls"}, msg="msg-1"),
        use("b", "Bash", {"command": "pwd"}, msg="msg-2"),
    )
    assert ids(data) == ["b"]


def test_a_new_prompt_ends_every_call() -> None:
    call = use("a", "Bash", {"command": "ls"})
    assert ids(jsonl(call, prompt("[Request interrupted by user]"))) == []
    assert ids(jsonl(call, prompt("injected context", isMeta=True))) == ["a"]


def test_history_copies_and_partial_lines_are_ignored() -> None:
    blob = {**use("old", "Bash", {"command": "ls"}), "type": "api-request-blob"}
    data = jsonl(use("a", "Bash", {"command": "ls"}), blob) + b'{"type": "assis'
    assert ids(data) == ["a"]
    cut = b'put": {}}]}}\n' + jsonl(use("a", "Bash", {"command": "ls"}))
    assert ids(cut, truncated=True) == ["a"]


def _tree(tmp_path: Path) -> tuple[LocalTranscriptByteSource, Path]:
    config_dir = str(tmp_path / "config")
    main = transcript_path(CWD, THREAD, config_dir)
    main.parent.mkdir(parents=True)
    return LocalTranscriptByteSource(CWD, THREAD, config_dir), main


def write_subagent(
    main: Path,
    agent_id: str,
    agent_type: str,
    records: bytes,
    folder: str = "",
) -> Path:
    root = main.with_suffix("") / "subagents" / folder
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"agent-{agent_id}.jsonl"
    path.write_bytes(records)
    (root / f"agent-{agent_id}.meta.json").write_text(
        json.dumps({"agentType": agent_type})
    )
    return path


def test_candidates_come_from_the_transcript_the_dialog_names(
    tmp_path: Path,
) -> None:
    source, main = _tree(tmp_path)
    main.write_bytes(jsonl(use("main-1", "Bash", {"command": "sleep 60"})))
    write_subagent(
        main, "a1", "general-purpose", jsonl(use("sub-1", "Bash", {"command": "ls"}))
    )
    write_subagent(
        main, "a2", "web-fetch", jsonl(use("web-1", "WebFetch", {"url": "u"}))
    )
    write_subagent(
        main,
        "a3",
        "general-purpose",
        jsonl(use("flow-1", "Bash", {"command": "id"})),
        folder="workflows/wf-1",
    )
    calls = TranscriptCalls(source)

    def found(agent: str | None, everywhere: bool = False) -> list[str]:
        return [c.tool_use_id for c in calls.candidates(agent, everywhere=everywhere)]

    assert found(None) == ["main-1"]
    assert found("general-purpose") == ["sub-1", "flow-1"]
    assert found("web-fetch") == ["web-1"]
    assert found("code-reviewer") == []
    assert found(None, everywhere=True) == ["main-1", "sub-1", "web-1", "flow-1"]


def test_resolution_is_read_from_the_calls_own_transcript(tmp_path: Path) -> None:
    source, main = _tree(tmp_path)
    main.write_bytes(jsonl(use("main-1", "Bash", {"command": "ls"})))
    sub = write_subagent(
        main, "a1", "general-purpose", jsonl(use("sub-1", "Bash", {"command": "ls"}))
    )
    calls = TranscriptCalls(source)
    (main_call,) = calls.candidates(None, everywhere=False)
    (sub_call,) = calls.candidates("general-purpose", everywhere=False)

    assert not calls.is_resolved(main_call)
    assert not calls.is_resolved(sub_call)
    with sub.open("ab") as fh:
        fh.write(jsonl(result("sub-1")))
    assert calls.is_resolved(sub_call)
    assert not calls.is_resolved(main_call)


def test_missing_transcript_yields_nothing(tmp_path: Path) -> None:
    source = LocalTranscriptByteSource(CWD, THREAD, str(tmp_path / "config"))
    calls = TranscriptCalls(source)
    assert calls.candidates(None, everywhere=True) == []
    assert source.read_tail(str(tmp_path / "plan.md"), 10) is None


def test_read_tail_marks_a_cut_window(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"0123456789")
    source = LocalTranscriptByteSource(CWD, THREAD, None)
    assert source.read_tail(str(path), 4) == Tail(b"6789", truncated=True)
    assert source.read_tail(str(path), 40) == Tail(b"0123456789")


class _FakeRemoteFs:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def glob(self, root: str, pattern: str) -> list[str]:
        if pattern.startswith("projects/"):
            return [p for p in self.files if p.endswith(f"/{THREAD}.jsonl")]
        depth = pattern.count("/")
        return sorted(
            p
            for p in self.files
            if p.startswith(f"{root}/")
            and p.endswith(".jsonl")
            and p.removeprefix(f"{root}/").count("/") == depth
        )

    def read_range(self, path: str, offset: int, limit: int) -> Any:
        if path not in self.files:
            return RemoteMissing()
        data = self.files[path]
        return RemoteRead(
            data=data[offset : offset + limit], size=len(data), device=1, inode=1
        )


def test_remote_source_reads_the_same_tree() -> None:
    base = f"/home/u/.claude/projects/p/{THREAD}"
    files = {
        f"{base}.jsonl": jsonl(use("main-1", "Bash", {"command": "ls"})),
        f"{base}/subagents/agent-a1.jsonl": jsonl(
            use("sub-1", "Bash", {"command": "pwd"})
        ),
        f"{base}/subagents/agent-a1.meta.json": b'{"agentType": "general-purpose"}',
        "/home/u/.claude/plans/p.md": b"0123456789",
    }
    source = RemoteClaudeTranscriptByteSource.__new__(RemoteClaudeTranscriptByteSource)
    source._fs = _FakeRemoteFs(files)  # type: ignore[assignment]
    source._thread_id = THREAD
    source._config_root = "~/.claude"
    source._identity = None
    source._path = None
    source._warned = set()
    calls = TranscriptCalls(source)

    assert [c.tool_use_id for c in calls.candidates(None, everywhere=True)] == [
        "main-1",
        "sub-1",
    ]
    assert source.read_tail("/home/u/.claude/plans/p.md", 4) == Tail(
        b"6789", truncated=True
    )
    assert source.read_tail("/home/u/.claude/plans/missing.md", 4) is None
