"""Claude Code backend plugin.

Owns the per-backend invariants that the runtime previously hard-coded:
permission-mode catalogue, model catalogue, capability flags, transport
adapter wiring, lifecycle (start/restore/import), control surface
(set_model/effort/permission_mode), thread enumeration, and the
system-note formatters. The runtime delegates by id; backend literals
no longer leak into runtime.py.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel

from waypoint.backends.capabilities import (
    BackendCapabilities,
    ModelSource,
)
from waypoint.backends.claude_code.adapter import ClaudeCliAdapter, ClaudeCliError
from waypoint.backends.claude_code.models import (
    CLAUDE_EFFORT_LEVELS,
    DEFAULT_CLAUDE_MODELS,
)
from waypoint.backends.claude_code.permission_modes import (
    CLAUDE_PERMISSION_MODE_SPECS,
    CLAUDE_PERMISSION_MODES,
)
from waypoint.backends.claude_code.remote import build_remote_claude_launch_factory
from waypoint.backends.claude_code.runtime_hook import ensure_claude_hook_bundle
from waypoint.backends.claude_code.threads import (
    ClaudeThreadInfo,
    find_local_claude_thread,
    list_local_claude_threads,
)
from waypoint.backends.claude_code.threads_remote import RemoteClaudeThreadEnumerator
from waypoint.git_meta import GitMeta
from waypoint.schemas import (
    ClaudeThreadImportRequest,
    ClaudeThreadSummary,
    SessionCreateRequest,
    SessionEnvelope,
    SessionRecord,
    SessionSource,
    SessionStatus,
    SessionTransport,
)
from waypoint.server_config import SshLaunchTargetConfig
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


log = logging.getLogger("waypoint.backends.claude_code")


class ClaudeCodePlugin:
    id = "claude_code"
    transport_id = "claude_cli"
    label = "Claude Code"
    import_request_schema: type[BaseModel] | None = ClaudeThreadImportRequest
    capabilities = BackendCapabilities(
        is_structured=True,
        supports_resume=False,
        supports_set_model_inline=True,
        supports_set_effort_inline=False,
        supports_set_effort_with_restart=True,
        supports_set_permission_mode_inline=True,
        supports_thread_discovery=True,
        supports_thread_import=True,
        supports_slash_compact=False,
        permission_modes=CLAUDE_PERMISSION_MODE_SPECS,
        effort_levels=CLAUDE_EFFORT_LEVELS,
        model_source=ModelSource.STATIC,
        slash_commands=(),
        badges={"glyph": "C", "color": "#a78bfa"},
        cli_binary="claude",
        target_aliases=("claude",),
    )

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        # Imported lazily to avoid the cycle: transport → adapter →
        # permission_modes → backends/claude_code/__init__ → plugin.
        from waypoint.backends.claude_code.transport import ClaudeTransport

        return ClaudeTransport(runtime)

    def setup(self, runtime: "SessionRuntime") -> None:
        # Build the PreToolUse webhook bundle, the CLI adapter, and the
        # remote thread enumerator — collectively the "claude side" of
        # the runtime. Resilient to ensure_claude_hook_bundle failing
        # (read-only data dir, missing scripts, etc.); we log and
        # leave runtime.claude=None so the runtime keeps working
        # without Claude support.
        if runtime.claude is not None:
            return  # already provisioned (e.g. tests injected a fake)
        try:
            hook = ensure_claude_hook_bundle(runtime.settings.data_dir)
        except Exception:  # noqa: BLE001
            log.exception("claude hook bundle setup failed; claude support disabled")
            return
        runtime.claude_hook = hook
        hook_url = f"http://127.0.0.1:{runtime.settings.port}"
        runtime.claude = ClaudeCliAdapter(
            runtime._emit_adapter_event,
            hook_settings_path=hook.settings_path,
            hook_secret=hook.secret,
            hook_url=hook_url,
        )
        runtime.claude_thread_enumerator = RemoteClaudeThreadEnumerator(
            hook.thread_enumerator_path
        )

    def is_available_for_managed_launch(self, runtime: "SessionRuntime") -> bool:
        # The Claude adapter is wired up lazily by setup() — if the
        # PreToolUse hook bundle failed to materialise we leave
        # runtime.claude=None and the runtime falls through to the tmux
        # plugin so the user still gets a session.
        return runtime.claude is not None

    def remote_executable(self, launch_target: SshLaunchTargetConfig) -> str:
        return launch_target.claude_bin

    async def terminate_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        if runtime.claude is not None:
            await runtime.claude.terminate_session(session.id)

    def on_session_deleted(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        # Claude caches per-launch-target thread listings remotely;
        # invalidate so a re-import after delete sees the freed slot.
        if session.launch_target_id and runtime.claude_thread_enumerator is not None:
            runtime.claude_thread_enumerator.invalidate(session.launch_target_id)

    def register_routes(self, app: FastAPI, context: Any) -> None:
        # Mount the PreToolUse approval webhook here so api.py stays
        # backend-agnostic. The hook itself runs inside the Claude CLI
        # subprocess and POSTs back to /api/internal/hooks/claude/approval
        # with a shared secret; a third backend has no business
        # registering the same route.
        @app.post("/api/internal/hooks/claude/approval")
        async def claude_hook_approval(request: Request) -> Any:
            secret = request.headers.get("x-waypoint-hook-secret", "")
            hook = context.runtime.claude_hook
            if hook is None or not hook.secret or secret != hook.secret:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="invalid hook secret",
                )
            if context.runtime.claude is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="claude adapter is not initialized",
                )
            try:
                payload = await request.json()
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="invalid json"
                ) from exc
            return await context.runtime.claude.await_approval(payload)

    def validate_permission_mode(self, mode: str | None) -> str | None:
        if mode is None or mode == "":
            return None
        if mode not in CLAUDE_PERMISSION_MODES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"unsupported {self.id} permission mode: {mode}; "
                    f"expected one of {', '.join(CLAUDE_PERMISSION_MODES)}"
                ),
            )
        return mode

    def static_model_options(self, runtime: "SessionRuntime") -> list[Any]:
        # Settings carries the (configurable) Claude model catalogue. The
        # plugin defers to it so deployments can patch the list via
        # waypoint.yaml without forking this module.
        return list(runtime.settings.claude_models)

    @property
    def permission_mode_ids(self) -> tuple[str, ...]:
        return CLAUDE_PERMISSION_MODES

    async def apply_permission_mode(
        self, runtime: "SessionRuntime", session: SessionRecord, mode: str
    ) -> None:
        if runtime.claude is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude adapter is not configured on this backend",
            )
        try:
            await runtime.claude.set_permission_mode(session.id, mode)
        except Exception as exc:  # noqa: BLE001 — surface adapter errors as 400
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    async def apply_model(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        model: str | None,
    ) -> None:
        if runtime.claude is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude adapter is not configured on this backend",
            )
        try:
            await runtime.claude.set_model(session.id, model)
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
        """Returns True when the runtime should also publish a system
        note describing the effort swap; False signals "nothing changed".
        """
        if runtime.claude is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude adapter is not configured on this backend",
            )
        # Claude has no in-process effort knob — set_effort terminates the
        # CLI and respawns it with `--resume <id> --effort <new>`. Skip
        # the swap when the value is unchanged so we don't restart for
        # nothing.
        if effort == (session.effort or None):
            return False
        try:
            await runtime.claude.set_effort(session.id, effort)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        return True

    async def list_models(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        default_model = runtime.settings.default_models.get(self.id)
        default_effort = runtime.settings.default_efforts.get(self.id)
        options = [
            opt.model_dump(mode="json") for opt in runtime.settings.claude_models
        ]
        if default_model is None:
            for opt in runtime.settings.claude_models:
                if opt.is_default:
                    default_model = opt.id
                    break
        return {
            "backend": self.id,
            "models": options,
            "default_model": default_model,
            "default_effort": default_effort,
            "supports_free_text": True,
        }

    def effort_swap_message(self, effort: str | None) -> str:
        return _claude_effort_swap_message(effort)

    async def maybe_handle_input(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        request: Any,
    ) -> SessionRecord | None:
        # Claude has no slash routing today; let the runtime forward
        # everything to the structured stdin path.
        return None

    async def answer_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        answer: str,
        tool_use_id: str | None,
        answers: list[dict[str, Any]] | None,
    ) -> SessionRecord:
        if runtime.claude is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="answer-question is only supported for Claude sessions",
            )
        try:
            handled = await runtime.claude.respond_to_ask_question(
                session.id, answer, tool_use_id
            )
        except ClaudeCliError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        if not handled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="no pending question for this session",
            )
        # Stash structured per-question answers + notes so the frontend
        # renders this user_input as a styled "answers" card instead of
        # the raw `"<question>"="<answer>" user notes: …` payload Claude
        # was tuned around.
        extra: dict[str, Any] = {"kind": "ask_user_question_answer"}
        if answers:
            extra["answers"] = answers
        if tool_use_id:
            extra["tool_use_id"] = tool_use_id
        # Same ordering as handle_input: flip status to RUNNING before
        # _record_user_event broadcasts the session_state snapshot,
        # otherwise the spinner stays off until Claude's next chunk.
        updated = runtime.storage.update_session(
            session.id, status=SessionStatus.RUNNING
        )
        await runtime._record_user_event(
            session.id, answer, submit=True, extra_metadata=extra
        )
        return updated

    async def post_approval(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        # Side-effect of an ExitPlanMode approval: the Claude adapter
        # has already flipped the binary's permission mode to default
        # via set_permission_mode. Sync storage + broadcast so the UI
        # pill reflects the change instead of staying stuck on "plan".
        if runtime.claude is None:
            return
        current = runtime.claude.session_permission_mode(session.id)
        if current is None:
            return
        previous = session.permission_mode or "default"
        if current == previous:
            return
        runtime.storage.update_session(session.id, permission_mode=current)
        await runtime.broadcast.publish(
            SessionEnvelope(
                type="session_list_update",
                payload={
                    "sessions": [
                        item.model_dump(mode="json") for item in runtime.list_sessions()
                    ]
                },
            )
        )

    async def restore_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        if runtime.claude is None:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                "Claude adapter unavailable; cannot restore",
                status=SessionStatus.ERROR,
            )
            return
        if not session.thread_id:
            runtime.storage.update_session(session.id, status=SessionStatus.EXITED)
            await runtime._record_system_event(
                session.id,
                "Claude session has no claude_session_id; marking exited",
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
                f"Claude session launch target {session.launch_target_id} is no longer configured",
                status=SessionStatus.ERROR,
            )
            return
        try:
            await runtime.claude.restore_session(
                session.id,
                session.cwd,
                session.thread_id,
                self.launch_factory(runtime, session.launch_target_id),
                permission_mode=session.permission_mode,
                model=session.model,
                effort=session.effort,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "claude restore failed",
                extra={
                    "session_id": session.id,
                    "claude_session_id": session.thread_id,
                },
            )
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"Claude session restore failed: {exc}",
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
        claude_session_id: str,
        cwd: str | None,
        launch_target: SshLaunchTargetConfig | None,
    ) -> str:
        if launch_target is not None:
            return (
                f"Claude session started via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd or launch_target.default_cwd}) ({claude_session_id})"
            )
        return f"Claude session started ({claude_session_id})"

    def format_restore_message(
        self,
        runtime: "SessionRuntime",
        cwd: str | None,
        launch_target_id: str | None,
    ) -> str:
        launch_target = runtime._find_launch_target(launch_target_id)
        if launch_target is not None:
            return (
                f"Claude session restored via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd or launch_target.default_cwd})"
            )
        return "Claude session restored from previous backend process"

    def format_import_message(
        self,
        cwd: str,
        launch_target: SshLaunchTargetConfig | None,
    ) -> str:
        if launch_target is not None:
            return (
                f"Imported stored Claude thread via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd})"
            )
        return f"Imported stored Claude thread ({cwd})"

    # --- launch / discovery helpers ----------------------------------

    def launch_factory(self, runtime: "SessionRuntime", launch_target_id: str | None):
        launch_target = runtime._find_launch_target(launch_target_id)
        if (
            launch_target is None
            or runtime.claude_hook is None
            or runtime.claude is None
        ):
            return None
        return build_remote_claude_launch_factory(
            launch_target,
            hook_script_path=runtime.claude_hook.hook_script_path,
            hook_secret=runtime.claude_hook.secret,
            local_backend_port=runtime.settings.port,
        )

    def generate_session_id(self) -> str:
        return str(uuid.uuid4())

    def _thread_summary(self, info: ClaudeThreadInfo) -> ClaudeThreadSummary:
        return ClaudeThreadSummary(
            id=info.id,
            title=info.title,
            cwd=info.cwd,
            repo_name=info.repo_name,
            branch=info.branch,
            preview=info.preview,
            created_at=info.created_at,
            updated_at=info.updated_at,
        )

    def _find_imported_session(
        self,
        runtime: "SessionRuntime",
        thread_id: str,
        launch_target_id: str | None,
    ) -> SessionRecord | None:
        for session in runtime.storage.list_sessions():
            if session.backend != self.id:
                continue
            if session.thread_id != thread_id:
                continue
            if session.launch_target_id != launch_target_id:
                continue
            return session
        return None

    async def list_threads(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
    ) -> list[ClaudeThreadSummary]:
        if runtime.claude is None:
            return []
        imported = {
            (session.launch_target_id, session.thread_id)
            for session in runtime.storage.list_sessions()
            if session.backend == self.id and session.thread_id
        }
        if launch_target_id is None:
            infos = await asyncio.to_thread(list_local_claude_threads)
        else:
            target = runtime._resolve_launch_target(launch_target_id, self.id)
            if target is None or runtime.claude_thread_enumerator is None:
                return []
            infos = await runtime.claude_thread_enumerator.list(target)
        return [
            self._thread_summary(info)
            for info in infos
            if (launch_target_id, info.id) not in imported
        ]

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
        if runtime.claude is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude adapter is not configured on this backend",
            )
        raw_log.touch(exist_ok=True)
        claude_session_id = self.generate_session_id()
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=request.backend,
            source=SessionSource.MANAGED,
            transport=SessionTransport.CLAUDE_CLI,
            title=title,
            cwd=request.cwd,
            launch_target_id=launch_target.id if launch_target else None,
            repo_name=git_meta.repo_name,
            branch=git_meta.branch,
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            thread_id=claude_session_id,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            permission_mode=permission_mode,
            model=resolved_model,
            effort=resolved_effort,
        )
        runtime.storage.create_session(session)
        try:
            await runtime.claude.start_session(
                session_id,
                request.cwd,
                claude_session_id,
                self.launch_factory(runtime, session.launch_target_id),
                permission_mode=session.permission_mode,
                model=session.model,
                effort=session.effort,
            )
        except (ClaudeCliError, FileNotFoundError, OSError) as exc:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await runtime._record_system_event(
            session.id,
            self.format_start_message(claude_session_id, request.cwd, launch_target),
            status=SessionStatus.IDLE,
        )
        return runtime.get_session(session.id)

    async def import_thread(
        self,
        runtime: "SessionRuntime",
        request: ClaudeThreadImportRequest,
    ) -> SessionRecord:
        if runtime.claude is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="claude adapter is not initialized",
            )
        launch_target = runtime._resolve_launch_target(
            request.launch_target_id, self.id
        )
        existing = self._find_imported_session(
            runtime, request.thread_id, request.launch_target_id
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude thread already imported",
            )
        if launch_target is None:
            info = await asyncio.to_thread(find_local_claude_thread, request.thread_id)
        else:
            if runtime.claude_thread_enumerator is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="claude thread enumerator is not initialized",
                )
            info = await runtime.claude_thread_enumerator.find(
                launch_target, request.thread_id
            )
        if info is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="claude thread not found",
            )
        if launch_target is None:
            cwd_path = Path(info.cwd).expanduser()
            if not cwd_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"claude thread cwd {info.cwd} no longer exists; "
                        "cannot resume"
                    ),
                )
            cwd = str(cwd_path)
        else:
            # Remote cwd lives on the SSH host; we can't stat it from here.
            cwd = info.cwd
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
            transport=SessionTransport.CLAUDE_CLI,
            title=info.title,
            cwd=cwd,
            launch_target_id=launch_target.id if launch_target else None,
            repo_name=info.repo_name,
            branch=info.branch,
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            thread_id=info.id,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            permission_mode="default",
        )
        runtime.storage.create_session(session)
        try:
            await runtime.claude.restore_session(
                session.id,
                cwd,
                info.id,
                self.launch_factory(runtime, session.launch_target_id),
                permission_mode=session.permission_mode,
                model=session.model,
                effort=session.effort,
            )
        except (ClaudeCliError, FileNotFoundError, OSError) as exc:
            log.exception(
                "claude import failed",
                extra={
                    "session_id": session.id,
                    "claude_session_id": info.id,
                    "launch_target_id": session.launch_target_id,
                },
            )
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"Claude thread import failed: {exc}",
                status=SessionStatus.ERROR,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to import claude thread: {exc}",
            ) from exc
        if launch_target is not None and runtime.claude_thread_enumerator is not None:
            runtime.claude_thread_enumerator.invalidate(launch_target.id)
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await runtime._record_system_event(
            session.id,
            self.format_import_message(cwd, launch_target),
            status=SessionStatus.IDLE,
            metadata={"imported_thread_id": info.id},
        )
        return runtime.get_session(session.id)


def _claude_effort_swap_message(effort: str | None) -> str:
    if effort:
        return f"Restarted Claude session with --effort {effort}"
    return "Restarted Claude session with default effort"


def build_plugin() -> ClaudeCodePlugin:
    return ClaudeCodePlugin()


__all__ = [
    "CLAUDE_EFFORT_LEVELS",
    "CLAUDE_PERMISSION_MODES",
    "DEFAULT_CLAUDE_MODELS",
    "ClaudeCodePlugin",
    "build_plugin",
]
