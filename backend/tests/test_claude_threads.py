import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoint.backends.claude_code.threads import (
    claude_projects_root,
    encode_project_dir,
    find_local_claude_thread,
    list_local_claude_threads,
)


def _write_transcript(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n",
        encoding="utf-8",
    )


def _make_user_record(
    *,
    cwd: str,
    text: str,
    git_branch: str | None = None,
    timestamp: str = "2026-04-29T15:47:09.826Z",
) -> dict:
    record = {
        "type": "user",
        "cwd": cwd,
        "timestamp": timestamp,
        "message": {"role": "user", "content": text},
    }
    if git_branch is not None:
        record["gitBranch"] = git_branch
    return record


@pytest.fixture
def claude_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    return root / "projects"


def test_claude_projects_root_honors_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "alt"))
    assert claude_projects_root() == tmp_path / "alt" / "projects"


@pytest.mark.parametrize(
    "cwd, expected",
    [
        ("/home/user/waypoint", "-home-user-waypoint"),
        # Leading-dot (hidden) component collapses `/.` to `--`; a naive
        # str.replace("/", "-") would leave the dot and miss the real dir.
        ("/home/user/.wq/task-1", "-home-user--wq-task-1"),
        ("/tmp/a.b_c:d", "-tmp-a-b-c-d"),
    ],
)
def test_encode_project_dir_matches_cli_encoding(cwd, expected) -> None:
    assert encode_project_dir(cwd) == expected


def test_list_local_claude_threads_extracts_metadata(claude_root) -> None:
    project = claude_root / "-private-tmp-project"
    transcript = project / "11111111-1111-4111-8111-111111111111.jsonl"
    _write_transcript(
        transcript,
        [
            {
                "type": "queue-operation",
                "operation": "enqueue",
                "sessionId": "11111111-1111-4111-8111-111111111111",
                "timestamp": "2026-04-29T15:47:09.000Z",
            },
            _make_user_record(
                cwd="/private/tmp/project",
                text="Investigate the cache miss",
                git_branch="feature/cache",
            ),
            {"type": "ai-title", "aiTitle": "Cache miss investigation"},
        ],
    )

    results = list_local_claude_threads()
    assert len(results) == 1
    info = results[0]
    assert info.id == "11111111-1111-4111-8111-111111111111"
    assert info.cwd == "/private/tmp/project"
    assert info.title == "Cache miss investigation"
    assert info.branch == "feature/cache"
    assert info.preview == "Investigate the cache miss"
    assert info.repo_name == "project"


def test_list_local_claude_threads_includes_short_valid_transcripts(
    claude_root,
) -> None:
    """A short but resumable transcript (queue-op + user + assistant)
    must show up — there is no minimum-byte floor."""
    project = claude_root / "-tmp-short"
    transcript = project / "77777777-7777-4777-8777-777777777777.jsonl"
    _write_transcript(
        transcript,
        [
            {"type": "queue-operation", "operation": "enqueue"},
            _make_user_record(cwd="/tmp/short", text="hi"),
            {
                "type": "assistant",
                "cwd": "/tmp/short",
                "message": {"role": "assistant", "content": "hello"},
            },
        ],
    )
    assert transcript.stat().st_size < 1024

    results = list_local_claude_threads()
    assert [info.id for info in results] == ["77777777-7777-4777-8777-777777777777"]


def test_list_local_claude_threads_skips_empty_and_invalid(claude_root) -> None:
    project = claude_root / "-private-tmp-project"
    project.mkdir(parents=True)

    # Bookkeeping-only transcript — no user record.
    bookkeeping = project / "22222222-2222-4222-8222-222222222222.jsonl"
    bookkeeping.write_text(
        json.dumps({"type": "queue-operation"}) + "\n",
        encoding="utf-8",
    )

    # File with non-UUID basename should be ignored.
    weird = project / "not-a-uuid.jsonl"
    weird.write_text("padding\n" * 200, encoding="utf-8")

    # Real transcript should still appear.
    real = project / "33333333-3333-4333-8333-333333333333.jsonl"
    _write_transcript(
        real,
        [_make_user_record(cwd="/private/tmp/project", text="hello")],
    )

    results = list_local_claude_threads()
    assert [info.id for info in results] == ["33333333-3333-4333-8333-333333333333"]


def test_list_local_claude_threads_sorts_by_updated_at(claude_root) -> None:
    project = claude_root / "-private-tmp-project"
    older = project / "44444444-4444-4444-8444-444444444444.jsonl"
    newer = project / "55555555-5555-4555-8555-555555555555.jsonl"
    _write_transcript(
        older,
        [_make_user_record(cwd="/tmp/project", text="older")],
    )
    _write_transcript(
        newer,
        [_make_user_record(cwd="/tmp/project", text="newer")],
    )
    older_ts = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    newer_ts = datetime(2026, 4, 1, tzinfo=UTC).timestamp()
    os.utime(older, (older_ts, older_ts))
    os.utime(newer, (newer_ts, newer_ts))

    results = list_local_claude_threads()
    assert [info.id for info in results] == [
        "55555555-5555-4555-8555-555555555555",
        "44444444-4444-4444-8444-444444444444",
    ]


def test_find_local_claude_thread_locates_by_session_id(claude_root) -> None:
    project_a = claude_root / "-tmp-a"
    project_b = claude_root / "-tmp-b"
    target_id = "66666666-6666-4666-8666-666666666666"
    _write_transcript(
        project_a / "11111111-1111-4111-8111-111111111111.jsonl",
        [_make_user_record(cwd="/tmp/a", text="not-this-one")],
    )
    _write_transcript(
        project_b / f"{target_id}.jsonl",
        [_make_user_record(cwd="/tmp/b", text="this-one")],
    )

    info = find_local_claude_thread(target_id)
    assert info is not None
    assert info.id == target_id
    assert info.cwd == "/tmp/b"


def test_find_local_claude_thread_rejects_invalid_uuid(claude_root) -> None:
    assert find_local_claude_thread("../etc/passwd") is None
    assert find_local_claude_thread("not-a-uuid") is None
