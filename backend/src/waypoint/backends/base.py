from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel

from waypoint.backends.capabilities import BackendCapabilities
from waypoint.backends.plugin_config import PluginConfig, PluginLaunchTargetConfig
from waypoint.schemas import SessionRecord
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from fastapi import FastAPI

    from waypoint.launch_targets import SshLaunchTargetConfig
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
    # Pydantic model used by the dispatcher in api.py to validate the
    # JSON body of POST /api/backends/{id}/sessions/import. ``None`` for
    # plugins that don't accept thread imports — the dispatcher gates on
    # ``capabilities.supports_thread_import`` first, so this only needs
    # to be set when that capability is True.
    import_request_schema: type[BaseModel] | None
    # Subclass of ``PluginConfig`` that the YAML validator parses
    # ``plugin_configs.<plugin_id>`` into. Plugins without bespoke
    # configuration can point at ``PluginConfig`` itself.
    config_schema: type[PluginConfig]
    # Subclass of ``PluginLaunchTargetConfig`` parsed out of
    # ``ssh_targets[*].plugin_configs.<plugin_id>``. Plugins without
    # per-target knobs beyond ``remote_bin`` point at the base class.
    launch_target_schema: type[PluginLaunchTargetConfig]

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

    async def restore_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        """Restore a previously-running session after a runtime restart."""
        ...

    async def list_threads(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
    ) -> list[Any]:
        """Return importable thread summaries for this backend.

        Each plugin returns its own summary type (CodexThreadSummary,
        ClaudeThreadSummary). The API serialises via ``model_dump`` so
        the wire shape is plugin-controlled.
        """
        ...

    async def import_thread(
        self, runtime: "SessionRuntime", request: Any
    ) -> SessionRecord:
        """Import an existing backend-side thread as a Waypoint session."""
        ...

    async def create_session(
        self,
        runtime: "SessionRuntime",
        request: Any,
        *,
        session_id: str,
        launch_target: Any,
        title: str,
        raw_log: Any,
        structured_log: Any,
        git_meta: Any,
        permission_mode: str | None,
        resolved_model: str | None,
        resolved_effort: str | None,
    ) -> SessionRecord:
        """Spawn a new session for this backend."""
        ...

    async def fork_session(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        new_session_id: str,
        title: str,
        raw_log: Any,
        structured_log: Any,
    ) -> SessionRecord:
        """Fork an existing session into a new branch."""
        ...

    async def maybe_handle_input(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        request: Any,
    ) -> SessionRecord | None:
        """Optional pre-send hook for slash routing.

        Return ``None`` to let the runtime forward the user input to
        ``transport.send_input``. Return a populated ``SessionRecord``
        to short-circuit (e.g. Codex's ``/compact`` slash routes
        through ``thread/compact/start`` instead of stdin).
        """
        ...

    async def answer_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        answer: str,
        tool_use_id: str | None,
        answers: list[dict[str, Any]] | None,
    ) -> SessionRecord:
        """Respond to a Claude AskUserQuestion tool call."""
        ...

    async def post_approval(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        """Run any side effects triggered by an approval response.

        Claude flips the CLI's permission mode after an ExitPlanMode
        approval; the plugin syncs the runtime + broadcast here so the
        UI pill reflects the new mode.
        """
        ...

    def setup(self, runtime: "SessionRuntime") -> None:
        """One-shot initialisation hook called from ``SessionRuntime.__init__``.

        Plugins use this to build their adapter, hook bundles, and any
        per-process resources. Default is a no-op so plugins that
        don't need bootstrapping (Tmux fallback) opt out.
        """
        ...

    async def shutdown(self, runtime: "SessionRuntime") -> None:
        """Tear down per-process resources owned by this plugin.

        Called from ``SessionRuntime.stop`` so plugins can close their
        adapter (kill subprocesses, drain queues, close SDK clients) in
        the same order they were brought up. Default is a no-op for
        plugins that don't own any background state.
        """
        ...

    def is_available_for_managed_launch(self, runtime: "SessionRuntime") -> bool:
        """Whether the plugin is ready to spawn a fresh managed session.

        Structured backends use this to signal that their adapter
        bootstrap (Claude's hook bundle, a future OAuth handshake, …)
        succeeded. The runtime falls back to the tmux plugin when the
        answer is False, preserving the ``backend == "claude_code"``
        fallback path without naming a specific backend.
        """
        ...

    def remote_executable(self, launch_target: "SshLaunchTargetConfig") -> str:
        """Return the absolute or PATH-resolvable binary name for this
        backend on a given SSH launch target.

        Used by the tmux fallback when wrapping a remote ``claude`` /
        ``codex`` invocation. Plugins typically read
        ``launch_target.remote_bin_for(self.id, self.capabilities.cli_binary)``
        so users can pin a remote install path via the per-target
        ``remote_bins`` mapping. Wrapper plugins that never get
        launched themselves (tmux) can return an empty string — the
        runtime only calls this on the inner backend.
        """
        ...

    async def terminate_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        """Tear down any in-process state for ``session``.

        Called before re-restoring an EXITED/ERROR session so the prior
        adapter slot (Claude/Codex stream watchers, subprocess handles)
        is dropped instead of left dangling. Default is a no-op.
        """
        ...

    def on_session_deleted(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        """Hook fired after a session row is deleted from storage.

        Plugins that cache cross-session metadata (e.g. Claude's remote
        thread enumerator) invalidate here so the next listing reflects
        the deletion.
        """
        ...

    def register_routes(self, app: "FastAPI", context: Any) -> None:
        """Optional FastAPI route-registration hook.

        Called once during ``create_app`` after the runtime is built.
        The Claude plugin uses this to mount its PreToolUse approval
        webhook; other plugins can mount internal routes (OpenCode
        webhook receiver, Codex stream proxies) without touching
        ``api.py``.
        """
        ...
