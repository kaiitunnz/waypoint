"""Tmux fallback plugin.

Tmux is the legacy attached-session path: terminal output is scraped
from a pane log instead of a structured stream-json/notification
channel. The plugin's capability descriptor advertises
``is_structured=False`` so the frontend renders the heuristic transcript
view, ``supports_resume=True`` so users can re-attach a detached pane,
and disables every inline control knob (model/effort/permission mode)
because no protocol is available to set them mid-session.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Never

from fastapi import HTTPException, status
from pydantic import BaseModel

from waypoint.backends.capabilities import BackendCapabilities, ModelSource
from waypoint.backends.plugin_config import PluginConfig
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.git_meta import GitMeta
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import (
    SessionCreateRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


def _unsupported(action: str) -> Never:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"{action} is not supported for tmux sessions",
    )


class TmuxPluginConfig(PluginConfig):
    """Tmux fallback plugin configuration block.

    Tmux exposes no model/effort knobs; the inherited
    :class:`PluginConfig` defaults are unused but the field is required
    so all plugins satisfy the same contract.
    """


class TmuxPlugin:
    id = "tmux"
    transport_id = "tmux"
    label = "Tmux"
    import_request_schema: type[BaseModel] | None = None
    config_schema: type[PluginConfig] = TmuxPluginConfig
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
        is_fallback_for_managed_launch=True,
    )

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        from waypoint.backends.tmux.transport import TmuxTransport

        return TmuxTransport(runtime)

    def setup(self, runtime: "SessionRuntime") -> None:
        return None

    async def shutdown(self, runtime: "SessionRuntime") -> None:
        return None

    def register_routes(self, app: Any, context: Any) -> None:
        return None

    def is_available_for_managed_launch(self, runtime: "SessionRuntime") -> bool:
        return True

    def remote_executable(self, launch_target: SshLaunchTargetConfig) -> str:
        # Tmux is the *wrapper*, not the wrapped binary — the runtime
        # only calls remote_executable on the inner backend, never on
        # the tmux plugin itself.
        return ""

    async def terminate_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        return None

    def on_session_deleted(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        return None

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

    async def maybe_handle_input(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        request: Any,
    ) -> SessionRecord | None:
        return None

    async def answer_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        answer: str,
        tool_use_id: str | None,
        answers: list[dict[str, Any]] | None,
    ) -> SessionRecord:
        _unsupported("answer-question")

    async def post_approval(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        return None

    async def restore_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        # Tmux sessions are restored by re-attaching the pane monitor;
        # there's no protocol round-trip to make here.
        runtime._ensure_monitor(session.id)

    async def list_threads(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
    ) -> list[Any]:
        return []

    async def import_thread(
        self, runtime: "SessionRuntime", request: Any
    ) -> SessionRecord:
        _unsupported("thread import")

    def format_start_message(
        self,
        backend_label: str,
        launch_target: SshLaunchTargetConfig | None,
        cwd: str | None,
    ) -> str:
        if launch_target is None:
            return f"Managed session started for {backend_label}"
        return (
            f"Managed session started for {backend_label} via SSH target {launch_target.name} "
            f"on {launch_target.ssh_destination} ({cwd or launch_target.default_cwd})"
        )

    async def create_session(
        self,
        runtime: "SessionRuntime",
        request: SessionCreateRequest,
        *,
        session_id: str,
        launch_target: SshLaunchTargetConfig | None,
        title: str,
        raw_log: Path,
        structured_log: Path,
        git_meta: GitMeta,
        permission_mode: str | None,
        resolved_model: str | None,
        resolved_effort: str | None,
    ) -> SessionRecord:
        # Tmux fallback launches the actual backend binary inside a tmux
        # pane and tails the pane log. The plugin doesn't pick the
        # binary itself — it asks the registry for the cli_binary the
        # requested backend advertises. A backend without a cli_binary
        # (e.g. an HTTP-only OpenCode) can opt out of tmux fallback by
        # leaving the capability unset.
        command = runtime._command_for_backend(
            request.backend, request.args, launch_target, request.cwd
        )
        try:
            target = await runtime.tmux.start_managed_session(
                session_id, request.cwd, command
            )
            await runtime.tmux.pipe_output(target.pane, raw_log)
        except TmuxError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=request.backend,
            source=SessionSource.MANAGED,
            transport=self.transport_id,
            title=title,
            cwd=request.cwd,
            launch_target_id=launch_target.id if launch_target else None,
            repo_name=git_meta.repo_name,
            branch=git_meta.branch,
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            tmux_session=target.session,
            tmux_window=target.window,
            tmux_pane=target.pane,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            pid=target.pane_pid,
        )
        runtime.storage.create_session(session)
        await runtime._record_system_event(
            session.id,
            self.format_start_message(request.backend, launch_target, request.cwd),
        )
        runtime._ensure_monitor(session.id)
        return runtime.get_session(session.id)


def build_plugin() -> TmuxPlugin:
    return TmuxPlugin()


__all__ = ["TmuxPlugin", "TmuxPluginConfig", "build_plugin"]
