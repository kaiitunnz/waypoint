"""Default registry assembly — entry point for built-in plugins.

The built-ins (Claude Code, Codex, tmux) are registered directly so
they always ship with waypoint. After that, entry points published
under the ``waypoint.backends`` group are loaded so third-party
packages can register additional plugins by adding a single line to
their ``pyproject.toml``:

    [project.entry-points."waypoint.backends"]
    opencode = "waypoint_opencode:build_plugin"

The entry-point value resolves to either a callable that returns a
``BackendPlugin`` instance or a plugin instance directly. Failures
during discovery or registration are logged and skipped — a broken
external plugin must never take down the runtime for everyone else.
"""

import logging
from importlib.metadata import entry_points

from waypoint.backends.base import BackendPlugin
from waypoint.backends.claude_code.plugin import ClaudeCodePlugin
from waypoint.backends.codex.plugin import CodexPlugin
from waypoint.backends.opencode.plugin import OpenCodePlugin
from waypoint.backends.registry import BackendRegistry
from waypoint.backends.tmux.plugin import TmuxPlugin

ENTRY_POINT_GROUP = "waypoint.backends"

log = logging.getLogger("waypoint.backends.bootstrap")


def build_default_registry() -> BackendRegistry:
    registry = BackendRegistry()
    registry.register(ClaudeCodePlugin())
    registry.register(CodexPlugin())
    registry.register(OpenCodePlugin())
    registry.register(TmuxPlugin())
    _register_entry_point_plugins(registry)
    return registry


def _register_entry_point_plugins(registry: BackendRegistry) -> None:
    """Discover and register plugins published via the
    ``waypoint.backends`` entry-point group. Logs and skips any plugin
    whose loader raises or whose ``register`` collides with an
    existing id/transport.
    """
    try:
        discovered = entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001 — defensive against stdlib regressions
        log.exception("failed to enumerate %s entry points", ENTRY_POINT_GROUP)
        return
    for ep in discovered:
        # Both ``ep.load()`` (import) and the subsequent factory call
        # (e.g. ``build_plugin()``) live under the same guard: a broken
        # external plugin must never take the runtime down regardless
        # of which step it fails at. Construction-time errors —
        # PluginConfig subclass exploding, hook bundle failing, etc. —
        # surface here too.
        try:
            loaded = ep.load()
            plugin = loaded() if callable(loaded) else loaded
        except Exception:  # noqa: BLE001
            log.exception("failed to load waypoint backend plugin %s", ep.name)
            continue
        if not isinstance(plugin, BackendPlugin):
            log.error(
                "waypoint backend plugin %s did not produce a BackendPlugin "
                "instance (got %r); skipping",
                ep.name,
                type(plugin),
            )
            continue
        try:
            registry.register(plugin)
        except ValueError:
            log.exception("failed to register waypoint backend plugin %s", ep.name)
