from typing import Any

import pytest

from waypoint.api import _pane_accepts_mouse
from waypoint.backends.bootstrap import build_default_registry
from waypoint.backends.capabilities import BackendCapabilities

UNLOCK = {"type": "hello", "terminal_protocol": 2, "interactive": True}


def _caps(transport: str) -> BackendCapabilities:
    registry = build_default_registry()
    return next(p.capabilities for p in registry.all() if p.id == transport)


def test_interactive_pane_always_accepts_mouse() -> None:
    assert _pane_accepts_mouse(_caps("tmux"), None)


def test_key_injection_pane_accepts_mouse_when_unlocked() -> None:
    assert _pane_accepts_mouse(_caps("claude_tty"), UNLOCK)


@pytest.mark.parametrize(
    "handshake",
    [
        None,
        {"type": "hello", "terminal_protocol": 2},
        {"type": "hello", "terminal_protocol": 2, "interactive": "true"},
        {"type": "hello", "terminal_protocol": 2, "interactive": 1},
        {"type": "resize", "cols": 80, "rows": 24, "interactive": True},
    ],
)
def test_key_injection_pane_stays_locked(handshake: Any) -> None:
    assert not _pane_accepts_mouse(_caps("claude_tty"), handshake)


def test_pane_without_key_injection_ignores_unlock() -> None:
    caps = _caps("claude_tty").model_copy(update={"terminal_key_injection": False})
    assert not _pane_accepts_mouse(caps, UNLOCK)
