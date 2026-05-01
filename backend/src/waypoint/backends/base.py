from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from waypoint.backends.capabilities import BackendCapabilities
from waypoint.schemas import SessionRecord
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


@runtime_checkable
class BackendPlugin(Protocol):
    """Source of truth for everything backend-specific.

    Steps 3-5 of the refactor migrated each backend's capability
    descriptor, permission catalogue, and control-surface application
    behind this contract. Steps later in the plan extend it with
    lifecycle / thread-discovery / event normalisation methods so the
    runtime can drop the remaining backend literals.
    """

    id: str
    transport_id: str
    label: str
    capabilities: BackendCapabilities

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        """Return a TransportAdapter routing send/interrupt/etc. for this plugin."""
        ...

    def validate_permission_mode(self, mode: str | None) -> str | None:
        """Validate a user-supplied permission mode for this backend.

        Returns the canonical mode string when accepted, ``None`` when
        the caller didn't pick one (so the runtime falls back to its
        defaults), and raises ``HTTPException`` for unknown modes.
        """
        ...

    async def apply_permission_mode(
        self, runtime: "SessionRuntime", session: SessionRecord, mode: str
    ) -> None:
        """Apply a validated permission mode mid-session.

        Only invoked when the plugin advertises
        ``supports_set_permission_mode_inline=True``.
        """
        ...

    async def apply_model(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        model: str | None,
    ) -> None:
        """Apply a model swap mid-session.

        Only invoked when the plugin advertises
        ``supports_set_model_inline=True``.
        """
        ...

    async def apply_effort(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        effort: str | None,
    ) -> bool:
        """Apply an effort swap mid-session.

        Returns ``True`` when the runtime should publish a system note
        announcing the swap (e.g. Claude restarts the CLI to pick up a
        new ``--effort``). Codex applies effort silently per turn so
        returns ``False``.
        """
        ...

    def effort_swap_message(self, effort: str | None) -> str:
        """User-visible system note text for an effort swap announcement."""
        ...

    async def list_models(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        """Return the model catalogue payload served by ``/api/backends/{id}/models``."""
        ...
