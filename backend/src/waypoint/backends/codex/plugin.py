"""Codex backend plugin.

Codex differs from Claude on two big knobs the capability descriptor
captures: ``model_source=LIVE_RPC`` (models come from the App Server's
``model/list`` notification, not a static alias table) and
``supports_set_effort_inline=True`` (effort is per-turn via
``turn_steer``, no session restart required). ``/compact`` is also
Codex-only today; it surfaces here as a registered slash command.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codex_app_server.client import AppServerClient
from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from waypoint.backends.capabilities import (
    BackendCapabilities,
    ModelSource,
    SlashCommandSpec,
)
from waypoint.backends.codex.adapter import (
    ClientFactory,
    CodexAppServerAdapter,
    default_client_factory,
)
from waypoint.backends.codex.permission_modes import (
    CODEX_PERMISSION_MODE_SPECS,
    CODEX_PERMISSION_PRESETS,
)
from waypoint.backends.codex.remote import build_remote_codex_client_factory
from waypoint.backends.codex.schemas import (
    CodexThreadImportRequest,
    CodexThreadSummary,
)
from waypoint.backends.plugin_config import PluginConfig, PluginLaunchTargetConfig
from waypoint.git_meta import GitMeta
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import (
    SessionCreateRequest,
    SessionInputRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.codex")


class CodexPluginConfig(PluginConfig):
    """Codex plugin configuration block.

    Codex discovers its model catalogue at runtime via ``model/list``,
    so no static model list lives here — only the per-plugin defaults
    inherited from :class:`PluginConfig`.
    """


class CodexLaunchTargetConfig(PluginLaunchTargetConfig):
    """Per-target Codex configuration.

    Adds ``config_overrides`` — a list of ``key=value`` strings fed
    through to the remote ``codex --config K=V`` flag (typically used
    to pin reasoning effort or model on a specific dev box).
    """

    config_overrides: list[str] = Field(default_factory=list)


class CodexPlugin:
    id = "codex"
    transport_id = "codex_app_server"
    label = "Codex"
    import_request_schema: type[BaseModel] | None = CodexThreadImportRequest
    config_schema: type[PluginConfig] = CodexPluginConfig
    launch_target_schema: type[PluginLaunchTargetConfig] = CodexLaunchTargetConfig
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
        slash_commands=(SlashCommandSpec("compact", "Compact the current thread"),),
        badges={"glyph": "X", "color": "#34d399"},
        cli_binary="codex",
        target_aliases=("codex",),
    )

    def __init__(self) -> None:
        self.adapter: CodexAppServerAdapter | None = None

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        # Lazy to avoid the same import-cycle pattern documented in
        # `backends/claude_code/plugin.py`.
        from waypoint.backends.codex.transport import CodexTransport

        return CodexTransport(runtime, self)

    def setup(self, runtime: "SessionRuntime") -> None:
        # Codex's adapter wires straight to the App Server SDK with no
        # external bootstrap. The plugin owns the adapter so every call
        # site reads ``self.adapter`` instead of reaching into the
        # runtime.
        self.adapter = CodexAppServerAdapter(runtime._emit_adapter_event)

    async def shutdown(self, runtime: "SessionRuntime") -> None:
        if self.adapter is not None:
            await self.adapter.shutdown()
            self.adapter = None

    def _require_adapter(self) -> CodexAppServerAdapter:
        assert self.adapter is not None, "codex plugin adapter not initialized"
        return self.adapter

    def register_routes(self, app: Any, context: Any) -> None:
        return None

    def is_available_for_managed_launch(self, runtime: "SessionRuntime") -> bool:
        return True

    def remote_executable(self, launch_target: SshLaunchTargetConfig) -> str:
        return launch_target.remote_bin_for(self.id, self.capabilities.cli_binary) or ""

    async def terminate_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        await self._require_adapter().terminate_session(session.id)

    def on_session_deleted(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        return None

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
            await self._require_adapter().set_model(session.id, model)
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
            await self._require_adapter().set_effort(session.id, effort)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        return False  # Codex doesn't surface a system-note for the swap

    def effort_swap_message(self, effort: str | None) -> str:
        return ""  # never published; apply_effort returns False

    async def answer_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        answer: str,
        tool_use_id: str | None,
        answers: list[dict[str, Any]] | None,
    ) -> SessionRecord:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="answer-question is only supported for Claude sessions",
        )

    async def post_approval(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        return None

    async def maybe_handle_input(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        request: SessionInputRequest,
    ) -> SessionRecord | None:
        # Codex's app-server doesn't parse user text as control
        # commands — `/compact` only takes effect via the
        # thread/compact/start RPC. Every other slash command
        # (`/help`, `/status`, `/permissions`, plus the Codex-/Claude-
        # specific extras) is forwarded as user input so the CLI/SDK
        # can surface its own response.
        command = request.text.strip()
        if command.split(None, 1)[0].lower() != "/compact":
            return None
        try:
            await self._require_adapter().compact_thread(session.id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        await runtime._record_user_event(
            session.id,
            request.text,
            submit=request.submit,
            status=session.status,
        )
        await runtime._record_system_event(
            session.id,
            "Compacting codex thread…",
            status=SessionStatus.RUNNING,
            metadata={"builtin_command": "/compact"},
        )
        return runtime.storage.update_session(session.id, status=SessionStatus.RUNNING)

    async def restore_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        thread_id = session.transport_state.get("thread_id")
        if not thread_id:
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
            await self._require_adapter().restore_session(
                session.id,
                session.cwd,
                thread_id,
                self.client_factory(runtime, session.launch_target_id),
                model=session.model,
                effort=session.effort,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "codex restore failed",
                extra={
                    "session_id": session.id,
                    "thread_id": thread_id,
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

    # --- launch / discovery helpers ----------------------------------

    def _config(self, runtime: "SessionRuntime") -> CodexPluginConfig:
        config = runtime.settings.plugin_config(self.id)
        assert isinstance(config, CodexPluginConfig)
        return config

    def client_factory(
        self, runtime: "SessionRuntime", launch_target_id: str | None
    ) -> ClientFactory | None:
        launch_target = runtime._find_launch_target(launch_target_id)
        if launch_target is None:
            return None
        return build_remote_codex_client_factory(launch_target)

    def client_cwd(
        self, runtime: "SessionRuntime", launch_target_id: str | None
    ) -> str:
        launch_target = runtime._find_launch_target(launch_target_id)
        if launch_target is not None:
            return launch_target.default_cwd
        return str(Path(runtime.settings.default_cwd).expanduser())

    async def run_client_operation(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None,
        operation: Callable[[AppServerClient], Awaitable[Any]],
        *,
        cwd: str | None = None,
    ) -> Any:
        default_cwd = self.client_cwd(runtime, launch_target_id)
        client_factory: ClientFactory = (
            self.client_factory(runtime, launch_target_id) or default_client_factory
        )
        client = client_factory(cwd or default_cwd, _deny_approval)
        try:
            await asyncio.to_thread(client.start)
            await asyncio.to_thread(client.initialize)
            return await operation(client)
        finally:
            with suppress(Exception):
                await asyncio.to_thread(client.close)

    async def _read_thread(
        self,
        runtime: "SessionRuntime",
        thread_id: str,
        launch_target_id: str | None,
    ) -> Any:
        runtime._resolve_launch_target(launch_target_id, self.id)

        async def operation(client: AppServerClient) -> Any:
            response = await asyncio.to_thread(client.thread_read, thread_id, False)
            return response.thread

        try:
            return await self.run_client_operation(
                runtime, launch_target_id, operation=operation
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to read codex thread: {exc}",
            ) from exc

    def _find_imported_session(
        self,
        runtime: "SessionRuntime",
        thread_id: str,
        launch_target_id: str | None,
    ) -> SessionRecord | None:
        for session in runtime.storage.list_sessions():
            if session.backend != self.id:
                continue
            if session.transport_state.get("thread_id") != thread_id:
                continue
            if session.launch_target_id != launch_target_id:
                continue
            return session
        return None

    def _thread_summary(self, thread: Any) -> CodexThreadSummary:
        cwd = _thread_cwd(thread)
        return CodexThreadSummary(
            id=thread.id,
            title=_thread_title(thread),
            cwd=cwd,
            repo_name=_thread_repo_name(thread),
            branch=_thread_branch(thread),
            preview=(thread.preview or "").strip() or None,
            created_at=datetime.fromtimestamp(thread.created_at, UTC),
            updated_at=datetime.fromtimestamp(thread.updated_at, UTC),
        )

    async def list_threads(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
    ) -> list[CodexThreadSummary]:
        runtime._resolve_launch_target(launch_target_id, self.id)
        imported: set[tuple[str | None, str]] = set()
        for session in runtime.storage.list_sessions():
            if session.backend != self.id:
                continue
            thread_id = session.transport_state.get("thread_id")
            if not thread_id:
                continue
            imported.add((session.launch_target_id, thread_id))

        async def operation(client: AppServerClient) -> list[Any]:
            threads: list[Any] = []
            cursor: str | None = None
            while True:
                response = await asyncio.to_thread(
                    client.thread_list,
                    {"archived": False, "cursor": cursor, "limit": 100},
                )
                threads.extend(response.data)
                if response.next_cursor is None:
                    return threads
                cursor = response.next_cursor

        threads = await self.run_client_operation(
            runtime, launch_target_id, operation=operation
        )
        summaries = [
            self._thread_summary(thread)
            for thread in threads
            if not thread.ephemeral and (launch_target_id, thread.id) not in imported
        ]
        return sorted(summaries, key=lambda thread: thread.updated_at, reverse=True)

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
        raw_log.touch(exist_ok=True)
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
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            permission_mode=permission_mode,
            model=resolved_model,
            effort=resolved_effort,
        )
        runtime.storage.create_session(session)
        try:
            thread_id = await self._require_adapter().start_session(
                session_id,
                request.cwd,
                self.client_factory(runtime, session.launch_target_id),
                model=resolved_model,
                effort=resolved_effort,
            )
        except Exception:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            raise
        runtime.storage.update_session(
            session.id,
            transport_state={**session.transport_state, "thread_id": thread_id},
            status=SessionStatus.IDLE,
        )
        await runtime._record_system_event(
            session.id,
            self.format_start_message(request.cwd, launch_target),
            status=SessionStatus.IDLE,
        )
        return runtime.get_session(session.id)

    async def import_thread(
        self,
        runtime: "SessionRuntime",
        request: CodexThreadImportRequest,
    ) -> SessionRecord:
        launch_target = runtime._resolve_launch_target(
            request.launch_target_id, self.id
        )
        existing = self._find_imported_session(
            runtime, request.thread_id, request.launch_target_id
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="codex thread already imported",
            )
        thread = await self._read_thread(
            runtime, request.thread_id, request.launch_target_id
        )
        if thread.ephemeral:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ephemeral codex threads cannot be imported",
            )
        session_id = runtime._generate_session_id(self.id)
        session_dir = runtime._session_dir(session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"
        raw_log.touch(exist_ok=True)
        cwd = _thread_cwd(thread)
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=self.id,
            source=SessionSource.MANAGED,
            transport=self.transport_id,
            title=_thread_title(thread),
            cwd=cwd,
            launch_target_id=launch_target.id if launch_target else None,
            repo_name=_thread_repo_name(thread),
            branch=_thread_branch(thread),
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"thread_id": thread.id},
            permission_mode="default",
        )
        runtime.storage.create_session(session)
        try:
            await self._require_adapter().restore_session(
                session.id,
                session.cwd,
                thread.id,
                self.client_factory(runtime, session.launch_target_id),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "codex import failed",
                extra={
                    "session_id": session.id,
                    "thread_id": thread.id,
                    "launch_target_id": session.launch_target_id,
                },
            )
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"Codex thread import failed: {exc}",
                status=SessionStatus.ERROR,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to import codex thread: {exc}",
            ) from exc
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await runtime._record_system_event(
            session.id,
            self.format_import_message(cwd, launch_target),
            status=SessionStatus.IDLE,
            metadata={"imported_thread_id": thread.id},
        )
        return runtime.get_session(session.id)

    async def list_models(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        config = self._config(runtime)
        default_model = config.default_model_id
        default_effort = config.default_effort
        cwd = self.client_cwd(runtime, launch_target_id)
        try:
            response = await self._require_adapter().list_models(
                cwd=cwd,
                client_factory_override=self.client_factory(runtime, launch_target_id),
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
        default_model_label: str | None = None
        if default_model:
            for model in models:
                if model.get("id") == default_model:
                    default_model_label = model.get("label")
                    break
        return {
            "backend": self.id,
            "models": models,
            "default_model_id": default_model,
            "default_model_label": default_model_label,
            "default_effort": default_effort,
            "supports_free_text": True,
        }


def _deny_approval(_method: str, _params: dict[str, Any] | None) -> dict[str, Any]:
    return {"decision": "decline"}


def _thread_cwd(thread: Any) -> str:
    cwd = getattr(thread, "cwd", "")
    return getattr(cwd, "root", cwd)


def _thread_title(thread: Any) -> str:
    if thread.name:
        return thread.name
    preview = (thread.preview or "").strip()
    if preview:
        return preview.splitlines()[0][:80]
    return f"Codex {Path(_thread_cwd(thread)).name or thread.id}"


def _thread_branch(thread: Any) -> str | None:
    git_info = getattr(thread, "git_info", None)
    return git_info.branch if git_info is not None else None


def _thread_repo_name(thread: Any) -> str | None:
    git_info = getattr(thread, "git_info", None)
    if git_info is not None and git_info.origin_url:
        normalized = git_info.origin_url.rstrip("/").removesuffix(".git")
        name = normalized.rsplit("/", 1)[-1]
        if name:
            return name
    return Path(_thread_cwd(thread)).name or None


def build_plugin() -> CodexPlugin:
    return CodexPlugin()


__all__ = [
    "CodexLaunchTargetConfig",
    "CodexPlugin",
    "CodexPluginConfig",
    "build_plugin",
]
