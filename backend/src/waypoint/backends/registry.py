from waypoint.backends.base import BackendPlugin
from waypoint.schemas import SessionRecord


class BackendRegistry:
    def __init__(self) -> None:
        self._by_id: dict[str, BackendPlugin] = {}
        self._by_transport: dict[str, BackendPlugin] = {}

    def register(self, plugin: BackendPlugin) -> None:
        if plugin.id in self._by_id:
            raise ValueError(f"backend plugin already registered: {plugin.id}")
        if plugin.transport_id in self._by_transport:
            raise ValueError(
                f"backend transport already registered: {plugin.transport_id}"
            )
        self._by_id[plugin.id] = plugin
        self._by_transport[plugin.transport_id] = plugin

    def get(self, backend_id: str) -> BackendPlugin:
        plugin = self._by_id.get(backend_id)
        if plugin is None:
            raise KeyError(f"unknown backend: {backend_id}")
        return plugin

    def for_transport(self, transport_id: str) -> BackendPlugin:
        plugin = self._by_transport.get(transport_id)
        if plugin is None:
            raise KeyError(f"unknown transport: {transport_id}")
        return plugin

    def plugin_for(self, session: SessionRecord) -> BackendPlugin:
        return self.for_transport(session.transport)

    def has_backend(self, backend_id: str) -> bool:
        return backend_id in self._by_id

    def has_transport(self, transport_id: str) -> bool:
        return transport_id in self._by_transport

    def all(self) -> list[BackendPlugin]:
        return list(self._by_id.values())

    def backends(self) -> set[str]:
        return set(self._by_id)

    def transports(self) -> set[str]:
        return set(self._by_transport)


_registry: BackendRegistry | None = None


def get_registry() -> BackendRegistry:
    global _registry
    if _registry is None:
        from waypoint.backends.bootstrap import build_default_registry

        _registry = build_default_registry()
    return _registry


def reset_registry_for_tests() -> None:
    """Test-only hook to drop the cached registry between scenarios."""
    global _registry
    _registry = None
