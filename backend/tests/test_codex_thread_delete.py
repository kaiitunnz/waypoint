"""Unit tests for CodexPlugin.delete_thread (local and remote stores)."""

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from waypoint.backends.codex.plugin import CodexPlugin
from waypoint.launch_targets import SshLaunchTargetConfig

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

# The local delete path never touches the runtime; a typed null keeps mypy
# happy without standing up a real SessionRuntime.
_NO_RUNTIME = cast("SessionRuntime", None)


def _runtime_resolving(target: SshLaunchTargetConfig | None) -> "SessionRuntime":
    """A minimal runtime whose ``_resolve_launch_target`` yields ``target``."""

    class _Runtime:
        def _resolve_launch_target(
            self, _launch_target_id: str, _backend: str
        ) -> SshLaunchTargetConfig | None:
            return target

    return cast("SessionRuntime", _Runtime())


@pytest.fixture
def plugin() -> CodexPlugin:
    return CodexPlugin()


def _make_rollout(home: Path, thread_id: str) -> Path:
    day = home / "sessions" / "2026" / "06" / "30"
    day.mkdir(parents=True, exist_ok=True)
    rollout = day / f"rollout-2026-06-30T12-00-00-{thread_id}.jsonl"
    rollout.write_text("{}\n")
    return rollout


@pytest.mark.asyncio
async def test_delete_thread_local_removes_matching_rollout(
    plugin: CodexPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    thread_id = str(uuid.uuid4())
    rollout = _make_rollout(tmp_path, thread_id)

    assert await plugin.delete_thread(_NO_RUNTIME, thread_id) is True
    assert not rollout.exists()


@pytest.mark.asyncio
async def test_delete_thread_local_missing_returns_false(
    plugin: CodexPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    kept = _make_rollout(tmp_path, str(uuid.uuid4()))

    assert await plugin.delete_thread(_NO_RUNTIME, str(uuid.uuid4())) is False
    assert kept.exists()


@pytest.mark.asyncio
async def test_delete_thread_rejects_non_uuid(
    plugin: CodexPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    kept = _make_rollout(tmp_path, str(uuid.uuid4()))

    assert await plugin.delete_thread(_NO_RUNTIME, "../../etc/passwd") is False
    assert await plugin.delete_thread(_NO_RUNTIME, "not-a-uuid") is False
    assert kept.exists()


@pytest.mark.asyncio
async def test_delete_thread_remote_routes_through_ssh(
    plugin: CodexPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = str(uuid.uuid4())
    calls: list[str] = []

    async def fake_ssh_capture(self: SshLaunchTargetConfig, remote_cmd: str) -> str:
        calls.append(remote_cmd)
        return "deleted\n" if f"-{thread_id}.jsonl" in remote_cmd else ""

    monkeypatch.setattr(SshLaunchTargetConfig, "ssh_capture", fake_ssh_capture)
    target = SshLaunchTargetConfig(id="t", name="t", ssh_destination="remote")

    assert (
        await plugin.delete_thread(_runtime_resolving(target), thread_id, "t") is True
    )
    assert len(calls) == 1
    cmd = calls[0]
    assert f"rollout-*-{thread_id}.jsonl" in cmd
    assert "$HOME/.codex" in cmd
    assert "rm -f" in cmd


@pytest.mark.asyncio
async def test_delete_thread_remote_reports_false_when_nothing_removed(
    plugin: CodexPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_ssh_capture(self: SshLaunchTargetConfig, _remote_cmd: str) -> str:
        return ""

    monkeypatch.setattr(SshLaunchTargetConfig, "ssh_capture", fake_ssh_capture)
    target = SshLaunchTargetConfig(id="t", name="t", ssh_destination="remote")

    deleted = await plugin.delete_thread(
        _runtime_resolving(target), str(uuid.uuid4()), "t"
    )
    assert deleted is False


@pytest.mark.asyncio
async def test_delete_thread_remote_rejects_non_uuid_without_ssh(
    plugin: CodexPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A malformed id must be refused before it can reach the remote shell.
    calls: list[str] = []

    async def fake_ssh_capture(self: SshLaunchTargetConfig, remote_cmd: str) -> str:
        calls.append(remote_cmd)
        return "deleted\n"

    monkeypatch.setattr(SshLaunchTargetConfig, "ssh_capture", fake_ssh_capture)
    target = SshLaunchTargetConfig(id="t", name="t", ssh_destination="remote")

    assert (
        await plugin.delete_thread(_runtime_resolving(target), "../../etc/passwd", "t")
        is False
    )
    assert calls == []
