"""Tmux fallback plugin.

Tmux is the legacy attached-session path: terminal output is scraped
from a pane log instead of a structured stream-json/notification
channel. The plugin's capability descriptor advertises
``is_structured=False`` so the frontend renders the heuristic transcript
view, ``supports_resume=True`` so users can re-attach a detached pane,
and disables every inline control knob (model/effort/permission mode)
because no protocol is available to set them mid-session.
"""

from typing import TYPE_CHECKING

from waypoint.backends.capabilities import BackendCapabilities, ModelSource
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


class TmuxPlugin:
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


def build_plugin() -> TmuxPlugin:
    return TmuxPlugin()


__all__ = ["TmuxPlugin", "build_plugin"]
