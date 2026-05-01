from typing import TYPE_CHECKING, Protocol, runtime_checkable

from waypoint.backends.capabilities import BackendCapabilities
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


@runtime_checkable
class BackendPlugin(Protocol):
    """Source of truth for everything backend-specific.

    Steps 3-5 of the refactor migrate each backend's lifecycle, control
    surface, model/thread discovery, slash routing, and event normalisation
    onto this contract. Step 1 only defines the contract and the registry;
    the legacy shims fan out to today's modules unchanged.
    """

    id: str
    transport_id: str
    label: str
    capabilities: BackendCapabilities

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        """Return a TransportAdapter routing send/interrupt/etc. for this plugin."""
        ...
