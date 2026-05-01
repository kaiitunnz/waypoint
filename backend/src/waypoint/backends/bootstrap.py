"""Default registry assembly — entry point for built-in plugins.

Adding a new backend means importing its plugin class and calling
``registry.register(...)`` here. External code should not need to
edit anything else under ``waypoint/`` to pick up a new backend.
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
