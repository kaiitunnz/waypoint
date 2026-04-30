import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware

from waypoint.auth import TokenStore, require_token
from waypoint.claude_runtime import ensure_claude_hook_bundle
from waypoint.config import Settings, load_settings
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    Backend,
    ClaudeThreadImportRequest,
    CodexThreadImportRequest,
    LoginRequest,
    MeResponse,
    ScheduleCreateRequest,
    SessionAnswerQuestionRequest,
    SessionApprovalRequest,
    SessionAttachRequest,
    SessionCreateRequest,
    SessionEffortRequest,
    SessionEnvelope,
    SessionInputRequest,
    SessionModelRequest,
    SessionPermissionModeRequest,
    TerminalSnapshot,
)
from waypoint.storage import Storage
from waypoint.tailnet import fetch_snapshot


class AppContext:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.settings.ensure_dirs()
        self.claude_hook = ensure_claude_hook_bundle(self.settings.data_dir)
        self.storage = Storage(self.settings.database_path)
        self.runtime = SessionRuntime(
            self.settings, self.storage, claude_hook=self.claude_hook
        )
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
        )

    @app.post("/api/internal/hooks/claude/approval")
    async def claude_hook_approval(request: Request) -> Any:
        secret = request.headers.get("x-waypoint-hook-secret", "")
        if not context.claude_hook.secret or secret != context.claude_hook.secret:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="invalid hook secret"
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
        decision = await context.runtime.claude.await_approval(payload)
        return decision

    @app.get("/api/tailnet/peers")
    async def tailnet_peers(_: Annotated[str, Depends(token_dependency())]) -> Any:
        snapshot = await fetch_snapshot()
        return snapshot.model_dump(mode="json")

    @app.get("/api/sessions")
    async def list_sessions(_: Annotated[str, Depends(token_dependency())]) -> Any:
        sessions = [
            session.model_dump(mode="json")
            for session in context.runtime.list_sessions()
        ]
        return {"sessions": sessions}

    @app.get("/api/codex/threads")
    async def list_codex_threads(
        _: Annotated[str, Depends(token_dependency())],
        launch_target_id: Annotated[str | None, Query()] = None,
    ) -> Any:
        threads = [
            thread.model_dump(mode="json")
            for thread in await context.runtime.list_importable_codex_threads(
                launch_target_id
            )
        ]
        return {"threads": threads}

    @app.post("/api/sessions")
    async def create_session(
        request: SessionCreateRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.create_session(request)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/import-codex")
    async def import_codex_thread(
        request: CodexThreadImportRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.import_codex_thread(request)
        return {"session": session.model_dump(mode="json")}

    @app.get("/api/claude/threads")
    async def list_claude_threads(
        _: Annotated[str, Depends(token_dependency())],
        launch_target_id: Annotated[str | None, Query()] = None,
    ) -> Any:
        threads = [
            thread.model_dump(mode="json")
            for thread in await context.runtime.list_importable_claude_threads(
                launch_target_id
            )
        ]
        return {"threads": threads}

    @app.post("/api/sessions/import-claude")
    async def import_claude_thread(
        request: ClaudeThreadImportRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.import_claude_thread(request)
        return {"session": session.model_dump(mode="json")}

    @app.get("/api/sessions/{session_id}")
    async def get_session(
        session_id: str, _: Annotated[str, Depends(token_dependency())]
    ) -> Any:
        session = context.runtime.get_session(session_id)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/sessions/{session_id}/input")
    async def session_input(
        session_id: str,
        request: SessionInputRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.handle_input(session_id, request)
        return {"session": session.model_dump(mode="json")}

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
    ) -> Any:
        await context.runtime.delete(session_id)
        return {"deleted": session_id}

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

    @app.get("/api/backends/{backend}/models")
    async def list_backend_models(
        backend: Backend,
        _: Annotated[str, Depends(token_dependency())],
        launch_target_id: Annotated[str | None, Query()] = None,
        include_hidden: Annotated[bool, Query()] = False,
    ) -> Any:
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
        request: ScheduleCreateRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        schedule = context.runtime.scheduler.create_schedule(request)
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

    @app.get(
        "/api/sessions/{session_id}/terminal-snapshot", response_model=TerminalSnapshot
    )
    async def terminal_snapshot(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> TerminalSnapshot:
        return TerminalSnapshot(
            session_id=session_id, text=context.runtime.terminal_snapshot(session_id)
        )

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
            while True:
                message = await queue.get()
                await websocket.send_json(message)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            context.runtime.broadcast.unsubscribe_session(session_id, queue)
            with suppress(Exception):
                await websocket.close()

    return app
