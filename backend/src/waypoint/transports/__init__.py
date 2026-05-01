"""Transport-adapter base protocol.

The per-backend transport classes now live in their plugin homes
(`waypoint.backends.<id>.transport`). This module keeps the
`TransportAdapter` ABC at a backend-agnostic location since plugins
can't depend on each other.
"""

from waypoint.transports.base import TransportAdapter

__all__ = ["TransportAdapter"]
