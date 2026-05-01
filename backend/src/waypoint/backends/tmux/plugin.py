"""Tmux fallback plugin.

Tmux is the legacy attached-session path: terminal output is scraped
from a pane log instead of a structured stream-json/notification
channel. The plugin's capability descriptor advertises
``is_structured=False`` so the frontend renders the heuristic transcript
view, ``supports_resume=True`` so users can re-attach a detached pane,
and disables every inline control knob (model/effort/permission mode)
because no protocol is available to set them mid-session.
"""

from typing import TYPE_CHECKING, Any, Never

from fastapi import HTTPException, status

from waypoint.backends.capabilities import BackendCapabilities, ModelSource
from waypoint.schemas import SessionRecord
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


def _unsupported(action: str) -> Never:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"{action} is not supported for tmux sessions",
    )


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

    def validate_permission_mode(self, mode: str | None) -> str | None:
        return None  # tmux has no concept of permission modes

    async def apply_permission_mode(
        self, runtime: "SessionRuntime", session: SessionRecord, mode: str
    ) -> None:
        _unsupported("permission mode")

    async def apply_model(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        model: str | None,
    ) -> None:
        _unsupported("model selection")

    async def apply_effort(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        effort: str | None,
    ) -> bool:
        _unsupported("effort selection")

    def effort_swap_message(self, effort: str | None) -> str:
        return ""

    async def list_models(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        return {
            "backend": self.id,
            "models": [],
            "default_model": None,
            "default_effort": None,
            "supports_free_text": False,
        }


def build_plugin() -> TmuxPlugin:
    return TmuxPlugin()


__all__ = ["TmuxPlugin", "build_plugin"]
