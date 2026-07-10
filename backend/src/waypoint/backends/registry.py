from waypoint.backends.base import BackendPlugin
from waypoint.backends.capabilities import BackendCapabilities
from waypoint.schemas import SessionRecord


def _supported_transports(plugin: BackendPlugin) -> tuple[str, ...]:
    """Transport ids a plugin can be driven over.

    An agent declares the generic transports it pairs with (its own
    native transport plus any pane wrapper) via ``supported_transports``;
    plugins that don't declare it fall back to their own transport only.
    Read defensively so a third-party plugin predating the attribute
    still registers and resolves.
    """
    declared = getattr(plugin, "supported_transports", None)
    if declared is not None:
        return tuple(declared)
    return (plugin.transport_id,)


class BackendRegistry:
    def __init__(self) -> None:
        self._by_id: dict[str, BackendPlugin] = {}
        self._by_transport: dict[str, BackendPlugin] = {}
        # (agent_id, transport_id) -> the plugin that drives that pair.
        # Cached after the first resolve; cleared on register since the
        # registry is otherwise immutable once bootstrap finishes.
        self._pairs: dict[tuple[str, str], BackendPlugin] | None = None

    def register(self, plugin: BackendPlugin) -> None:
        if plugin.id in self._by_id:
            raise ValueError(f"backend plugin already registered: {plugin.id}")
        if plugin.transport_id in self._by_transport:
            raise ValueError(
                f"backend transport already registered: {plugin.transport_id}"
            )
        self._by_id[plugin.id] = plugin
        self._by_transport[plugin.transport_id] = plugin
        self._pairs = None

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

    def _pair_map(self) -> dict[tuple[str, str], BackendPlugin]:
        """Build (and cache) the (agent, transport) -> driver-plugin map.

        Each agent contributes one pair per transport it declares
        support for; the driving plugin is whichever plugin *owns* that
        transport id — the agent itself for its native structured
        transport, or a generic pane wrapper (tmux) for a wrapped pair.
        """
        if self._pairs is None:
            pairs: dict[tuple[str, str], BackendPlugin] = {}
            for plugin in self._by_id.values():
                for transport_id in _supported_transports(plugin):
                    owner = self._by_transport.get(transport_id)
                    if owner is not None:
                        pairs[(plugin.id, transport_id)] = owner
            self._pairs = pairs
        return self._pairs

    def resolve(self, backend_id: str, transport_id: str) -> BackendPlugin:
        """Resolve the plugin that drives a session's (agent, transport) pair.

        A session is an (agent, transport) pair, so resolution keys on
        both: the agent selects among plugins that declare the transport,
        and the transport selects the driver (a native structured adapter
        vs. a shared pane wrapper). Falls back to the transport owner for
        a pair no agent declared, so legacy session rows still resolve.
        """
        plugin = self._pair_map().get((backend_id, transport_id))
        if plugin is not None:
            return plugin
        return self.for_transport(transport_id)

    def plugin_for(self, session: SessionRecord) -> BackendPlugin:
        return self.resolve(session.backend, session.transport)

    def capabilities_for_pair(
        self, backend_id: str, transport_id: str
    ) -> BackendCapabilities:
        """Compose the capabilities of an arbitrary ``(agent, transport)`` pair.

        The agent axis comes from ``get(backend_id)`` and the transport axis
        from ``for_transport(transport_id)`` — the same composition
        :meth:`capabilities_for` does for a session, but for a pair that may not
        yet be persisted (e.g. projecting a switch target). Raises ``KeyError``
        for an unknown agent or transport.
        """
        agent = self.get(backend_id)
        transport_owner = self.for_transport(transport_id)
        return BackendCapabilities.from_split(
            agent.capabilities.agent_capabilities(),
            transport_owner.capabilities.transport_capabilities(),
        )

    def capabilities_for(self, session: SessionRecord) -> BackendCapabilities:
        """Compose the session's capabilities from its (agent, transport) axes.

        ``plugin_for(session).capabilities`` reads the flat descriptor of
        whichever plugin *owns* the transport — for a wrapped pair (e.g. a
        Claude/Codex session driven over the generic ``tmux`` transport)
        that's the wrapper, not the agent, so agent-axis fields like
        ``config_dir_env_var`` read as unset even though the wrapped agent
        has one. Compose instead: the agent axis comes from ``self.get
        (session.backend)`` (the agent's own config-dir/thread-store/CLI
        traits), the transport axis from ``self.for_transport
        (session.transport)`` (the driver's resume/restart/terminal
        story). For a native pair both lookups land on the same plugin, so
        this equals the flat descriptor; for a wrapped pair it reflects
        what the pair can actually do.
        """
        return self.capabilities_for_pair(session.backend, session.transport)

    def supported_transports(self, backend_id: str) -> tuple[str, ...]:
        """Transport ids the named agent declares it can be driven over."""
        return _supported_transports(self.get(backend_id))

    def has_backend(self, backend_id: str) -> bool:
        return backend_id in self._by_id

    def has_transport(self, transport_id: str) -> bool:
        return transport_id in self._by_transport

    def all(self) -> list[BackendPlugin]:
        return list(self._by_id.values())

    def fallback_for_managed_launch(self) -> BackendPlugin | None:
        """Return the plugin marked as the managed-launch fallback.

        Used when a structured plugin's adapter isn't ready (Claude's
        hook bundle failed to materialise) or when the requested
        plugin isn't structured at all. Returns ``None`` when no
        fallback is registered, in which case the runtime surfaces
        the underlying error instead of silently swapping plugins.
        """
        for plugin in self._by_id.values():
            if plugin.capabilities.is_fallback_for_managed_launch:
                return plugin
        return None

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
