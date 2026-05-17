import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware

from waypoint.auth import TokenStore, require_token
from waypoint.backends import BackendRegistry
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.backends.tmux.renderer import (
    Osc52Extractor,
    SyncFrameTracker,
    make_renderer,
)
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    LoginRequest,
    MeResponse,
    ScheduleCreateRequest,
    SessionAnswerQuestionRequest,
    SessionApprovalRequest,
    SessionAttachRequest,
    SessionCompletionsResponse,
    SessionCreateRequest,
    SessionEffortRequest,
    SessionEnvelope,
    SessionInputRequest,
    SessionModelRequest,
    SessionPermissionModeRequest,
    SessionPlanApprovalRequest,
    SessionStatus,
    SessionTitleRequest,
    TerminalSnapshot,
)
from waypoint.settings import Settings, load_settings
from waypoint.storage import Storage
from waypoint.tailnet import fetch_snapshot
from waypoint.usage_dashboard import build_dashboard

log = logging.getLogger("waypoint.api")


def _backend_descriptors(registry: BackendRegistry) -> list[dict[str, Any]]:
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
                "label": plugin.label,
                "badges": dict(caps.badges),
                "capabilities": caps.model_dump(mode="json"),
            }
        )
    return payload


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
            backends=_backend_descriptors(context.runtime.registry),
        )

    @app.get("/api/backends")
    async def list_backends(
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        return {"backends": _backend_descriptors(context.runtime.registry)}

    # Plugin-registered routes (e.g. the Claude PreToolUse hook) come
    # in here so api.py stays backend-agnostic.
    for plugin in context.runtime.registry.all():
        plugin.register_routes(app, context)

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
        threads = [
            thread.model_dump(mode="json")
            for thread in await plugin.list_threads(context.runtime, launch_target_id)
        ]
        return {"threads": threads}

    @app.post("/api/sessions")
    async def create_session(
        request: SessionCreateRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.create_session(request)
        return {"session": session.model_dump(mode="json")}

    @app.post("/api/backends/{backend}/sessions/import")
    async def import_backend_thread(
        backend: str,
        body: dict[str, Any],
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        if not context.runtime.registry.has_backend(backend):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown backend: {backend}",
            )
        plugin = context.runtime.registry.get(backend)
        if plugin.capabilities.is_fallback_for_managed_launch:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{backend} is a managed-launch wrapper and "
                    "cannot be requested as the target backend"
                ),
            )
        if not plugin.capabilities.supports_thread_import:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"thread import is not supported for {backend}",
            )
        # Each plugin declares its own request shape via
        # `import_request_schema`. The dispatcher validates and hands a
        # typed object to `import_thread`; plugins without a schema fall
        # back to the raw dict.
        schema = plugin.import_request_schema
        request: Any = schema.model_validate(body) if schema is not None else body
        session = await plugin.import_thread(context.runtime, request)
        return {"session": session.model_dump(mode="json")}

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
        dashboard = build_dashboard(context.runtime.list_sessions())
        return dashboard.model_dump(mode="json")

    @app.post("/api/usage/refresh")
    async def refresh_usage_dashboard(
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        # Refresh one representative session per bucket — every session in
        # a bucket shares the same account-level rate limit, so probing
        # one is enough to update the bucket's snapshot.
        dashboard = build_dashboard(context.runtime.list_sessions())
        targets = [
            bucket.session_ids[0] for bucket in dashboard.buckets if bucket.session_ids
        ]
        if targets:
            await asyncio.gather(
                *(context.runtime.refresh_rate_limit_usage(sid) for sid in targets),
                return_exceptions=True,
            )
        refreshed = build_dashboard(context.runtime.list_sessions())
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
    ) -> Any:
        # `force=true` skips terminate failures entirely — last-resort escape
        # hatch when the adapter is wedged (SSH stuck, etc.) and the
        # graceful path won't complete.
        await context.runtime.delete(session_id, force=force)
        return {"deleted": session_id}

    @app.patch("/api/sessions/{session_id}/title")
    async def session_set_title(
        session_id: str,
        body: SessionTitleRequest,
        _: Annotated[str, Depends(token_dependency())],
    ) -> Any:
        session = await context.runtime.set_title(session_id, body.title)
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
        # Tmux is the only transport that gives us a live pane to mirror;
        # structured backends already publish to the event stream and have
        # no pty to wrap.
        if session.transport != "tmux":
            await websocket.close(code=4403)
            return
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

        # Wait briefly for the client's initial resize so the capture-pane
        # seed is rendered at the viewport's dimensions, not the pane's
        # previous size. The frontend always sends a resize on socket open,
        # so the timeout only matters when an unusual client connects.
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
                        await adapter.resize_pane(pane, viewport_cols, viewport_rows)
        except WebSocketDisconnect:
            # Client closed during the handshake; nothing left to seed.
            return
        except (TimeoutError, json.JSONDecodeError):
            # No handshake — fall through with default viewport.
            pass

        # Give the pane process a beat to handle SIGWINCH and emit its
        # redraw before we capture — otherwise the seed reflects the stale
        # pre-resize buffer (cursor at the old pane's bottom row, content
        # clipped to the old size) and the live stream has to overpaint it.
        if viewport_cols and viewport_rows:
            await asyncio.sleep(0.1)

        # Server-side terminal emulator: bytes from the pane go through
        # pyte, which maintains the authoritative screen state. The
        # renderer emits cell-level deltas — only cells whose final
        # value differs from what xterm last saw — so the client never
        # has to interpret DECSTBM scroll regions, partial sync-output
        # frames, or other sequences browser emulators handle
        # inconsistently from native terminals.
        cols = viewport_cols or 80
        rows = viewport_rows or 24
        renderer = make_renderer(cols, rows)

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

                for frame in frames:
                    try:
                        payload = json.loads(frame)
                    except json.JSONDecodeError:
                        continue
                    kind = payload.get("type")
                    if kind == "input":
                        data = payload.get("data", "")
                        if isinstance(data, str) and data:
                            input_bytes += data.encode("utf-8")
                    elif kind == "resize":
                        try:
                            r_cols = int(payload.get("cols", 0))
                            r_rows = int(payload.get("rows", 0))
                        except (TypeError, ValueError):
                            continue
                        if r_cols > 0 and r_rows > 0:
                            resize_target = (r_cols, r_rows)

                if input_bytes:
                    with suppress(TmuxError):
                        await adapter.send_bytes(pane, input_bytes)
                if resize_target is not None:
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
