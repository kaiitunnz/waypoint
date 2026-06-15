"""Unit tests for CodexPlugin launch-contract helpers."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from waypoint.backends.codex.plugin import CodexPlugin


@pytest.fixture
def plugin() -> CodexPlugin:
    return CodexPlugin()


def test_launch_flags_codex_maps_model_and_permission_modes(
    plugin: CodexPlugin,
) -> None:
    assert plugin.launch_flags(
        model="gpt-5", effort="high", permission_mode="default"
    ) == ["-m", "gpt-5", "-a", "on-request", "-s", "workspace-write"]
    assert plugin.launch_flags(
        model=None, effort=None, permission_mode="full_access"
    ) == ["--dangerously-bypass-approvals-and-sandbox"]
    assert plugin.launch_flags(model=None, effort="high", permission_mode="plan") == []


def test_resume_args_codex_prepends_resume_subcommand(plugin: CodexPlugin) -> None:
    assert plugin.resume_args("abc-123", ["--model", "o3"]) == [
        "resume",
        "abc-123",
        "--model",
        "o3",
    ]
    assert plugin.resume_args("abc-123", ["resume", "old", "--model", "o3"]) == [
        "resume",
        "abc-123",
        "--model",
        "o3",
    ]


def test_pregenerate_thread_id_codex_returns_none(plugin: CodexPlugin) -> None:
    assert plugin.pregenerate_thread_id() is None


def test_codex_rollout_uuid_regex(plugin: CodexPlugin) -> None:
    filename = (
        "rollout-2026-05-16T12-00-00-" "01234567-89ab-cdef-0123-456789abcdef.jsonl"
    )
    match = plugin._ROLLOUT_UUID_RE.search(filename)
    assert match is not None
    assert match.group(1) == "01234567-89ab-cdef-0123-456789abcdef"
    assert plugin._ROLLOUT_UUID_RE.search("rollout-nope.jsonl") is None


def test_codex_rollout_matches_cwd_top_level(
    plugin: CodexPlugin, tmp_path: Path
) -> None:
    log = tmp_path / "rollout-2026-05-16T12-00-00-abc.jsonl"
    log.write_text(
        json.dumps({"id": "abc", "cwd": "/Users/me/proj", "timestamp": "..."}) + "\n"
    )

    assert plugin._codex_rollout_matches_cwd(log, "/Users/me/proj") is True
    assert plugin._codex_rollout_matches_cwd(log, "/Users/me/other") is False


def test_codex_rollout_matches_cwd_nested_payload(
    plugin: CodexPlugin, tmp_path: Path
) -> None:
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
    plugin: CodexPlugin, tmp_path: Path
) -> None:
    log = tmp_path / "garbage.jsonl"
    log.write_text("not json\n")

    assert plugin._codex_rollout_matches_cwd(log, "/anywhere") is False
    assert (
        plugin._codex_rollout_matches_cwd(tmp_path / "missing.jsonl", "/anywhere")
        is False
    )


def test_find_codex_thread_id_local_filters_by_cwd_and_since(
    plugin: CodexPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex"
    sessions_dir = codex_home / "sessions" / "2026" / "05" / "16"
    sessions_dir.mkdir(parents=True)
    older_uuid = "11111111-1111-1111-1111-111111111111"
    newer_uuid = "22222222-2222-2222-2222-222222222222"
    ignored_uuid = "33333333-3333-3333-3333-333333333333"
    older = sessions_dir / f"rollout-2026-05-16T12-00-00-{older_uuid}.jsonl"
    newer = sessions_dir / f"rollout-2026-05-16T12-00-01-{newer_uuid}.jsonl"
    ignored = sessions_dir / f"rollout-2026-05-16T12-00-02-{ignored_uuid}.jsonl"
    older.write_text(json.dumps({"cwd": "/repo"}) + "\n")
    newer.write_text(json.dumps({"payload": {"cwd": "/repo"}}) + "\n")
    ignored.write_text(json.dumps({"cwd": "/other"}) + "\n")
    older_ts = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC).timestamp()
    newer_ts = datetime(2026, 5, 16, 12, 0, 1, tzinfo=UTC).timestamp()
    ignored_ts = datetime(2026, 5, 16, 12, 0, 2, tzinfo=UTC).timestamp()
    os.utime(older, (older_ts, older_ts))
    os.utime(newer, (newer_ts, newer_ts))
    os.utime(ignored, (ignored_ts, ignored_ts))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    found = plugin._find_codex_thread_id_local(
        "/repo", datetime(2026, 5, 16, 11, 59, 59, tzinfo=UTC)
    )

    assert found == newer_uuid


@pytest.mark.asyncio
async def test_find_codex_thread_id_remote_filters_by_cwd(
    plugin: CodexPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    uuid_a = "11111111-1111-1111-1111-111111111111"
    uuid_b = "22222222-2222-2222-2222-222222222222"
    payload = (
        f"/r/.codex/sessions/2099/01/01/rollout-2099-01-01T00-00-00-{uuid_a}.jsonl"
        "\t"
        f'{{"id": "{uuid_a}", "cwd": "/other/proj"}}\n'
        f"/r/.codex/sessions/2099/01/01/rollout-2099-01-01T00-00-01-{uuid_b}.jsonl"
        "\t"
        f'{{"id": "{uuid_b}", "cwd": "/remote/proj"}}\n'
    )

    async def fake_ssh_capture(_target: object, _remote_cmd: str) -> str:
        return payload

    monkeypatch.setattr(CodexPlugin, "_ssh_capture", staticmethod(fake_ssh_capture))

    found = await plugin._find_codex_thread_id_remote(
        "/remote/proj",
        datetime(2026, 1, 1, tzinfo=UTC),
        object(),  # type: ignore[arg-type]
    )

    assert found == uuid_b


@pytest.mark.asyncio
async def test_capture_thread_id_pulls_uuid_from_filename(
    plugin: CodexPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class _Session:
        transport_state: dict[str, object] = {}

    class _Storage:
        def get_session(self, _sid: str) -> _Session:
            return _Session()

        def update_session(self, sid: str, **fields: object) -> None:
            captured[sid] = fields

    class _Runtime:
        storage = _Storage()
        _tmux_thread_id_watchers: dict[str, Any] = {}

    cwd = "/Users/me/proj"
    codex_home = tmp_path / "codex"
    sessions_dir = codex_home / "sessions" / "2026" / "05" / "16"
    sessions_dir.mkdir(parents=True)
    uuid_str = "01234567-89ab-cdef-0123-456789abcdef"
    rollout = sessions_dir / f"rollout-2026-05-16T12-00-00-{uuid_str}.jsonl"
    rollout.write_text(json.dumps({"id": uuid_str, "cwd": cwd}) + "\n")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("waypoint.backends.codex.plugin.asyncio.sleep", fake_sleep)

    await plugin.capture_thread_id(
        _Runtime(),  # type: ignore[arg-type]
        "sess-1",
        cwd,
        datetime.now(UTC),
        None,
    )

    assert captured["sess-1"] == {"transport_state": {"thread_id": uuid_str}}
