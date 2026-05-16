"""Unit tests for TmuxPlugin's terminate/resume helpers.

The end-to-end lifecycle is covered by the integration paths in
``test_runtime.py``; these tests pin down the pure-function pieces that
decide how reconnect rebuilds an inner CLI's command line and how the
Codex rollout-file watcher extracts a thread id from a filename.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoint.backends.tmux.plugin import TmuxPlugin


@pytest.fixture
def plugin() -> TmuxPlugin:
    return TmuxPlugin()


def test_resume_args_claude_swaps_session_id_for_resume(plugin: TmuxPlugin) -> None:
    # ``create_session`` prepends ``--session-id <uuid>`` for claude
    # launches. On reconnect we want ``--resume <uuid>`` instead — the
    # two flags are mutually exclusive — with the rest of the user's
    # original args preserved in order.
    args = plugin._resume_args(
        "claude_code",
        "abc-123",
        ["--session-id", "abc-123", "--model", "sonnet", "extra"],
    )
    assert args == ["--resume", "abc-123", "--model", "sonnet", "extra"]


def test_resume_args_claude_without_thread_id_is_verbatim(plugin: TmuxPlugin) -> None:
    # No thread captured → fall through to a fresh launch with the
    # stored args. (Shouldn't happen for claude in practice since
    # create_session always generates one, but the contract is robust.)
    args = plugin._resume_args("claude_code", None, ["--model", "sonnet"])
    assert args == ["--model", "sonnet"]


def test_resume_args_codex_prepends_resume_subcommand(plugin: TmuxPlugin) -> None:
    # Codex's interactive CLI exposes resume only via the ``resume``
    # subcommand; arguments after the id are preserved.
    args = plugin._resume_args("codex", "abc-123", ["--model", "o3"])
    assert args == ["resume", "abc-123", "--model", "o3"]


def test_resume_args_codex_without_thread_id_is_verbatim(plugin: TmuxPlugin) -> None:
    # The watcher only captures an id once Codex has written its
    # rollout file. If the user disconnected before sending any input,
    # there's no conversation to resume — replay the launch verbatim.
    args = plugin._resume_args("codex", None, ["--model", "o3"])
    assert args == ["--model", "o3"]


def test_resume_args_unknown_backend_is_verbatim(plugin: TmuxPlugin) -> None:
    args = plugin._resume_args("opencode", "abc-123", ["--foo"])
    assert args == ["--foo"]


def test_codex_rollout_matches_cwd_top_level(
    plugin: TmuxPlugin, tmp_path: Path
) -> None:
    # SessionMeta with cwd at the top level of the JSONL header.
    log = tmp_path / "rollout-2026-05-16T12-00-00-abc.jsonl"
    log.write_text(
        json.dumps({"id": "abc", "cwd": "/Users/me/proj", "timestamp": "..."}) + "\n"
    )
    assert plugin._codex_rollout_matches_cwd(log, "/Users/me/proj") is True
    assert plugin._codex_rollout_matches_cwd(log, "/Users/me/other") is False


def test_codex_rollout_matches_cwd_nested_payload(
    plugin: TmuxPlugin, tmp_path: Path
) -> None:
    # Newer rollout schemas wrap SessionMeta under "payload".
    log = tmp_path / "rollout-2026-05-16T12-00-00-abc.jsonl"
    log.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "abc", "cwd": "/Users/me/proj"},
            }
        )
        + "\n"
    )
    assert plugin._codex_rollout_matches_cwd(log, "/Users/me/proj") is True


def test_codex_rollout_matches_cwd_missing_or_bad_json(
    plugin: TmuxPlugin, tmp_path: Path
) -> None:
    log = tmp_path / "garbage.jsonl"
    log.write_text("not json\n")
    assert plugin._codex_rollout_matches_cwd(log, "/anywhere") is False
    missing = tmp_path / "nope.jsonl"
    assert plugin._codex_rollout_matches_cwd(missing, "/anywhere") is False


@pytest.mark.asyncio
async def test_capture_codex_thread_id_pulls_uuid_from_filename(
    plugin: TmuxPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drop a rollout file in the mocked CODEX_HOME and confirm the
    watcher captures the UUID into transport_state via the runtime's
    storage."""
    # Stub a minimal runtime with just the storage surface the watcher
    # touches. The real SessionRuntime is too heavy for a pure unit
    # test.
    captured: dict[str, object] = {}

    class _Session:
        transport_state: dict[str, object] = {}

    class _Storage:
        def get_session(self, sid: str) -> _Session:
            return _Session()

        def update_session(self, sid: str, **fields: object) -> None:
            captured[sid] = fields

    class _Runtime:
        storage = _Storage()
        _tmux_thread_id_watchers: dict[str, object] = {}

    cwd = "/Users/me/proj"
    codex_home = tmp_path / "codex"
    sessions_dir = codex_home / "sessions" / "2026" / "05" / "16"
    sessions_dir.mkdir(parents=True)
    uuid_str = "01234567-89ab-cdef-0123-456789abcdef"
    rollout = sessions_dir / f"rollout-2026-05-16T12-00-00-{uuid_str}.jsonl"
    rollout.write_text(json.dumps({"id": uuid_str, "cwd": cwd}) + "\n")

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    # Tight loop so the test doesn't pay the 2 s default poll interval.
    monkeypatch.setattr(
        "waypoint.backends.tmux.plugin.asyncio.sleep",
        _fake_sleep,
    )
    await plugin._capture_codex_thread_id(
        _Runtime(),  # type: ignore[arg-type]
        "sess-1",
        cwd,
        datetime.now(UTC),
    )
    assert captured["sess-1"] == {
        "transport_state": {"thread_id": uuid_str},
    }


async def _fake_sleep(_seconds: float) -> None:
    # Yield control once so the watcher's loop runs to next iteration
    # without burning wall-clock time.
    return None
