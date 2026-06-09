import asyncio
import json
import shutil
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
session_app = typer.Typer(
    help="Launch sessions via an in-process runtime (one-shot, no running server).",
    no_args_is_help=True,
)
sessions_app = typer.Typer(
    help="Manage sessions on a running Waypoint server over HTTP.",
    no_args_is_help=True,
)
app.add_typer(session_app, name="session")
app.add_typer(sessions_app, name="sessions")


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
                args=list(args or []),
            )
        },
    )


@sessions_app.command("send")
def sessions_send(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    text: Annotated[str, typer.Argument()],
) -> None:
    """Send a message to a session."""
    _emit(
        _settings_from_ctx(ctx), lambda c: {"session": c.send_input(session_id, text)}
    )


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
