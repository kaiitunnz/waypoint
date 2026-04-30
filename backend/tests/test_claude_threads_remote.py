import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from waypoint import claude_threads_remote
from waypoint.claude_threads_remote import RemoteClaudeThreadEnumerator
from waypoint.server_config import SshLaunchTargetConfig

ENUMERATOR_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "claude_thread_enumerator.sh"
)

VALID_ID = "11111111-1111-4111-8111-111111111111"
VALID_RECORD = {
    "id": VALID_ID,
    "cwd": "/srv/project",
    "branch": "main",
    "title": "Investigation",
    "preview": "Pick up where we left off",
    "mtime": 1_700_000_000,
    "first_ts": "2026-04-29T15:47:09.000Z",
}


@pytest.fixture
def target() -> SshLaunchTargetConfig:
    return SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        default_cwd="/home/dev",
    )


def _make_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["ssh"],
        returncode=returncode,
        stdout=stdout.encode("utf-8"),
        stderr=stderr.encode("utf-8"),
    )


def _stub_subprocess(monkeypatch, completed_or_factory) -> list[dict]:
    """Replace subprocess.run with a stub that records calls. Pass either
    a CompletedProcess (returned for every call) or a callable that takes
    the recorded-call dict and returns a CompletedProcess (or raises).
    """
    calls: list[dict] = []

    def fake_run(args, **kwargs):  # noqa: ANN001
        record = {"args": args, **kwargs}
        calls.append(record)
        if callable(completed_or_factory):
            return completed_or_factory(record)
        return completed_or_factory

    monkeypatch.setattr(claude_threads_remote.subprocess, "run", fake_run)
    return calls


def test_list_parses_jsonl_after_sentinel(monkeypatch, target) -> None:
    stdout = (
        "Welcome from rcfile\nMOTD line\n"
        f"{claude_threads_remote.SENTINEL}\n"
        f"{json.dumps(VALID_RECORD)}\n"
    )
    _stub_subprocess(monkeypatch, _make_completed(stdout=stdout))

    enumerator = RemoteClaudeThreadEnumerator(ENUMERATOR_PATH)
    results = asyncio.run(enumerator.list(target))

    assert [info.id for info in results] == [VALID_ID]
    assert results[0].cwd == "/srv/project"
    assert results[0].title == "Investigation"
    assert results[0].branch == "main"


def test_list_returns_empty_on_nonzero_exit(monkeypatch, target) -> None:
    _stub_subprocess(monkeypatch, _make_completed(stderr="boom", returncode=1))
    enumerator = RemoteClaudeThreadEnumerator(ENUMERATOR_PATH)
    assert asyncio.run(enumerator.list(target)) == []


def test_list_returns_empty_on_jq_missing(monkeypatch, target) -> None:
    _stub_subprocess(
        monkeypatch,
        _make_completed(stderr="jq is required on the remote host", returncode=64),
    )
    enumerator = RemoteClaudeThreadEnumerator(ENUMERATOR_PATH)
    assert asyncio.run(enumerator.list(target)) == []


def test_list_returns_empty_on_timeout(monkeypatch, target) -> None:
    def raise_timeout(_record):
        raise subprocess.TimeoutExpired(cmd=["ssh"], timeout=30.0)

    _stub_subprocess(monkeypatch, raise_timeout)
    enumerator = RemoteClaudeThreadEnumerator(ENUMERATOR_PATH)
    assert asyncio.run(enumerator.list(target)) == []


def test_list_returns_empty_on_missing_sentinel(monkeypatch, target) -> None:
    _stub_subprocess(
        monkeypatch, _make_completed(stdout=json.dumps(VALID_RECORD) + "\n")
    )
    enumerator = RemoteClaudeThreadEnumerator(ENUMERATOR_PATH)
    assert asyncio.run(enumerator.list(target)) == []


def test_list_returns_empty_when_ssh_binary_missing(monkeypatch, target) -> None:
    """A misconfigured ssh_bin must not bubble out as a 500. The argv
    builder calls _resolve_local_binary which raises FileNotFoundError;
    the enumerator should swallow it and return an empty list."""

    def boom(_binary: str) -> str:
        raise FileNotFoundError("binary not found on PATH: ssh")

    monkeypatch.setattr("waypoint.server_config._resolve_local_binary", boom)
    # subprocess.run must NEVER be called when argv resolution fails.
    monkeypatch.setattr(
        claude_threads_remote.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("subprocess.run must not be invoked"),
    )

    enumerator = RemoteClaudeThreadEnumerator(ENUMERATOR_PATH)
    assert asyncio.run(enumerator.list(target)) == []


def test_warnings_are_deduped_per_target_and_error_class(
    monkeypatch, target, caplog
) -> None:
    """A persistently noisy remote must not flood the log: the same
    (target, error-class) pair produces exactly one WARN per process."""
    _stub_subprocess(
        monkeypatch,
        _make_completed(stdout=json.dumps(VALID_RECORD) + "\n"),  # no sentinel
    )

    enumerator = RemoteClaudeThreadEnumerator(ENUMERATOR_PATH, ttl_seconds=60.0)

    async def list_three_times() -> None:
        for _ in range(3):
            await enumerator.list(target)
            enumerator.invalidate(target.id)

    with caplog.at_level("WARNING", logger="waypoint.claude_threads_remote"):
        asyncio.run(list_three_times())

    sentinel_warnings = [
        record
        for record in caplog.records
        if record.name == "waypoint.claude_threads_remote"
        and "missing sentinel" in getattr(record, "detail", "")
    ]
    assert len(sentinel_warnings) == 1


def test_cache_hit_within_ttl_skips_subprocess(monkeypatch, target) -> None:
    stdout = f"{claude_threads_remote.SENTINEL}\n{json.dumps(VALID_RECORD)}\n"
    calls = _stub_subprocess(monkeypatch, _make_completed(stdout=stdout))

    enumerator = RemoteClaudeThreadEnumerator(ENUMERATOR_PATH, ttl_seconds=60.0)

    async def fetch_twice() -> tuple[list, list]:
        first = await enumerator.list(target)
        second = await enumerator.list(target)
        return first, second

    first, second = asyncio.run(fetch_twice())

    assert len(first) == 1
    assert len(second) == 1
    assert len(calls) == 1


def test_invalidate_forces_refresh(monkeypatch, target) -> None:
    stdout = f"{claude_threads_remote.SENTINEL}\n{json.dumps(VALID_RECORD)}\n"
    calls = _stub_subprocess(monkeypatch, _make_completed(stdout=stdout))

    enumerator = RemoteClaudeThreadEnumerator(ENUMERATOR_PATH, ttl_seconds=60.0)

    async def fetch_invalidate_fetch() -> None:
        await enumerator.list(target)
        enumerator.invalidate(target.id)
        await enumerator.list(target)

    asyncio.run(fetch_invalidate_fetch())
    assert len(calls) == 2


def test_concurrent_calls_share_one_subprocess_invocation(monkeypatch, target) -> None:
    stdout = f"{claude_threads_remote.SENTINEL}\n{json.dumps(VALID_RECORD)}\n"
    barrier = asyncio.Event()
    calls: list[dict] = []

    def fake_run(args, **kwargs):  # noqa: ANN001
        # Block briefly so a second concurrent call has a chance to enter
        # the lock-protected critical section before this one resolves.
        calls.append({"args": args, **kwargs})
        return _make_completed(stdout=stdout)

    monkeypatch.setattr(claude_threads_remote.subprocess, "run", fake_run)

    enumerator = RemoteClaudeThreadEnumerator(ENUMERATOR_PATH, ttl_seconds=60.0)

    async def both() -> tuple[list, list]:
        # Kick the second call off slightly after the first so they
        # genuinely contend for the per-target lock.
        first_task = asyncio.create_task(enumerator.list(target))
        await asyncio.sleep(0)
        second_task = asyncio.create_task(enumerator.list(target))
        return await asyncio.gather(first_task, second_task)

    barrier.set()  # unused; kept for readability above
    results_a, results_b = asyncio.run(both())

    assert results_a and results_b
    # Cache hit on the second call after the lock releases means subprocess
    # is invoked exactly once, validating the per-target async lock.
    assert len(calls) == 1


def test_find_passes_thread_id_via_env(monkeypatch, target) -> None:
    stdout = f"{claude_threads_remote.SENTINEL}\n{json.dumps(VALID_RECORD)}\n"

    captured: list[tuple] = []

    def fake_build(target_arg, *, env=None):  # noqa: ANN001
        captured.append((target_arg.id, env))
        return ("ssh", "dest", "remote-cmd")

    monkeypatch.setattr(
        claude_threads_remote, "build_remote_thread_enumeration_args", fake_build
    )
    _stub_subprocess(monkeypatch, _make_completed(stdout=stdout))

    enumerator = RemoteClaudeThreadEnumerator(ENUMERATOR_PATH)
    info = asyncio.run(enumerator.find(target, VALID_ID))
    assert info is not None
    assert info.id == VALID_ID
    assert captured == [(target.id, {"WAYPOINT_THREAD_ID": VALID_ID})]


def test_find_returns_none_when_no_match(monkeypatch, target) -> None:
    stdout = f"{claude_threads_remote.SENTINEL}\n"
    _stub_subprocess(monkeypatch, _make_completed(stdout=stdout))
    enumerator = RemoteClaudeThreadEnumerator(ENUMERATOR_PATH)
    assert asyncio.run(enumerator.find(target, VALID_ID)) is None


# ---------------------------------------------------------------------------
# Integration test: run the actual helper script via local `bash -s`.
# Catches bash + jq edge cases the mocks can't (regex, jq filter, perl
# stat, sentinel emission, multimodal-content extraction).
# ---------------------------------------------------------------------------


def _make_user_record(*, cwd: str, text: str, branch: str | None = None) -> dict:
    rec: dict = {
        "type": "user",
        "cwd": cwd,
        "timestamp": "2026-04-29T15:47:09.826Z",
        "message": {"role": "user", "content": text},
    }
    if branch is not None:
        rec["gitBranch"] = branch
    return rec


@pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("jq") or not shutil.which("perl"),
    reason="bash + jq + perl required for integration test",
)
def test_helper_script_against_real_fixtures(tmp_path, monkeypatch) -> None:
    claude_root = tmp_path / "claude"
    projects = claude_root / "projects" / "-tmp-fixture"
    projects.mkdir(parents=True)
    transcript_a = projects / "11111111-1111-4111-8111-111111111111.jsonl"
    transcript_a.write_text(
        "\n".join(
            [
                json.dumps({"type": "queue-operation", "operation": "enqueue"}),
                json.dumps(
                    _make_user_record(
                        cwd="/tmp/fixture",
                        text="Investigate the cache miss",
                        branch="feature/cache",
                    )
                ),
                json.dumps({"type": "ai-title", "aiTitle": "Cache miss probe"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    transcript_b = projects / "22222222-2222-4222-8222-222222222222.jsonl"
    transcript_b.write_text(
        "\n".join(
            [
                json.dumps(
                    _make_user_record(cwd="/tmp/fixture", text="hello", branch="main")
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    # Bookkeeping-only — no user record. Must not appear in output.
    bookkeeping = projects / "33333333-3333-4333-8333-333333333333.jsonl"
    bookkeeping.write_text(
        json.dumps({"type": "queue-operation", "operation": "enqueue"}) + "\n",
        encoding="utf-8",
    )
    # Non-UUID basename — must be ignored.
    weird = projects / "not-a-uuid.jsonl"
    weird.write_text("garbage\n", encoding="utf-8")

    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(claude_root)
    completed = subprocess.run(
        ["bash", str(ENUMERATOR_PATH)],
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8")
    stdout = completed.stdout.decode("utf-8")
    assert claude_threads_remote.SENTINEL in stdout
    payload = stdout.split(claude_threads_remote.SENTINEL, 1)[1]
    records = [json.loads(line) for line in payload.splitlines() if line.strip()]
    ids = {r["id"] for r in records}
    assert ids == {
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    }
    by_id = {r["id"]: r for r in records}
    assert by_id["11111111-1111-4111-8111-111111111111"]["title"] == (
        "Cache miss probe"
    )
    assert by_id["11111111-1111-4111-8111-111111111111"]["branch"] == ("feature/cache")
    assert by_id["11111111-1111-4111-8111-111111111111"]["preview"] == (
        "Investigate the cache miss"
    )


@pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("jq") or not shutil.which("perl"),
    reason="bash + jq + perl required for integration test",
)
def test_helper_script_single_record_mode(tmp_path) -> None:
    claude_root = tmp_path / "claude"
    projects = claude_root / "projects" / "-tmp-fixture"
    projects.mkdir(parents=True)
    target_id = "44444444-4444-4444-8444-444444444444"
    other_id = "55555555-5555-4555-8555-555555555555"
    (projects / f"{target_id}.jsonl").write_text(
        json.dumps(_make_user_record(cwd="/tmp/fixture", text="target")) + "\n",
        encoding="utf-8",
    )
    (projects / f"{other_id}.jsonl").write_text(
        json.dumps(_make_user_record(cwd="/tmp/fixture", text="other")) + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(claude_root)
    env["WAYPOINT_THREAD_ID"] = target_id
    completed = subprocess.run(
        ["bash", str(ENUMERATOR_PATH)],
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8")
    stdout = completed.stdout.decode("utf-8")
    payload = stdout.split(claude_threads_remote.SENTINEL, 1)[1]
    records = [json.loads(line) for line in payload.splitlines() if line.strip()]
    assert [r["id"] for r in records] == [target_id]
