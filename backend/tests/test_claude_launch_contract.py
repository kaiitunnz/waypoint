"""Unit tests for ClaudeCodePlugin's AgentLaunchContract methods.

These pin down the pane-wrapper launch knowledge ported off the
``backend == "claude_code"`` branches in ``backends/tmux/plugin.py``:
the ``--session-id`` pregeneration, the ``--session-id``/``--resume``
scrub on resume, and the ``~/.claude/projects/*/<uuid>.jsonl`` glob
(local + SSH).
"""

from pathlib import Path
from typing import cast

import pytest

from waypoint.backends.claude_code.plugin import ClaudeCodePlugin
from waypoint.launch_targets import SshLaunchTargetConfig


@pytest.fixture
def plugin() -> ClaudeCodePlugin:
    return ClaudeCodePlugin()


def test_launch_flags_maps_each_knob(plugin: ClaudeCodePlugin) -> None:
    assert plugin.launch_flags(
        model="opus", effort="high", permission_mode="acceptEdits"
    ) == [
        "--model",
        "opus",
        "--effort",
        "high",
        "--permission-mode",
        "acceptEdits",
    ]


def test_launch_flags_omits_unset(plugin: ClaudeCodePlugin) -> None:
    assert plugin.launch_flags(model="opus", effort=None, permission_mode=None) == [
        "--model",
        "opus",
    ]
    assert plugin.launch_flags(model=None, effort=None, permission_mode="plan") == [
        "--permission-mode",
        "plan",
    ]
    assert plugin.launch_flags() == []


def test_pregenerate_thread_id_is_a_uuid(plugin: ClaudeCodePlugin) -> None:
    import uuid

    first = plugin.pregenerate_thread_id()
    second = plugin.pregenerate_thread_id()
    assert first is not None and second is not None
    # Parses as a UUID and is fresh each call.
    uuid.UUID(first)
    assert first != second


def test_resume_args_swaps_session_id_for_resume(plugin: ClaudeCodePlugin) -> None:
    # The create form carries ``--session-id <uuid>``; resume wants
    # ``--resume <uuid>`` instead, with the rest preserved in order.
    args = plugin.resume_args(
        "abc-123",
        ["--session-id", "abc-123", "--model", "sonnet", "extra"],
    )
    assert args == ["--resume", "abc-123", "--model", "sonnet", "extra"]


def test_resume_args_does_not_double_inject_on_second_cycle(
    plugin: ClaudeCodePlugin,
) -> None:
    cycle1 = plugin.resume_args(
        "abc-123", ["--session-id", "abc-123", "--model", "sonnet"]
    )
    cycle2 = plugin.resume_args("abc-123", cycle1)
    assert cycle2 == ["--resume", "abc-123", "--model", "sonnet"]


def test_resume_args_scrubs_prior_resume_prefix(plugin: ClaudeCodePlugin) -> None:
    args = plugin.resume_args("abc-123", ["--resume", "old-id", "--model", "sonnet"])
    assert args == ["--resume", "abc-123", "--model", "sonnet"]


@pytest.mark.asyncio
async def test_conversation_exists_local(
    plugin: ClaudeCodePlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Claude stores conversations at
    # ~/.claude/projects/<cwd-with-/-replaced-by-->/<uuid>.jsonl. Pivot
    # HOME so the lookup hits our fixture tree.
    monkeypatch.setenv("HOME", str(tmp_path))
    cwd = "/Users/me/proj"
    project_dir = tmp_path / ".claude" / "projects" / cwd.replace("/", "-")
    project_dir.mkdir(parents=True)
    uuid_str = "00000000-0000-0000-0000-000000000001"
    (project_dir / f"{uuid_str}.jsonl").write_text("")
    assert await plugin.conversation_exists(uuid_str, cwd, None) is True
    # Missing uuid (user terminated before first message) → False, so the
    # caller falls back to a verbatim launch.
    assert (
        await plugin.conversation_exists(
            "ffffffff-ffff-ffff-ffff-ffffffffffff", cwd, None
        )
        is False
    )


@pytest.mark.asyncio
async def test_conversation_exists_local_no_projects_dir(
    plugin: ClaudeCodePlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert await plugin.conversation_exists("any-uuid", "/anywhere", None) is False


@pytest.mark.asyncio
async def test_conversation_exists_routes_through_ssh_when_launch_target_set(
    plugin: ClaudeCodePlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stub the SSH helper so the unit test never spawns ssh; the dashed-cwd
    # key can't be computed reliably for remote paths, so the remote branch
    # globs the project dirs and matches the uuid by filename.
    calls: list[str] = []

    async def fake_ssh_capture(self: SshLaunchTargetConfig, remote_cmd: str) -> str:
        calls.append(remote_cmd)
        if "00000000-0000-0000-0000-000000000001" in remote_cmd:
            return (
                "/remote/.claude/projects/-remote-proj/"
                "00000000-0000-0000-0000-000000000001.jsonl\n"
            )
        return ""

    monkeypatch.setattr(SshLaunchTargetConfig, "ssh_capture", fake_ssh_capture)
    target = SshLaunchTargetConfig(id="t", name="t", ssh_destination="remote")
    cwd = "/remote/proj"
    assert (
        await plugin.conversation_exists(
            "00000000-0000-0000-0000-000000000001", cwd, target
        )
        is True
    )
    assert (
        await plugin.conversation_exists(
            "ffffffff-ffff-ffff-ffff-ffffffffffff", cwd, target
        )
        is False
    )
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_conversation_exists_ssh_leaves_home_unquoted(
    plugin: ClaudeCodePlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: shlex.quote over the whole remote path single-quoted
    # ``$HOME`` and made the remote shell look for a literal ``$HOME``
    # directory. ``$HOME`` must stay outside any single-quoted span so the
    # remote shell expands it, while the uuid needle is still quoted.
    captured: list[str] = []

    async def fake_ssh_capture(self: SshLaunchTargetConfig, remote_cmd: str) -> str:
        captured.append(remote_cmd)
        return ""

    monkeypatch.setattr(SshLaunchTargetConfig, "ssh_capture", fake_ssh_capture)
    target = SshLaunchTargetConfig(id="t", name="t", ssh_destination="remote")
    await plugin.conversation_exists("abc-123", "/remote/proj", target)
    assert len(captured) == 1
    cmd = captured[0]
    assert "$HOME/.claude/projects/" in cmd
    assert "'$HOME'" not in cmd
    assert "abc-123.jsonl" in cmd


@pytest.mark.asyncio
async def test_capture_thread_id_is_noop(plugin: ClaudeCodePlugin) -> None:
    # Claude pregenerates its id via --session-id, so there is nothing to
    # discover post-launch — the inherited DefaultLaunchContract no-op.
    from datetime import UTC, datetime

    # Must not raise — the inherited no-op simply returns.
    await plugin.capture_thread_id(
        cast("object", None),  # type: ignore[arg-type]
        "sess-1",
        "/anywhere",
        datetime.now(UTC),
        None,
    )
