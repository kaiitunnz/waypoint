"""A claude_tty approval names the call it answers, or says it can't.

Captured panes against fake transcripts on disk, through the tailer that raises
the ask and the transport that replies to it.
"""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_tty_transcripts import (
    FETCH_INPUT,
    FIXTURES,
    SUBAGENT_COMMAND,
    append,
    jsonl,
    result,
    session_tree,
    use,
    write_subagent,
)
from waypoint.backends.claude_tty.pending_calls import PendingCall
from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin, _TailerRun
from waypoint.backends.claude_tty.tailer import TranscriptTailer
from waypoint.backends.claude_tty.transport import ClaudeTtyTransport
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus

BASH = {"command": "mkdir -p probe_dir_one", "description": "Create probe dir"}
SUB_BASH = {
    "command": SUBAGENT_COMMAND,
    "description": "Create directory and echo marker text",
    "dangerouslyDisableSandbox": True,
}
DECOY = {"command": "echo main-start && sleep 60", "description": "Sleep"}
PLAN_NAME = "make-a-very-short-pure-nest-of-cdf35b79-a2ab-4155-9ae2-a402c6ec93f9.md"


def _screen(fixture: str) -> str:
    return (FIXTURES / fixture).read_text()


def _session(permission_mode: str | None = None) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id="s1",
        backend="claude_code",
        source=SessionSource.MANAGED,
        transport="claude_tty",
        title="t",
        cwd="/tmp/probe/repo",
        status=SessionStatus.RUNNING,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/structured.log",
        transport_state={"tmux_pane": "%0"},
        permission_mode=permission_mode,
    )


class Pane:
    def __init__(
        self, tmp_path: Path, fixture: str, permission_mode: str | None = None
    ) -> None:
        self.screen = _screen(fixture)
        source, self.main = session_tree(tmp_path)
        self.plugin = ClaudeTtyPlugin()
        self.session = _session(permission_mode)
        runtime = MagicMock()
        runtime.storage.get_session.return_value = self.session
        runtime.storage.list_approval_events.return_value = []
        runtime.tmux.capture_snapshot = AsyncMock(side_effect=lambda *args: self.screen)
        runtime.tmux.send_input = AsyncMock()
        runtime.tmux.send_bytes = AsyncMock()
        runtime._emit_adapter_event = AsyncMock()
        runtime._record_system_event = AsyncMock()
        runtime.update_session_fields = AsyncMock()
        self.runtime = runtime
        self.tailer = TranscriptTailer("s1", source, runtime, self.plugin)
        self.plugin._tailers["s1"] = _TailerRun(self.tailer, MagicMock())
        self.transport = ClaudeTtyTransport(runtime, self.plugin)

    async def poll(self, times: int = 2) -> None:
        for _ in range(times):
            await self.tailer.dialogs.poll()

    @property
    def asks(self) -> list[dict[str, Any]]:
        return [c.args[3] for c in self.runtime._emit_adapter_event.call_args_list]

    @property
    def notes(self) -> list[str]:
        return [c.args[1] for c in self.runtime._record_system_event.call_args_list]

    async def reply(self, decision: str = "approve") -> bool:
        return await self.transport.respond_to_approval(self.session, decision, None)

    def assert_no_keys(self) -> None:
        self.runtime.tmux.send_input.assert_not_called()
        self.runtime.tmux.send_bytes.assert_not_called()


def _named(ask: dict[str, Any]) -> tuple[bool, str | None]:
    return ask["call_verified"], ask.get("tool_use_id")


async def test_subagent_dialog_names_the_subagents_call_not_the_decoy(
    tmp_path: Path,
) -> None:
    pane = Pane(tmp_path, "approval_subagent_bash_decoy.txt")
    append(pane.main, use("decoy", "Bash", DECOY))
    write_subagent(
        pane.main, "a1", "general-purpose", jsonl(use("sub", "Bash", SUB_BASH))
    )

    await pane.poll()

    (ask,) = pane.asks
    assert _named(ask) == (True, "sub")
    assert ask["tool_name"] == "Bash"
    assert ask["tool_input"] == SUB_BASH
    assert ask["subagent"] == "general-purpose"
    assert ask["interaction"]["body"].endswith(SUB_BASH["command"])


async def test_subagent_dialog_without_its_record_is_unverified(tmp_path: Path) -> None:
    pane = Pane(tmp_path, "approval_subagent_bash_decoy.txt")
    # The main thread runs the very same command.
    append(pane.main, use("main", "Bash", SUB_BASH))

    await pane.poll()

    (ask,) = pane.asks
    assert _named(ask) == (False, None)
    assert ask["tool_input"] == {"command": SUB_BASH["command"]}
    assert ask["subagent"] == "general-purpose"


async def test_wrapped_command_carries_the_full_input(tmp_path: Path) -> None:
    pane = Pane(tmp_path, "approval_bash_wrapped_boxed.txt")
    command = (
        "touch probe_wrapped_alpha_beta_gamma_delta_epsilon_zeta_eta_theta_iota_"
        'kappa.txt && echo "this is a long probe line that must wrap in the dialog '
        "box because it keeps going and going and going past one hundred and twenty "
        'columns for sure"'
    )
    tool_input = {
        "command": command,
        "description": "Create probe file and echo long line",
        "timeout": 90000,
    }
    append(pane.main, use("wrapped", "Bash", tool_input))

    await pane.poll()

    (ask,) = pane.asks
    assert _named(ask) == (True, "wrapped")
    assert ask["tool_input"] == tool_input


async def test_calls_that_cannot_be_told_apart_stay_unverified(tmp_path: Path) -> None:
    pane = Pane(tmp_path, "approval_subagent_bash_decoy.txt")
    write_subagent(
        pane.main, "a1", "general-purpose", jsonl(use("one", "Bash", SUB_BASH))
    )
    sandboxed = {**SUB_BASH, "dangerouslyDisableSandbox": False}
    write_subagent(
        pane.main, "a2", "general-purpose", jsonl(use("two", "Bash", sandboxed))
    )

    await pane.poll(8)

    (ask,) = pane.asks
    assert _named(ask) == (False, None)


async def test_late_record_raises_the_card_again_verified(tmp_path: Path) -> None:
    pane = Pane(tmp_path, "approval_bash_boxed.txt")
    await pane.poll()
    (first,) = pane.asks
    assert _named(first) == (False, None)

    append(pane.main, use("late", "Bash", BASH))
    await pane.poll(1)

    assert _named(pane.asks[-1]) == (True, "late")
    assert pane.notes == ["Approval request updated"]


async def test_second_identical_call_gets_its_own_ask_after_a_reply(
    tmp_path: Path,
) -> None:
    pane = Pane(tmp_path, "approval_bash_boxed.txt")
    append(pane.main, use("first", "Bash", BASH))
    await pane.poll()
    assert await pane.reply() is True
    pane.runtime.tmux.send_input.assert_called_once_with("%0", "1", submit=False)

    # The next call's dialog is drawn before a poll sees the first one gone.
    append(pane.main, result("first"), use("second", "Bash", BASH, msg="msg-2"))
    await pane.poll(3)

    assert [_named(ask) for ask in pane.asks] == [(True, "first"), (True, "second")]


async def test_lingering_dialog_is_not_raised_again_at_once(tmp_path: Path) -> None:
    pane = Pane(tmp_path, "approval_bash_boxed.txt")
    append(pane.main, use("first", "Bash", BASH))
    await pane.poll()
    assert await pane.reply() is True

    await pane.poll(2)
    assert len(pane.asks) == 1

    # Still drawn polls later: the keystroke did not take.
    await pane.poll(4)
    assert [_named(ask) for ask in pane.asks] == [(True, "first"), (True, "first")]


async def test_second_identical_call_answered_in_the_terminal(tmp_path: Path) -> None:
    pane = Pane(tmp_path, "approval_bash_boxed.txt")
    append(pane.main, use("first", "Bash", BASH))
    await pane.poll()

    append(pane.main, result("first"), use("second", "Bash", BASH, msg="msg-2"))
    await pane.poll(6)

    assert [_named(ask) for ask in pane.asks] == [(True, "first"), (True, "second")]
    assert pane.notes == ["Approval dialog closed in the terminal"]


async def test_reply_is_refused_once_the_dialog_changed(tmp_path: Path) -> None:
    pane = Pane(tmp_path, "approval_bash_boxed.txt")
    append(pane.main, use("first", "Bash", BASH))
    await pane.poll()

    pane.screen = _screen("approval_subagent_bash_decoy.txt")
    assert await pane.reply() is False

    pane.assert_no_keys()
    assert pane.notes == ["Reply not sent: the terminal no longer shows this request"]
    await pane.poll()
    assert pane.asks[-1]["tool_input"] == {"command": SUB_BASH["command"]}


async def test_reply_is_refused_once_the_named_call_resolved(tmp_path: Path) -> None:
    pane = Pane(tmp_path, "approval_bash_boxed.txt")
    append(pane.main, use("first", "Bash", BASH))
    await pane.poll()

    # The same words on screen, asked by a call the ask does not name.
    unsandboxed = {**BASH, "dangerouslyDisableSandbox": True}
    append(pane.main, result("first"), use("second", "Bash", unsandboxed, msg="m2"))
    assert await pane.reply() is False

    pane.assert_no_keys()
    await pane.poll()
    assert pane.asks[-1]["tool_input"] == unsandboxed


async def test_redrawn_options_move_the_keys_a_reply_presses(tmp_path: Path) -> None:
    pane = Pane(tmp_path, "approval_bash_boxed.txt")
    append(pane.main, use("first", "Bash", BASH))
    full = pane.screen
    # First drawn with only Yes and No.
    pane.screen = "\n".join(
        line.replace("4. No", "2. No")
        for line in full.splitlines()
        if not line.startswith(("   2. Yes", "   3. Yes"))
    )
    await pane.poll()

    pane.screen = full
    await pane.poll()
    assert len(pane.asks) == 1
    assert await pane.reply("decline") is True
    pane.runtime.tmux.send_input.assert_called_once_with("%0", "4", submit=False)


async def test_reply_is_refused_when_the_pane_moves_during_its_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pane = Pane(tmp_path, "approval_bash_boxed.txt")
    append(pane.main, use("first", "Bash", BASH))
    await pane.poll()
    reads = pane.tailer.dialogs._calls
    candidates = reads.candidates

    def move_on(agent: str | None) -> list[PendingCall]:
        found = candidates(agent)
        pane.screen = _screen("approval_subagent_bash_decoy.txt")
        return found

    monkeypatch.setattr(reads, "candidates", move_on)

    assert await pane.reply() is False
    pane.assert_no_keys()


async def test_refusal_leaves_a_card_raised_during_the_reply_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pane = Pane(tmp_path, "approval_bash_boxed.txt")
    append(pane.main, use("first", "Bash", BASH))
    await pane.poll()
    reads = pane.tailer.dialogs._calls
    candidates = reads.candidates
    newer = replace(pane.plugin._pending_approvals["s1"], approval_id="newer")

    def replaced(agent: str | None) -> list[PendingCall]:
        pane.plugin._pending_approvals["s1"] = newer
        pane.screen = _screen("ready.txt")
        return candidates(agent)

    monkeypatch.setattr(reads, "candidates", replaced)

    assert await pane.reply() is False
    pane.assert_no_keys()
    assert pane.notes == []
    assert pane.plugin._pending_approvals["s1"] is newer


async def test_reply_in_flight_leaves_the_card_alone(tmp_path: Path) -> None:
    pane = Pane(tmp_path, "approval_bash_boxed.txt")
    append(pane.main, use("first", "Bash", BASH))
    await pane.poll()
    pane.plugin._pending_approvals["s1"].replying = True

    pane.screen = _screen("ready.txt")
    await pane.poll(6)

    assert len(pane.asks) == 1
    assert pane.notes == []
    assert await pane.reply() is False
    pane.assert_no_keys()


async def test_reply_to_an_unverified_card_needs_the_same_dialog(
    tmp_path: Path,
) -> None:
    pane = Pane(tmp_path, "approval_bash_boxed.txt")
    await pane.poll()
    pane.screen = pane.screen.replace("probe_dir_one", "probe_dir_two")
    assert await pane.reply("decline") is False
    pane.assert_no_keys()


async def test_footerless_fetch_dialog_raises_an_ask(tmp_path: Path) -> None:
    pane = Pane(tmp_path, "approval_fetch_subagent.txt")
    write_subagent(
        pane.main, "a1", "web-fetch", jsonl(use("fetch", "WebFetch", FETCH_INPUT))
    )

    await pane.poll()

    (ask,) = pane.asks
    assert _named(ask) == (True, "fetch")
    assert ask["interaction"]["title"] == "Approve WebFetch"
    assert await pane.reply("decline") is True
    pane.runtime.tmux.send_input.assert_called_once_with("%0", "3", submit=False)


@pytest.fixture
def plan_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    path = tmp_path / "home" / ".claude" / "plans" / PLAN_NAME
    path.parent.mkdir(parents=True)
    path.write_text("- step one\n")
    return path


async def test_plan_card_carries_the_file_not_a_rebuild(
    tmp_path: Path, plan_file: Path
) -> None:
    pane = Pane(tmp_path, "plan_approval_file_footer.txt", permission_mode="plan")
    # The transcript's write and an Edit Claude rejected would rebuild other text.
    append(
        pane.main,
        use("w", "Write", {"file_path": str(plan_file), "content": "- step one\n"}),
        result("w"),
        use(
            "e",
            "Edit",
            {"file_path": str(plan_file), "old_string": "step", "new_string": "STEP"},
            msg="msg-2",
        ),
        result("e"),
    )

    await pane.poll()

    (ask,) = pane.asks
    assert ask["call_verified"] is True
    assert ask["tool_input"]["plan"] == "- step one\n"
    assert ask["interaction"]["body"] == "- step one\n"


async def test_changed_plan_file_raises_the_card_again(
    tmp_path: Path, plan_file: Path
) -> None:
    pane = Pane(tmp_path, "plan_approval_file_footer.txt", permission_mode="plan")
    await pane.poll()

    plan_file.write_text("- step two\n")
    assert await pane.reply() is False
    pane.assert_no_keys()
    await pane.poll()
    assert pane.asks[-1]["tool_input"]["plan"] == "- step two\n"
    assert await pane.reply() is True
    pane.runtime.tmux.send_input.assert_called_once_with("%0", "2", submit=False)

    # Edited again while the dialog is up: the card follows the file.
    plan_file.write_text("- step three\n")
    await pane.poll(6)
    assert pane.asks[-1]["tool_input"]["plan"] == "- step three\n"


async def test_unreadable_plan_keeps_the_card_it_already_has(
    tmp_path: Path, plan_file: Path
) -> None:
    pane = Pane(tmp_path, "plan_approval_file_footer.txt", permission_mode="plan")
    await pane.poll()
    plan_file.unlink()

    await pane.poll(3)
    assert len(pane.asks) == 1
    assert await pane.reply() is False
    pane.assert_no_keys()
