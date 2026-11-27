"""Reading a claude_tty session's pending tool calls off its transcripts."""

from pathlib import Path

import pytest

from claude_tty_transcripts import (
    CWD,
    THREAD,
    append,
    jsonl,
    prompt,
    result,
    session_tree,
    use,
    write_subagent,
)
from waypoint.backends.claude_tty import pending_calls as pending_calls_mod
from waypoint.backends.claude_tty.byte_source import (
    FileChunk,
    LocalTranscriptByteSource,
)
from waypoint.backends.claude_tty.pending_calls import PendingCall, TranscriptCalls


def pending(tmp_path: Path, data: bytes) -> list[PendingCall]:
    source, main = session_tree(tmp_path)
    main.write_bytes(data)
    return TranscriptCalls(source).candidates(None)


def ids(calls: list[PendingCall]) -> list[str]:
    return [c.tool_use_id for c in calls]


def test_calls_without_a_result_are_pending(tmp_path: Path) -> None:
    data = jsonl(
        use("a", "Bash", {"command": "ls"}),
        use("b", "Bash", {"command": "pwd"}),
        result("a"),
    )
    (call,) = pending(tmp_path, data)
    assert (call.tool_use_id, call.name, call.input, call.cwd, call.transcript) == (
        "b",
        "Bash",
        {"command": "pwd"},
        CWD,
        None,
    )


def test_only_the_latest_message_can_be_pending(tmp_path: Path) -> None:
    data = jsonl(
        use("a", "Bash", {"command": "ls"}, msg="msg-1"),
        use("b", "Bash", {"command": "pwd"}, msg="msg-2"),
    )
    assert ids(pending(tmp_path, data)) == ["b"]


@pytest.mark.parametrize(
    ("injected", "left"),
    [
        (prompt("[Request interrupted by user]"), []),
        (prompt("injected context", isMeta=True), ["a"]),
    ],
)
def test_a_new_prompt_ends_every_call(
    tmp_path: Path, injected: dict, left: list[str]
) -> None:
    data = jsonl(use("a", "Bash", {"command": "ls"}), injected)
    assert ids(pending(tmp_path, data)) == left


def test_history_copies_are_ignored_and_a_partial_line_waits(tmp_path: Path) -> None:
    source, main = session_tree(tmp_path)
    blob = {**use("old", "Bash", {"command": "ls"}), "type": "api-request-blob"}
    second = jsonl(use("b", "Bash", {"command": "ls"}))
    main.write_bytes(jsonl(use("a", "Bash", {"command": "ls"}), blob) + second[:20])
    calls = TranscriptCalls(source)
    assert ids(calls.candidates(None)) == ["a"]
    with main.open("ab") as fh:
        fh.write(second[20:])
    assert ids(calls.candidates(None)) == ["a", "b"]


def test_a_window_starting_mid_record_skips_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    last = jsonl(use("a", "Bash", {"command": "ls"}))
    monkeypatch.setattr(pending_calls_mod, "_FIRST_WINDOW", len(last) + 10)
    data = jsonl(use("old", "Bash", {"command": "x" * 100}, msg="msg-0")) + last
    assert ids(pending(tmp_path, data)) == ["a"]


class _Counting(LocalTranscriptByteSource):
    """Records how many bytes each ranged read returned."""

    def __init__(self, cwd: str, thread_id: str, config_dir: str) -> None:
        super().__init__(cwd, thread_id, config_dir)
        self.reads: list[int] = []

    def read_range(self, path: str, offset: int, limit: int) -> FileChunk | None:
        chunk = super().read_range(path, offset, limit)
        assert chunk is not None
        self.reads.append(len(chunk.data))
        return chunk


def test_a_followed_transcript_is_read_only_for_what_was_appended(
    tmp_path: Path,
) -> None:
    _, main = session_tree(tmp_path)
    main.write_bytes(jsonl(use("a", "Bash", {"command": "x" * 1000})))
    source = _Counting(CWD, THREAD, str(tmp_path / "config"))
    calls = TranscriptCalls(source)
    calls.candidates(None)
    added = jsonl(use("b", "Bash", {"command": "ls"}))
    with main.open("ab") as fh:
        fh.write(added)
    source.reads.clear()
    assert ids(calls.candidates(None)) == ["a", "b"]
    assert ids(calls.candidates(None)) == ["a", "b"]
    assert source.reads == [len(added), 0]


@pytest.mark.parametrize("change", ["too far behind", "replaced"])
def test_a_transcript_that_cannot_be_followed_is_read_afresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    source, main = session_tree(tmp_path)
    main.write_bytes(jsonl(use("a", "Bash", {"command": "ls"})))
    calls = TranscriptCalls(source)
    (first,) = calls.candidates(None)
    fresh = jsonl(use("b", "Bash", {"command": "ls"}, msg="msg-2"))
    if change == "replaced":
        other = main.with_suffix(".new")
        other.write_bytes(jsonl(prompt("p" * 500)) + fresh)
        other.rename(main)
    else:
        monkeypatch.setattr(pending_calls_mod, "_GAP_LIMIT", 64)
        monkeypatch.setattr(pending_calls_mod, "_FIRST_WINDOW", len(fresh) + 5)
        with main.open("ab") as fh:
            fh.write(jsonl(prompt("p" * 500)) + fresh)
    assert ids(calls.candidates(None)) == ["b"]
    assert calls.is_resolved(first)


def test_candidates_come_from_the_transcript_the_dialog_names(tmp_path: Path) -> None:
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


def test_a_call_behind_a_large_result_is_still_pending(tmp_path: Path) -> None:
    source, main = session_tree(tmp_path)
    main.write_bytes(
        jsonl(
            use("read-1", "Read", {"file_path": "big"}),
            use("bash-1", "Bash", {"command": "ls"}),
        )
    )
    calls = TranscriptCalls(source)
    _, bash = calls.candidates(None)

    # The sibling's result pushes the call's own record out of the window.
    append(main, result("read-1", "x" * (2 * 1024 * 1024)))
    assert not calls.is_resolved(bash)
    append(main, result("bash-1"))
    assert calls.is_resolved(bash)


@pytest.mark.parametrize(
    ("record", "resolved"),
    [
        (result("other"), False),
        (result("a"), True),
        (prompt("stop"), True),
        (use("b", "Bash", {"command": "ls"}, msg="msg-2"), True),
    ],
)
def test_a_call_ends_only_on_what_ends_it(
    tmp_path: Path, record: dict, resolved: bool
) -> None:
    source, main = session_tree(tmp_path)
    main.write_bytes(jsonl(use("a", "Bash", {"command": "ls"})))
    calls = TranscriptCalls(source)
    (call,) = calls.candidates(None)
    assert not calls.is_resolved(call)
    append(main, record)
    assert calls.is_resolved(call) is resolved


def test_a_subagent_transcript_is_read_again_once_it_grows(tmp_path: Path) -> None:
    source, main = session_tree(tmp_path)
    sub = write_subagent(
        main, "a1", "general-purpose", jsonl(use("sub-1", "Bash", {"command": "ls"}))
    )
    calls = TranscriptCalls(source)
    assert [c.tool_use_id for c in calls.candidates("general-purpose")] == ["sub-1"]
    assert [c.tool_use_id for c in calls.candidates("general-purpose")] == ["sub-1"]
    append(sub, result("sub-1"))
    assert calls.candidates("general-purpose") == []


def test_missing_transcript_yields_nothing(tmp_path: Path) -> None:
    source = LocalTranscriptByteSource(CWD, THREAD, str(tmp_path / "config"))
    calls = TranscriptCalls(source)
    assert calls.candidates(None) == []
    assert calls.candidates("general-purpose") == []
    assert source.read_tail(str(tmp_path / "plan.md"), 10) is None
    assert source.read_range(str(tmp_path / "plan.md"), 0, 10) is None


def test_a_resumed_subagent_is_a_candidate_again(tmp_path: Path) -> None:
    source, main = session_tree(tmp_path)
    records = jsonl(use("sub-1", "Bash", {"command": "ls"}), result("sub-1"))
    sub = write_subagent(
        main, "a1", "general-purpose", records, meta={"finished": True}
    )
    calls = TranscriptCalls(source)
    assert calls.candidates("general-purpose") == []
    # Resuming it rewrites the meta without ``finished``.
    sub.with_suffix(".meta.json").write_text('{"agentType": "general-purpose"}')
    append(sub, use("sub-2", "Bash", {"command": "pwd"}, msg="msg-2"))
    assert ids(calls.candidates("general-purpose")) == ["sub-2"]
