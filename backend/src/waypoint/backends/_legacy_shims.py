"""Default registry assembly.

Used to host legacy shim plugin classes during Steps 1-4 of the
refactor; now that every built-in backend has its own plugin module
this file is just the registration entry point. Kept under the
``_legacy_shims`` name for one release so any callers that imported
``build_default_registry`` from here keep resolving; the next pass
moves it to ``backends/registry.py``.
"""

from waypoint.backends.claude_code.plugin import ClaudeCodePlugin
from waypoint.backends.codex.plugin import CodexPlugin
from waypoint.backends.registry import BackendRegistry
from waypoint.backends.tmux.plugin import TmuxPlugin


def build_default_registry() -> BackendRegistry:
    registry = BackendRegistry()
    registry.register(ClaudeCodePlugin())
    registry.register(CodexPlugin())
    registry.register(TmuxPlugin())
    return registry
