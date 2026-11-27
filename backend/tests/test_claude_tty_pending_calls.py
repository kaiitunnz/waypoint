"""Reading a claude_tty session's pending tool calls off its transcripts."""

from pathlib import Path
from typing import Any

from claude_tty_transcripts import (
    CWD,
    THREAD,
    jsonl,
    prompt,
    result,
    session_tree,
    use,
    write_subagent,
)
from waypoint.backends.claude_tty.byte_source import (
    LocalTranscriptByteSource,
    RemoteClaudeTranscriptByteSource,
    Tail,
)
from waypoint.backends.claude_tty.pending_calls import (
    TranscriptCalls,
    pending_calls,
)
from waypoint.backends.transcript_fs_remote import RemoteMissing, RemoteRead


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


def test_candidates_come_from_the_transcript_the_dialog_names(
    tmp_path: Path,
) -> None:
    source, main = session_tree(tmp_path)
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
    write_subagent(
        main,
        "a4",
        "general-purpose",
        jsonl(use("done-1", "Bash", {"command": "ls"})),
        meta={"finished": True},
    )
    calls = TranscriptCalls(source)

    def found(agent: str | None) -> list[str]:
        return [c.tool_use_id for c in calls.candidates(agent)]

    assert found(None) == ["main-1"]
    assert found("general-purpose") == ["sub-1", "flow-1"]
    assert found("web-fetch") == ["web-1"]
    assert found("code-reviewer") == []


def test_an_unreadable_subagent_yields_no_candidates(tmp_path: Path) -> None:
    source, main = session_tree(tmp_path)
    main.write_bytes(b"")
    write_subagent(
        main, "a1", "general-purpose", jsonl(use("sub-1", "Bash", {"command": "ls"}))
    )
    broken = write_subagent(main, "a2", "general-purpose", b"")
    broken.unlink()
    broken.mkdir()
    assert TranscriptCalls(source).candidates("general-purpose") == []


def test_resolution_is_read_from_the_calls_own_transcript(tmp_path: Path) -> None:
    source, main = session_tree(tmp_path)
    main.write_bytes(jsonl(use("main-1", "Bash", {"command": "ls"})))
    sub = write_subagent(
        main, "a1", "general-purpose", jsonl(use("sub-1", "Bash", {"command": "ls"}))
    )
    calls = TranscriptCalls(source)
    (main_call,) = calls.candidates(None)
    (sub_call,) = calls.candidates("general-purpose")

    assert not calls.is_resolved(main_call)
    assert not calls.is_resolved(sub_call)
    with sub.open("ab") as fh:
        fh.write(jsonl(result("sub-1")))
    assert calls.is_resolved(sub_call)
    assert not calls.is_resolved(main_call)
    # A later message means the call is behind the transcript.
    with main.open("ab") as fh:
        fh.write(jsonl(use("main-2", "Bash", {"command": "ls"}, msg="msg-2")))
    assert calls.is_resolved(main_call)


def test_missing_transcript_yields_nothing(tmp_path: Path) -> None:
    source = LocalTranscriptByteSource(CWD, THREAD, str(tmp_path / "config"))
    calls = TranscriptCalls(source)
    assert calls.candidates(None) == []
    assert calls.candidates("general-purpose") == []
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

    assert [c.tool_use_id for c in calls.candidates(None)] == ["main-1"]
    assert [c.tool_use_id for c in calls.candidates("general-purpose")] == ["sub-1"]
    assert source.read_tail("/home/u/.claude/plans/p.md", 4) == Tail(
        b"6789", truncated=True
    )
    assert source.read_tail("/home/u/.claude/plans/missing.md", 4) is None
