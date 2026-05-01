"""Legacy plugin shims.

Step 1 of the refactor wires the registry without changing behaviour.
Each shim advertises today's per-backend transport adapter and a
capability descriptor that mirrors the hard-coded flags those adapters
expose. Steps 3-5 progressively replace these shims with real plugin
classes (e.g. ``ClaudeCodePlugin``) that own backend-specific
lifecycle/control/discovery methods.
"""

from typing import TYPE_CHECKING

from waypoint.backends.capabilities import (
    BackendCapabilities,
    ModelSource,
    PermissionModeSpec,
)
from waypoint.backends.claude_code.plugin import ClaudeCodePlugin
from waypoint.backends.registry import BackendRegistry
from waypoint.transports.base import TransportAdapter
from waypoint.transports.codex import CodexTransport
from waypoint.transports.tmux import TmuxTransport

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


_CODEX_PERMISSION_MODES: tuple[PermissionModeSpec, ...] = (
    PermissionModeSpec("default", "Default"),
    PermissionModeSpec("auto_review", "Auto review"),
    PermissionModeSpec("full_access", "Full access"),
)


class _LegacyCodexPlugin:
    id = "codex"
    transport_id = "codex_app_server"
    label = "Codex"
    capabilities = BackendCapabilities(
        is_structured=True,
        supports_resume=False,
        supports_set_model_inline=True,
        supports_set_effort_inline=True,
        supports_set_permission_mode_inline=True,
        supports_thread_discovery=True,
        supports_thread_import=True,
        supports_slash_compact=True,
        permission_modes=_CODEX_PERMISSION_MODES,
        model_source=ModelSource.LIVE_RPC,
        badges={"glyph": "X", "color": "#34d399"},
    )

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        return CodexTransport(runtime)


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
        return TmuxTransport(runtime)


def build_default_registry() -> BackendRegistry:
    registry = BackendRegistry()
    registry.register(ClaudeCodePlugin())
    registry.register(_LegacyCodexPlugin())
    registry.register(_LegacyTmuxPlugin())
    return registry
