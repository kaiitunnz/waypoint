import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from waypoint.backends.capabilities import (
    BackendCapabilities,
    ModelSource,
    PermissionModeSpec,
    SlashCommandSpec,
)
from waypoint.backends.opencode.adapter import OpenCodeAdapter, OpenCodeError
from waypoint.backends.plugin_config import PluginConfig, PluginLaunchTargetConfig
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

log = logging.getLogger("waypoint.backends.opencode")

DEFAULT_OPENCODE_MODEL = "opencode/minimax-m2.5-free"

OPENCODE_PERMISSION_MODES = (
    PermissionModeSpec(
        id="default",
        label="Default",
        description="Use OpenCode's built-in defaults (no rule attached)",
    ),
    PermissionModeSpec(
        id="ask", label="Ask", description="Ask for permission on every action"
    ),
    PermissionModeSpec(
        id="allow", label="Allow", description="Automatically approve actions"
    ),
    PermissionModeSpec(id="deny", label="Deny", description="Deny all actions"),
)
OPENCODE_PERMISSION_ACTIONS = {"ask", "allow", "deny"}

OPENCODE_SLASH_COMMANDS = (
    SlashCommandSpec(
        name="compact", description="Compact the session to reduce context"
    ),
    SlashCommandSpec(name="resume", description="Resume a previous session"),
    SlashCommandSpec(name="new", description="Start a new session"),
)


def _ruleset_for_mode(mode: str | None) -> list[dict[str, str]] | None:
    # The runtime substitutes "default" when no mode is selected; that means
    # "let OpenCode decide" — don't send a permission key at all.
    if mode not in OPENCODE_PERMISSION_ACTIONS:
        return None
    return [{"permission": "*", "pattern": "*", "action": mode}]


class OpenCodePluginConfig(PluginConfig):
    pass


class OpenCodeThreadImportRequest(BaseModel):
    thread_id: str


class OpenCodeThreadSummary(BaseModel):
    id: str
    title: str
    directory: str | None = None
    created_at: int | None = None
    updated_at: int | None = None


class OpenCodePlugin:
    id = "opencode"
    transport_id = "opencode_http"
    label = "OpenCode"
    import_request_schema: type[BaseModel] | None = OpenCodeThreadImportRequest
    config_schema: type[PluginConfig] = OpenCodePluginConfig
    launch_target_schema: type[PluginLaunchTargetConfig] = PluginLaunchTargetConfig

    capabilities = BackendCapabilities(
        is_structured=True,
        supports_resume=False,
        supports_set_model_inline=True,
        supports_set_effort_inline=True,
        supports_set_effort_with_restart=False,
        supports_set_permission_mode_inline=True,
        supports_thread_discovery=True,
        supports_thread_import=True,
        supports_slash_compact=True,
        permission_modes=OPENCODE_PERMISSION_MODES,
        effort_levels=(),
        model_source=ModelSource.LIVE_RPC,
        slash_commands=OPENCODE_SLASH_COMMANDS,
        badges={"glyph": "O", "color": "#cbd5e1"},
        cli_binary="opencode",
        target_aliases=("opencode",),
    )

    def __init__(self) -> None:
        self._adapter: OpenCodeAdapter | None = None
        self._lock = asyncio.Lock()

    def _require_adapter(self) -> OpenCodeAdapter:
        if self._adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="opencode adapter not initialized",
            )
        return self._adapter

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        from waypoint.backends.opencode.transport import OpenCodeTransport

        return OpenCodeTransport(runtime, self)

    def setup(self, runtime: "SessionRuntime") -> None:
        log.info("setting up opencode plugin")
        self._adapter = OpenCodeAdapter(
            emit_event=runtime._emit_adapter_event,
        )

    async def shutdown(self, runtime: "SessionRuntime") -> None:
        if self._adapter is not None:
            await self._adapter.shutdown()
            self._adapter = None

    def is_available_for_managed_launch(self, runtime: "SessionRuntime") -> bool:
        return self._adapter is not None

    def remote_executable(self, launch_target: SshLaunchTargetConfig) -> str:
        return launch_target.remote_bin_for(self.id, self.capabilities.cli_binary) or ""

    def _ensure_local_only(self, launch_target_id: str | None) -> None:
        if launch_target_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="opencode SSH launch targets are not supported yet",
            )

    async def terminate_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        if self._adapter is not None:
            await self._adapter.terminate_session(session.id)

    def on_session_deleted(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        pass

    def register_routes(self, app: FastAPI, context: Any) -> None:
        pass

    def validate_permission_mode(self, mode: str | None) -> str | None:
        if mode is None or mode == "":
            return None
        # "default" is a real, user-selectable mode for OpenCode — it clears
        # any attached ruleset so the upstream defaults apply. We pass it
        # through so set_permission_mode round-trips it (the runtime rejects
        # `None`), and apply_permission_mode handles it via _ruleset_for_mode.
        if mode == "default":
            return "default"
        if mode not in OPENCODE_PERMISSION_ACTIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"unsupported opencode permission mode: {mode}; "
                    f"expected one of {', '.join(sorted(OPENCODE_PERMISSION_ACTIONS))}"
                ),
            )
        return mode

    async def apply_permission_mode(
        self, runtime: "SessionRuntime", session: SessionRecord, mode: str
    ) -> None:
        adapter = self._require_adapter()
        ruleset = _ruleset_for_mode(mode) or []
        success = await adapter.set_session_permission(session.id, ruleset)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to set permission mode to {mode}",
            )

    async def apply_model(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        model: str | None,
    ) -> None:
        if model is None:
            return
        adapter = self._require_adapter()
        success = await adapter.set_model(session.id, model)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to set model to {model}",
            )

    async def apply_effort(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        effort: str | None,
    ) -> bool:
        adapter = self._require_adapter()
        success = await adapter.set_effort(session.id, effort)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to set effort to {effort}",
            )
        return False

    def effort_swap_message(self, effort: str | None) -> str:
        return ""

    async def list_models(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        self._ensure_local_only(launch_target_id)
        adapter = self._require_adapter()
        try:
            providers = await adapter.list_providers()
        except OpenCodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"failed to list opencode providers: {exc}",
            ) from exc
        models = self._flatten_provider_models(providers, include_hidden=include_hidden)
        default_model_id, default_model_label = self._select_default_model(
            models, providers
        )
        return {
            "backend": self.id,
            "models": models,
            "default_model_id": default_model_id,
            "default_model_label": default_model_label,
            "default_effort": None,
            "supports_free_text": True,
        }

    def _flatten_provider_models(
        self,
        providers: dict[str, Any],
        *,
        include_hidden: bool,
    ) -> list[dict[str, Any]]:
        # `/provider` returns every provider in the models.dev manifest, but
        # OpenCode only resolves models from providers it has actually
        # *connected* (env API key, stored auth, or auto-loaded). Listing
        # the rest leads the user into picking a model that the runtime
        # rejects with "Model not found" mid-prompt. Filter when the key
        # is present; if it's missing entirely (older server payloads or
        # tests), fall back to listing everything.
        raw_connected = providers.get("connected")
        connected: set[str] | None
        if isinstance(raw_connected, list):
            connected = {
                item for item in raw_connected if isinstance(item, str) and item
            }
        else:
            connected = None
        flattened: list[dict[str, Any]] = []
        for provider in providers.get("all", []) or []:
            if not isinstance(provider, dict):
                continue
            provider_id = provider.get("id")
            if not isinstance(provider_id, str) or not provider_id:
                continue
            if connected is not None and provider_id not in connected:
                continue
            provider_label = provider.get("name") or provider_id
            for model_id, model in (provider.get("models") or {}).items():
                if not isinstance(model_id, str) or not model_id:
                    continue
                if not isinstance(model, dict):
                    continue
                if not include_hidden and model.get("status") == "deprecated":
                    continue
                model_label = model.get("name") or model_id

                supported_efforts = []
                variants = model.get("variants")
                if isinstance(variants, dict):
                    supported_efforts = list(variants.keys())

                flattened.append(
                    {
                        "id": f"{provider_id}/{model_id}",
                        "label": f"{provider_label} · {model_label}",
                        "supported_efforts": supported_efforts,
                        "default_effort": None,
                    }
                )
        flattened.sort(key=lambda entry: entry["id"])
        return flattened

    def _select_default_model(
        self,
        models: list[dict[str, Any]],
        providers: dict[str, Any],
    ) -> tuple[str, str | None]:
        available = {entry["id"] for entry in models}
        default_id: str | None = None
        if DEFAULT_OPENCODE_MODEL in available:
            default_id = DEFAULT_OPENCODE_MODEL
        else:
            defaults = providers.get("default") or {}
            if isinstance(defaults, dict):
                for provider_id, model_id in defaults.items():
                    if isinstance(provider_id, str) and isinstance(model_id, str):
                        candidate = f"{provider_id}/{model_id}"
                        if candidate in available:
                            default_id = candidate
                            break
            if default_id is None and models:
                default_id = models[0]["id"]
        if default_id is None:
            default_id = DEFAULT_OPENCODE_MODEL

        for model in models:
            if model["id"] == default_id:
                return default_id, model["label"]
        return default_id, None

    async def maybe_handle_input(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        request: Any,
    ) -> SessionRecord | None:
        text = getattr(request, "text", None) or ""
        if not text.startswith("/"):
            return None

        adapter = self._require_adapter()
        rest = text[1:].split(maxsplit=1)
        command = rest[0] if rest else ""

        if command == "compact":
            try:
                await adapter.compact_session(session.id)
            except OpenCodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
            await runtime._record_system_event(
                session.id,
                "Compacting session...",
                status=SessionStatus.RUNNING,
            )
            return runtime.get_session(session.id)
        elif command == "resume":
            await runtime._record_system_event(
                session.id,
                "Resuming session...",
                status=SessionStatus.RUNNING,
            )
            return runtime.get_session(session.id)

        return None

    async def answer_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        answer: str,
        tool_use_id: str | None,
        answers: list[dict[str, Any]] | None,
    ) -> SessionRecord:
        adapter = self._require_adapter()
        request_id = tool_use_id or adapter.current_question_id(session.id)
        if not request_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="no pending question to answer",
            )
        structured_answers = self._serialize_question_answers(answer, answers)
        success = await adapter.answer_question(
            session.id,
            request_id,
            structured_answers,
        )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="failed to answer question",
            )
        updated = runtime.storage.update_session(
            session.id, status=SessionStatus.RUNNING
        )
        metadata: dict[str, Any] = {"kind": "ask_user_question_answer"}
        if answers:
            metadata["answers"] = answers
        metadata["tool_use_id"] = request_id
        await runtime._record_user_event(
            session.id,
            answer,
            submit=True,
            extra_metadata=metadata,
        )
        return updated

    async def post_approval(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        pass

    async def restore_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        if self._adapter is None:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                "OpenCode adapter unavailable; cannot restore",
                status=SessionStatus.ERROR,
            )
            return
        opencode_session_id = session.transport_state.get("opencode_session_id")
        if not opencode_session_id:
            runtime.storage.update_session(session.id, status=SessionStatus.EXITED)
            await runtime._record_system_event(
                session.id,
                "OpenCode session has no opencode_session_id; marking exited",
                status=SessionStatus.EXITED,
            )
            return
        try:
            await self._adapter.restore_session(
                session.id,
                session.cwd,
                opencode_session_id,
                model=session.model,
                effort=session.effort,
            )
        except Exception as exc:
            log.exception("opencode restore failed", extra={"session_id": session.id})
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"OpenCode session restore failed: {exc}",
                status=SessionStatus.ERROR,
            )
            return
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await runtime._record_system_event(
            session.id,
            "OpenCode session restored from previous backend process",
            status=SessionStatus.IDLE,
        )

    async def list_threads(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
    ) -> list[OpenCodeThreadSummary]:
        self._ensure_local_only(launch_target_id)
        adapter = self._require_adapter()
        sessions = await adapter.list_sessions()
        imported = {
            (s.transport_state.get("opencode_session_id"), s.launch_target_id)
            for s in runtime.storage.list_sessions()
            if s.backend == self.id
        }
        result = []
        for sess in sessions:
            sess_id = sess.get("id")
            if not sess_id:
                continue
            if (sess_id, launch_target_id) in imported:
                continue
            time_data = sess.get("time", {})
            result.append(
                OpenCodeThreadSummary(
                    id=sess_id,
                    title=sess.get("title", "Untitled"),
                    directory=sess.get("directory"),
                    created_at=time_data.get("created"),
                    updated_at=time_data.get("updated"),
                )
            )
        return result

    async def import_thread(
        self, runtime: "SessionRuntime", request: OpenCodeThreadImportRequest
    ) -> SessionRecord:
        adapter = self._require_adapter()
        opencode_session_id = request.thread_id
        for s in runtime.storage.list_sessions():
            if (
                s.backend == self.id
                and s.transport_state.get("opencode_session_id") == opencode_session_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="session already imported",
                )
        sess = await adapter.get_session(opencode_session_id)
        if not sess:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="session not found in OpenCode",
            )
        cwd = sess.get("directory", ".")
        session_id = runtime._generate_session_id(self.id)
        session_dir = runtime._session_dir(session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"
        raw_log.touch(exist_ok=True)
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=self.id,
            source=SessionSource.MANAGED,
            transport=self.transport_id,
            title=sess.get("title", "Imported session"),
            cwd=cwd,
            launch_target_id=None,
            repo_name=None,
            branch=None,
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"opencode_session_id": opencode_session_id},
            permission_mode="ask",
        )
        runtime.storage.create_session(session)
        try:
            await adapter.restore_session(
                session.id,
                cwd,
                opencode_session_id,
            )
        except Exception as exc:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to restore session: {exc}",
            ) from exc
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await runtime._record_system_event(
            session.id,
            f"Imported OpenCode session ({opencode_session_id})",
            status=SessionStatus.IDLE,
        )
        return runtime.get_session(session.id)

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
        if launch_target is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="opencode SSH launch targets are not supported yet",
            )
        if self._adapter is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="opencode adapter not configured",
            )
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
            transport_state={},
            permission_mode=permission_mode,
            model=resolved_model,
            effort=resolved_effort,
        )
        runtime.storage.create_session(session)
        try:
            opencode_session_id = await self._adapter.start_session(
                session_id,
                request.cwd,
                model=resolved_model,
                effort=resolved_effort,
                title=title,
                permission=_ruleset_for_mode(permission_mode),
            )
            session.transport_state = {"opencode_session_id": opencode_session_id}
            runtime.storage.update_session(
                session.id, transport_state=session.transport_state
            )
        except OpenCodeError as exc:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        # The adapter already emits "OpenCode session started ({session_id})"
        # with the remote id; re-recording here would duplicate the note.
        return runtime.get_session(session.id)

    def _serialize_question_answers(
        self,
        answer: str,
        answers: list[dict[str, Any]] | None,
    ) -> list[list[str]]:
        if not answers:
            return [[answer]]

        structured: list[list[str]] = []
        for entry in answers:
            selected: list[str] = []
            answer_value = entry.get("answer")
            if isinstance(answer_value, str) and answer_value.strip():
                selected.extend(
                    item.strip() for item in answer_value.split(",") if item.strip()
                )
            notes = entry.get("notes")
            if isinstance(notes, str) and notes.strip():
                selected.append(notes.strip())
            if selected:
                structured.append(selected)

        return structured or [[answer]]


def build_plugin() -> OpenCodePlugin:
    return OpenCodePlugin()
