"""A claude_tty session takes keystrokes only into a drawn composer."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from waypoint.backends.claude_code.plugin import ClaudeCodePlugin
from waypoint.backends.claude_tty import transport as transport_mod
from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
from waypoint.backends.claude_tty.transport import ClaudeTtyTransport
from waypoint.transports import InputBlockedError

FIXTURES = Path(__file__).parent / "fixtures" / "claude_tty_pane"
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


async def test_drawn_composer_takes_the_message() -> None:
    ready = (FIXTURES / "ready.txt").read_text()
    transport, tmux, session = _transport(ready)
    await transport.send_input(session, "hello")
    assert tmux.type_input.await_count + tmux.send_input.await_count == 1
