import asyncio
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from typing import Annotated, Any, NamedTuple, Protocol, cast

import click
import typer
import uvicorn
from fastapi import HTTPException
from websockets.exceptions import WebSocketException

from waypoint.api import AppContext, create_app
from waypoint.backends.registry import get_registry
from waypoint.client import (
    WaypointClient,
    WaypointError,
    is_event_envelope,
    session_status_from_envelope,
    write_cli_token,
)
from waypoint.launch_env import validate_launch_env
from waypoint.presets import resolve_session_create_request
from waypoint.schemas import (
    LaunchMode,
    SessionAttachRequest,
    SessionLaunchRequest,
    SessionStatus,
)
from waypoint.settings import Settings, load_settings
from waypoint.storage import Storage

# Statuses that, by default, end a `sessions wait`: the session is idle,
# blocked on the user, or finished. `starting`/`running`/`interrupted` are
# transient and keep the wait blocked.
WAIT_DEFAULT_STATUSES: frozenset[str] = frozenset(
    {
        SessionStatus.IDLE,
        SessionStatus.WAITING_INPUT,
        SessionStatus.EXITED,
        SessionStatus.ERROR,
    }
)
# Lifecycle-terminal statuses that stop `sessions events --follow` — the
# process is gone, so no further events will arrive.
FOLLOW_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {SessionStatus.EXITED, SessionStatus.ERROR}
)
# Conventional "timeout" exit code (matches GNU coreutils `timeout`).
WAIT_TIMEOUT_EXIT_CODE = 124
WAIT_POLL_INTERVAL_SECONDS = 2.0
# ``inbox wait`` outcomes. ``resolved``/``update`` exit 0; timeout reuses 124;
# ``gone`` (the item was deleted while waiting) gets its own code — 3, chosen to
# avoid Typer/Click's usage-error code 2 and the timeout code 124 — so a waiting
# lead can branch on a withdrawn ask.
INBOX_WAIT_UNTIL_CHOICES: frozenset[str] = frozenset({"resolved", "update"})
INBOX_GONE_EXIT_CODE = 3


def _backend_choices() -> list[str]:
    """Backend ids accepted by ``session`` / ``sessions`` launch commands.

    Excludes only the managed-launch fallback wrapper (``tmux``, flagged
    ``is_fallback_for_managed_launch``): it is routed to via the registry, not
    selected directly. ``claude_tty`` stays selectable as a legacy alias that
    bundles the Claude agent with its tty-tail transport under one id; the
    preferred way to reach that transport is ``--backend claude_code
    --transport claude_tty`` (or omitting ``--transport``, since ``claude_code``
    defaults to the Emulated transport).
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
attachments_app = typer.Typer(
    help="Manage a session's file attachments on a running Waypoint server.",
    no_args_is_help=True,
)
board_app = typer.Typer(
    help="Blackboard messaging shared across sessions.",
    no_args_is_help=True,
)
inbox_app = typer.Typer(
    help="Durable human-facing inbox for lead-initiated checkpoints.",
    no_args_is_help=True,
)
schedule_app = typer.Typer(
    help="Manage scheduled session launches on a running Waypoint server.",
    no_args_is_help=True,
)
schedule_message_app = typer.Typer(
    help="Manage scheduled messages on a running Waypoint server.",
    no_args_is_help=True,
)
maintenance_app = typer.Typer(
    help="Maintenance commands for the Waypoint server data.",
    no_args_is_help=True,
)
presets_app = typer.Typer(
    help="Manage reusable session-launch presets on a running Waypoint server.",
    no_args_is_help=True,
)
app.add_typer(backends_app, name="backends")
app.add_typer(session_app, name="session")
app.add_typer(sessions_app, name="sessions")
sessions_app.add_typer(attachments_app, name="attachments")
app.add_typer(board_app, name="board")
app.add_typer(inbox_app, name="inbox")
app.add_typer(schedule_app, name="schedule")
schedule_app.add_typer(schedule_message_app, name="message")
app.add_typer(maintenance_app, name="maintenance")
app.add_typer(presets_app, name="presets")


def _version_callback(value: bool) -> None:
    if value:
        try:
            ver = importlib.metadata.version("waypoint")
        except PackageNotFoundError:
            ver = "0.0.0"
        typer.echo(ver)
        raise typer.Exit()


@app.callback()
def _root(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
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


@app.command()
def usage(
    ctx: typer.Context,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="POST /api/usage/refresh to pull fresh rate-limit data before "
            "returning the dashboard.",
        ),
    ] = False,
) -> None:
    """Show the usage dashboard (token usage, rate limits, cost per session)."""

    def run(c: WaypointClient) -> Any:
        if refresh:
            return c.refresh_usage()
        return c.get_usage()

    _emit(_settings_from_ctx(ctx), run)


@app.command("help")
def help_(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the command tree as structured JSON."),
    ] = False,
) -> None:
    """Dump the entire CLI surface (all nested commands) in one call."""
    root = cast(click.Group, typer.main.get_command(app))
    commands = _walk_commands(root, "waypoint")
    if json_output:
        typer.echo(json.dumps(commands, indent=2))
    else:
        typer.echo(_render_help_text(commands))


@session_app.command("start")
def session_start(
    ctx: typer.Context,
    backend: Annotated[
        str | None,
        typer.Option(callback=_validate_backend, autocompletion=_complete_backend),
    ] = None,
    cwd: Annotated[
        str | None, typer.Option(help="Working directory for the session.")
    ] = None,
    preset: Annotated[
        str | None,
        typer.Option(
            "--preset", help="Apply a session preset (by id or name) before launch."
        ),
    ] = None,
    no_preset: Annotated[
        bool,
        typer.Option(
            "--no-preset",
            help="Do not apply the default preset when --preset is unset.",
        ),
    ] = False,
    launch_target_id: Annotated[str | None, typer.Option()] = None,
    launch_mode: Annotated[
        LaunchMode | None,
        typer.Option(
            help="Transport to drive the agent: 'auto' (default), 'direct' "
            "(native structured adapter), or 'tmux_wrapper' (generic tmux pane).",
        ),
    ] = None,
    transport: Annotated[
        str | None,
        typer.Option(
            help="Pin the transport (interface) the agent is driven over: "
            "'claude_cli' (Chat), 'claude_tty' (Emulated), or 'tmux' (Terminal). "
            "Must be one of the agent's supported transports; takes precedence "
            "over --launch-mode. Omit to use the agent's default transport "
            "(claude_code defaults to Emulated).",
        ),
    ] = None,
    title: Annotated[str | None, typer.Option()] = None,
    launch_env: Annotated[
        list[str] | None,
        typer.Option(
            "--launch-env",
            help=(
                "Environment variable for the agent process as KEY=VALUE. "
                "Repeatable; values may contain '='."
            ),
        ),
    ] = None,
    args: Annotated[list[str] | None, typer.Argument()] = None,
) -> None:
    """Start a session in-process and print it as JSON."""
    asyncio.run(
        _session_start(
            _settings_from_ctx(ctx),
            backend=backend,
            cwd=cwd,
            preset=preset,
            no_preset=no_preset,
            launch_target_id=launch_target_id,
            launch_mode=launch_mode,
            transport=transport,
            title=title,
            launch_env=(
                _parse_launch_env(launch_env) if launch_env is not None else None
            ),
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
    backend: str | None,
    cwd: str | None,
    preset: str | None,
    no_preset: bool,
    launch_target_id: str | None,
    launch_mode: LaunchMode | None,
    transport: str | None,
    title: str | None,
    launch_env: dict[str, str] | None,
    args: list[str],
) -> None:
    context = AppContext(settings)
    context.settings.ensure_dirs()
    try:
        request_fields: dict[str, Any] = {
            "launch_target_id": launch_target_id,
            "title": title,
            "args": list(args),
        }
        # Only send backend/cwd when supplied; a preset may fill them in.
        if backend is not None:
            request_fields["backend"] = backend
        if cwd is not None:
            request_fields["cwd"] = cwd
        if launch_env is not None:
            request_fields["launch_env"] = launch_env
        # Omit launch_mode when unset so the request model's AUTO default applies.
        if launch_mode is not None:
            request_fields["launch_mode"] = launch_mode.value
        # Omit transport when unset so the request model's None default keeps
        # today's launch_mode-derived behavior.
        if transport is not None:
            request_fields["transport"] = transport
        if preset is not None:
            request_fields["preset_id"] = preset
        elif not no_preset:
            request_fields["use_default_preset"] = True
        try:
            resolved, matched = resolve_session_create_request(
                context.storage, SessionLaunchRequest(**request_fields)
            )
        except HTTPException as exc:
            raise typer.BadParameter(str(exc.detail)) from exc
        session = await context.runtime.create_session(
            resolved,
            preset_id=matched.id if matched else None,
            preset_name=matched.name if matched else None,
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


def _run_client(settings: Settings, run: Callable[[WaypointClient], Any]) -> Any:
    """Run a call against the live server, mapping transport errors to exit 1."""
    try:
        with WaypointClient(settings) as client:
            return run(client)
    except WaypointError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _emit(settings: Settings, run: Callable[[WaypointClient], Any]) -> None:
    """Run a client call against the live server and print the JSON result."""
    typer.echo(json.dumps(_run_client(settings, run), indent=2))


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


def _parse_launch_env(items: list[str] | None) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep:
            raise typer.BadParameter(f"--launch-env expects KEY=VALUE, got: {item}")
        env[key] = value
    try:
        return validate_launch_env(env)
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


def _parse_tags(items: list[str] | None) -> dict[str, str]:
    """Parse ``--tag`` values into a dict. ``key=value`` sets a value; a bare
    ``key`` stores an empty value (matched by presence)."""
    tags: dict[str, str] = {}
    for item in items or []:
        key, _, value = item.partition("=")
        if not key:
            raise typer.BadParameter(f"--tag expects a key, got: {item}")
        tags[key] = value
    return tags


_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_duration(value: str) -> float:
    """Parse a duration like ``90s``/``5m``/``2h``/``1d`` (a bare number is
    seconds) into seconds."""
    text = value.strip().lower()
    if not text:
        raise typer.BadParameter("duration is empty")
    unit = _DURATION_UNITS.get(text[-1])
    number = text[:-1] if unit is not None else text
    try:
        seconds = float(number) * (unit if unit is not None else 1)
    except ValueError as exc:
        raise typer.BadParameter(f"invalid duration: {value}") from exc
    if seconds < 0:
        raise typer.BadParameter(f"duration must be non-negative: {value}")
    return seconds


def _last_activity(session: dict[str, Any]) -> datetime | None:
    """A session's last-activity timestamp, parsed from ``last_event_at``.

    Coerces a naive timestamp (e.g. a tz-less value from imported thread
    history) to UTC so it can be compared against an aware ``now``."""
    raw = session.get("last_event_at")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _filter_idle(
    sessions: list[dict[str, Any]], idle_seconds: float
) -> list[dict[str, Any]]:
    """Keep sessions whose last activity is at least ``idle_seconds`` ago.
    Sessions with no parsable timestamp are treated as active (dropped)."""
    cutoff = datetime.now(UTC) - timedelta(seconds=idle_seconds)
    kept: list[dict[str, Any]] = []
    for session in sessions:
        last = _last_activity(session)
        if last is not None and last <= cutoff:
            kept.append(session)
    return kept


def _build_session_tree(
    sessions: list[dict[str, Any]], root_id: str
) -> dict[str, Any] | None:
    """Nested spawn tree rooted at ``root_id``, or ``None`` if it isn't present.

    Cycle-safe via a visited set so a self-referential or looped
    ``spawner_session_id`` can't recurse forever.
    """
    by_id = {s["id"]: s for s in sessions}
    if root_id not in by_id:
        return None
    children_map: dict[str, list[dict[str, Any]]] = {}
    for session in sessions:
        parent = session.get("spawner_session_id")
        if parent is not None:
            children_map.setdefault(parent, []).append(session)

    def node(session: dict[str, Any], seen: set[str]) -> dict[str, Any]:
        sid = session["id"]
        entry: dict[str, Any] = {
            "id": sid,
            "title": session.get("title"),
            "status": session.get("status"),
            "last_event_at": session.get("last_event_at"),
        }
        # Forward-compatible: surface tags when the server reports them.
        if "tags" in session:
            entry["tags"] = session["tags"]
        children: list[dict[str, Any]] = []
        for child in children_map.get(sid, []):
            if child["id"] in seen:
                continue
            seen.add(child["id"])
            children.append(node(child, seen))
        entry["children"] = children
        return entry

    return node(by_id[root_id], {root_id})


def compute_ready_tasks(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read-only view over the workqueue/crew ``task:``/``status:`` convention:
    the tasks that are pending and whose every dep is done.

    Follows the documented layout — ``deps=`` on the immutable ``task:<n>`` cell,
    ``state=`` on the mutable ``status:<n>`` cell — but tolerates both keys living
    on either cell (a one-cell layout after a metadata patch). A task is *ready*
    when its own state is ``todo``/unset and every id in its ``deps`` has state
    ``done``; a missing dep cell counts as not-done. Channels that don't follow
    the convention yield an empty list rather than an error.
    """
    meta_by_key = {
        cell["key"]: (cell.get("metadata") or {})
        for cell in cells
        if cell.get("key") is not None
    }
    text_by_key = {
        cell["key"]: cell.get("text", "")
        for cell in cells
        if cell.get("key") is not None
    }

    def meta_for(n: str) -> dict[str, Any]:
        # Merge task/status metadata so a key on either cell is seen.
        return {
            **meta_by_key.get(f"task:{n}", {}),
            **meta_by_key.get(f"status:{n}", {}),
        }

    def state_of(n: str) -> str:
        return str(meta_for(n).get("state", "") or "")

    task_numbers = sorted(
        (key.split(":", 1)[1] for key in meta_by_key if key.startswith("task:")),
        key=lambda n: (0, int(n)) if n.isdigit() else (1, 0),
    )
    ready: list[dict[str, Any]] = []
    for n in task_numbers:
        own_state = state_of(n)
        if own_state not in ("", "todo"):
            continue
        deps_raw = str(meta_for(n).get("deps", "") or "")
        deps = [d.strip() for d in deps_raw.split(",") if d.strip()]
        if all(state_of(dep) == "done" for dep in deps):
            ready.append(
                {"task": n, "text": text_by_key.get(f"task:{n}", ""), "deps": deps}
            )
    return ready


def parse_wait_until(raw: str | None) -> frozenset[str]:
    """Parse a comma-separated ``--until`` list into a set of valid statuses.

    ``None`` yields the default terminal/idle set; unknown statuses are an
    error so a typo never blocks forever.
    """
    if raw is None:
        return WAIT_DEFAULT_STATUSES
    requested = {item.strip() for item in raw.split(",") if item.strip()}
    if not requested:
        raise typer.BadParameter("--until needs at least one status")
    unknown = sorted(requested - {str(status) for status in SessionStatus})
    if unknown:
        raise typer.BadParameter(
            f"--until has unknown status(es): {', '.join(unknown)}"
        )
    return frozenset(requested)


def exit_code_for_wait(status: str | None) -> int:
    """Map a ``sessions wait`` outcome to a process exit code.

    ``None`` means the timeout elapsed (124); ``error`` is a failure (1);
    every other reached status is success (0), so shell ``&&`` chains compose.
    """
    if status is None:
        return WAIT_TIMEOUT_EXIT_CODE
    if status == SessionStatus.ERROR:
        return 1
    return 0


def _emit_ndjson(payload: Any) -> None:
    """Write one compact JSON object per line and flush.

    The streaming counterpart to ``_emit``'s single pretty-printed blob; used
    by ``sessions events --follow`` so each event is consumable as it arrives.
    """
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def parse_inbox_wait_until(raw: str | None) -> frozenset[str]:
    """Parse the inbox ``--until`` list into ``{resolved, update}``.

    Distinct from ``parse_wait_until`` (which validates session statuses);
    ``None`` defaults to ``resolved`` — the final gate.
    """
    if raw is None:
        return frozenset({"resolved"})
    requested = {item.strip() for item in raw.split(",") if item.strip()}
    if not requested:
        raise typer.BadParameter("--until needs one of: resolved, update")
    unknown = sorted(requested - INBOX_WAIT_UNTIL_CHOICES)
    if unknown:
        raise typer.BadParameter(f"--until has unknown value(s): {', '.join(unknown)}")
    return frozenset(requested)


class _InboxWaitClient(Protocol):
    """The slice of ``WaypointClient`` the wait engine needs (so tests can
    substitute a fake without depending on the whole client)."""

    def stream_inbox_envelopes(self, item_id: str) -> AsyncIterator[dict[str, Any]]: ...

    def get_inbox(self, item_id: str) -> dict[str, Any]: ...


class _InboxWaitResult(NamedTuple):
    outcome: str  # resolved | update | gone | timeout
    item: dict[str, Any] | None


def inbox_wait_exit_code(outcome: str) -> int:
    if outcome == "timeout":
        return WAIT_TIMEOUT_EXIT_CODE
    if outcome == "gone":
        return INBOX_GONE_EXIT_CODE
    return 0


def _inbox_condition_met(
    item: dict[str, Any], until: frozenset[str], baseline: int
) -> str | None:
    """Return the satisfied outcome (``resolved``/``update``) or ``None``.

    Evaluated against a snapshot — including the hydration frame — so an
    already-satisfied wait returns immediately instead of hanging.
    """
    if "resolved" in until and item.get("status") == "resolved":
        return "resolved"
    if "update" in until and int(item.get("version", 0)) > baseline:
        return "update"
    return None


def _resolve_baseline(baseline: list[int | None], item: dict[str, Any]) -> int:
    # First observed frame (WS hydration or first poll) fixes the ``update``
    # baseline so it never self-triggers, and — because the holder is shared —
    # a change seen by the WS path survives a WS→poll handoff instead of being
    # re-baselined off a fresh fetch.
    current = baseline[0]
    if current is None:
        current = int(item.get("version", 0))
        baseline[0] = current
    return current


async def _await_inbox_via_ws(
    client: _InboxWaitClient,
    item_id: str,
    until: frozenset[str],
    baseline: list[int | None],
) -> _InboxWaitResult | None:
    """Block on the per-item WS stream until the condition is met or the item
    is deleted. Returns ``None`` if the stream cannot connect (caller polls)."""
    try:
        async for envelope in client.stream_inbox_envelopes(item_id):
            if envelope.get("type") != "inbox_update":
                continue
            payload = envelope.get("payload", {})
            if payload.get("deleted"):
                return _InboxWaitResult("gone", None)
            item = payload.get("item")
            if not isinstance(item, dict):
                continue
            outcome = _inbox_condition_met(
                item, until, _resolve_baseline(baseline, item)
            )
            if outcome is not None:
                return _InboxWaitResult(outcome, item)
    except (OSError, WebSocketException):
        return None
    return None


async def _await_inbox_via_poll(
    client: _InboxWaitClient,
    item_id: str,
    until: frozenset[str],
    baseline: list[int | None],
) -> _InboxWaitResult:
    while True:
        try:
            # Off the loop: get_inbox is sync httpx, so an inline call would
            # block asyncio.timeout from firing.
            item = await asyncio.to_thread(client.get_inbox, item_id)
        except WaypointError as exc:
            if exc.status_code == 404:
                return _InboxWaitResult("gone", None)
            if exc.status_code is not None and 400 <= exc.status_code < 500:
                raise  # a genuine client error (e.g. 401) — don't spin on it
            # Transient (connection refused → status_code None, or a 5xx while
            # the backend restarts): a checkpoint is a durable gate, so keep
            # polling within the --timeout budget instead of aborting the wait.
            await asyncio.sleep(WAIT_POLL_INTERVAL_SECONDS)
            continue
        outcome = _inbox_condition_met(item, until, _resolve_baseline(baseline, item))
        if outcome is not None:
            return _InboxWaitResult(outcome, item)
        await asyncio.sleep(WAIT_POLL_INTERVAL_SECONDS)


async def _wait_for_inbox(
    client: _InboxWaitClient,
    item_id: str,
    until: frozenset[str],
    since: int | None,
    timeout: float | None,
) -> _InboxWaitResult:
    """Block until the item meets ``until``, is deleted (``gone``), or times out.

    Prefers the WS stream, falling back to polling if it cannot connect. The
    ``update`` baseline is shared across both paths so a version bump seen just
    before a WS drop is still reported after the poll takeover.
    """
    baseline: list[int | None] = [since]
    try:
        async with asyncio.timeout(timeout):
            streamed = await _await_inbox_via_ws(client, item_id, until, baseline)
            if streamed is not None:
                return streamed
            return await _await_inbox_via_poll(client, item_id, until, baseline)
    except TimeoutError:
        try:
            item: dict[str, Any] | None = await asyncio.to_thread(
                client.get_inbox, item_id
            )
        except WaypointError:
            item = None
        return _InboxWaitResult("timeout", item)


async def _await_status_via_ws(
    client: WaypointClient, session_id: str, until: frozenset[str]
) -> tuple[dict[str, Any], str] | None:
    """Block on the WS stream until a status in ``until`` is seen.

    Returns the final session and its status, or ``None`` if the stream cannot
    connect or closes first, so the caller can fall back to polling.
    """
    try:
        async for envelope in client.stream_session_envelopes(session_id):
            status = session_status_from_envelope(envelope)
            if status is None:
                continue
            if status in until:
                return envelope["payload"]["session"], status
    except (OSError, WebSocketException):
        return None
    return None


async def _await_status_via_poll(
    client: WaypointClient, session_id: str, until: frozenset[str]
) -> tuple[dict[str, Any], str]:
    while True:
        # Off the event loop: get_session is sync httpx, so running it inline
        # would block asyncio.timeout from firing until the request returned.
        session = await asyncio.to_thread(client.get_session, session_id)
        status = session.get("status")
        if isinstance(status, str) and status in until:
            return session, status
        await asyncio.sleep(WAIT_POLL_INTERVAL_SECONDS)


async def _wait_for_session(
    client: WaypointClient,
    session_id: str,
    until: frozenset[str],
    timeout: float | None,
) -> tuple[dict[str, Any], str | None]:
    """Block until the session reaches ``until`` or ``timeout`` elapses.

    Prefers the WS stream and falls back to polling if it cannot connect.
    Returns the final session plus the reached status, or a ``None`` status on
    timeout (the caller maps that to exit code 124).
    """
    try:
        async with asyncio.timeout(timeout):
            streamed = await _await_status_via_ws(client, session_id, until)
            if streamed is not None:
                return streamed
            return await _await_status_via_poll(client, session_id, until)
    except TimeoutError:
        session = await asyncio.to_thread(client.get_session, session_id)
        return session, None


async def _follow_events_fleet(
    settings: Settings,
    session_ids: list[str],
    *,
    spawned_by: str | None,
    mine: bool,
    filter_type: str | None,
) -> None:
    """Stream event envelopes as NDJSON across one or more sessions.

    Each output line is a compact JSON envelope; multi-session output adds a
    ``session_id`` key to disambiguate. Stops when all tracked sessions reach
    a terminal status or SIGINT fires.
    """
    try:
        with WaypointClient(settings) as client:
            extra_ids: list[str] = []
            if spawned_by or mine:
                sessions = await asyncio.to_thread(client.list_sessions)
                if spawned_by:
                    extra_ids += [
                        s["id"]
                        for s in sessions
                        if s.get("spawner_session_id") == spawned_by
                    ]
                if mine:
                    my_id = os.environ.get("WAYPOINT_SESSION_ID")
                    if my_id:
                        extra_ids += [
                            s["id"]
                            for s in sessions
                            if s.get("spawner_session_id") == my_id
                        ]

            # Deduplicate while preserving order.
            seen: set[str] = set()
            all_ids: list[str] = []
            for sid in session_ids + extra_ids:
                if sid not in seen:
                    seen.add(sid)
                    all_ids.append(sid)

            if not all_ids:
                typer.echo("error: no sessions to follow", err=True)
                raise typer.Exit(code=1)

            if len(all_ids) == 1:
                sid = all_ids[0]
                async for envelope in client.stream_session_envelopes(sid):
                    if is_event_envelope(envelope):
                        kind = envelope.get("payload", {}).get("event", {}).get("kind")
                        if filter_type is None or kind == filter_type:
                            _emit_ndjson(envelope)
                    if (
                        session_status_from_envelope(envelope)
                        in FOLLOW_TERMINAL_STATUSES
                    ):
                        break
                return

            # Multiple sessions: merge streams, prefix each line with session_id.
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

            async def _stream_one(sid: str) -> None:
                try:
                    async for envelope in client.stream_session_envelopes(sid):
                        if is_event_envelope(envelope):
                            await queue.put({"session_id": sid, **envelope})
                        if (
                            session_status_from_envelope(envelope)
                            in FOLLOW_TERMINAL_STATUSES
                        ):
                            break
                except (WaypointError, OSError, WebSocketException):
                    pass
                finally:
                    await queue.put(None)

            tasks = [asyncio.create_task(_stream_one(sid)) for sid in all_ids]
            remaining = len(all_ids)
            try:
                while remaining > 0:
                    item = await queue.get()
                    if item is None:
                        remaining -= 1
                    else:
                        kind = item.get("payload", {}).get("event", {}).get("kind")
                        if filter_type is None or kind == filter_type:
                            _emit_ndjson(item)
            finally:
                for task in tasks:
                    task.cancel()

    except (WaypointError, OSError, WebSocketException) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


async def _wait_sessions_concurrent(
    client: WaypointClient,
    session_ids: list[str],
    until: frozenset[str],
    timeout: float | None,
    *,
    first_wins: bool,
) -> list[tuple[dict[str, Any], str | None]]:
    """Wait for all sessions (or the first one) to reach ``until`` or timeout.

    Returns a list of ``(session, status)`` pairs.  ``status`` is ``None`` when
    the session's slot timed out before reaching the target set.
    """
    tasks = [
        asyncio.create_task(_wait_for_session(client, sid, until, None))
        for sid in session_ids
    ]
    try:
        async with asyncio.timeout(timeout):
            if first_wins:
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                first = next(iter(done))
                try:
                    return [first.result()]
                except Exception:
                    session = await asyncio.to_thread(
                        client.get_session, session_ids[0]
                    )
                    return [(session, None)]
            else:
                pairs = await asyncio.gather(*tasks, return_exceptions=True)
                results: list[tuple[dict[str, Any], str | None]] = []
                for i, pair in enumerate(pairs):
                    if isinstance(pair, BaseException):
                        try:
                            session = await asyncio.to_thread(
                                client.get_session, session_ids[i]
                            )
                        except Exception:
                            session = {"id": session_ids[i]}
                        results.append((session, None))
                    else:
                        results.append(pair)
                return results
    except TimeoutError:
        for t in tasks:
            t.cancel()
        results = []
        for sid in session_ids:
            try:
                session = await asyncio.to_thread(client.get_session, sid)
            except Exception:
                session = {"id": sid}
            results.append((session, None))
        return results


@sessions_app.command("list")
def sessions_list(
    ctx: typer.Context,
    spawned_by: Annotated[
        str | None,
        typer.Option(
            "--spawned-by", help="Return only sessions spawned by this session id."
        ),
    ] = None,
    mine: Annotated[
        bool,
        typer.Option(
            "--mine",
            help="Return only sessions spawned by $WAYPOINT_SESSION_ID.",
        ),
    ] = False,
    tag: Annotated[
        list[str] | None,
        typer.Option(
            "--tag",
            help="Keep only sessions matching key=value (exact) or a bare key "
            "(present). Repeatable; all must match.",
        ),
    ] = None,
    recursive: Annotated[
        bool,
        typer.Option(
            "--recursive",
            "-r",
            help="With --spawned-by/--mine, include the whole spawn subtree "
            "(transitive descendants), not just direct children.",
        ),
    ] = False,
    idle_for: Annotated[
        str | None,
        typer.Option(
            "--idle-for",
            help="Keep only sessions idle at least this long (e.g. 30m, 2h, 1d), "
            "measured from last activity.",
        ),
    ] = None,
) -> None:
    """List all sessions."""
    if mine:
        spawned_by = os.environ.get("WAYPOINT_SESSION_ID")
        if not spawned_by:
            raise typer.BadParameter(
                "$WAYPOINT_SESSION_ID is not set; cannot use --mine",
                param_hint="--mine",
            )
    if recursive and spawned_by is None:
        raise typer.BadParameter(
            "--recursive requires --spawned-by or --mine",
            param_hint="--recursive",
        )
    idle_seconds = _parse_duration(idle_for) if idle_for is not None else None

    def _run(c: WaypointClient) -> dict[str, Any]:
        sessions = c.list_sessions(spawned_by=spawned_by, tags=tag, recursive=recursive)
        if idle_seconds is not None:
            sessions = _filter_idle(sessions, idle_seconds)
        return {"sessions": sessions}

    _emit(_settings_from_ctx(ctx), _run)


@sessions_app.command("show")
def sessions_show(
    ctx: typer.Context, session_id: Annotated[str, typer.Argument()]
) -> None:
    """Show one session."""
    _emit(_settings_from_ctx(ctx), lambda c: {"session": c.get_session(session_id)})


@sessions_app.command("tag")
def sessions_tag(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    set_: Annotated[
        list[str] | None,
        typer.Option(
            "--set",
            help="Set a tag as key=value (or a bare key). Repeatable.",
        ),
    ] = None,
    unset: Annotated[
        list[str] | None,
        typer.Option("--unset", help="Remove a tag key. Repeatable."),
    ] = None,
) -> None:
    """Add or remove tags on an existing session."""
    if not set_ and not unset:
        raise typer.BadParameter("provide --set and/or --unset.")
    set_tags = _parse_tags(set_)

    def _run(c: WaypointClient) -> dict[str, Any]:
        return {
            "session": c.set_session_tags(
                session_id, set_tags=set_tags, unset=list(unset or [])
            )
        }

    _emit(_settings_from_ctx(ctx), _run)


@sessions_app.command("tree")
def sessions_tree(
    ctx: typer.Context, session_id: Annotated[str, typer.Argument()]
) -> None:
    """Show the spawn subtree rooted at a session (reconstructed from the server)."""

    def _run(c: WaypointClient) -> dict[str, Any]:
        tree = _build_session_tree(c.list_sessions(), session_id)
        if tree is None:
            typer.echo(f"error: no session with id '{session_id}'", err=True)
            raise typer.Exit(code=1)
        return {"tree": tree}

    _emit(_settings_from_ctx(ctx), _run)


@sessions_app.command("events")
def sessions_events(
    ctx: typer.Context,
    session_ids: Annotated[list[str] | None, typer.Argument()] = None,
    messages: Annotated[int | None, typer.Option()] = None,
    before_sequence: Annotated[int | None, typer.Option()] = None,
    follow: Annotated[
        bool,
        typer.Option(
            "--follow",
            "-f",
            help="Stream new event envelopes as NDJSON (one per line) until a "
            "terminal status or Ctrl+C, instead of printing the transcript.",
        ),
    ] = False,
    spawned_by: Annotated[
        str | None,
        typer.Option(
            "--spawned-by",
            help="(--follow only) Also follow sessions whose spawner_session_id "
            "matches this id.",
        ),
    ] = None,
    mine: Annotated[
        bool,
        typer.Option(
            "--mine",
            help="(--follow only) Also follow sessions spawned by this session "
            "(WAYPOINT_SESSION_ID env).",
        ),
    ] = False,
    filter_type: Annotated[
        str | None,
        typer.Option(
            "--filter",
            help="(--follow only) Print only events whose kind matches this value "
            "(e.g. approval_request, agent_output).",
        ),
    ] = None,
    coalesce: Annotated[
        bool,
        typer.Option(
            "--coalesce",
            "-c",
            help="(--no-follow only) Coalesce streaming deltas into logical events.",
        ),
    ] = False,
) -> None:
    """Show a session's transcript, or stream live events with --follow.

    Pass one or more SESSION_IDs, or use --spawned-by / --mine with --follow
    to resolve the set dynamically from the running server.
    """
    if follow:
        try:
            asyncio.run(
                _follow_events_fleet(
                    _settings_from_ctx(ctx),
                    list(session_ids or []),
                    spawned_by=spawned_by,
                    mine=mine,
                    filter_type=filter_type,
                )
            )
        except KeyboardInterrupt:
            pass
        return

    # Non-follow: exactly one session id required.
    if not session_ids or len(session_ids) != 1:
        typer.echo(
            "error: exactly one SESSION_ID is required without --follow", err=True
        )
        raise typer.Exit(code=1)
    session_id = session_ids[0]

    def _get_and_process(c: WaypointClient) -> dict[str, Any]:
        page = c.get_events(
            session_id, messages=messages, before_sequence=before_sequence
        )
        if coalesce:
            from waypoint.events import coalesce_events

            page["events"] = coalesce_events(page["events"])
        return page

    _emit(
        _settings_from_ctx(ctx),
        _get_and_process,
    )


def _conversation_events(events_page: dict[str, Any]) -> list[dict[str, Any]]:
    visible = {"user_input", "agent_output"}
    return [event for event in events_page["events"] if event.get("kind") in visible]


@sessions_app.command("wait")
def sessions_wait(
    ctx: typer.Context,
    session_ids: Annotated[list[str], typer.Argument()],
    until: Annotated[
        str | None,
        typer.Option(
            "--until",
            help="Comma-separated statuses to wait for. Defaults to "
            "idle,waiting_input,exited,error.",
        ),
    ] = None,
    timeout: Annotated[
        float | None,
        typer.Option("--timeout", help="Give up after this many seconds (exit 124)."),
    ] = None,
    any_: Annotated[
        bool,
        typer.Option(
            "--any",
            help="Return as soon as the first session reaches the until-set "
            "(default: wait for ALL).",
        ),
    ] = False,
) -> None:
    """Block until one or more sessions reach a terminal/idle status.

    With a single id emits ``{"session": {...}}``; with multiple ids emits
    ``{"sessions": [...]}``.  Exits with a status-mapped code (error=1,
    timeout=124, otherwise 0) so it composes in shell ``&&`` chains.  Prefers
    the WS stream, falling back to polling if it cannot connect.
    """
    if not session_ids:
        typer.echo("error: provide at least one SESSION_ID", err=True)
        raise typer.Exit(code=1)

    until_set = parse_wait_until(until)
    outcome_statuses: list[str | None] = []

    def run(c: WaypointClient) -> dict[str, Any]:
        pairs = asyncio.run(
            _wait_sessions_concurrent(
                c, session_ids, until_set, timeout, first_wins=any_
            )
        )
        outcome_statuses.extend(status for _, status in pairs)
        if len(session_ids) == 1:
            session, _ = pairs[0]
            return {"session": session}
        return {"sessions": [session for session, _ in pairs]}

    _emit(_settings_from_ctx(ctx), run)

    if any(s is None for s in outcome_statuses):
        raise typer.Exit(code=WAIT_TIMEOUT_EXIT_CODE)
    if any(s == SessionStatus.ERROR for s in outcome_statuses):
        raise typer.Exit(code=1)


def _create_worktree(branch: str, base: str | None, cwd: str) -> str:
    """Create a git worktree for ``branch`` and return its path.

    The worktree is placed in a sibling directory of the repo root so it
    never appears as an untracked file inside the working tree.
    """
    try:
        repo_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        typer.echo(f"error: not a git repository: {exc.stderr.strip()}", err=True)
        raise typer.Exit(code=1) from exc

    if base is None:
        try:
            base = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except subprocess.CalledProcessError:
            base = "main"

    # Sanitize branch name for use as a directory component.
    safe = branch.replace("/", "-").replace("\\", "-")
    repo_dir = Path(repo_root)
    worktree_path = str(repo_dir.parent / f"{repo_dir.name}-{safe}")

    try:
        subprocess.run(
            ["git", "worktree", "add", worktree_path, "-b", branch, base],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        typer.echo(f"error: git worktree add failed: {exc.stderr.strip()}", err=True)
        raise typer.Exit(code=1) from exc

    return worktree_path


def _warn_unknown_model(
    client: WaypointClient,
    backend: str,
    model: str,
    launch_target_id: str | None,
) -> None:
    """Warn (never block) when ``--model`` isn't in the backend's catalogue.

    A wrong id spawns fine and only dies on the first turn, so surfacing it
    here gives a fast hint. Backends accept free-text ids, so this only warns;
    if discovery is unavailable the check is skipped rather than guessed.
    """
    try:
        catalog = client.list_models(backend, launch_target_id=launch_target_id)
    except WaypointError:
        return
    ids = {m.get("id") for m in catalog.get("models", []) if m.get("id")}
    if not ids or model in ids:
        return
    if catalog.get("supports_free_text"):
        tail = (
            "the backend accepts free-text ids, but confirm the worker survives "
            "its first turn"
        )
    else:
        tail = "it will likely be rejected on the first turn"
    typer.echo(
        f"warning: model {model!r} is not among {backend}'s advertised models "
        f"({', '.join(sorted(ids))}); {tail}.",
        err=True,
    )


def _validate_launch_permission_mode(
    client: WaypointClient, backend: str, mode: str
) -> None:
    """Validate a launch-time ``--permission-mode`` against the chosen backend.

    Mirrors ``_validate_permission_mode`` but for session creation: the mode is
    a launch flag, not an in-place change, so it checks the agent's advertised
    ``permission_modes`` vocabulary without requiring inline-set support. A
    backend that advertises no modes is left to the server to reject.
    """
    descriptor = next(
        (b for b in client.list_backends() if b.get("id") == backend), None
    )
    if descriptor is None:
        return
    caps = descriptor.get("capabilities", {})
    valid = [spec["id"] for spec in caps.get("permission_modes", [])]
    if valid and mode not in valid:
        raise typer.BadParameter(
            f"unknown permission mode {mode!r} for backend {backend!r}; "
            f"choose one of: {', '.join(valid)}",
            param_hint="--permission-mode",
        )


def _preset_spec_for_warnings(
    client: WaypointClient, preset_ref: str | None, use_default: bool
) -> dict[str, Any]:
    """Fetch the applicable preset's redacted spec, for computing the effective
    launch values the model/permission warnings run against.

    Best-effort only: any discovery failure yields ``{}`` so warnings degrade to
    the explicit-flag behavior instead of erroring."""
    try:
        if preset_ref is not None:
            preset = client.get_session_preset(preset_ref)
        elif use_default:
            listing = client.list_session_presets()
            default_id = listing.get("default_preset_id")
            if not default_id:
                return {}
            preset = client.get_session_preset(default_id)
        else:
            return {}
    except WaypointError:
        return {}
    spec = preset.get("spec")
    return spec if isinstance(spec, dict) else {}


@sessions_app.command("start")
def sessions_start(
    ctx: typer.Context,
    backend: Annotated[
        str | None,
        typer.Option(callback=_validate_backend, autocompletion=_complete_backend),
    ] = None,
    cwd: Annotated[
        str | None, typer.Option(help="Working directory for the session.")
    ] = None,
    preset: Annotated[
        str | None,
        typer.Option(
            "--preset",
            help="Apply a session preset (by id or name) before launch. Explicit "
            "flags override preset values.",
        ),
    ] = None,
    no_preset: Annotated[
        bool,
        typer.Option(
            "--no-preset",
            help="Do not apply the default preset when --preset is unset.",
        ),
    ] = False,
    launch_target_id: Annotated[str | None, typer.Option()] = None,
    launch_mode: Annotated[
        LaunchMode | None,
        typer.Option(
            help="Transport to drive the agent: 'auto' (default), 'direct' "
            "(native structured adapter), or 'tmux_wrapper' (generic tmux pane).",
        ),
    ] = None,
    transport: Annotated[
        str | None,
        typer.Option(
            help="Pin the transport (interface) the agent is driven over: "
            "'claude_cli' (Chat), 'claude_tty' (Emulated), or 'tmux' (Terminal). "
            "Must be one of the agent's supported transports; takes precedence "
            "over --launch-mode. Omit to use the agent's default transport "
            "(claude_code defaults to Emulated).",
        ),
    ] = None,
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
    worktree: Annotated[
        str | None,
        typer.Option(
            "--worktree",
            help="Create a git worktree on this new branch and use it as the session cwd.",
        ),
    ] = None,
    worktree_base: Annotated[
        str | None,
        typer.Option(
            "--worktree-base",
            help="Base ref for the new worktree branch (default: current HEAD, else main).",
        ),
    ] = None,
    tag: Annotated[
        list[str] | None,
        typer.Option(
            "--tag",
            help="Tag the session as key=value (or a bare key). Repeatable. "
            "Filter later with `sessions list --tag` / `reap --tag`.",
        ),
    ] = None,
    launch_env: Annotated[
        list[str] | None,
        typer.Option(
            "--launch-env",
            help=(
                "Environment variable for the agent process as KEY=VALUE. "
                "Repeatable; values may contain '='."
            ),
        ),
    ] = None,
    args: Annotated[list[str] | None, typer.Argument()] = None,
) -> None:
    """Launch a new session on the running server."""
    use_default = preset is None and not no_preset
    effective_cwd = cwd
    worktree_path: str | None = None
    if worktree is not None:
        if cwd is None:
            raise typer.BadParameter(
                "--cwd is required with --worktree", param_hint="--cwd"
            )
        worktree_path = _create_worktree(worktree, worktree_base, cwd)
        effective_cwd = worktree_path
    tags = _parse_tags(tag)
    launch_env_map = _parse_launch_env(launch_env) if launch_env is not None else None

    def _run(c: WaypointClient) -> dict[str, Any]:
        # Compute the effective backend/model/permission after the preset would
        # apply (explicit flag wins) so the warnings still fire for stale preset
        # values even when the user passes only --preset.
        spec = _preset_spec_for_warnings(c, preset, use_default)
        eff_backend = backend or spec.get("backend")
        eff_model = model or spec.get("model")
        eff_permission = permission_mode or spec.get("permission_mode")
        eff_target = launch_target_id or spec.get("launch_target_id")
        if eff_backend:
            if eff_permission is not None:
                _validate_launch_permission_mode(c, eff_backend, eff_permission)
            if eff_model is not None:
                _warn_unknown_model(c, eff_backend, eff_model, eff_target)
        return {
            "session": c.create_session(
                backend=backend,
                cwd=effective_cwd,
                launch_target_id=launch_target_id,
                launch_mode=launch_mode.value if launch_mode is not None else None,
                transport=transport,
                title=title,
                model=model,
                effort=effort,
                permission_mode=permission_mode,
                spawner_session_id=spawner_session_id,
                worktree_path=worktree_path,
                args=list(args or []),
                tags=tags,
                launch_env=launch_env_map,
                preset_id=preset,
                use_default_preset=use_default,
            )
        }

    _emit(_settings_from_ctx(ctx), _run)


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
    attachment_id: Annotated[
        list[str] | None,
        typer.Option(
            "--attachment-id",
            help="ID of an already-uploaded attachment to include (repeatable). "
            "Use `sessions upload` to obtain IDs. Files from --attach are "
            "uploaded first; --attachment-id values follow in order.",
        ),
    ] = None,
) -> None:
    """Send a message to a session.

    Exits 0 on confirmed delivery or when the server accepted the input.
    On transport timeout, reports ``{"session": {..., "send": "delivered"}}``
    when the session advanced to running, or ``{"send": "unknown"}`` when
    delivery cannot be confirmed, and exits 1 in the unknown case.
    """

    def _run(c: WaypointClient) -> dict[str, Any]:
        uploaded = [
            c.upload_attachment(session_id, path)["id"] for path in attach or []
        ]
        combined = uploaded + list(attachment_id or [])
        return {"session": c.send_input(session_id, text, attachments=combined or None)}

    result = _run_client(_settings_from_ctx(ctx), _run)
    typer.echo(json.dumps(result, indent=2))
    if result.get("session", {}).get("send") == "unknown":
        raise typer.Exit(code=1)


@sessions_app.command("upload")
def sessions_upload(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    files: Annotated[
        list[Path],
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="File(s) to upload.",
        ),
    ],
    pin: Annotated[
        bool,
        typer.Option(
            "--pin",
            help="Exempt the upload(s) from the orphan sweep so they persist "
            "without being sent in a message.",
        ),
    ] = False,
) -> None:
    """Upload file attachment(s) to a session without sending a message."""

    def _run(c: WaypointClient) -> dict[str, Any]:
        specs = [c.upload_attachment(session_id, path, pin=pin) for path in files]
        return {"attachments": specs}

    _emit(_settings_from_ctx(ctx), _run)


@attachments_app.command("list")
def attachments_list(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
) -> None:
    """List a session's attachments (newest first)."""

    def _run(c: WaypointClient) -> dict[str, Any]:
        return {"attachments": c.list_attachments(session_id)}

    _emit(_settings_from_ctx(ctx), _run)


@attachments_app.command("get")
def attachments_get(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    attachment_id: Annotated[str, typer.Argument()],
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            "-o",
            dir_okay=True,
            help="Write to this path (a directory uses the original filename); "
            "defaults to the original filename in the current directory.",
        ),
    ] = None,
) -> None:
    """Download an attachment to a local file."""

    def _run(c: WaypointClient) -> dict[str, Any]:
        content, filename = c.download_attachment(session_id, attachment_id)
        destination = out if out is not None else Path(filename)
        if destination.is_dir():
            destination = destination / filename
        destination.write_bytes(content)
        return {"attachment_id": attachment_id, "path": str(destination.resolve())}

    _emit(_settings_from_ctx(ctx), _run)


@attachments_app.command("delete")
def attachments_delete(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    attachment_id: Annotated[str, typer.Argument()],
) -> None:
    """Delete a single attachment."""

    def _run(c: WaypointClient) -> dict[str, Any]:
        c.delete_attachment(session_id, attachment_id)
        return {"attachment_id": attachment_id, "deleted": True}

    _emit(_settings_from_ctx(ctx), _run)


@attachments_app.command("delete-all")
def attachments_delete_all(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
) -> None:
    """Delete every attachment on a session."""
    if not yes:
        typer.confirm(f"Delete all attachments on {session_id}?", abort=True)

    def _run(c: WaypointClient) -> dict[str, Any]:
        c.delete_all_attachments(session_id)
        return {"session_id": session_id, "deleted_all": True}

    _emit(_settings_from_ctx(ctx), _run)


@attachments_app.command("pin")
def attachments_pin(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    attachment_id: Annotated[str, typer.Argument()],
) -> None:
    """Pin an existing attachment so the orphan sweep never reaps it."""

    def _run(c: WaypointClient) -> dict[str, Any]:
        c.pin_attachment(session_id, attachment_id)
        return {"attachment_id": attachment_id, "pinned": True}

    _emit(_settings_from_ctx(ctx), _run)


@attachments_app.command("unpin")
def attachments_unpin(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    attachment_id: Annotated[str, typer.Argument()],
) -> None:
    """Unpin an attachment, re-exposing it to the orphan sweep."""

    def _run(c: WaypointClient) -> dict[str, Any]:
        c.unpin_attachment(session_id, attachment_id)
        return {"attachment_id": attachment_id, "pinned": False}

    _emit(_settings_from_ctx(ctx), _run)


def _validate_permission_mode(
    client: WaypointClient, session: dict[str, Any], mode: str
) -> None:
    """Validate ``mode`` against the session's backend before the round trip.

    Keeps the error local and lists the accepted ids rather than relaying a
    bare server 400. A backend that doesn't advertise its modes (or that the
    catalogue can't resolve) is left to the server to reject.
    """
    backend = session.get("backend")
    descriptor = next(
        (b for b in client.list_backends() if b.get("id") == backend), None
    )
    if descriptor is None:
        return
    caps = descriptor.get("capabilities", {})
    if not caps.get("supports_set_permission_mode_inline"):
        raise typer.BadParameter(
            f"backend {backend!r} does not support setting the permission mode",
            param_hint="MODE",
        )
    valid = [spec["id"] for spec in caps.get("permission_modes", [])]
    if valid and mode not in valid:
        raise typer.BadParameter(
            f"unknown permission mode {mode!r} for backend {backend!r}; "
            f"choose one of: {', '.join(valid)}",
            param_hint="MODE",
        )


@sessions_app.command("set-permission-mode")
@sessions_app.command("mode")
def sessions_set_permission_mode(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument()],
    mode: Annotated[str, typer.Argument()],
) -> None:
    """Change a running session's permission mode in place.

    Only structured backends that apply the change live accept it; others are
    rejected with the accepted ids. Avoids reap + respawn just to widen a
    stalled worker's auto-approval posture.
    """

    def _run(c: WaypointClient) -> dict[str, Any]:
        _validate_permission_mode(c, c.get_session(session_id), mode)
        return {"session": c.set_permission_mode(session_id, mode)}

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
    prune_branches: Annotated[
        bool,
        typer.Option(
            "--prune-branches",
            help="Force-delete the worktree's branch even if unmerged. Without "
            "this, an unmerged branch is left in place (merged ones are always "
            "pruned).",
        ),
    ] = False,
) -> None:
    """Terminate (if needed) and remove a session record."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: c.delete(session_id, force=force, prune_branches=prune_branches),
    )


@sessions_app.command("reap")
def sessions_reap(
    ctx: typer.Context,
    spawned_by: Annotated[
        str | None,
        typer.Option(
            "--spawned-by", help="Reap only sessions spawned by this session id."
        ),
    ] = None,
    mine: Annotated[
        bool,
        typer.Option(
            "--mine",
            help="Reap only sessions spawned by $WAYPOINT_SESSION_ID.",
        ),
    ] = False,
    all_sessions: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Reap all sessions regardless of spawner. Required when no scope is given.",
        ),
    ] = False,
    prune_branches: Annotated[
        bool,
        typer.Option(
            "--prune-branches",
            help="Force-delete each reaped worktree's branch even if unmerged. "
            "Use for crew teardown where worker branches are discarded; "
            "leftover branches otherwise collide with a respawn's --worktree.",
        ),
    ] = False,
    tag: Annotated[
        list[str] | None,
        typer.Option(
            "--tag",
            help="Reap only sessions matching key=value (exact) or a bare key "
            "(present). Repeatable; all must match.",
        ),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude",
            help="Session id(s) to spare from the reap. Repeatable. Use to keep "
            "the standing crew while tearing down overflow.",
        ),
    ] = None,
) -> None:
    """Terminate and delete sessions in bulk."""
    if mine:
        spawned_by = os.environ.get("WAYPOINT_SESSION_ID")
        if not spawned_by:
            raise typer.BadParameter(
                "$WAYPOINT_SESSION_ID is not set; cannot use --mine",
                param_hint="--mine",
            )

    if spawned_by is None and not all_sessions and not tag:
        raise typer.BadParameter(
            "pass --spawned-by <id>, --mine, --all, or --tag to select a scope",
            param_hint="--spawned-by/--mine/--all/--tag",
        )

    excluded = set(exclude or [])

    def _run(client: WaypointClient) -> dict[str, Any]:
        sessions = client.list_sessions(spawned_by=spawned_by, tags=tag)
        reaped: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []
        for session in sessions:
            sid = session["id"]
            if sid in excluded:
                skipped.append(sid)
                continue
            try:
                client.delete(sid, prune_branches=prune_branches)
                reaped.append(sid)
            except Exception:
                failed.append(sid)
        return {"reaped": reaped, "failed": failed, "skipped": skipped}

    _emit(_settings_from_ctx(ctx), _run)


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
        str | None,
        typer.Option(
            "--json",
            help="Path to a JSON object body, or - to read it from stdin.",
            metavar="FILE|-",
        ),
    ] = None,
    thread_id: Annotated[
        str | None,
        typer.Option("--thread-id", help="Backend-native thread id to import."),
    ] = None,
    import_history: Annotated[
        bool | None,
        typer.Option(
            "--import-history/--no-import-history",
            help=(
                "Replay the thread's prior conversation into the new session's "
                "transcript (default). --no-import-history starts empty and only "
                "resumes the agent's own context."
            ),
        ),
    ] = None,
    launch_env: Annotated[
        list[str] | None,
        typer.Option(
            "--launch-env",
            help=(
                "Environment variable for the agent process as KEY=VALUE. "
                "Repeatable; values may contain '='."
            ),
        ),
    ] = None,
) -> None:
    """Import a backend-native thread into Waypoint.

    Provide the request body via ``--json`` and/or the individual flags; an
    explicit flag overrides the same field in the JSON body.
    """
    body = _parse_json_object(json_source) if json_source is not None else {}
    if thread_id is not None:
        body["thread_id"] = thread_id
    if import_history is not None:
        body["import_history"] = import_history
    if launch_env is not None:
        body["launch_env"] = _parse_launch_env(launch_env)
    if not body.get("thread_id"):
        raise typer.BadParameter("pass --thread-id or a --json body with thread_id")
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
    raw: Annotated[
        bool,
        typer.Option(
            "--raw",
            help="Return all raw event deltas without coalescing.",
        ),
    ] = False,
) -> None:
    """Show just the conversational transcript from a session."""
    if text:
        page = _run_client(
            _settings_from_ctx(ctx),
            lambda c: c.get_events(session_id, messages=messages),
        )
        if not raw:
            from waypoint.events import coalesce_events

            page["events"] = coalesce_events(page["events"])

        agent_text = ("\n\n" if not raw else "").join(
            event["text"]
            for event in _conversation_events(page)
            if event.get("kind") == "agent_output"
        )
        typer.echo(agent_text, nl=False)
        return

    def _get_and_process(c: WaypointClient) -> dict[str, Any]:
        page = c.get_events(session_id, messages=messages)
        if not raw:
            from waypoint.events import coalesce_events

            page["events"] = coalesce_events(page["events"])
        return {"events": _conversation_events(page)}

    _emit(
        _settings_from_ctx(ctx),
        _get_and_process,
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
            "Keyed cells are pruned when that session is deleted; "
            "keyless log posts survive as durable history.",
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
    json_output: Annotated[
        bool,
        typer.Option(
            "--json", help="Emit structured JSON instead of the rendered view."
        ),
    ] = False,
) -> None:
    """Read entries from a board channel.

    Default view: a Cells section followed by a Log section (newest-first).
    With --json: ``{"channel": ..., "cells": [...], "log": [...]}``.
    """
    entries = _run_client(
        _settings_from_ctx(ctx), lambda c: c.read_board(channel, since=since, key=key)
    )
    cells = [e for e in entries if e.get("key") is not None]
    log = [e for e in entries if e.get("key") is None]

    if key is not None and not cells:
        typer.echo(f"no cell '{key}' matched in {channel}", err=True)

    if json_output:
        typer.echo(
            json.dumps({"channel": channel, "cells": cells, "log": log}, indent=2)
        )
        return

    typer.echo(f"=== Cells ({channel}) ===")
    if cells:
        for cell in cells:
            meta_str = "  ".join(
                f"{k}={v}" for k, v in (cell.get("metadata") or {}).items()
            )
            line = cell.get("key", "")
            if meta_str:
                line += f"  [{meta_str}]"
            line += f"  {cell.get('text', '')}"
            typer.echo(line)
    else:
        typer.echo("(no cells)")

    typer.echo(f"\n=== Log ({channel}) ===")
    if log:
        for post in reversed(log):
            ts = post.get("created_at", "")
            author = post.get("author_label") or post.get("author_session_id") or "—"
            typer.echo(f"{ts}  {author}: {post.get('text', '')}")
    else:
        typer.echo("(no posts)")


@board_app.command("ready")
def board_ready(
    ctx: typer.Context,
    channel: Annotated[str, typer.Argument()],
) -> None:
    """List tasks in a channel whose deps are all done (read-only helper).

    Reads the ``task:``/``status:`` cell convention and reports the tasks that
    are pending with every dependency satisfied. This is a convenience view; it
    enforces nothing and stays out of the way of the skill-side task logic.
    """
    entries = _run_client(_settings_from_ctx(ctx), lambda c: c.read_board(channel))
    ready = compute_ready_tasks(entries)
    typer.echo(json.dumps({"channel": channel, "ready": ready}, indent=2))


@board_app.command("log")
def board_log(
    ctx: typer.Context,
    channel: Annotated[str, typer.Argument()],
    since: Annotated[
        int | None,
        typer.Option("--since", help="Only posts with an id greater than this."),
    ] = None,
    author: Annotated[
        str | None,
        typer.Option("--author", help="Filter by author session id."),
    ] = None,
    grep: Annotated[
        str | None,
        typer.Option("--grep", help="Substring match on post text (case-insensitive)."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Maximum number of posts to show."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json", help="Emit structured JSON instead of the rendered view."
        ),
    ] = False,
) -> None:
    """Show the append-log (keyless posts) for a channel, newest-first."""
    entries = _run_client(
        _settings_from_ctx(ctx), lambda c: c.read_board(channel, since=since)
    )
    posts = [e for e in entries if e.get("key") is None]

    if author is not None:
        posts = [p for p in posts if p.get("author_session_id") == author]
        if not posts:
            typer.echo(f"no posts by '{author}' matched in {channel}", err=True)

    if grep is not None:
        posts = [p for p in posts if grep.lower() in (p.get("text") or "").lower()]
        if not posts:
            typer.echo(f"no posts matching '{grep}' matched in {channel}", err=True)

    posts = list(reversed(posts))
    if limit is not None:
        posts = posts[:limit]

    if json_output:
        typer.echo(json.dumps(posts, indent=2))
        return

    if posts:
        for post in posts:
            ts = post.get("created_at", "")
            author_label = (
                post.get("author_label") or post.get("author_session_id") or "—"
            )
            typer.echo(f"{ts}  {author_label}: {post.get('text', '')}")
    else:
        typer.echo("(no posts)")


@board_app.command("channels")
def board_channels(ctx: typer.Context) -> None:
    """List board channels and their entry counts."""
    _emit(_settings_from_ctx(ctx), lambda c: {"channels": c.list_board_channels()})


@board_app.command("clear")
def board_clear(
    ctx: typer.Context,
    channel: Annotated[str, typer.Argument()],
    keep_last: Annotated[
        int | None,
        typer.Option(
            "--keep-last",
            help="Retain the N most-recent log posts; cells are always dropped.",
            min=1,
        ),
    ] = None,
) -> None:
    """Remove all posts from a channel, keeping the (now empty) channel.

    With --keep-last N, the N most-recent keyless log posts are kept; cells
    are always deleted.
    """
    _emit(
        _settings_from_ctx(ctx), lambda c: c.clear_board(channel, keep_last=keep_last)
    )


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


@board_app.command("set-meta")
def board_set_meta(
    ctx: typer.Context,
    channel: Annotated[str, typer.Argument()],
    meta: Annotated[
        list[str] | None,
        typer.Option("--meta", help="Set metadata key=value. Repeatable."),
    ] = None,
    key: Annotated[
        str | None, typer.Option("--key", help="Cell key to target.")
    ] = None,
    entry_id: Annotated[
        int | None, typer.Option("--entry-id", help="Entry id to target.")
    ] = None,
    merge: Annotated[
        bool,
        typer.Option(
            "--merge/--replace",
            help="Merge --meta into the existing metadata (patch) instead of "
            "replacing the whole blob.",
        ),
    ] = False,
    unset: Annotated[
        list[str] | None,
        typer.Option("--unset", help="Remove metadata key(s). Repeatable."),
    ] = None,
) -> None:
    """Update a keyed cell's metadata without changing its text."""
    if key is None and entry_id is None:
        raise typer.BadParameter("Provide --key or --entry-id.")
    if key is not None and entry_id is not None:
        raise typer.BadParameter("--key and --entry-id are mutually exclusive.")
    metadata = _parse_meta(meta)

    def _run(c: WaypointClient) -> dict[str, Any]:
        if key is not None:
            entries = c.read_board(channel, key=key)
            if not entries:
                typer.echo(f"error: no entry with key '{key}' in {channel}", err=True)
                raise typer.Exit(code=1)
            eid: int = entries[0]["id"]
        else:
            assert entry_id is not None
            eid = entry_id
        return {
            "entry": c.update_board_entry(
                channel,
                eid,
                text=None,
                metadata=metadata,
                merge=merge,
                unset=unset,
            )
        }

    _emit(_settings_from_ctx(ctx), _run)


@inbox_app.command("post")
def inbox_post(
    ctx: typer.Context,
    json_source: Annotated[
        str,
        typer.Option(
            "--json",
            help="Path to a JSON object body ({subject, blocks[]}), or - for stdin.",
            metavar="FILE|-",
        ),
    ],
    from_session_id: Annotated[
        str | None,
        typer.Option(
            envvar="WAYPOINT_SESSION_ID",
            help="Requesting session; defaults to this session's id. Reply "
            "attachments are pinned into this session's store.",
        ),
    ] = None,
) -> None:
    """Post an inbox item. The body is JSON because a multi-block item is
    inherently structured; skills build the block list."""
    body = _parse_json_object(json_source)
    subject = body.get("subject")
    blocks = body.get("blocks", [])
    if not isinstance(subject, str) or not subject:
        raise typer.BadParameter("--json must include a non-empty 'subject'")
    if not isinstance(blocks, list):
        raise typer.BadParameter("--json 'blocks' must be a list")
    resolved_session = from_session_id or body.get("from_session_id")
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {
            "item": c.post_inbox(subject, blocks, from_session_id=resolved_session)
        },
    )


@inbox_app.command("get")
def inbox_get(ctx: typer.Context, item_id: Annotated[str, typer.Argument()]) -> None:
    """Read a single inbox item, including block answers and replies."""
    _emit(_settings_from_ctx(ctx), lambda c: {"item": c.get_inbox(item_id)})


@inbox_app.command("list")
def inbox_list(
    ctx: typer.Context,
    status: Annotated[
        str | None,
        typer.Option("--status", help="Filter by status: open | resolved."),
    ] = None,
    q: Annotated[
        str | None,
        typer.Option("--q", help="Search subject + sender label."),
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Page size (default 20).")
    ] = None,
    cursor: Annotated[
        str | None, typer.Option("--cursor", help="Load-more cursor from a prior page.")
    ] = None,
) -> None:
    """List inbox items (status filter, subject/label search, load-more)."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: c.list_inbox(status=status, query=q, limit=limit, cursor=cursor),
    )


@inbox_app.command("answer")
def inbox_answer(
    ctx: typer.Context,
    item_id: Annotated[str, typer.Argument()],
    block_id: Annotated[str, typer.Argument()],
    answer_json: Annotated[
        str | None,
        typer.Option(
            "--answer-json",
            help='JSON answer for the block, e.g. \'{"selected":["yes"]}\' '
            'or \'{"decision":"approve"}\'.',
        ),
    ] = None,
    notes: Annotated[
        str | None,
        typer.Option("--notes", help="Free-text reply note attached to the block."),
    ] = None,
    attach: Annotated[
        list[str] | None,
        typer.Option(
            "--attach",
            help="Attach an existing blob as session_id:attachment_id. Repeatable.",
        ),
    ] = None,
) -> None:
    """Answer and/or reply to one block (scripting path; the UI is primary)."""
    answer: dict[str, Any] | None = None
    if answer_json is not None:
        try:
            parsed = json.loads(answer_json)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"--answer-json is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise typer.BadParameter("--answer-json must be a JSON object")
        answer = parsed
    reply: dict[str, Any] | None = None
    if notes is not None or attach:
        attachments: list[dict[str, str]] = []
        for item in attach or []:
            session_part, sep, attachment_part = item.partition(":")
            if not sep or not session_part or not attachment_part:
                raise typer.BadParameter(
                    f"--attach expects session_id:attachment_id, got: {item}"
                )
            attachments.append(
                {"session_id": session_part, "attachment_id": attachment_part}
            )
        reply = {"notes": notes, "attachments": attachments}
    if answer is None and reply is None:
        raise typer.BadParameter("provide --answer-json and/or --notes/--attach")
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {
            "item": c.submit_inbox_block(item_id, block_id, answer=answer, reply=reply)
        },
    )


@inbox_app.command("read")
def inbox_read(ctx: typer.Context, item_id: Annotated[str, typer.Argument()]) -> None:
    """Mark an item read (resolves a no-action FYI item)."""
    _emit(_settings_from_ctx(ctx), lambda c: {"item": c.mark_inbox_read(item_id)})


@inbox_app.command("delete")
def inbox_delete(ctx: typer.Context, item_id: Annotated[str, typer.Argument()]) -> None:
    """Delete an inbox item (a waiting lead sees a terminal ``gone`` outcome)."""
    _emit(_settings_from_ctx(ctx), lambda c: c.delete_inbox(item_id))


@inbox_app.command("wait")
def inbox_wait(
    ctx: typer.Context,
    item_id: Annotated[str, typer.Argument()],
    until: Annotated[
        str | None,
        typer.Option(
            "--until",
            help="resolved (all required blocks answered) or update (first "
            "change past --since). Defaults to resolved.",
        ),
    ] = None,
    since: Annotated[
        int | None,
        typer.Option(
            "--since",
            help="Version baseline for --until update; defaults to the version "
            "observed at connect.",
        ),
    ] = None,
    timeout: Annotated[
        str | None,
        typer.Option(
            "--timeout",
            help="Give up after this duration (e.g. 5m, 2h); exit 124 on timeout.",
        ),
    ] = None,
) -> None:
    """Block until an item resolves, changes, or is deleted.

    Emits ``{"outcome": ..., "item": ...}`` where outcome is resolved, update,
    timeout, or gone. Exit codes: 0 on resolved/update, 124 on timeout, 3 on
    gone — so a lead can branch in a shell chain. Prefers the WS stream,
    falling back to polling.
    """
    until_set = parse_inbox_wait_until(until)
    timeout_seconds = _parse_duration(timeout) if timeout is not None else None
    outcomes: list[str] = []

    def run(c: WaypointClient) -> dict[str, Any]:
        result = asyncio.run(
            _wait_for_inbox(c, item_id, until_set, since, timeout_seconds)
        )
        outcomes.append(result.outcome)
        return {"outcome": result.outcome, "item": result.item}

    _emit(_settings_from_ctx(ctx), run)
    code = inbox_wait_exit_code(outcomes[0]) if outcomes else 1
    if code:
        raise typer.Exit(code=code)


@schedule_app.command("list")
def schedule_list(ctx: typer.Context) -> None:
    """List all scheduled sessions."""
    _emit(_settings_from_ctx(ctx), lambda c: {"schedules": c.list_schedules()})


@schedule_app.command("create")
def schedule_create(
    ctx: typer.Context,
    backend: Annotated[
        str | None,
        typer.Option(callback=_validate_backend, autocompletion=_complete_backend),
    ] = None,
    cwd: Annotated[
        str | None, typer.Option(help="Working directory for the session.")
    ] = None,
    preset: Annotated[
        str | None,
        typer.Option(
            "--preset",
            help="Apply a session preset (by id or name) before scheduling. "
            "Explicit flags override preset values.",
        ),
    ] = None,
    no_preset: Annotated[
        bool,
        typer.Option(
            "--no-preset",
            help="Do not apply the default preset when --preset is unset.",
        ),
    ] = False,
    launch_target_id: Annotated[str | None, typer.Option()] = None,
    launch_mode: Annotated[str | None, typer.Option()] = None,
    transport: Annotated[
        str | None,
        typer.Option(
            help="Pin the transport (interface) the scheduled agent is driven "
            "over: 'claude_cli' (Chat), 'claude_tty' (Emulated), or 'tmux' "
            "(Terminal). Must be one of the agent's supported transports; takes "
            "precedence over --launch-mode. Omit to use the agent's default "
            "transport (claude_code defaults to Emulated).",
        ),
    ] = None,
    title: Annotated[str | None, typer.Option()] = None,
    model: Annotated[str | None, typer.Option()] = None,
    effort: Annotated[str | None, typer.Option()] = None,
    permission_mode: Annotated[str | None, typer.Option()] = None,
    prompt: Annotated[
        str | None,
        typer.Option("--prompt", help="Initial prompt sent to the session on launch."),
    ] = None,
    launch_env: Annotated[
        list[str] | None,
        typer.Option(
            "--launch-env",
            help=(
                "Environment variable for the agent process as KEY=VALUE. "
                "Repeatable; values may contain '='."
            ),
        ),
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
    use_default = preset is None and not no_preset
    launch_env_map = _parse_launch_env(launch_env) if launch_env is not None else None

    def _run(c: WaypointClient) -> dict[str, Any]:
        # Preserve the launch warnings on the values the preset would resolve to.
        spec = _preset_spec_for_warnings(c, preset, use_default)
        eff_backend = backend or spec.get("backend")
        eff_model = model or spec.get("model")
        eff_permission = permission_mode or spec.get("permission_mode")
        eff_target = launch_target_id or spec.get("launch_target_id")
        if eff_backend:
            if eff_permission is not None:
                _validate_launch_permission_mode(c, eff_backend, eff_permission)
            if eff_model is not None:
                _warn_unknown_model(c, eff_backend, eff_model, eff_target)
        return {
            "schedule": c.create_schedule(
                backend=backend,
                cwd=cwd,
                launch_target_id=launch_target_id,
                launch_mode=launch_mode,
                transport=transport,
                title=title,
                model=model,
                effort=effort,
                permission_mode=permission_mode,
                initial_prompt=prompt,
                args=list(args or []),
                delay_seconds=delay_seconds,
                scheduled_at=scheduled_at,
                launch_env=launch_env_map,
                preset_id=preset,
                use_default_preset=use_default,
            )
        }

    _emit(_settings_from_ctx(ctx), _run)


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


def _preset_spec_payload(
    *,
    backend: str | None,
    launch_target_id: str | None,
    launch_mode: str | None,
    transport: str | None,
    model: str | None,
    effort: str | None,
    permission_mode: str | None,
    args: list[str] | None,
    config_override: list[str] | None,
    launch_env: list[str] | None,
    tag: list[str] | None,
) -> dict[str, Any]:
    """Build a preset spec body from launch options, including only the fields the
    caller supplied so update PATCH-merges instead of clobbering omitted fields.

    Presets deliberately omit cwd/title (per-launch specifics), so those are not
    accepted here — they stay on ``sessions start`` / ``schedule create``."""
    spec: dict[str, Any] = {}
    for key, value in (
        ("backend", backend),
        ("launch_target_id", launch_target_id),
        ("launch_mode", launch_mode),
        ("transport", transport),
        ("model", model),
        ("effort", effort),
        ("permission_mode", permission_mode),
    ):
        if value is not None:
            spec[key] = value
    if args is not None:
        spec["args"] = list(args)
    if config_override is not None:
        spec["config_overrides"] = list(config_override)
    if launch_env is not None:
        spec["launch_env"] = _parse_launch_env(launch_env)
    if tag is not None:
        spec["tags"] = _parse_tags(tag)
    return spec


_PresetBackendOption = Annotated[
    str | None, typer.Option(help="Backend id for the preset.")
]
_PresetLaunchEnvOption = Annotated[
    list[str] | None,
    typer.Option(
        "--launch-env",
        help="Environment variable as KEY=VALUE. Repeatable; values may contain '='.",
    ),
]
_PresetConfigOverrideOption = Annotated[
    list[str] | None,
    typer.Option("--config-override", help="Backend config override. Repeatable."),
]
_PresetTagOption = Annotated[
    list[str] | None,
    typer.Option("--tag", help="Tag as key=value (or a bare key). Repeatable."),
]


@presets_app.command("list")
def presets_list(ctx: typer.Context) -> None:
    """List session presets (env values redacted)."""
    _emit(_settings_from_ctx(ctx), lambda c: c.list_session_presets())


@presets_app.command("show")
def presets_show(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="Preset id or name.")],
    show_secrets: Annotated[
        bool,
        typer.Option("--show-secrets", help="Include launch_env values in output."),
    ] = False,
) -> None:
    """Show a single preset. Env values are redacted unless --show-secrets."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {
            "preset": c.get_session_preset(ref, include_secret_values=show_secrets)
        },
    )


@presets_app.command("create")
def presets_create(
    ctx: typer.Context,
    name: Annotated[str, typer.Option(help="Unique preset name.")],
    description: Annotated[str | None, typer.Option()] = None,
    default: Annotated[
        bool, typer.Option("--default", help="Mark this preset as the default.")
    ] = False,
    backend: _PresetBackendOption = None,
    launch_target_id: Annotated[str | None, typer.Option()] = None,
    launch_mode: Annotated[str | None, typer.Option()] = None,
    transport: Annotated[str | None, typer.Option()] = None,
    model: Annotated[str | None, typer.Option()] = None,
    effort: Annotated[str | None, typer.Option()] = None,
    permission_mode: Annotated[str | None, typer.Option()] = None,
    launch_env: _PresetLaunchEnvOption = None,
    config_override: _PresetConfigOverrideOption = None,
    tag: _PresetTagOption = None,
    args: Annotated[list[str] | None, typer.Argument()] = None,
) -> None:
    """Create a session preset from launch options (cwd/title are per-launch)."""
    spec = _preset_spec_payload(
        backend=backend,
        launch_target_id=launch_target_id,
        launch_mode=launch_mode,
        transport=transport,
        model=model,
        effort=effort,
        permission_mode=permission_mode,
        args=args,
        config_override=config_override,
        launch_env=launch_env,
        tag=tag,
    )
    body: dict[str, Any] = {"name": name, "spec": spec, "is_default": default}
    if description is not None:
        body["description"] = description
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {"preset": c.create_session_preset(body)},
    )


@presets_app.command("update")
def presets_update(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="Preset id or name.")],
    name: Annotated[str | None, typer.Option()] = None,
    description: Annotated[str | None, typer.Option()] = None,
    backend: _PresetBackendOption = None,
    launch_target_id: Annotated[str | None, typer.Option()] = None,
    launch_mode: Annotated[str | None, typer.Option()] = None,
    transport: Annotated[str | None, typer.Option()] = None,
    model: Annotated[str | None, typer.Option()] = None,
    effort: Annotated[str | None, typer.Option()] = None,
    permission_mode: Annotated[str | None, typer.Option()] = None,
    launch_env: _PresetLaunchEnvOption = None,
    config_override: _PresetConfigOverrideOption = None,
    tag: _PresetTagOption = None,
    args: Annotated[list[str] | None, typer.Argument()] = None,
) -> None:
    """Update a preset. Only the fields you pass change; the rest are preserved."""
    spec = _preset_spec_payload(
        backend=backend,
        launch_target_id=launch_target_id,
        launch_mode=launch_mode,
        transport=transport,
        model=model,
        effort=effort,
        permission_mode=permission_mode,
        args=args,
        config_override=config_override,
        launch_env=launch_env,
        tag=tag,
    )
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if description is not None:
        body["description"] = description
    if spec:
        body["spec"] = spec
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {"preset": c.update_session_preset(ref, body)},
    )


@presets_app.command("delete")
def presets_delete(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="Preset id or name.")],
) -> None:
    """Delete a preset. Sessions/schedules created from it are unaffected."""
    _emit(_settings_from_ctx(ctx), lambda c: c.delete_session_preset(ref))


@presets_app.command("default")
def presets_default(
    ctx: typer.Context,
    ref: Annotated[
        str | None,
        typer.Argument(help="Preset id or name. Omit to show the current default."),
    ] = None,
) -> None:
    """Set the default preset, or show the current default when no id is given."""
    if ref is None:
        _emit(
            _settings_from_ctx(ctx),
            lambda c: {
                "default_preset_id": c.list_session_presets().get("default_preset_id")
            },
        )
    else:
        _emit(
            _settings_from_ctx(ctx),
            lambda c: {"preset": c.set_default_session_preset(ref)},
        )


@presets_app.command("clear-default")
def presets_clear_default(ctx: typer.Context) -> None:
    """Clear the default preset (leaves all presets in place)."""
    _emit(_settings_from_ctx(ctx), lambda c: c.clear_default_session_preset())


@schedule_message_app.command("list")
def schedule_message_list(
    ctx: typer.Context,
    session_id: Annotated[
        str | None,
        typer.Option(help="Filter by target session id."),
    ] = None,
) -> None:
    """List all scheduled messages."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {
            "message_schedules": c.list_message_schedules(session_id=session_id)
        },
    )


@schedule_message_app.command("create")
def schedule_message_create(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument(help="Target session id.")],
    text: Annotated[str, typer.Argument(help="Message text to send.")],
    delay_seconds: Annotated[
        int | None,
        typer.Option(help="Send this many seconds from now."),
    ] = None,
    scheduled_at: Annotated[
        str | None,
        typer.Option(help="ISO 8601 datetime at which to send the message."),
    ] = None,
    no_submit: Annotated[
        bool,
        typer.Option("--no-submit", help="Do not auto-submit the message."),
    ] = False,
) -> None:
    """Schedule a message to be sent to a session."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {
            "message_schedule": c.create_message_schedule(
                session_id,
                text,
                submit=not no_submit,
                delay_seconds=delay_seconds,
                scheduled_at=scheduled_at,
            )
        },
    )


@schedule_message_app.command("delete")
def schedule_message_delete(
    ctx: typer.Context,
    schedule_id: Annotated[str, typer.Argument(help="Message schedule id to cancel.")],
) -> None:
    """Cancel and remove a scheduled message."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: {"message_schedule": c.delete_message_schedule(schedule_id)},
    )


@schedule_message_app.command("clear-history")
def schedule_message_clear_history(
    ctx: typer.Context,
    session_id: Annotated[
        str | None,
        typer.Option(help="Only clear history for this target session."),
    ] = None,
) -> None:
    """Remove completed/cancelled/failed message schedule records."""
    _emit(
        _settings_from_ctx(ctx),
        lambda c: c.clear_message_schedule_history(session_id=session_id),
    )


@maintenance_app.command("stats")
def maintenance_stats(ctx: typer.Context) -> None:
    """Print DB table sizes and FS footprint."""
    settings = _settings_from_ctx(ctx)
    storage = Storage(settings.database_path)
    try:
        stats = storage.db_stats()
        orphans = storage.scan_orphan_session_dirs(settings.sessions_dir)
        stats["orphan_session_dirs"] = len(orphans)
        typer.echo(json.dumps(stats, indent=2))
    finally:
        storage.close()


@maintenance_app.command("prune-orphans")
def maintenance_prune_orphans(
    ctx: typer.Context,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Confirm deletion. Without this flag the command is a dry run.",
        ),
    ] = False,
) -> None:
    """Delete orphaned session directories."""
    settings = _settings_from_ctx(ctx)
    storage = Storage(settings.database_path)
    try:
        orphans = storage.scan_orphan_session_dirs(settings.sessions_dir)
        if not orphans:
            typer.echo("No orphaned session directories found.")
            return

        if not yes:
            typer.echo(f"Found {len(orphans)} orphaned session directories (dry run):")
            for o in orphans:
                typer.echo(f"  - {o}")
            typer.echo("Run with --yes to remove them.")
            return

        for o in orphans:
            shutil.rmtree(o, ignore_errors=True)
            typer.echo(f"removed {o}")
    finally:
        storage.close()


@maintenance_app.command("trim-events")
def maintenance_trim_events(
    ctx: typer.Context,
    transport: Annotated[
        list[str] | None,
        typer.Option("--transport", help="Filter by transport. Repeatable."),
    ] = None,
    status: Annotated[
        list[str] | None,
        typer.Option("--status", help="Filter by session status. Repeatable."),
    ] = None,
    older_than: Annotated[
        int | None,
        typer.Option("--older-than", help="Filter by sessions older than X days."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Confirm deletion. Without this flag the command is a dry run.",
        ),
    ] = False,
) -> None:
    """Delete content events for the given transport(s)."""
    if not transport:
        raise typer.BadParameter(
            "trim-events requires --transport (e.g. --transport tmux). "
            "Refusing to delete content events across all transports: structured "
            "sessions render their agent_output in the transcript, so an unscoped "
            "delete would destroy real history."
        )
    settings = _settings_from_ctx(ctx)
    cutoff = (
        datetime.now(UTC) - timedelta(days=older_than)
        if older_than is not None
        else None
    )

    storage = Storage(settings.database_path)
    try:
        count = storage.delete_events_for(
            transports=transport,
            statuses=status,
            older_than=cutoff,
            dry_run=not yes,
        )
        if not yes:
            typer.echo(
                f"Would delete {count} events (dry run). Run with --yes to confirm."
            )
        else:
            typer.echo(f"Deleted {count} events.")
    finally:
        storage.close()


@maintenance_app.command("vacuum")
def maintenance_vacuum(ctx: typer.Context) -> None:
    """Run SQLite VACUUM."""
    settings = _settings_from_ctx(ctx)
    storage = Storage(settings.database_path)
    try:
        storage.vacuum()
        typer.echo("Database vacuumed.")
    finally:
        storage.close()


@maintenance_app.command("clear-structured-logs")
def maintenance_clear_structured_logs(
    ctx: typer.Context,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Confirm deletion. Without this flag the command is a dry run.",
        ),
    ] = False,
) -> None:
    """Delete per-session events.jsonl audit logs (redundant with the DB)."""
    settings = _settings_from_ctx(ctx)
    storage = Storage(settings.database_path)
    try:
        # Skip RUNNING sessions: if structured logging is enabled the runtime
        # holds an open append handle, and unlinking it would silently orphan
        # the inode the runtime keeps writing to.
        running = {
            s.id for s in storage.list_sessions() if s.status == SessionStatus.RUNNING
        }
        logs = [
            p
            for p in storage.scan_structured_logs(settings.sessions_dir)
            if p.parent.name not in running
        ]
        if not logs:
            typer.echo("No structured logs to clear (RUNNING sessions skipped).")
            return
        total = sum(p.stat().st_size for p in logs if p.exists())
        if not yes:
            typer.echo(
                f"Found {len(logs)} events.jsonl files "
                f"({total / 1e6:.1f} MB) (dry run). Run with --yes to delete."
            )
            return
        removed = 0
        for log_path in logs:
            try:
                log_path.unlink()
                removed += 1
            except OSError as exc:
                typer.echo(f"skipped {log_path}: {exc}")
        typer.echo(f"Deleted {removed} events.jsonl files ({total / 1e6:.1f} MB).")
    finally:
        storage.close()


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


def _walk_commands(group: click.Group, prefix: str) -> list[dict[str, Any]]:
    """Recursively flatten a Click command tree into serializable descriptors.

    Sub-groups are descended into; non-group commands are leaves. Hidden
    commands/params and the auto-added ``--help`` option are skipped. Output is
    sorted by command path for stable text/JSON dumps.
    """
    out: list[dict[str, Any]] = []
    for name, cmd in group.commands.items():
        if cmd.hidden:
            continue
        path = f"{prefix} {name}"
        # Typer vendors its own Click, so a sub-app is not an ``isinstance`` of
        # the top-level ``click.Group``; detect groups by the ``commands`` map.
        if isinstance(getattr(cmd, "commands", None), dict):
            # A group that runs without a subcommand (e.g. ``backends``) is a
            # usable command in its own right, so describe it alongside its
            # children rather than only recursing.
            if getattr(cmd, "invoke_without_command", False):
                out.append(_describe_command(cmd, path))
            out.extend(_walk_commands(cast(click.Group, cmd), path))
            continue
        out.append(_describe_command(cmd, path))
    out.sort(key=lambda entry: entry["command"])
    return out


def _json_safe(value: Any) -> Any:
    """Coerce an option default to a JSON-serializable form.

    Defaults are usually primitives, but some (enums, paths, frozensets) are
    not; fall back to ``str`` so the dump never fails to serialize.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _describe_command(cmd: click.Command, path: str) -> dict[str, Any]:
    arguments: list[dict[str, Any]] = []
    options: list[dict[str, Any]] = []
    with click.Context(cmd, info_name=path) as ctx:
        for param in cmd.get_params(ctx):
            if getattr(param, "hidden", False) or param.name == "help":
                continue
            if param.param_type_name == "option":
                options.append(
                    {
                        "flags": list(param.opts),
                        "type": param.type.name,
                        "required": param.required,
                        "default": _json_safe(param.default),
                        "help": getattr(param, "help", None),
                    }
                )
            else:
                arguments.append(
                    {"name": param.name, "required": param.required, "help": None}
                )
    # Strip the leading "waypoint " (or other root) from the reported command.
    command = path.split(" ", 1)[1] if " " in path else path
    return {
        "command": command,
        "help": cmd.get_short_help_str() or None,
        "arguments": arguments,
        "options": options,
    }


def _render_help_text(commands: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for entry in commands:
        lines.append(entry["command"])
        if entry["help"]:
            lines.append(f"  {entry['help']}")
        if entry["arguments"]:
            lines.append("  ARGUMENTS:")
            for arg in entry["arguments"]:
                req = "required" if arg["required"] else "optional"
                lines.append(f"    {arg['name']} ({req})")
        if entry["options"]:
            lines.append("  OPTIONS:")
            for opt in entry["options"]:
                flags = ", ".join(opt["flags"])
                bits = [opt["type"]]
                bits.append("required" if opt["required"] else "optional")
                if opt["default"] is not None:
                    bits.append(f"default={opt['default']}")
                detail = ", ".join(bits)
                line = f"    {flags} [{detail}]"
                if opt["help"]:
                    line += f" — {opt['help']}"
                lines.append(line)
        lines.append("")
    return "\n".join(lines).rstrip()


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
