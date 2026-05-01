"""Legacy plugin shims.

Step 1 of the refactor wires the registry without changing behaviour.
Each shim advertises today's per-backend transport adapter and a
capability descriptor that mirrors the hard-coded flags those adapters
expose. Steps 3-5 will migrate the backends behind these shims into
`backends/<id>/` modules with their own lifecycle/control/discovery
methods, at which point the shims are deleted.
"""

from typing import TYPE_CHECKING

from waypoint.backends.capabilities import (
    BackendCapabilities,
    ModelSource,
    PermissionModeSpec,
)
from waypoint.backends.registry import BackendRegistry
from waypoint.transports.base import TransportAdapter
from waypoint.transports.claude import ClaudeTransport
from waypoint.transports.codex import CodexTransport
from waypoint.transports.tmux import TmuxTransport

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


_CLAUDE_PERMISSION_MODES: tuple[PermissionModeSpec, ...] = (
    PermissionModeSpec("default", "Default"),
    PermissionModeSpec("acceptEdits", "Accept edits"),
    PermissionModeSpec("plan", "Plan"),
    PermissionModeSpec("bypassPermissions", "Bypass"),
    PermissionModeSpec("dontAsk", "Don't ask"),
    PermissionModeSpec("denyAll", "Deny all"),
)

_CODEX_PERMISSION_MODES: tuple[PermissionModeSpec, ...] = (
    PermissionModeSpec("default", "Default"),
    PermissionModeSpec("auto_review", "Auto review"),
    PermissionModeSpec("full_access", "Full access"),
)


class _LegacyClaudePlugin:
    id = "claude_code"
    transport_id = "claude_cli"
    label = "Claude Code"
    capabilities = BackendCapabilities(
        is_structured=True,
        supports_resume=False,
        supports_set_model_inline=True,
        supports_set_effort_inline=False,
        supports_set_permission_mode_inline=True,
        supports_thread_discovery=True,
        supports_thread_import=True,
        supports_slash_compact=False,
        permission_modes=_CLAUDE_PERMISSION_MODES,
        effort_levels=("low", "medium", "high", "xhigh"),
        model_source=ModelSource.STATIC,
        badges={"glyph": "C", "color": "#a78bfa"},
    )

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        return ClaudeTransport(runtime)


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
    registry.register(_LegacyClaudePlugin())
    registry.register(_LegacyCodexPlugin())
    registry.register(_LegacyTmuxPlugin())
    return registry
