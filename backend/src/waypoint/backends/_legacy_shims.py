"""Legacy plugin shims.

Step 1 of the refactor wires the registry without changing behaviour.
Each shim advertises today's per-backend transport adapter and a
capability descriptor that mirrors the hard-coded flags those adapters
expose. Steps 3-5 progressively replace these shims with real plugin
classes (e.g. ``ClaudeCodePlugin``, ``CodexPlugin``) that own
backend-specific lifecycle/control/discovery methods.
"""

from typing import TYPE_CHECKING

from waypoint.backends.capabilities import BackendCapabilities, ModelSource
from waypoint.backends.claude_code.plugin import ClaudeCodePlugin
from waypoint.backends.codex.plugin import CodexPlugin
from waypoint.backends.registry import BackendRegistry
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


class _LegacyTmuxPlugin:
    id = "tmux"
    transport_id = "tmux"
    label = "Tmux"
    capabilities = BackendCapabilities(
        is_structured=False,
        supports_resume=True,
        supports_set_model_inline=False,
        supports_set_effort_inline=False,
        supports_set_permission_mode_inline=False,
        supports_thread_discovery=False,
        supports_thread_import=False,
        supports_slash_compact=False,
        model_source=ModelSource.NONE,
        badges={"glyph": "T", "color": "#94a3b8"},
    )

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        from waypoint.transports.tmux import TmuxTransport

        return TmuxTransport(runtime)


def build_default_registry() -> BackendRegistry:
    registry = BackendRegistry()
    registry.register(ClaudeCodePlugin())
    registry.register(CodexPlugin())
    registry.register(_LegacyTmuxPlugin())
    return registry
