"""Codex backend plugin.

Codex differs from Claude on two big knobs the capability descriptor
captures: ``model_source=LIVE_RPC`` (models come from the App Server's
``model/list`` notification, not a static alias table) and
``supports_set_effort_inline=True`` (effort is per-turn via
``turn_steer``, no session restart required). ``/compact`` is also
Codex-only today; it surfaces here as a registered slash command.
"""

import logging
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

from waypoint.backends.capabilities import (
    BackendCapabilities,
    ModelSource,
    SlashCommandSpec,
)
from waypoint.backends.codex.permission_modes import (
    CODEX_PERMISSION_MODE_SPECS,
    CODEX_PERMISSION_PRESETS,
)
from waypoint.schemas import SessionRecord, SessionStatus
from waypoint.server_config import SshLaunchTargetConfig
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.codex")


class CodexPlugin:
    id = "codex"
    transport_id = "codex_app_server"
    label = "Codex"
    capabilities = BackendCapabilities(
        is_structured=True,
        supports_resume=False,
        supports_set_model_inline=True,
        supports_set_effort_inline=True,
        supports_set_permission_mode_inline=True,
        supports_thread_discovery=True,
        supports_thread_import=True,
        supports_slash_compact=True,
        permission_modes=CODEX_PERMISSION_MODE_SPECS,
        effort_levels=(),  # discovered per-model from `model/list`
        model_source=ModelSource.LIVE_RPC,
        slash_commands=(
            SlashCommandSpec("compact", "Compact the current thread"),
        ),
        badges={"glyph": "X", "color": "#34d399"},
        cli_binary="codex",
        target_aliases=("codex",),
    )

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        # Lazy to avoid the same import-cycle pattern documented in
        # `backends/claude_code/plugin.py`.
        from waypoint.backends.codex.transport import CodexTransport

        return CodexTransport(runtime)

    def validate_permission_mode(self, mode: str | None) -> str | None:
        if mode is None or mode == "":
            return None
        if mode not in CODEX_PERMISSION_PRESETS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"unsupported {self.id} permission mode: {mode}; "
                    f"expected one of {', '.join(CODEX_PERMISSION_PRESETS)}"
                ),
            )
        return mode

    @property
    def permission_mode_ids(self) -> tuple[str, ...]:
        return tuple(CODEX_PERMISSION_PRESETS)

    async def apply_permission_mode(
        self, runtime: "SessionRuntime", session: SessionRecord, mode: str
    ) -> None:
        # Codex applies on next turn_start — no protocol round-trip here.
        return None

    async def apply_model(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        model: str | None,
    ) -> None:
        try:
            await runtime.codex.set_model(session.id, model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    async def apply_effort(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        effort: str | None,
    ) -> bool:
        try:
            await runtime.codex.set_effort(session.id, effort)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        return False  # Codex doesn't surface a system-note for the swap

    def effort_swap_message(self, effort: str | None) -> str:
        return ""  # never published; apply_effort returns False

    async def restore_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        if not session.thread_id:
            runtime.storage.update_session(session.id, status=SessionStatus.EXITED)
            await runtime._record_system_event(
                session.id,
                "Codex session has no thread id; marking exited",
                status=SessionStatus.EXITED,
            )
            return
        if (
            session.launch_target_id
            and runtime._find_launch_target(session.launch_target_id) is None
        ):
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"Codex session launch target {session.launch_target_id} is no longer configured",
                status=SessionStatus.ERROR,
            )
            return
        try:
            await runtime.codex.restore_session(
                session.id,
                session.cwd,
                session.thread_id,
                runtime._codex_client_factory(session.launch_target_id),
                model=session.model,
                effort=session.effort,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "codex restore failed",
                extra={
                    "session_id": session.id,
                    "thread_id": session.thread_id,
                    "cwd": session.cwd,
                },
            )
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"Codex session restore failed: {exc}",
                status=SessionStatus.ERROR,
            )
            return
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await runtime._record_system_event(
            session.id,
            self.format_restore_message(runtime, session.cwd, session.launch_target_id),
            status=SessionStatus.IDLE,
        )

    def format_start_message(
        self,
        cwd: str | None,
        launch_target: SshLaunchTargetConfig | None,
    ) -> str:
        if launch_target is not None:
            return (
                f"Codex app-server session started via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd or launch_target.default_cwd})"
            )
        return "Codex app-server session started"

    def format_restore_message(
        self,
        runtime: "SessionRuntime",
        cwd: str | None,
        launch_target_id: str | None,
    ) -> str:
        launch_target = runtime._find_launch_target(launch_target_id)
        if launch_target is not None:
            return (
                f"Codex session restored via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd or launch_target.default_cwd})"
            )
        return "Codex session restored from previous backend process"

    def format_import_message(
        self,
        cwd: str,
        launch_target: SshLaunchTargetConfig | None,
    ) -> str:
        if launch_target is not None:
            return (
                f"Imported stored Codex thread via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd})"
            )
        return f"Imported stored Codex thread ({cwd})"

    async def list_models(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        default_model = runtime.settings.default_models.get(self.id)
        default_effort = runtime.settings.default_efforts.get(self.id)
        cwd = runtime._codex_client_cwd(launch_target_id)
        try:
            response = await runtime.codex.list_models(
                cwd=cwd,
                client_factory_override=runtime._codex_client_factory(launch_target_id),
                include_hidden=include_hidden,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"codex model discovery failed: {exc}",
            ) from exc
        models: list[dict[str, Any]] = []
        for entry in response.data:
            if entry.hidden and not include_hidden:
                continue
            supported_efforts = [
                option.reasoning_effort.value
                for option in (entry.supported_reasoning_efforts or [])
            ]
            models.append(
                {
                    "id": entry.model,
                    "label": entry.display_name or entry.model,
                    "description": entry.description or None,
                    "is_default": entry.is_default,
                    "hidden": entry.hidden,
                    "supported_efforts": supported_efforts,
                    "default_effort": (
                        entry.default_reasoning_effort.value
                        if entry.default_reasoning_effort is not None
                        else None
                    ),
                }
            )
            if default_model is None and entry.is_default:
                default_model = entry.model
        return {
            "backend": self.id,
            "models": models,
            "default_model": default_model,
            "default_effort": default_effort,
            "supports_free_text": True,
        }


def build_plugin() -> CodexPlugin:
    return CodexPlugin()


__all__ = [
    "CodexPlugin",
    "build_plugin",
]
