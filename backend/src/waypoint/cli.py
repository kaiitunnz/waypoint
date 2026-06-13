import asyncio
import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
import uvicorn

from waypoint.api import AppContext, create_app
from waypoint.backends.registry import get_registry
from waypoint.client import WaypointClient, WaypointError, write_cli_token
from waypoint.schemas import SessionAttachRequest, SessionCreateRequest
from waypoint.settings import Settings, load_settings


def _backend_choices() -> list[str]:
    """Backend ids accepted by ``session`` / ``sessions`` launch commands.

    Excludes managed-launch fallback wrappers (capabilities flag
    ``is_fallback_for_managed_launch``) — those are routed to via the
    registry, not selected as a real backend.
    """
    return [
        plugin.id
        for plugin in get_registry().all()
        if not plugin.capabilities.is_fallback_for_managed_launch
    ]


def _validate_backend(value: str | None) -> str | None:
    if value is None:
        return None
    choices = _backend_choices()
    if value not in choices:
        raise typer.BadParameter(
            f"unknown backend: {value!r} (choose from {', '.join(choices)})"
        )
    return value


def _complete_backend(incomplete: str) -> list[str]:
    return [choice for choice in _backend_choices() if choice.startswith(incomplete)]


# Reusable option/argument aliases keep the command signatures readable and
# the `--backend` validation/completion consistent across `session` and
# `sessions`.
BackendOption = Annotated[
    str,
    typer.Option(callback=_validate_backend, autocompletion=_complete_backend),
]
BackendHintOption = Annotated[
    str | None,
    typer.Option(callback=_validate_backend, autocompletion=_complete_backend),
]

app = typer.Typer(
    help="Waypoint backend control CLI.",
    no_args_is_help=True,
    add_completion=True,
)
backends_app = typer.Typer(
    help="Inspect backend capabilities and importable threads on a running server.",
    invoke_without_command=True,
)
session_app = typer.Typer(
    help="Launch sessions via an in-process runtime (one-shot, no running server).",
    no_args_is_help=True,
)
sessions_app = typer.Typer(
    help="Manage sessions on a running Waypoint server over HTTP.",
    no_args_is_help=True,
)
board_app = typer.Typer(
    help="Blackboard messaging shared across sessions.",
    no_args_is_help=True,
)
schedule_app = typer.Typer(
    help="Manage scheduled session launches on a running Waypoint server.",
    no_args_is_help=True,
)
app.add_typer(backends_app, name="backends")
app.add_typer(session_app, name="session")
app.add_typer(sessions_app, name="sessions")
app.add_typer(board_app, name="board")
app.add_typer(schedule_app, name="schedule")


@app.callback()
def _root(
    ctx: typer.Context,
    config: Annotated[
        str | None,
        typer.Option("--config", help="Path to waypoint.yaml.", metavar="PATH"),
    ] = None,
) -> None:
    # Stash the raw config path; commands resolve Settings lazily so `--help`
    # and shell completion don't read the config file.
    ctx.obj = {"config": config}


def _settings_from_arg(raw: str | None) -> Settings:
    return load_settings(Path(raw).expanduser() if raw else None)


def _settings_from_ctx(ctx: typer.Context) -> Settings:
    config = ctx.obj.get("config") if ctx.obj else None
    return _settings_from_arg(config)


@app.command()
def serve(
    ctx: typer.Context,
    host: Annotated[str | None, typer.Option(help="Bind host override.")] = None,
    port: Annotated[int | None, typer.Option(help="Bind port override.")] = None,
) -> None:
    """Run the API server."""
    settings = _settings_from_ctx(ctx)
    fastapi_app = create_app(settings)
    context = fastapi_app.state.context
    # Issue a local token the same-host `waypoint sessions` CLI (and the
    # personal assistant shelling out to it) can read without the password.
    # 0600 in the data dir; treat it as a secret.
    token = context.tokens.issue().token
    token_path = write_cli_token(context.settings, token)
    typer.echo(f"wrote CLI token to {token_path}")
    bind_host = host or context.settings.host
    bind_port = port or context.settings.port
    # Cap graceful-shutdown so a stuck websocket can never hold uvicorn past
    # Ctrl+C. The first SIGINT triggers shutdown; any in-flight ws connection
    # that doesn't close on cancel within this window gets force-closed.
    uvicorn.run(
        fastapi_app, host=bind_host, port=bind_port, timeout_graceful_shutdown=5
    )


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Report the resolved config path and discovered CLI binaries."""
    run_doctor(_settings_from_ctx(ctx))


@backends_app.callback()
def backends(ctx: typer.Context) -> None:
    """List backends and their capabilities (permission modes, approval decisions)."""
    if ctx.invoked_subcommand is not None:
        return
    _emit(_settings_from_ctx(ctx), lambda c: {"backends": c.list_backends()})


@backends_app.command("threads")
def backends_threads(
    ctx: typer.Context,
    backend: Annotated[str, typer.Argument()],
    launch_target_id: Annotated[
        str | None,
        typer.Option(
            help="Resolve importable threads for a specific launch target "
            "(e.g. a remote host or worktree)."
        ),
    ] = None,
) -> None:
    """List a backend's importable threads."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {
            "threads": c.list_threads(backend, launch_target_id=launch_target_id)
        },
    )


@app.command()
def models(
    ctx: typer.Context,
    backend: Annotated[
        str | None,
        typer.Argument(callback=_validate_backend, autocompletion=_complete_backend),
    ] = None,
    launch_target_id: Annotated[
        str | None,
        typer.Option(
            help="Resolve models for a specific launch target (e.g. a remote "
            "host or worktree) rather than the backend's default context."
        ),
    ] = None,
    include_hidden: Annotated[
        bool,
        typer.Option("--include-hidden", help="Include models the backend hides."),
    ] = False,
) -> None:
    """List the models a backend offers (its ids, labels, and reasoning efforts).

    With no BACKEND, queries every selectable backend; a backend whose live
    model discovery is unavailable is reported with an ``error`` entry rather
    than failing the whole listing.
    """

    def run(c: WaypointClient) -> Any:
        if backend is not None:
            return c.list_models(
                backend,
                launch_target_id=launch_target_id,
                include_hidden=include_hidden,
            )
        catalogues: list[dict[str, Any]] = []
        for descriptor in c.list_backends():
            if descriptor["capabilities"].get("is_fallback_for_managed_launch"):
                continue
            backend_id = descriptor["id"]
            try:
                catalogues.append(
                    c.list_models(
                        backend_id,
                        launch_target_id=launch_target_id,
                        include_hidden=include_hidden,
                    )
                )
            except WaypointError as exc:
                catalogues.append({"backend": backend_id, "error": str(exc)})
        return {"backends": catalogues}

    _emit(_settings_from_ctx(ctx), run)


@app.command()
def reset(
    ctx: typer.Context,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Confirm destruction. Without this flag the command is a dry run.",
        ),
    ] = False,
) -> None:
    """Wipe runtime data (sessions, events, tokens, schedules, logs). Config is untouched."""
    run_reset(_settings_from_ctx(ctx), confirmed=yes)


@session_app.command("start")
def session_start(
    ctx: typer.Context,
    backend: BackendOption,
    cwd: Annotated[str, typer.Option(help="Working directory for the session.")],
    launch_target_id: Annotated[str | None, typer.Option()] = None,
    title: Annotated[str | None, typer.Option()] = None,
    args: Annotated[list[str] | None, typer.Argument()] = None,
) -> None:
    """Start a session in-process and print it as JSON."""
    asyncio.run(
        _session_start(
            _settings_from_ctx(ctx),
            backend=backend,
            cwd=cwd,
            launch_target_id=launch_target_id,
            title=title,
            args=args or [],
        )
    )


@session_app.command("attach")
def session_attach(
    ctx: typer.Context,
    tmux: Annotated[str, typer.Option(help="Target tmux pane/session.")],
    backend_hint: BackendHintOption = None,
    title: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Attach an existing tmux pane as a session."""
    asyncio.run(
        _session_attach(
            _settings_from_ctx(ctx),
            tmux=tmux,
            backend_hint=backend_hint,
            title=title,
        )
    )


async def _session_start(
    settings: Settings,
    *,
    backend: str,
    cwd: str,
    launch_target_id: str | None,
    title: str | None,
    args: list[str],
) -> None:
    context = AppContext(settings)
    context.settings.ensure_dirs()
    try:
        session = await context.runtime.create_session(
            SessionCreateRequest(
                backend=backend,
                cwd=cwd,
                launch_target_id=launch_target_id,
                title=title,
                args=list(args),
            )
        )
        typer.echo(json.dumps({"session": session.model_dump(mode="json")}, indent=2))
    finally:
        await context.runtime.stop()


async def _session_attach(
    settings: Settings,
    *,
    tmux: str,
    backend_hint: str | None,
    title: str | None,
) -> None:
    context = AppContext(settings)
    context.settings.ensure_dirs()
    try:
        payload: dict[str, Any] = {"tmux_target": tmux, "title": title}
        if backend_hint:
            payload["backend_hint"] = backend_hint
        session = await context.runtime.attach_tmux(
            SessionAttachRequest.model_validate(payload)
        )
        typer.echo(json.dumps({"session": session.model_dump(mode="json")}, indent=2))
    finally:
        await context.runtime.stop()


def _emit(settings: Settings, run: Callable[[WaypointClient], Any]) -> None:
    """Run a client call against the live server and print the JSON result."""
    try:
        with WaypointClient(settings) as client:
            result = run(client)
    except WaypointError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2))


def _parse_answers(raw: str | None) -> list[dict[str, Any]] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--answers-json is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list) or not all(
        isinstance(item, dict) for item in parsed
    ):
        raise typer.BadParameter("--answers-json must be a JSON array of objects")
    return parsed


def _parse_json_object(source: str) -> dict[str, Any]:
    try:
        raw = sys.stdin.read() if source == "-" else Path(source).read_text("utf-8")
    except OSError as exc:
        raise typer.BadParameter(f"--json could not read {source}: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--json is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("--json must be a JSON object")
    return parsed


def _parse_meta(items: list[str] | None) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep:
            raise typer.BadParameter(f"--meta expects key=value, got: {item}")
        metadata[key] = value
    return metadata


@sessions_app.command("list")
def sessions_list(ctx: typer.Context) -> None:
    """List all sessions."""
    _emit(_settings_from_ctx(ctx), lambda c: {"sessions": c.list_sessions()})


@sessions_app.command("show")
def sessions_show(
    ctx: typer.Context, session_id: Annotated[str, typer.Argument()]
) -> None:
    """Show one session."""
    _emit(_settings_from_ctx(ctx), lambda c: {"session": c.get_session(session_id)})


@sessions_app.command("events")
def sessions_events(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    messages: Annotated[int | None, typer.Option()] = None,
    before_sequence: Annotated[int | None, typer.Option()] = None,
) -> None:
    """Show a session's transcript."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: c.get_events(
            session_id, messages=messages, before_sequence=before_sequence
        ),
    )


def _conversation_events(events_page: dict[str, Any]) -> list[dict[str, Any]]:
    visible = {"user_input", "agent_output"}
    return [event for event in events_page["events"] if event.get("kind") in visible]


@sessions_app.command("start")
def sessions_start(
    ctx: typer.Context,
    backend: BackendOption,
    cwd: Annotated[str, typer.Option(help="Working directory for the session.")],
    launch_target_id: Annotated[str | None, typer.Option()] = None,
    title: Annotated[str | None, typer.Option()] = None,
    model: Annotated[str | None, typer.Option()] = None,
    effort: Annotated[str | None, typer.Option()] = None,
    permission_mode: Annotated[str | None, typer.Option()] = None,
    spawner_session_id: Annotated[
        str | None,
        typer.Option(
            envvar="WAYPOINT_SESSION_ID",
            help="Spawner session; the child inherits its permission mode. "
            "Defaults to this session's id when run inside one.",
        ),
    ] = None,
    args: Annotated[list[str] | None, typer.Argument()] = None,
) -> None:
    """Launch a new session on the running server."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {
            "session": c.create_session(
                backend=backend,
                cwd=cwd,
                launch_target_id=launch_target_id,
                title=title,
                model=model,
                effort=effort,
                permission_mode=permission_mode,
                spawner_session_id=spawner_session_id,
                args=list(args or []),
            )
        },
    )


@sessions_app.command("send")
def sessions_send(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    text: Annotated[str, typer.Argument()],
    attach: Annotated[
        list[Path] | None,
        typer.Option(
            "--attach",
            "-a",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Attach a file to the message (repeatable). Images ride "
            "natively where the backend supports it; other files are delivered "
            "by host path or inline depending on the backend.",
        ),
    ] = None,
) -> None:
    """Send a message to a session."""

    def _run(c: WaypointClient) -> dict[str, Any]:
        ids = [c.upload_attachment(session_id, path)["id"] for path in attach or []]
        return {"session": c.send_input(session_id, text, attachments=ids or None)}

    _emit(_settings_from_ctx(ctx), _run)


@sessions_app.command("interrupt")
def sessions_interrupt(
    ctx: typer.Context, session_id: Annotated[str, typer.Argument()]
) -> None:
    """Interrupt a session."""
    _emit(_settings_from_ctx(ctx), lambda c: {"session": c.interrupt(session_id)})


@sessions_app.command("terminate")
def sessions_terminate(
    ctx: typer.Context, session_id: Annotated[str, typer.Argument()]
) -> None:
    """Terminate a session."""
    _emit(_settings_from_ctx(ctx), lambda c: {"session": c.terminate(session_id)})


@sessions_app.command("delete")
def sessions_delete(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Drop the record even if graceful terminate fails (wedged adapter).",
        ),
    ] = False,
) -> None:
    """Terminate (if needed) and remove a session record."""
    _emit(_settings_from_ctx(ctx), lambda c: c.delete(session_id, force=force))


@sessions_app.command("approve")
def sessions_approve(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    decision: Annotated[str, typer.Argument()],
    text: Annotated[str | None, typer.Option()] = None,
    approval_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Respond to an approval request."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {
            "session": c.approve(
                session_id, decision, text=text, approval_id=approval_id
            )
        },
    )


@sessions_app.command("answer-question")
def sessions_answer_question(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    answer: Annotated[
        str,
        typer.Option(
            "--answer", help="Free-text answer to the session's pending question."
        ),
    ],
    tool_use_id: Annotated[
        str | None,
        typer.Option(
            help="Target a specific question by tool-use id. Omit to answer the "
            "sole pending question.",
        ),
    ] = None,
    answers_json: Annotated[
        str | None,
        typer.Option(
            "--answers-json",
            help='Structured per-question answers as JSON, e.g. \'[{"question": '
            '"...", "answer": "...", "notes": "..."}]\'.',
        ),
    ] = None,
) -> None:
    """Answer a session's blocking question (not the same as send or approve)."""
    answers = _parse_answers(answers_json)
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {
            "session": c.answer_question(
                session_id, answer, tool_use_id=tool_use_id, answers=answers
            )
        },
    )


@sessions_app.command("import")
def sessions_import(
    ctx: typer.Context,
    backend: Annotated[str, typer.Argument()],
    json_source: Annotated[
        str,
        typer.Option(
            "--json",
            help="Path to a JSON object body, or - to read it from stdin.",
            metavar="FILE|-",
        ),
    ],
) -> None:
    """Import a backend-native thread into Waypoint."""
    body = _parse_json_object(json_source)
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {"session": c.import_thread(backend, body)},
    )


@sessions_app.command("output")
def sessions_output(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    messages: Annotated[int | None, typer.Option()] = None,
    text: Annotated[
        bool,
        typer.Option(
            "--text",
            help="Print only the concatenated agent output text for shell piping.",
        ),
    ] = False,
) -> None:
    """Show just the conversational transcript from a session."""
    if text:
        settings = _settings_from_ctx(ctx)
        try:
            with WaypointClient(settings) as client:
                events = _conversation_events(
                    client.get_events(session_id, messages=messages)
                )
        except WaypointError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(
            "".join(
                event["text"] for event in events if event.get("kind") == "agent_output"
            ),
            nl=False,
        )
        return
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {
            "events": _conversation_events(c.get_events(session_id, messages=messages))
        },
    )


@board_app.command("post")
def board_post(
    ctx: typer.Context,
    channel: Annotated[str, typer.Argument()],
    text: Annotated[str, typer.Argument()],
    key: Annotated[
        str | None,
        typer.Option(
            "--key", help="Upsert this (channel, key) cell instead of appending."
        ),
    ] = None,
    meta: Annotated[
        list[str] | None,
        typer.Option("--meta", help="Attach metadata as key=value. Repeatable."),
    ] = None,
    author_session_id: Annotated[
        str | None,
        typer.Option(
            envvar="WAYPOINT_SESSION_ID",
            help="Authoring session; defaults to this session's id. "
            "Posts are pruned when that session is deleted.",
        ),
    ] = None,
) -> None:
    """Post to a board channel (append, or upsert a cell with --key)."""
    metadata = _parse_meta(meta)
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {
            "entry": c.post_board(
                channel,
                text,
                key=key,
                author_session_id=author_session_id,
                metadata=metadata,
            )
        },
    )


@board_app.command("read")
def board_read(
    ctx: typer.Context,
    channel: Annotated[str, typer.Argument()],
    since: Annotated[
        int | None,
        typer.Option("--since", help="Only entries with an id greater than this."),
    ] = None,
    key: Annotated[
        str | None, typer.Option("--key", help="Only the cell with this key.")
    ] = None,
) -> None:
    """Read entries from a board channel."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {"entries": c.read_board(channel, since=since, key=key)},
    )


@board_app.command("channels")
def board_channels(ctx: typer.Context) -> None:
    """List board channels and their entry counts."""
    _emit(_settings_from_ctx(ctx), lambda c: {"channels": c.list_board_channels()})


@board_app.command("clear")
def board_clear(
    ctx: typer.Context,
    channel: Annotated[str, typer.Argument()],
) -> None:
    """Remove all posts from a channel, keeping the (now empty) channel."""
    _emit(_settings_from_ctx(ctx), lambda c: c.clear_board(channel))


@board_app.command("delete")
def board_delete(
    ctx: typer.Context,
    channel: Annotated[str, typer.Argument()],
) -> None:
    """Delete a channel entirely, posts and all."""
    _emit(_settings_from_ctx(ctx), lambda c: c.delete_board(channel))


@board_app.command("delete-entry")
def board_delete_entry(
    ctx: typer.Context,
    channel: Annotated[str, typer.Argument()],
    entry_id: Annotated[int, typer.Argument()],
) -> None:
    """Delete a single post (log entry or cell) by its id."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: c.delete_board_entry(channel, entry_id),
    )


@board_app.command("edit-entry")
def board_edit_entry(
    ctx: typer.Context,
    channel: Annotated[str, typer.Argument()],
    entry_id: Annotated[int, typer.Argument()],
    text: Annotated[str, typer.Argument()],
    meta: Annotated[
        list[str] | None,
        typer.Option("--meta", help="Replace metadata with key=value. Repeatable."),
    ] = None,
) -> None:
    """Edit a post's text and metadata in place (the cell key is immutable)."""
    metadata = _parse_meta(meta)
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {"entry": c.update_board_entry(channel, entry_id, text, metadata)},
    )


@schedule_app.command("list")
def schedule_list(ctx: typer.Context) -> None:
    """List all scheduled sessions."""
    _emit(_settings_from_ctx(ctx), lambda c: {"schedules": c.list_schedules()})


@schedule_app.command("create")
def schedule_create(
    ctx: typer.Context,
    backend: BackendOption,
    cwd: Annotated[str, typer.Option(help="Working directory for the session.")],
    launch_target_id: Annotated[str | None, typer.Option()] = None,
    launch_mode: Annotated[str | None, typer.Option()] = None,
    title: Annotated[str | None, typer.Option()] = None,
    model: Annotated[str | None, typer.Option()] = None,
    effort: Annotated[str | None, typer.Option()] = None,
    permission_mode: Annotated[str | None, typer.Option()] = None,
    prompt: Annotated[
        str | None,
        typer.Option("--prompt", help="Initial prompt sent to the session on launch."),
    ] = None,
    delay_seconds: Annotated[
        int | None,
        typer.Option(help="Launch this many seconds from now."),
    ] = None,
    scheduled_at: Annotated[
        str | None,
        typer.Option(help="ISO 8601 datetime at which to launch the session."),
    ] = None,
    args: Annotated[list[str] | None, typer.Argument()] = None,
) -> None:
    """Schedule a session launch on the running server."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {
            "schedule": c.create_schedule(
                backend=backend,
                cwd=cwd,
                launch_target_id=launch_target_id,
                launch_mode=launch_mode,
                title=title,
                model=model,
                effort=effort,
                permission_mode=permission_mode,
                initial_prompt=prompt,
                args=list(args or []),
                delay_seconds=delay_seconds,
                scheduled_at=scheduled_at,
            )
        },
    )


@schedule_app.command("delete")
def schedule_delete(
    ctx: typer.Context, schedule_id: Annotated[str, typer.Argument()]
) -> None:
    """Cancel and remove a scheduled session."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {"schedule": c.delete_schedule(schedule_id)},
    )


@schedule_app.command("clear-history")
def schedule_clear_history(ctx: typer.Context) -> None:
    """Remove completed/cancelled schedule records."""
    _emit(_settings_from_ctx(ctx), lambda c: c.clear_schedule_history())


def run_reset(settings: Settings | None = None, *, confirmed: bool) -> None:
    settings = settings or load_settings()
    db_path = settings.database_path
    sessions_dir = settings.sessions_dir
    # SQLite write-ahead log siblings live next to the main file; nuke them too
    # so a re-init starts with a clean slate.
    db_siblings = [
        db_path.with_suffix(db_path.suffix + suffix) for suffix in ("-wal", "-shm")
    ]
    targets: list[Path] = [db_path, *db_siblings, sessions_dir]
    existing = [target for target in targets if target.exists()]

    if not existing:
        print(f"Nothing to reset under {settings.data_dir}.")
        return

    if not confirmed:
        print(f"Would remove (data_dir: {settings.data_dir}):")
        for target in existing:
            print(f"  - {target}")
        print()
        print("Stop the backend before running, then re-run with --yes to confirm.")
        print("Config is untouched in either case.")
        return

    for target in existing:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        print(f"removed {target}")
    settings.ensure_dirs()
    print(f"recreated {settings.data_dir}")


def run_doctor(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    # System binaries are checked unconditionally; per-plugin binaries
    # come from each registered plugin's ``capabilities.cli_binary``
    # (overridable per-plugin via ``plugin_configs.<id>.local_bin``)
    # so a new backend shows up here automatically without editing
    # this file.
    checks: dict[str, Any] = {
        "tmux": shutil.which("tmux"),
        "ssh": shutil.which("ssh"),
        "tailscale": shutil.which("tailscale"),
    }
    for plugin in get_registry().all():
        binary = (
            settings.plugin_config(plugin.id).local_bin
            or plugin.capabilities.cli_binary
        )
        if binary is None or binary in checks:
            continue
        checks[binary] = shutil.which(binary)
    checks["config_path"] = str(settings.config_path) if settings.config_path else None
    print(json.dumps(checks, indent=2))


def main() -> None:
    app()
