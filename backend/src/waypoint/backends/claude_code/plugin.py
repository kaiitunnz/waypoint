"""Claude Code backend plugin.

Owns the per-backend invariants that the runtime previously hard-coded:
permission-mode catalogue, model catalogue, capability flags, and the
transport adapter wiring. Steps 4-6 of the refactor migrate the
runtime's Claude-specific lifecycle helpers behind plugin methods so
``runtime.py`` becomes a generic dispatcher.
"""

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

from waypoint.backends.capabilities import (
    BackendCapabilities,
    ModelSource,
)
from waypoint.backends.claude_code.models import (
    CLAUDE_EFFORT_LEVELS,
    DEFAULT_CLAUDE_MODELS,
)
from waypoint.backends.claude_code.permission_modes import (
    CLAUDE_PERMISSION_MODE_SPECS,
    CLAUDE_PERMISSION_MODES,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


class ClaudeCodePlugin:
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
        permission_modes=CLAUDE_PERMISSION_MODE_SPECS,
        effort_levels=CLAUDE_EFFORT_LEVELS,
        model_source=ModelSource.STATIC,
        slash_commands=(),
        badges={"glyph": "C", "color": "#a78bfa"},
    )

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        # Imported lazily to avoid the cycle: transports.claude →
        # claude_cli → backends.claude_code.permission_modes →
        # backends/claude_code/__init__ → backends.claude_code.plugin.
        from waypoint.transports.claude import ClaudeTransport

        return ClaudeTransport(runtime)

    def validate_permission_mode(self, mode: str | None) -> str | None:
        if mode is None or mode == "":
            return None
        if mode not in CLAUDE_PERMISSION_MODES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"unsupported {self.id} permission mode: {mode}; "
                    f"expected one of {', '.join(CLAUDE_PERMISSION_MODES)}"
                ),
            )
        return mode

    def static_model_options(self, runtime: "SessionRuntime") -> list[Any]:
        # Settings carries the (configurable) Claude model catalogue. The
        # plugin defers to it so deployments can patch the list via
        # waypoint.yaml without forking this module.
        return list(runtime.settings.claude_models)

    @property
    def permission_mode_ids(self) -> tuple[str, ...]:
        return CLAUDE_PERMISSION_MODES


def build_plugin() -> ClaudeCodePlugin:
    return ClaudeCodePlugin()


__all__ = [
    "CLAUDE_EFFORT_LEVELS",
    "CLAUDE_PERMISSION_MODES",
    "DEFAULT_CLAUDE_MODELS",
    "ClaudeCodePlugin",
    "build_plugin",
]
