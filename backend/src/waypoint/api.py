import asyncio
import json
import logging
import mimetypes
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from waypoint.auth import TokenStore, require_token
from waypoint.backends import BackendRegistry
from waypoint.backends.account_profiles import redacted_profile_metadata
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.backends.tmux.renderer import (
    Osc52Extractor,
    SyncFrameTracker,
    make_renderer,
)
from waypoint.presets import (
    redact_preset,
    resolve_schedule_create_request,
    resolve_session_create_request,
)
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    AssistantAttachRequest,
    AssistantResetRequest,
    AssistantSummary,
    BoardEntryUpdateRequest,
    BoardPostRequest,
    InboxBatchDeleteRequest,
    InboxBatchDeleteResponse,
    InboxBlockSubmitRequest,
    InboxPostRequest,
    InboxStatus,
    LaunchSettingsUpdateRequest,
    LaunchTargetConnectRequest,
    LaunchTargetConnectResponse,
    LoginRequest,
    MeResponse,
    ScheduledMessageCreateRequest,
    ScheduleLaunchRequest,
    SessionAnswerQuestionRequest,
    SessionApprovalRequest,
    SessionAttachRequest,
    SessionCompletionsResponse,
    SessionEffortRequest,
    SessionEnvelope,
    SessionInputRequest,
    SessionLaunchRequest,
    SessionModelRequest,
    SessionPermissionModeRequest,
    SessionPlanApprovalRequest,
    SessionPresetCreateRequest,
    SessionPresetListResponse,
    SessionPresetUpdateRequest,
    SessionRecord,
    SessionStatus,
    SessionTagsUpdateRequest,
    SessionTitleRequest,
)
from waypoint.settings import Settings, load_settings
from waypoint.storage import (
    InboxBlockNotFoundError,
    InboxBlockTypeError,
    Storage,
)
from waypoint.tailnet import fetch_snapshot
from waypoint.usage_dashboard import build_dashboard
from waypoint.workspace_git import git_file_diff, git_list_files, git_status
from waypoint.workspace_preview import (
    WorkspacePathError,
    is_denied,
    list_dir,
    rank_files,
    read_text_capped,
    relative_to_base,
    resolve_in_base,
    walk_files,
)

log = logging.getLogger("waypoint.api")


def session_matches_tag_filters(tags: dict[str, str], filters: list[str]) -> bool:
    """Whether ``tags`` satisfies every filter (AND semantics).

    A ``key=value`` filter matches on exact value; a bare ``key`` filter matches
    on key presence regardless of value.
    """
    for spec in filters:
        key, sep, value = spec.partition("=")
        if sep:
            if tags.get(key) != value:
                return False
        elif key not in tags:
            return False
    return True


def _descendant_ids(sessions: list[SessionRecord], root_id: str) -> set[str]:
    """Ids of every transitive descendant of ``root_id`` (excluding the root).

    Walks the ``spawner_session_id`` parent pointers with a visited set so a
    cycle or an orphaned subtree can't loop forever.
    """
    children: dict[str, list[str]] = {}
    for session in sessions:
        if session.spawner_session_id is not None:
            children.setdefault(session.spawner_session_id, []).append(session.id)
    found: set[str] = set()
    queue = list(children.get(root_id, []))
    while queue:
        current = queue.pop()
        if current in found or current == root_id:
            continue
        found.add(current)
        queue.extend(children.get(current, []))
    return found


def _backend_descriptors(
    registry: BackendRegistry, settings: Settings
) -> list[dict[str, Any]]:
    """Serialise every registered backend for the frontend catalogue.

    The frontend consumes this from ``GET /api/backends`` (and from the
    ``backends`` field on ``GET /api/me``) instead of mirroring the
    permission modes / model sources / badge palettes in TypeScript.
    Adding a new plugin shows up in the picker the moment it registers.
    """
    payload: list[dict[str, Any]] = []
    for plugin in registry.all():
        caps = plugin.capabilities
        payload.append(
            {
                "id": plugin.id,
                "transport_id": plugin.transport_id,
                # Transports this agent can be driven over (its native one plus
                # any pane wrapper / tty-tail it pairs with) and the default
                # when a launch doesn't pin one. Lets the picker render an
                # agent-primary control with a transport/fidelity toggle.
                "supported_transports": list(registry.supported_transports(plugin.id)),
                "default_transport": getattr(
                    plugin, "default_transport", plugin.transport_id
                ),
                "label": plugin.label,
                "badges": dict(caps.badges),
                "default_launch_env": dict(settings.plugin_config(plugin.id).env),
                # Redacted global account-profile metadata ({id, label,
                # config_dir_key} only). Non-empty for agent backends that host
                # profiles (claude_code, codex); empty otherwise. Target-merged
                # profiles live on GET /api/me per launch target.
                "account_profiles": redacted_profile_metadata(settings, plugin.id),
                # The flat ``capabilities`` object stays byte-identical for
                # existing consumers; the split sub-objects are emitted
                # alongside it so the frontend can migrate to the (agent,
                # transport) axes without an immediate break.
                "capabilities": caps.model_dump(mode="json"),
                "agent_capabilities": caps.agent_capabilities().model_dump(mode="json"),
                "transport_capabilities": caps.transport_capabilities().model_dump(
                    mode="json"
                ),
            }
        )
    return payload


def _default_preset_id(context: "AppContext") -> str | None:
    default = context.runtime.presets.default()
    return default.id if default is not None else None


class AppContext:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.settings.ensure_dirs()
        self.storage = Storage(self.settings.database_path)
        self.runtime = SessionRuntime(self.settings, self.storage)
        self.tokens = TokenStore(self.settings, self.storage)


def create_app(settings: Settings | None = None) -> FastAPI:
    context = AppContext(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await context.runtime.start()
        try:
            yield
        finally:
            await context.runtime.stop()

    app = FastAPI(title="Waypoint API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=context.settings.cors_origins,
        allow_origin_regex=context.settings.cors_allow_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.context = context

    def token_dependency() -> Callable[..., str]:
        def wrapper(authorization: Annotated[str | None, Header()] = None) -> str:
            return require_token(authorization, context.tokens)

        return wrapper

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/auth/login")
    async def login(request: LoginRequest) -> Any:
        if request.password != context.settings.password:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid password"
            )
        return context.tokens.issue()

    @app.get("/api/me", response_model=MeResponse)
    async def me(_: Annotated[str, Depends(token_dependency())]) -> MeResponse:
        return MeResponse(
            default_backend=context.settings.default_backend,
            default_cwd=context.settings.default_cwd,
            launch_targets=context.runtime.launch_target_summaries(),
            backends=_backend_descriptors(context.runtime.registry, context.settings),
            assistant=context.runtime.assistant_summary(),
            session_presets=[
                redact_preset(preset) for preset in context.runtime.presets.list()
            ],
            default_preset_id=_default_preset_id(context),
        )

    @app.post(
        "/api/launch-targets/{target_id}/connect",
        response_model=LaunchTargetConnectResponse,
    )
    async def connect_launch_target(
        target_id: str,
        body: LaunchTargetConnectRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> LaunchTargetConnectResponse:
        # The password is used once to seed the ControlMaster socket and is
        # never logged or persisted; do not echo ``body`` anywhere.
        result = await context.runtime.connect_launch_target(target_id, body.password)
        return LaunchTargetConnectResponse(
            target_id=result.target_id,
            connected=result.connected,
            detail=result.detail,
        )

    @app.get(
        "/api/launch-targets/{target_id}/status",
        response_model=LaunchTargetConnectResponse,
    )
    async def launch_target_status(
        target_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> LaunchTargetConnectResponse:
        result = await context.runtime.launch_target_status(target_id)
        return LaunchTargetConnectResponse(
            target_id=result.target_id,
            connected=result.connected,
            detail=result.detail,
        )

    @app.post(
        "/api/launch-targets/{target_id}/disconnect",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def disconnect_launch_target(
        target_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> None:
        await context.runtime.disconnect_launch_target(target_id)

    @app.post("/api/assistant/reset", response_model=AssistantSummary)
    async def assistant_reset(
        body: AssistantResetRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> AssistantSummary:
        # Rebuild the assistant on a fresh thread — clear context (same backend)
        # or switch backends. The old thread is demoted to a normal stopped
        # session, never deleted.
        return await context.runtime.reset_assistant(
            backend=body.backend,
            transport=body.transport,
            model=body.model,
            effort=body.effort,
            permission_mode=body.permission_mode,
        )

    @app.post("/api/assistant/attach", response_model=AssistantSummary)
    async def assistant_attach(
        body: AssistantAttachRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> AssistantSummary:
        # Adopt an existing backend-native thread as the assistant, replacing the
        # current thread (which is demoted to a normal stopped session).
        return await context.runtime.attach_assistant(
            backend=body.backend,
            thread_id=body.thread_id,
            launch_target_id=body.launch_target_id,
        )

    @app.post("/api/assistant/terminate", response_model=AssistantSummary)
    async def assistant_terminate(
        _: Annotated[str, Depends(token_dependency())],
    ) -> AssistantSummary:
        return await context.runtime.terminate_assistant()

    @app.post("/api/assistant/reattach", response_model=AssistantSummary)
    async def assistant_reattach(
        _: Annotated[str, Depends(token_dependency())],
    ) -> AssistantSummary:
        return await context.runtime.reattach_assistant()

    @app.get("/api/backends")
    async def list_backends(
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        return {
            "backends": _backend_descriptors(context.runtime.registry, context.settings)
        }

    # Plugin-registered routes (e.g. the Claude PreToolUse hook) come
    # in here so api.py stays backend-agnostic.
    for plugin in context.runtime.registry.all():
        plugin.register_routes(app, context)

    @app.get("/api/tailnet/peers")
    async def tailnet_peers(_: Annotated[str, Depends(token_dependency())]) -> Any:
        snapshot = await fetch_snapshot()
        return snapshot.model_dump(mode="json")

    @app.get("/api/sessions")
    async def list_sessions(
        _: Annotated[str, Depends(token_dependency())],
        spawned_by: Annotated[str | None, Query()] = None,
        tag: Annotated[list[str] | None, Query()] = None,
        recursive: Annotated[bool, Query()] = False,
    ) -> Any:
        all_sessions = context.runtime.list_sessions()
        if spawned_by is not None:
            if recursive:
                wanted = _descendant_ids(all_sessions, spawned_by)
                all_sessions = [s for s in all_sessions if s.id in wanted]
            else:
                all_sessions = [
                    s for s in all_sessions if s.spawner_session_id == spawned_by
                ]
        if tag:
            all_sessions = [
                s for s in all_sessions if session_matches_tag_filters(s.tags, tag)
            ]
        sessions = [session.model_dump(mode="json") for session in all_sessions]
        return {"sessions": sessions}

    @app.get("/api/backends/{backend}/threads")
    async def list_backend_threads(
        backend: str,
        _: Annotated[str, Depends(token_dependency())],
        launch_target_id: Annotated[str | None, Query()] = None,
    ) -> Any:
        if not context.runtime.registry.has_backend(backend):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown backend: {backend}",
            )
        plugin = context.runtime.registry.get(backend)
        if not plugin.capabilities.supports_thread_discovery:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"thread discovery is not supported for {backend}",
            )
        # A password-auth target with no live master can't be reached; skip the
        # remote enumeration instead of letting it fail/stall the picker.
        if context.runtime.remote_probe_blocked(launch_target_id):
            return {"threads": []}
        threads = [
            thread.model_dump(mode="json")
            for thread in await plugin.list_threads(context.runtime, launch_target_id)
        ]
        return {"threads": threads}

    @app.post("/api/sessions")
    async def create_session(
        request: SessionLaunchRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        resolved, preset = resolve_session_create_request(context.storage, request)
        session = await context.runtime.create_session(
            resolved,
            preset_id=preset.id if preset else None,
            preset_name=preset.name if preset else None,
        )
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/backends/{backend}/sessions/import")
    async def import_backend_thread(
        backend: str,
        body: dict[str, Any],
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        # The runtime owns import orchestration: it validates the agent and a
        # pinned transport, resolves the (agent, transport) driver, and
        # persists backend=agent — mirroring create_session.
        session = await context.runtime.import_thread(backend, body)
        return {"session": session.model_dump(mode="json")}

    @app.delete("/api/backends/{backend}/threads/{thread_id}")
    async def delete_backend_thread(
        backend: str,
        thread_id: str,
        _: Annotated[str, Depends(token_dependency())],
        launch_target_id: Annotated[str | None, Query()] = None,
    ) -> Any:
        if not context.runtime.registry.has_backend(backend):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown backend: {backend}",
            )
        plugin = context.runtime.registry.get(backend)
        if not plugin.capabilities.supports_thread_delete:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"thread deletion is not supported for {backend}",
            )
        # Refuse to delete a transcript still backing a Waypoint session — its
        # adapter resumes from that file, so removing it would break the live
        # session. (Discovery already hides imported threads from the list.)
        for session in context.runtime.storage.list_sessions():
            if session.backend == backend and plugin.native_thread_id(session) == (
                thread_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"thread {thread_id} is in use by session {session.id}; "
                        "delete that session first"
                    ),
                )
        deleted = await plugin.delete_thread(
            context.runtime, thread_id, launch_target_id
        )
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no resumable thread {thread_id} for {backend}",
            )
        return {"deleted": thread_id}

    @app.get("/api/sessions/{session_id}")
    async def get_session(
        session_id: str, _: Annotated[str, Depends(token_dependency())]
    ) -> Any:
        session = context.runtime.get_session(session_id)
        return {"session": session.model_dump(mode="json")}

    @app.get(
        "/api/sessions/{session_id}/completions",
        response_model=SessionCompletionsResponse,
    )
    async def session_completions(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
        trigger: Annotated[str, Query(min_length=1, max_length=8)] = "/",
        prefix: Annotated[str, Query(max_length=256)] = "",
        force_refresh: Annotated[bool, Query()] = False,
    ) -> SessionCompletionsResponse:
        completions, refreshing = await context.runtime.get_command_completions(
            session_id,
            trigger=trigger,
            prefix=prefix,
            force_refresh=force_refresh,
        )
        return SessionCompletionsResponse(
            completions=completions,
            refreshing=refreshing,
        )

    @app.post("/api/sessions/{session_id}/input")
    async def session_input(
        session_id: str,
        request: SessionInputRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.handle_input(session_id, request)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/{session_id}/attachments")
    async def upload_attachment(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
        file: Annotated[UploadFile, File()],
        pin: Annotated[bool, Form()] = False,
    ) -> Any:
        # 404 on an unknown session before persisting anything.
        context.runtime.get_session(session_id)
        max_bytes = context.settings.max_upload_bytes
        data = await file.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"attachment exceeds {max_bytes} byte limit",
            )
        spec = context.runtime.attachments.save(
            session_id,
            data=data,
            filename=file.filename or "file",
            content_type=file.content_type,
        )
        if pin:
            context.runtime.attachments.mark_pinned(session_id, [spec.id])
        return spec.model_dump(mode="json")

    @app.get("/api/sessions/{session_id}/attachments")
    async def list_attachments(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> list[dict[str, Any]]:
        context.runtime.get_session(session_id)
        context.runtime.attachments.sweep(
            session_id, context.settings.attachment_orphan_ttl_seconds
        )
        pinned = context.runtime.attachments.pinned_ids(session_id)
        return [
            {
                **spec.model_dump(mode="json"),
                "uploaded_at": uploaded_at,
                "pinned": spec.id in pinned,
            }
            for spec, uploaded_at in context.runtime.attachments.entries(session_id)
        ]

    @app.delete("/api/sessions/{session_id}/attachments")
    async def delete_all_attachments(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Response:
        context.runtime.get_session(session_id)
        context.runtime.attachments.discard(session_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/sessions/{session_id}/attachments/{attachment_id}")
    async def serve_attachment(
        session_id: str,
        attachment_id: str,
        token: Annotated[str, Query()] = "",
    ) -> FileResponse:
        # ``<img>`` tags can't send an Authorization header, so this mirrors
        # the WebSocket endpoints and takes the token as a query parameter.
        if not context.tokens.validate(token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token"
            )
        resolved = context.runtime.attachments.resolve(session_id, attachment_id)
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="attachment not found"
            )
        spec, path = resolved
        return FileResponse(
            path,
            media_type=spec.mime,
            filename=spec.filename,
            content_disposition_type="inline",
        )

    def _workspace_session(session_id: str) -> SessionRecord:
        if not context.settings.workspace_preview_enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="disabled"
            )
        session = context.runtime.get_session(session_id)
        if session.launch_target_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="workspace preview unavailable for remote sessions",
            )
        return session

    @app.get("/api/sessions/{session_id}/workspace/tree")
    async def workspace_tree(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
        path: Annotated[str, Query()] = "",
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    ) -> Any:
        session = _workspace_session(session_id)
        base = Path(session.worktree_path or session.cwd)
        try:
            entries, truncated, overflow, resolved_dir = list_dir(
                base,
                path,
                limit,
                denylist=context.settings.workspace_denylist,
                follow_symlinks=context.settings.workspace_follow_symlinks,
                offset=offset,
            )
        except WorkspacePathError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="workspace path denied"
            ) from exc
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workspace path not found"
            ) from exc
        return {
            "root": {"cwd": session.cwd, "worktree_path": session.worktree_path},
            "path": relative_to_base(base, resolved_dir),
            "entries": entries,
            "offset": offset,
            "truncated": truncated,
            "overflow": overflow,
        }

    @app.get("/api/sessions/{session_id}/workspace/find")
    async def workspace_find(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
        q: Annotated[str, Query()] = "",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> Any:
        session = _workspace_session(session_id)
        query = q.strip()
        if not query:
            return {"matches": [], "truncated": False}
        base = Path(session.worktree_path or session.cwd)
        denylist = context.settings.workspace_denylist
        # Prefer git's index (fast, .gitignore-aware); fall back to a capped walk
        # for non-repo workspaces.
        listed = await git_list_files(base)
        walk_truncated = False
        if listed is None:
            candidates, walk_truncated = await asyncio.to_thread(
                walk_files,
                base,
                denylist,
                context.settings.workspace_follow_symlinks,
            )
        else:
            candidates = listed
        matches, rank_truncated = rank_files(query, candidates, denylist, limit)
        return {
            "matches": [{"path": path, "kind": "file"} for path in matches],
            "truncated": walk_truncated or rank_truncated,
        }

    @app.get("/api/sessions/{session_id}/workspace/file")
    async def workspace_file(
        session_id: str,
        authorization: Annotated[str | None, Header()] = None,
        path: Annotated[str, Query()] = "",
        raw: Annotated[bool, Query()] = False,
        token: Annotated[str, Query()] = "",
    ) -> Any:
        if raw:
            # ``<img>`` tags cannot send an Authorization header.
            if not context.tokens.validate(token):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token"
                )
        else:
            require_token(authorization, context.tokens)
        session = _workspace_session(session_id)
        base = Path(session.worktree_path or session.cwd)
        try:
            if is_denied(path, context.settings.workspace_denylist):
                raise WorkspacePathError("path is denied")
            resolved = resolve_in_base(
                base,
                path,
                follow_symlinks=context.settings.workspace_follow_symlinks,
            )
            if not resolved.exists() or not resolved.is_file():
                raise FileNotFoundError(resolved)
            if raw:
                media_type = (
                    mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
                )
                return FileResponse(
                    resolved,
                    media_type=media_type,
                    filename=resolved.name,
                    content_disposition_type="inline",
                )
            stat = resolved.stat()
            content, truncated, binary, encoding = read_text_capped(
                resolved, context.settings.workspace_max_file_bytes
            )
        except WorkspacePathError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="workspace path denied"
            ) from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workspace path not found"
            ) from exc
        return {
            "path": relative_to_base(base, resolved),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "encoding": encoding,
            "truncated": truncated,
            "binary": binary,
            "content": content,
        }

    @app.get("/api/sessions/{session_id}/workspace/resolve")
    async def workspace_resolve(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
        path: Annotated[str, Query()] = "",
    ) -> Any:
        # ``path`` may be absolute or base-relative; both must resolve inside the
        # workspace. Used by the transcript to turn an agent-printed filesystem
        # path into a canonical relative path plus its kind, so the frontend can
        # open a file preview or reveal a directory in the tree.
        session = _workspace_session(session_id)
        base = Path(session.worktree_path or session.cwd)
        try:
            if is_denied(path, context.settings.workspace_denylist):
                raise WorkspacePathError("path is denied")
            resolved = resolve_in_base(
                base,
                path,
                follow_symlinks=context.settings.workspace_follow_symlinks,
            )
            if not resolved.exists():
                raise FileNotFoundError(resolved)
        except WorkspacePathError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="workspace path denied"
            ) from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workspace path not found"
            ) from exc
        return {
            "path": relative_to_base(base, resolved),
            "kind": "dir" if resolved.is_dir() else "file",
        }

    @app.get("/api/sessions/{session_id}/workspace/git/status")
    async def workspace_git_status(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = _workspace_session(session_id)
        disabled: dict[str, Any] = {
            "enabled": False,
            "branch": None,
            "detached": False,
            "files": [],
        }
        if not context.settings.workspace_git_enabled:
            return disabled
        base = Path(session.worktree_path or session.cwd)
        result = await git_status(base)
        if result is None:
            return disabled
        # Hide denied paths so the Changes list matches what the tree, file, and
        # diff endpoints will serve.
        denylist = context.settings.workspace_denylist
        result = result.model_copy(
            update={
                "files": [
                    entry
                    for entry in result.files
                    if not is_denied(entry.path, denylist)
                ]
            }
        )
        return {"enabled": True, **result.model_dump(mode="json")}

    @app.get("/api/sessions/{session_id}/workspace/git/diff")
    async def workspace_git_diff(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
        path: Annotated[str, Query()] = "",
        staged: Annotated[bool, Query()] = False,
    ) -> Any:
        session = _workspace_session(session_id)
        if not context.settings.workspace_git_enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="disabled"
            )
        base = Path(session.worktree_path or session.cwd)
        try:
            if not path or is_denied(path, context.settings.workspace_denylist):
                raise WorkspacePathError("path is denied")
            # Validate the path stays inside the workspace without requiring it to
            # exist on disk — a deleted file still has a diff against HEAD.
            resolve_in_base(
                base,
                path,
                follow_symlinks=context.settings.workspace_follow_symlinks,
            )
        except WorkspacePathError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="workspace path denied"
            ) from exc
        preview = await git_file_diff(
            base,
            path,
            staged=staged,
            max_file_bytes=context.settings.workspace_max_file_bytes,
        )
        if preview is None:
            return {
                "schema_version": 1,
                "phase": "aggregate",
                "files": [],
                "total_additions": 0,
                "total_deletions": 0,
                "truncated": False,
            }
        return preview.model_dump(mode="json")

    @app.delete("/api/sessions/{session_id}/attachments/{attachment_id}")
    async def delete_attachment(
        session_id: str,
        attachment_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Response:
        # Frees a single blob. Called both by the composer (removing a pending
        # upload) and by the session files manager, which can delete a file a
        # sent message still references — that message's transcript card then
        # 404s on its thumbnail, which the client renders as a file fallback.
        context.runtime.get_session(session_id)
        if not context.runtime.attachments.delete(session_id, attachment_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="attachment not found"
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/sessions/{session_id}/attachments/{attachment_id}/pin")
    async def pin_attachment(
        session_id: str,
        attachment_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Response:
        # Exempt the blob from the orphan sweep. 404 on an unknown attachment so
        # a typo doesn't silently pin nothing.
        context.runtime.get_session(session_id)
        if context.runtime.attachments.resolve(session_id, attachment_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="attachment not found"
            )
        context.runtime.attachments.mark_pinned(session_id, [attachment_id])
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.delete("/api/sessions/{session_id}/attachments/{attachment_id}/pin")
    async def unpin_attachment(
        session_id: str,
        attachment_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Response:
        # Re-expose the blob to the orphan sweep. Idempotent: unpinning an
        # already-unpinned (or unknown) id is a clean no-op.
        context.runtime.get_session(session_id)
        context.runtime.attachments.unmark_pinned(session_id, [attachment_id])
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/sessions/{session_id}/interrupt")
    async def session_interrupt(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.interrupt(session_id)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/{session_id}/approve")
    async def session_approve(
        session_id: str,
        request: SessionApprovalRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.approve(session_id, request)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/{session_id}/approve-plan")
    async def session_approve_plan(
        session_id: str,
        request: SessionPlanApprovalRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.approve_plan(session_id, request)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/{session_id}/answer-question")
    async def session_answer_question(
        session_id: str,
        request: SessionAnswerQuestionRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        answers = (
            [item.model_dump(mode="json") for item in request.answers]
            if request.answers
            else None
        )
        session = await context.runtime.answer_question(
            session_id, request.answer, request.tool_use_id, answers
        )
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/{session_id}/resume")
    async def session_resume(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.resume(session_id)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/{session_id}/fork")
    async def session_fork(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.fork_session(session_id)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/{session_id}/reattach")
    async def session_reattach(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        # Distinct from /resume (tmux's "wake up an idle pane"): /reattach
        # re-establishes a structured backend's connection after EXITED/ERROR
        # without requiring the user to send a message first.
        session = await context.runtime.reattach(session_id)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/{session_id}/rate-limit-usage/refresh")
    async def session_refresh_rate_limit_usage(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.refresh_rate_limit_usage(session_id)
        return {"session": session.model_dump(mode="json")}

    @app.get("/api/usage")
    async def get_usage_dashboard(
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        dashboard = build_dashboard(
            context.runtime.list_sessions(), context.runtime.registry
        )
        return dashboard.model_dump(mode="json")

    @app.post("/api/usage/refresh")
    async def refresh_usage_dashboard(
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        # Refresh one representative session per bucket — every session in
        # a bucket shares the same account-level rate limit, so probing
        # one is enough to update the bucket's snapshot.
        dashboard = build_dashboard(
            context.runtime.list_sessions(), context.runtime.registry
        )
        targets = [
            bucket.session_ids[0] for bucket in dashboard.buckets if bucket.session_ids
        ]
        if targets:
            await asyncio.gather(
                *(context.runtime.refresh_rate_limit_usage(sid) for sid in targets),
                return_exceptions=True,
            )
        refreshed = build_dashboard(
            context.runtime.list_sessions(), context.runtime.registry
        )
        return refreshed.model_dump(mode="json")

    @app.post("/api/sessions/{session_id}/terminate")
    async def session_terminate(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.terminate(session_id)
        return {"session": session.model_dump(mode="json")}

    @app.delete("/api/sessions/{session_id}")
    async def session_delete(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
        force: Annotated[bool, Query()] = False,
        prune_branches: Annotated[bool, Query()] = False,
    ) -> Any:
        # `force=true` skips terminate failures entirely — last-resort escape
        # hatch when the adapter is wedged (SSH stuck, etc.) and the
        # graceful path won't complete. `prune_branches=true` force-deletes a
        # worktree session's branch even when unmerged (crew teardown).
        await context.runtime.delete(
            session_id, force=force, prune_branches=prune_branches
        )
        return {"deleted": session_id}

    @app.get("/api/board")
    async def board_channels(_: Annotated[str, Depends(token_dependency())]) -> Any:
        channels = [
            channel.model_dump(mode="json")
            for channel in context.runtime.list_board_channels()
        ]
        return {"channels": channels}

    @app.get("/api/board/{channel}")
    async def board_read(
        channel: str,
        _: Annotated[str, Depends(token_dependency())],
        since: Annotated[int | None, Query()] = None,
        key: Annotated[str | None, Query()] = None,
        limit: Annotated[int | None, Query(ge=1)] = None,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> Any:
        # Paged read for the UI: all cells + a bounded, back-pageable window of
        # the append-log, with the full log count. `since`/`key` stay the
        # unbounded cursor/lookup path the CLI uses.
        if limit is not None or before is not None:
            page, log_total = context.runtime.read_board_channel(
                channel, log_limit=limit, before=before
            )
            return {
                "channel": channel,
                "entries": [entry.model_dump(mode="json") for entry in page],
                "log_total": log_total,
            }
        entries = [
            entry.model_dump(mode="json")
            for entry in context.runtime.list_board_entries(
                channel, since=since, key=key
            )
        ]
        return {"channel": channel, "entries": entries}

    @app.post("/api/board/{channel}")
    async def board_post(
        channel: str,
        body: BoardPostRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        entry = await context.runtime.post_board_entry(channel, body)
        return {"entry": entry.model_dump(mode="json")}

    @app.post("/api/board/{channel}/clear")
    async def board_clear(
        channel: str,
        _: Annotated[str, Depends(token_dependency())],
        keep_last: Annotated[int | None, Query(ge=1)] = None,
    ) -> Any:
        # Remove the channel's posts but keep the (now empty) channel.
        # With keep_last, the N most-recent log posts are retained; cells are
        # always dropped.
        removed = await context.runtime.clear_board_channel(
            channel, keep_last=keep_last
        )
        return {"channel": channel, "cleared": removed}

    @app.delete("/api/board/{channel}")
    async def board_delete(
        channel: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        # Remove the channel entirely, posts and all.
        removed = await context.runtime.delete_board_channel(channel)
        return {"channel": channel, "deleted": removed}

    @app.delete("/api/board/{channel}/entries/{entry_id}")
    async def board_delete_entry(
        channel: str,
        entry_id: int,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        deleted = await context.runtime.delete_board_entry(channel, entry_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="board entry not found")
        return {"channel": channel, "entry_id": entry_id, "deleted": True}

    @app.patch("/api/board/{channel}/entries/{entry_id}")
    async def board_edit_entry(
        channel: str,
        entry_id: int,
        body: BoardEntryUpdateRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        entry = await context.runtime.update_board_entry(channel, entry_id, body)
        if entry is None:
            raise HTTPException(status_code=404, detail="board entry not found")
        return {"entry": entry.model_dump(mode="json")}

    @app.post("/api/inbox")
    async def inbox_post(
        body: InboxPostRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        item = await context.runtime.post_inbox_item(body)
        return {"item": item.model_dump(mode="json")}

    @app.get("/api/inbox")
    async def inbox_list(
        _: Annotated[str, Depends(token_dependency())],
        status_filter: Annotated[InboxStatus | None, Query(alias="status")] = None,
        q: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: Annotated[str | None, Query()] = None,
    ) -> Any:
        page = context.runtime.list_inbox_items(
            status=status_filter, query=q, limit=limit, cursor=cursor
        )
        return page.model_dump(mode="json")

    @app.get("/api/inbox/unresolved-count")
    async def inbox_unresolved_count(
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        # Seeds the cross-session badge before the first WS event arrives.
        return {"unresolved_count": context.runtime.unresolved_inbox_count()}

    # Literal paths registered before ``/api/inbox/{item_id}`` so the id matcher
    # never shadows them (both are POST; the id route is GET, so there is no
    # method overlap either).
    @app.post("/api/inbox/batch-delete")
    async def inbox_batch_delete(
        body: InboxBatchDeleteRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        deleted = await context.runtime.delete_inbox_items(body.item_ids)
        return InboxBatchDeleteResponse(
            deleted_ids=deleted, count=len(deleted)
        ).model_dump(mode="json")

    @app.post("/api/inbox/delete-resolved")
    async def inbox_delete_resolved(
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        deleted = await context.runtime.delete_resolved_inbox_items()
        return InboxBatchDeleteResponse(
            deleted_ids=deleted, count=len(deleted)
        ).model_dump(mode="json")

    @app.get("/api/inbox/{item_id}")
    async def inbox_get(
        item_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        item = context.runtime.get_inbox_item(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="inbox item not found")
        return {"item": item.model_dump(mode="json")}

    @app.post("/api/inbox/{item_id}/blocks/{block_id}")
    async def inbox_submit_block(
        item_id: str,
        block_id: str,
        body: InboxBlockSubmitRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        # One submit carries an optional answer and/or reply; the answer is
        # validated against the target block's type in the storage layer.
        try:
            item = await context.runtime.submit_inbox_block(
                item_id, block_id, answer=body.answer, reply=body.reply
            )
        except InboxBlockNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="inbox block not found"
            ) from exc
        except InboxBlockTypeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="inbox item not found")
        return {"item": item.model_dump(mode="json")}

    @app.post("/api/inbox/{item_id}/read")
    async def inbox_read(
        item_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        item = await context.runtime.mark_inbox_read(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="inbox item not found")
        return {"item": item.model_dump(mode="json")}

    @app.delete("/api/inbox/{item_id}")
    async def inbox_delete(
        item_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        deleted = await context.runtime.delete_inbox_item(item_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="inbox item not found")
        return {"item_id": item_id, "deleted": True}

    @app.patch("/api/sessions/{session_id}/title")
    async def session_set_title(
        session_id: str,
        body: SessionTitleRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.set_title(session_id, body.title)
        return {"session": session.model_dump(mode="json")}

    @app.patch("/api/sessions/{session_id}/tags")
    async def session_set_tags(
        session_id: str,
        body: SessionTagsUpdateRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.set_tags(session_id, body.set, body.unset)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/{session_id}/mode")
    async def session_set_mode(
        session_id: str,
        body: SessionPermissionModeRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.set_permission_mode(session_id, body.mode)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/{session_id}/model")
    async def session_set_model(
        session_id: str,
        body: SessionModelRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.set_model(session_id, body.model)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/{session_id}/effort")
    async def session_set_effort(
        session_id: str,
        body: SessionEffortRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.set_effort(session_id, body.effort)
        return {"session": session.model_dump(mode="json")}

    @app.get("/api/sessions/{session_id}/launch-settings")
    async def session_get_launch_settings(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        return context.runtime.get_launch_settings(session_id).model_dump(mode="json")

    @app.patch("/api/sessions/{session_id}/launch-settings")
    async def session_update_launch_settings(
        session_id: str,
        body: LaunchSettingsUpdateRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.update_launch_settings(session_id, body)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/{session_id}/side-questions/{sqid}/fork")
    async def fork_side_question(
        session_id: str,
        sqid: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        new_session = await context.runtime.fork_side_question(session_id, sqid)
        return {"session": new_session.model_dump(mode="json")}

    @app.delete("/api/sessions/{session_id}/side-questions/{sqid}")
    async def dismiss_side_question(
        session_id: str,
        sqid: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Response:
        session = context.runtime.get_session(session_id)
        plugin = context.runtime.registry.plugin_for(session)
        await plugin.dismiss_side_question(context.runtime, session, sqid)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/backends/{backend}/models")
    async def list_backend_models(
        backend: str,
        _: Annotated[str, Depends(token_dependency())],
        launch_target_id: Annotated[str | None, Query()] = None,
        include_hidden: Annotated[bool, Query()] = False,
    ) -> Any:
        if not context.runtime.registry.has_backend(backend):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown backend: {backend}",
            )
        # Fall back to the local catalogue when the password-auth target isn't
        # connected, rather than SSHing to an unreachable host for model probes.
        if context.runtime.remote_probe_blocked(launch_target_id):
            launch_target_id = None
        return await context.runtime.list_backend_models(
            backend,
            launch_target_id=launch_target_id,
            include_hidden=include_hidden,
        )

    @app.post("/api/sessions/{session_id}/pin")
    async def session_pin(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.set_pinned(session_id, pinned=True)
        return {"session": session.model_dump(mode="json")}

    @app.delete("/api/sessions/{session_id}/pin")
    async def session_unpin(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.set_pinned(session_id, pinned=False)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/attach-tmux")
    async def attach_tmux(
        request: SessionAttachRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.attach_tmux(request)
        return {"session": session.model_dump(mode="json")}

    @app.get("/api/schedules")
    async def list_schedules(_: Annotated[str, Depends(token_dependency())]) -> Any:
        schedules = [
            schedule.model_dump(mode="json")
            for schedule in context.runtime.scheduler.list_schedules()
        ]
        return {"schedules": schedules}

    @app.post("/api/schedules")
    async def create_schedule(
        request: ScheduleLaunchRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        resolved, preset = resolve_schedule_create_request(context.storage, request)
        schedule = context.runtime.scheduler.create_schedule(
            resolved,
            preset_id=preset.id if preset else None,
            preset_name=preset.name if preset else None,
        )
        return {"schedule": schedule.model_dump(mode="json")}

    @app.delete("/api/schedules/{schedule_id}")
    async def cancel_schedule(
        schedule_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        schedule = context.runtime.scheduler.cancel_schedule(schedule_id)
        return {"schedule": schedule.model_dump(mode="json")}

    @app.post("/api/schedules/clear-history")
    async def clear_schedule_history(
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        removed = context.runtime.scheduler.clear_history()
        return {"removed": removed}

    # ── Session presets ──────────────────────────────────────────────────
    @app.get("/api/session-presets", response_model=SessionPresetListResponse)
    async def list_session_presets(
        _: Annotated[str, Depends(token_dependency())],
    ) -> SessionPresetListResponse:
        presets = context.runtime.presets.list()
        return SessionPresetListResponse(
            presets=[redact_preset(preset) for preset in presets],
            default_preset_id=next(
                (preset.id for preset in presets if preset.is_default), None
            ),
        )

    @app.post("/api/session-presets")
    async def create_session_preset(
        request: SessionPresetCreateRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        preset = context.runtime.presets.create(request)
        return {"preset": redact_preset(preset).model_dump(mode="json")}

    # Registered before ``/{preset_id}`` so "default" is not captured as an id.
    @app.delete("/api/session-presets/default")
    async def clear_default_session_preset(
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        context.runtime.presets.set_default(None)
        return {"default_preset_id": None}

    @app.get("/api/session-presets/{preset_id}")
    async def get_session_preset(
        preset_id: str,
        _: Annotated[str, Depends(token_dependency())],
        include_secret_values: bool = Query(default=False),
    ) -> Any:
        preset = context.runtime.presets.require_ref(preset_id)
        if include_secret_values:
            return {"preset": preset.model_dump(mode="json")}
        return {"preset": redact_preset(preset).model_dump(mode="json")}

    @app.patch("/api/session-presets/{preset_id}")
    async def update_session_preset(
        preset_id: str,
        request: SessionPresetUpdateRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        preset = context.runtime.presets.update(preset_id, request)
        return {"preset": redact_preset(preset).model_dump(mode="json")}

    @app.delete("/api/session-presets/{preset_id}")
    async def delete_session_preset(
        preset_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        deleted = context.runtime.presets.delete(preset_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown preset: {preset_id!r}",
            )
        return {"deleted": True}

    @app.post("/api/session-presets/{preset_id}/default")
    async def set_default_session_preset(
        preset_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        preset = context.runtime.presets.set_default(preset_id)
        return {
            "preset": redact_preset(preset).model_dump(mode="json") if preset else None
        }

    @app.get("/api/message-schedules")
    async def list_message_schedules(
        _: Annotated[str, Depends(token_dependency())],
        session_id: str | None = Query(default=None),
    ) -> Any:
        schedules = [
            s.model_dump(mode="json")
            for s in context.runtime.scheduler.list_message_schedules(
                session_id=session_id
            )
        ]
        return {"message_schedules": schedules}

    @app.post("/api/sessions/{session_id}/message-schedules")
    async def create_message_schedule(
        session_id: str,
        body: ScheduledMessageCreateRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        record = context.runtime.scheduler.create_message_schedule(session_id, body)
        return {"message_schedule": record.model_dump(mode="json")}

    @app.delete("/api/message-schedules/{schedule_id}")
    async def cancel_message_schedule(
        schedule_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        record = context.runtime.scheduler.cancel_message_schedule(schedule_id)
        return {"message_schedule": record.model_dump(mode="json")}

    @app.post("/api/message-schedules/clear-history")
    async def clear_message_schedule_history(
        _: Annotated[str, Depends(token_dependency())],
        session_id: str | None = Query(default=None),
    ) -> Any:
        removed = context.runtime.scheduler.clear_message_history(session_id=session_id)
        return {"removed": removed}

    @app.get("/api/sessions/{session_id}/events")
    async def list_events(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
        cursor: Annotated[int | None, Query()] = None,
        messages: Annotated[int | None, Query(ge=1)] = None,
        before_sequence: Annotated[int | None, Query(ge=0)] = None,
    ) -> Any:
        # `cursor` (id-after) is the legacy reconnect/catch-up path; if a
        # caller still passes it, honor those semantics and return *all*
        # events newer than the cursor with no clamp.
        if cursor is not None:
            events = [
                event.model_dump(mode="json")
                for event in context.runtime.session_events(session_id, cursor)
            ]
            return {"events": events, "has_more": False}
        # Tail / before_sequence path: serve a single bounded window of
        # logical chat messages (deltas of one agent reply or one tool
        # pair count as one). Clamp the client-supplied count to the
        # configured page size as both default and upper bound.
        max_page = context.settings.chat_page_messages
        effective_limit = max_page if messages is None else min(messages, max_page)
        page = context.runtime.session_events_page(
            session_id,
            message_limit=effective_limit,
            before_sequence=before_sequence,
        )
        return page.model_dump(mode="json")

    @app.websocket("/ws/sessions")
    async def ws_sessions(websocket: WebSocket) -> None:
        await websocket.accept()
        token = websocket.query_params.get("token", "")
        if not context.tokens.validate(token):
            await websocket.close(code=4401)
            return
        queue = context.runtime.broadcast.subscribe_global()
        try:
            initial = SessionEnvelope(
                type="session_list_update",
                payload={
                    "sessions": [
                        item.model_dump(mode="json")
                        for item in context.runtime.list_sessions()
                    ]
                },
            )
            await websocket.send_json(initial.model_dump(mode="json"))
            while True:
                message = await queue.get()
                await websocket.send_json(message)
        except (WebSocketDisconnect, asyncio.CancelledError):
            # Either the client disconnected or the server is shutting down —
            # treat both as an orderly exit so we don't surface a 500/close
            # 1011 through Starlette's exception machinery (that close attempt
            # during teardown was hanging shutdown).
            pass
        finally:
            context.runtime.broadcast.unsubscribe_global(queue)
            with suppress(Exception):
                await websocket.close()

    @app.websocket("/ws/sessions/{session_id}")
    async def ws_session(websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        token = websocket.query_params.get("token", "")
        if not context.tokens.validate(token):
            await websocket.close(code=4401)
            return
        queue = context.runtime.broadcast.subscribe_session(session_id)
        try:
            session = context.runtime.get_session(session_id)
            await websocket.send_json(
                SessionEnvelope(
                    type="session_state",
                    payload={"session": session.model_dump(mode="json")},
                ).model_dump(mode="json")
            )
            # Hydrate pending side-questions so a fresh-load client sees open
            # cards. Tag them ``hydrated`` so the client can tell a replay-on-
            # connect from a live push and avoid auto-expanding old asides.
            for sq in session.transport_state.get("pending_side_questions", []):
                if isinstance(sq, dict):
                    await websocket.send_json(
                        SessionEnvelope(
                            type="side_question",
                            payload={"side_question": sq, "hydrated": True},
                        ).model_dump(mode="json")
                    )
            while True:
                message = await queue.get()
                await websocket.send_json(message)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            context.runtime.broadcast.unsubscribe_session(session_id, queue)
            with suppress(Exception):
                await websocket.close()

    @app.websocket("/ws/inbox/{item_id}")
    async def ws_inbox(websocket: WebSocket, item_id: str) -> None:
        await websocket.accept()
        token = websocket.query_params.get("token", "")
        if not context.tokens.validate(token):
            await websocket.close(code=4401)
            return
        queue = context.runtime.broadcast.subscribe_inbox(item_id)
        try:
            item = context.runtime.get_inbox_item(item_id)
            if item is None:
                # Already gone at connect: emit the terminal ``deleted`` frame
                # so a ``wait`` resolves to ``gone`` instead of hanging.
                await websocket.send_json(
                    SessionEnvelope(
                        type="inbox_update",
                        payload={"item_id": item_id, "deleted": True, "item": None},
                    ).model_dump(mode="json")
                )
                return
            # Hydrate the full item so ``wait`` can evaluate its condition
            # against the current snapshot before awaiting the next change.
            await websocket.send_json(
                SessionEnvelope(
                    type="inbox_update",
                    payload={
                        "item_id": item.id,
                        "deleted": False,
                        "item": item.model_dump(mode="json"),
                    },
                ).model_dump(mode="json")
            )
            while True:
                message = await queue.get()
                await websocket.send_json(message)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            context.runtime.broadcast.unsubscribe_inbox(item_id, queue)
            with suppress(Exception):
                await websocket.close()

    @app.websocket("/ws/sessions/{session_id}/terminal")
    async def ws_terminal(websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        token = websocket.query_params.get("token", "")
        if not context.tokens.validate(token):
            await websocket.close(code=4401)
            return
        try:
            session = context.runtime.get_session(session_id)
        except HTTPException:
            await websocket.close(code=4404)
            return
        # Only transports with a terminal pane can be mirrored here; backends
        # with no pane (pure structured streams) publish via the event WS.
        plugin = context.runtime.registry.plugin_for(session)
        caps = plugin.capabilities
        if not caps.has_terminal_pane:
            await websocket.close(code=4403)
            return
        terminal_interactive = caps.terminal_interactive
        # Discrete key-bar / scroll-wheel injection is allowed for fully
        # interactive panes and for panes that opt into the narrow escape hatch
        # (claude_tty). Free-form input (input_submit) still requires the full
        # interactive flag.
        terminal_input_injection = terminal_interactive or caps.terminal_key_injection
        terminal_resizable = caps.terminal_resizable
        # Refuse to attach to an already-dead pane — the renderer would
        # seed from a stale capture and the stream would never produce
        # bytes. 4410 tells the frontend to surface the reconnect
        # button rather than auto-retry.
        if session.status == SessionStatus.EXITED:
            await websocket.close(code=4410)
            return
        tmux_state = session.transport_state or {}
        pane = tmux_state.get("tmux_pane") or session.id
        tmux_session = tmux_state.get("tmux_session") or session.id
        adapter = context.runtime.tmux
        raw_log_path = Path(session.raw_log_path)

        # Resizable transports wait for the client's initial resize frame so
        # the seed is rendered at the viewport's dimensions. Non-resizable
        # transports (claude_tty) have their size fixed by the pane manager;
        # query the actual dimensions instead.
        cols = 80
        rows = 24
        if terminal_resizable:
            viewport_cols = 0
            viewport_rows = 0
            try:
                first = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                handshake = json.loads(first)
                if handshake.get("type") == "resize":
                    try:
                        viewport_cols = int(handshake.get("cols", 0))
                        viewport_rows = int(handshake.get("rows", 0))
                    except (TypeError, ValueError):
                        viewport_cols = viewport_rows = 0
                    if viewport_cols > 0 and viewport_rows > 0:
                        with suppress(TmuxError):
                            await adapter.resize_window(
                                tmux_session, viewport_cols, viewport_rows
                            )
                            await adapter.resize_pane(
                                pane, viewport_cols, viewport_rows
                            )
            except WebSocketDisconnect:
                # Client closed during the handshake; nothing left to seed.
                return
            except (TimeoutError, json.JSONDecodeError):
                # No handshake — fall through with default viewport.
                pass
            cols = viewport_cols or 80
            rows = viewport_rows or 24
            # Give the pane process a beat to handle SIGWINCH and emit its
            # redraw before we capture — otherwise the seed reflects the stale
            # pre-resize buffer (cursor at the old pane's bottom row, content
            # clipped to the old size) and the live stream has to overpaint it.
            if viewport_cols and viewport_rows:
                await asyncio.sleep(0.1)
        else:
            # Seed the renderer at the pane's actual current dimensions so
            # the diff encoding matches what the pane produces.
            with suppress(TmuxError):
                cols, rows = await adapter.pane_dimensions(pane)

        # Server-side terminal emulator: bytes from the pane go through
        # pyte, which maintains the authoritative screen state. The
        # renderer emits cell-level deltas — only cells whose final
        # value differs from what xterm last saw — so the client never
        # has to interpret DECSTBM scroll regions, partial sync-output
        # frames, or other sequences browser emulators handle
        # inconsistently from native terminals.
        # Read-only panes can't send mouse input, and mirroring mouse modes
        # would make xterm swallow wheel events (blocking scroll), so only
        # forward them for interactive transports.
        renderer = make_renderer(cols, rows, forward_mouse_modes=terminal_interactive)

        # Seed pyte with the pane's current ANSI snapshot so the renderer
        # has the same starting state the user would see if they ran
        # `tmux attach`. capture-pane's trailing LF would scroll the
        # screen by one row inside pyte, so strip it.
        try:
            snapshot = await adapter.capture_snapshot(pane, start_line=0)
        except TmuxError:
            snapshot = ""
        # capture-pane carries neither the alt-screen toggle nor cursor
        # positioning. Probe tmux for both: a pane on the alt screen
        # (Codex TUI mid-frame) needs us to flip pyte before feeding the
        # snapshot, or the seed lands in pyte's normal buffer and the
        # next agent frame paints over a blank alt screen.
        alt_screen = False
        cursor_pos: tuple[int, int] | None = None
        with suppress(TmuxError):
            alt_flag, cur_col, cur_row = await adapter.pane_screen_state(pane)
            alt_screen = alt_flag
            cursor_pos = (cur_col - 1, cur_row - 1)
        if alt_screen:
            renderer.feed(b"\x1b[?1049h")
        snapshot = snapshot.rstrip("\r\n")
        if snapshot:
            # ``capture-pane`` separates rows with bare LF, but pyte
            # processes LF as line-feed-only (cursor drops a row but
            # keeps its column). Promote LF → CRLF so each row starts
            # at column 0, matching the visual layout of the pane.
            seed_bytes = snapshot.replace("\n", "\r\n").encode(
                "utf-8", errors="replace"
            )
            renderer.feed(seed_bytes)
        if cursor_pos is not None:
            renderer.set_cursor(*cursor_pos)

        # Recover tracked private-mode state (mouse / focus / paste)
        # from the raw_log prefix we're about to skip over. The pane
        # typically requests these once at startup, so a small head-of-
        # file scan covers the realistic case without blocking the WS
        # on a multi-megabyte read.
        PREFIX_SCAN_BYTES = 128 * 1024
        with suppress(OSError):
            if raw_log_path.exists():
                with raw_log_path.open("rb") as fh:
                    prefix_bytes = fh.read(PREFIX_SCAN_BYTES)
                if prefix_bytes:
                    renderer.snoop_modes(prefix_bytes)

        # Non-resizable panes own their geometry server-side, so the client
        # must size its grid to ours rather than fitting to the viewport —
        # otherwise the CUP-positioned deltas below land in the wrong cells.
        # xterm ignores in-band resize ops (CSI 8 t), so announce the size as
        # an out-of-band JSON frame the client applies via term.resize(). It
        # precedes the repaint so the seed lands in the matching grid.
        # Resizable transports drive their own size, so we don't fight them.
        if not terminal_resizable:
            with suppress(Exception):
                await websocket.send_text(
                    json.dumps({"type": "size", "cols": cols, "rows": rows})
                )
        initial_frame = renderer.render_full()
        if initial_frame:
            with suppress(Exception):
                # Bracketed in DECSET 2026 so xterm.js v6 batches the
                # whole seed into one animation frame instead of
                # painting each row separately.
                await websocket.send_text("\x1b[?2026h" + initial_frame + "\x1b[?2026l")

        tail_offset = raw_log_path.stat().st_size if raw_log_path.exists() else 0

        # Codex (and other ratatui apps) brackets each render in
        # DECSET/DECRST 2026 markers. Emitting a diff mid-frame would
        # let xterm paint an intermediate state (cursor briefly on the
        # status row, textbox half-redrawn) so the tracker gates
        # emission on the *byte-level* frame boundary — a substring
        # scan would miss markers split across poll chunks.
        # SYNC_HOLD_TIMEOUT is a defensive flush so a missing end
        # marker can't freeze the viewport indefinitely.
        SYNC_HOLD_TIMEOUT = 0.25
        # Re-check the session record every N polls to detect ``EXITED``
        # promptly (the monitor publishes a session_state event over the
        # broadcast channel, but the terminal WS owns its own loop and
        # we don't want to add a per-session subscription here).
        STATUS_CHECK_INTERVAL_POLLS = 50  # 50 * 20 ms = ~1 s
        frame_tracker = SyncFrameTracker()
        # Pyte absorbs OSC 52 silently — no diff bytes carry it onward —
        # so the wrapped CLI's ``/copy`` (Claude) or clipboard write
        # (Codex) never reaches xterm. Extract the payload upstream and
        # forward it to the session-state socket so the frontend can
        # call ``navigator.clipboard.writeText``.
        osc52_extractor = Osc52Extractor()
        # Both stream_loop (chunk arrivals, defensive flush) and
        # recv_loop (post-resize repaint) call emit_diff. Without a
        # lock, render_diff() updates from one task can interleave with
        # send_text() suspensions in the other and corrupt the wire.
        emit_lock = asyncio.Lock()

        async def emit_diff() -> None:
            async with emit_lock:
                diff = renderer.render_diff()
                if not diff:
                    return
                # Wrap the diff in DECSET/DECRST 2026 so xterm.js v6
                # batches every cell mutation into one animation frame.
                with suppress(Exception):
                    await websocket.send_text("\x1b[?2026h" + diff + "\x1b[?2026l")

        async def stream_loop() -> None:
            offset = tail_offset
            frame_open_at: float | None = None
            poll_count = 0

            while True:
                chunk: bytes | None = None
                try:
                    if raw_log_path.exists():
                        size = raw_log_path.stat().st_size
                        if size > offset:
                            with raw_log_path.open("rb") as fh:
                                fh.seek(offset)
                                chunk = fh.read(size - offset)
                            offset = size
                        elif size < offset:
                            # pipe-pane was restarted or the file rotated.
                            offset = 0
                except OSError:
                    break

                if chunk:
                    for clipboard_text in osc52_extractor.feed(chunk):
                        await context.runtime.broadcast.publish(
                            SessionEnvelope(
                                type="clipboard_copy",
                                payload={"text": clipboard_text},
                            ),
                            session_id=session_id,
                        )
                    # Split at every in→out frame transition so each
                    # closed frame gets its own diff emit, even when a
                    # single chunk contains multiple frames or
                    # finishes mid-next-frame. Feeding-then-emitting
                    # per segment keeps pyte's screen state in sync
                    # with what xterm sees.
                    for segment, ended_out in frame_tracker.split_at_frame_ends(chunk):
                        renderer.feed(segment)
                        if ended_out:
                            frame_open_at = None
                            await emit_diff()
                        elif frame_open_at is None:
                            frame_open_at = asyncio.get_event_loop().time()

                # Defensive: if a frame has been open longer than the
                # timeout (no end marker arrived), flush what we have so
                # the viewport doesn't go dark.
                if frame_tracker.in_frame and frame_open_at is not None:
                    if (
                        asyncio.get_event_loop().time() - frame_open_at
                        > SYNC_HOLD_TIMEOUT
                    ):
                        frame_open_at = None
                        await emit_diff()

                poll_count += 1
                if poll_count >= STATUS_CHECK_INTERVAL_POLLS:
                    poll_count = 0
                    try:
                        current = context.runtime.get_session(session_id)
                    except HTTPException:
                        break
                    if current.status == SessionStatus.EXITED:
                        # Flush any pending diff and close with a custom
                        # code so the frontend stops auto-reconnecting
                        # and surfaces the reconnect affordance instead.
                        await emit_diff()
                        with suppress(Exception):
                            await websocket.close(code=4410)
                        break
                    # For non-resizable panes (e.g. claude_tty) the pane is
                    # replaced on each relaunch. Detect a target change and
                    # close so the client reconnects to the new pane.
                    if not terminal_resizable:
                        cur_state = current.transport_state or {}
                        cur_pane = cur_state.get("tmux_pane") or current.id
                        if cur_pane != pane:
                            await emit_diff()
                            with suppress(Exception):
                                await websocket.close(code=4410)
                            break
                # 20ms keeps perceived latency below the threshold most
                # users notice for typing echo without burning CPU on
                # idle polling.
                await asyncio.sleep(0.02)

        async def recv_loop() -> None:
            # Each ``tmux send-keys`` invocation forks a fresh tmux client
            # (~30 ms). We rely on the natural backpressure of that fork
            # to batch typing without adding our own latency: while one
            # ``send-keys`` is in flight, any keystrokes that arrive on
            # the socket queue up and are drained (timeout=0) at the top
            # of the next iteration. Solo keypresses flush immediately;
            # bursts coalesce into one ``send-keys`` per iteration.
            while True:
                first = await websocket.receive_text()
                frames = [first]
                while True:
                    try:
                        frames.append(
                            await asyncio.wait_for(websocket.receive_text(), timeout=0)
                        )
                    except TimeoutError:
                        break

                input_bytes = b""
                resize_target: tuple[int, int] | None = None
                # Whole-message submissions from the quick-compose drawer.
                # Kept separate from ``input_bytes`` so each one round-trips
                # through ``send_input`` (which appends Enter when
                # ``submit`` is true) instead of being concatenated with
                # raw keystrokes.
                submits: list[tuple[str, bool]] = []

                for frame in frames:
                    try:
                        payload = json.loads(frame)
                    except json.JSONDecodeError:
                        continue
                    kind = payload.get("type")
                    if kind == "input":
                        if terminal_input_injection:
                            data = payload.get("data", "")
                            if isinstance(data, str) and data:
                                input_bytes += data.encode("utf-8")
                    elif kind == "input_submit":
                        if terminal_interactive:
                            text = payload.get("text", "")
                            if not isinstance(text, str):
                                continue
                            submit = bool(payload.get("submit", True))
                            if text or submit:
                                submits.append((text, submit))
                    elif kind == "resize":
                        if not terminal_resizable:
                            continue
                        try:
                            r_cols = int(payload.get("cols", 0))
                            r_rows = int(payload.get("rows", 0))
                        except (TypeError, ValueError):
                            continue
                        if r_cols > 0 and r_rows > 0:
                            resize_target = (r_cols, r_rows)

                if terminal_input_injection and input_bytes:
                    with suppress(TmuxError):
                        await adapter.send_bytes(pane, input_bytes)
                if terminal_interactive:
                    for text, submit in submits:
                        with suppress(TmuxError):
                            await adapter.send_input(pane, text, submit=submit)
                if terminal_resizable and resize_target is not None:
                    new_cols, new_rows = resize_target
                    renderer.resize(new_cols, new_rows)
                    with suppress(TmuxError):
                        await adapter.resize_window(tmux_session, new_cols, new_rows)
                        await adapter.resize_pane(pane, new_cols, new_rows)
                    # Resize invalidates the renderer's mirror and marks
                    # every row dirty. If the pane is idle, the stream
                    # loop has no chunk to react to and xterm would
                    # keep showing reflowed-old content at the new
                    # geometry until the next agent output. Emit the
                    # fresh frame immediately so the new size paints
                    # right away.
                    await emit_diff()

        stream_task = asyncio.create_task(stream_loop())
        recv_task = asyncio.create_task(recv_loop())
        try:
            _, pending = await asyncio.wait(
                {stream_task, recv_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            for task in (stream_task, recv_task):
                if not task.done():
                    task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.debug(
                        "terminal-ws task raised during cleanup",
                        exc_info=True,
                    )
            with suppress(Exception):
                await websocket.close()

    return app
