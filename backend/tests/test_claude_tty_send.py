"""A claude_tty session takes keystrokes only into a drawn composer."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_tty_transcripts import FIXTURES
from waypoint.backends.claude_code.plugin import ClaudeCodePlugin
from waypoint.backends.claude_tty import transport as transport_mod
from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
from waypoint.backends.claude_tty.transport import ClaudeTtyTransport
from waypoint.transports import InputBlockedError

RULE = "─" * 100
# Popups nothing classifies, in place of the composer.
UNKNOWN_POPUPS = [
    f"❯ earlier prompt\n\n{RULE}\n What do you want to do?\n\n ❯ 1. Stop\n   2. Go\n",
    f"❯ earlier prompt\n\n{RULE}\n Pick one\n\n ❯ This\n   That\n",
    "booting",
]


def _transport(screen: str) -> tuple[ClaudeTtyTransport, MagicMock, MagicMock]:
    tmux = MagicMock()
    for name in ("capture_snapshot", "send_input", "type_input", "submit", "resume"):
        setattr(tmux, name, AsyncMock())
    tmux.capture_snapshot.return_value = screen
    runtime = MagicMock()
    runtime.tmux = tmux
    # A claude_tty session's backend is the agent, which reads its composer.
    runtime.registry.get.return_value = ClaudeCodePlugin()
    session = MagicMock()
    session.id = "s1"
    session.backend = "claude_code"
    session.transport_state = {"tmux_pane": "%0"}
    return ClaudeTtyTransport(runtime, ClaudeTtyPlugin()), tmux, session


@pytest.fixture(autouse=True)
def _short_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport_mod.asyncio, "sleep", AsyncMock())


def _assert_untouched(tmux: MagicMock) -> None:
    for name in ("send_input", "type_input", "submit", "resume"):
        getattr(tmux, name).assert_not_called()


@pytest.mark.parametrize(
    "screen",
    [
        *UNKNOWN_POPUPS,
        (FIXTURES / "approval_fetch_subagent.txt").read_text(),
        (FIXTURES / "approval_mcp.txt").read_text(),
        (FIXTURES / "plan_approval.txt").read_text(),
    ],
    ids=["numbered", "unnumbered", "booting", "fetch", "mcp", "plan"],
)
async def test_nothing_is_typed_without_the_composer(screen: str) -> None:
    transport, tmux, session = _transport(screen)
    with pytest.raises(InputBlockedError):
        await transport.send_input(session, "hello")
    with pytest.raises(InputBlockedError):
        await transport.resume(session)
    _assert_untouched(tmux)


async def test_no_enter_into_a_popup_that_opens_after_the_first() -> None:
    ready = (FIXTURES / "ready.txt").read_text()
    transport, tmux, session = _transport(ready)

    async def open_popup(*_: Any) -> None:
        tmux.capture_snapshot.return_value = UNKNOWN_POPUPS[1]

    tmux.submit.side_effect = open_popup
    await transport.send_input(session, "hello")
    assert tmux.submit.await_count == 1


async def test_drawn_composer_takes_the_message() -> None:
    ready = (FIXTURES / "ready.txt").read_text()
    transport, tmux, session = _transport(ready)
    await transport.send_input(session, "hello")
    assert tmux.type_input.await_count + tmux.send_input.await_count == 1


@pytest.mark.parametrize("holds_input", [True, False])
async def test_resume_supersedes_an_interrupt_only_when_it_submits(
    holds_input: bool,
) -> None:
    ready = (FIXTURES / "ready.txt").read_text()
    # The prompt is followed by a no-break space.
    typed = ready.replace('❯\xa0Try "fix typecheck errors"', "❯\xa0msg")
    transport, _tmux, session = _transport(typed if holds_input else ready)
    expected = MagicMock()
    transport._plugin.expected_interrupt = MagicMock(return_value=expected)  # type: ignore[method-assign]

    assert await transport.resume(session) is holds_input
    assert expected.supersede.call_count == int(holds_input)


async def test_the_submit_budget_outlasts_image_ingestion() -> None:
    # The TUI drops Enters while it turns pasted image paths into chips.
    ready = (FIXTURES / "ready.txt").read_text()
    # The prompt is followed by a no-break space.
    holding = ready.replace('❯\xa0Try "fix typecheck errors"', "❯\xa0[Image #1] msg")
    assert holding != ready
    transport, tmux, session = _transport(holding)

    async def clear_late(*_: Any) -> None:
        if tmux.submit.await_count == 20:
            tmux.capture_snapshot.return_value = ready

    tmux.submit.side_effect = clear_late
    await transport.send_input(session, "msg")
    assert tmux.submit.await_count == 20
