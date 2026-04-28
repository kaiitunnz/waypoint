from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware

from waypoint.auth import TokenStore, require_token
from waypoint.config import Settings
from waypoint.runtime import SessionRuntime
from waypoint.server_config import load_server_config
from waypoint.schemas import (
    LoginRequest,
    MeResponse,
    SessionApprovalRequest,
    SessionAttachRequest,
    SessionCreateRequest,
    SessionEnvelope,
    SessionInputRequest,
    TerminalSnapshot,
)
from waypoint.storage import Storage
from waypoint.tailnet import fetch_snapshot


class AppContext:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.settings.ensure_dirs()
        self.server_config = load_server_config(self.settings.config_path)
        self.storage = Storage(self.settings.database_path)
        self.runtime = SessionRuntime(self.settings, self.storage, self.server_config)
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
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid password")
        return context.tokens.issue()

    @app.get("/api/me", response_model=MeResponse)
    async def me(_: Annotated[str, Depends(token_dependency())]) -> MeResponse:
        return MeResponse()

    @app.get("/api/tailnet/peers")
    async def tailnet_peers(_: Annotated[str, Depends(token_dependency())]) -> Any:
        snapshot = await fetch_snapshot()
        return snapshot.model_dump(mode="json")

    @app.get("/api/sessions")
    async def list_sessions(_: Annotated[str, Depends(token_dependency())]) -> Any:
        sessions = [session.model_dump(mode="json") for session in context.runtime.list_sessions()]
        return {"sessions": sessions}

    @app.post("/api/sessions")
    async def create_session(
        request: SessionCreateRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.create_session(request)
        return {"session": session.model_dump(mode="json")}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, _: Annotated[str, Depends(token_dependency())]) -> Any:
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

    @app.post("/api/sessions/attach-tmux")
    async def attach_tmux(
        request: SessionAttachRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.attach_tmux(request)
        return {"session": session.model_dump(mode="json")}

    @app.get("/api/sessions/{session_id}/events")
    async def list_events(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
        cursor: Annotated[int | None, Query()] = None,
    ) -> Any:
        events = [event.model_dump(mode="json") for event in context.runtime.session_events(session_id, cursor)]
        return {"events": events}

    @app.get("/api/sessions/{session_id}/terminal-snapshot", response_model=TerminalSnapshot)
    async def terminal_snapshot(
        session_id: str,
        _: Annotated[str, Depends(token_dependency())],
    ) -> TerminalSnapshot:
        return TerminalSnapshot(session_id=session_id, text=context.runtime.terminal_snapshot(session_id))

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
                payload={"sessions": [item.model_dump(mode="json") for item in context.runtime.list_sessions()]},
            )
            await websocket.send_json(initial.model_dump(mode="json"))
            while True:
                message = await queue.get()
                await websocket.send_json(message)
        except WebSocketDisconnect:
            pass
        finally:
            context.runtime.broadcast.unsubscribe_global(queue)

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
        except WebSocketDisconnect:
            pass
        finally:
            context.runtime.broadcast.unsubscribe_session(session_id, queue)

    return app
