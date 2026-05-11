import os
import subprocess
from pathlib import Path

import typer

from waypointctl.client import DaemonUnavailableError, ensure_daemon
from waypointctl.config import load_stack_config
from waypointctl.paths import resolve_state_dir, resolve_waypoint_home
from waypointctl.protocol import DaemonResponse
from waypointctl.stack import WaypointStack

app = typer.Typer(
    add_completion=False, no_args_is_help=True, help="Waypoint control plane"
)


def _ctx_home(ctx: typer.Context) -> Path:
    obj = ctx.obj or {}
    return obj["home"]


@app.callback()
def bootstrap(
    ctx: typer.Context,
    home: Path | None = typer.Option(
        None,
        "--home",
        envvar="WAYPOINT_HOME",
        help="Waypoint repository root.",
    ),
) -> None:
    ctx.obj = {"home": resolve_waypoint_home(home)}


@app.command()
def start(ctx: typer.Context, service: str = typer.Argument("all")) -> None:
    _run_control_command(ctx, "start", [service])


@app.command()
def stop(ctx: typer.Context, service: str = typer.Argument("all")) -> None:
    _run_control_command(ctx, "stop", [service])


@app.command()
def restart(ctx: typer.Context, service: str = typer.Argument("all")) -> None:
    _run_control_command(ctx, "restart", [service])


@app.command()
def status(ctx: typer.Context) -> None:
    _run_control_command(ctx, "status", [])


@app.command()
def logs(ctx: typer.Context, service: str = typer.Argument("all")) -> None:
    home = _ctx_home(ctx)
    stack = WaypointStack(load_stack_config(home))
    try:
        argv = stack.logs_argv(service)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    completed = subprocess.run(argv, check=False)
    raise typer.Exit(code=completed.returncode)


@app.command(hidden=True)
def daemon(ctx: typer.Context) -> None:
    from waypointctl.daemon import serve

    serve(_ctx_home(ctx))


@app.command()
def doctor(ctx: typer.Context) -> None:
    home = _ctx_home(ctx)
    typer.echo(f"WAYPOINT_HOME={home}")
    typer.echo(f"WAYPOINTCTL_STATE_DIR={resolve_state_dir()}")


def _run_control_command(ctx: typer.Context, command: str, args: list[str]) -> None:
    home = _ctx_home(ctx)
    if _daemon_requested():
        try:
            response = ensure_daemon(home).request(command, args)
        except DaemonUnavailableError as exc:
            typer.echo(f"waypointd unavailable: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        _emit_response(response)
        raise typer.Exit(code=response.returncode)

    _run_in_process(home, command, args)


def _run_in_process(home: Path, command: str, args: list[str]) -> None:
    stack = WaypointStack(load_stack_config(home))

    def log(stream: str, line: str) -> None:
        typer.echo(line, err=(stream == "stderr"))

    if command == "start":
        result = stack.start(log)
    elif command == "stop":
        result = stack.stop(log)
    elif command == "restart":
        target = args[0] if args else "all"
        result = stack.restart(target, log)
    elif command == "status":
        result = stack.status(log)
    else:
        typer.echo(f"unknown command: {command}", err=True)
        raise typer.Exit(code=2)

    if not result.ok:
        if result.message:
            typer.echo(result.message, err=True)
        raise typer.Exit(code=1)


def _daemon_requested() -> bool:
    return os.environ.get("WAYPOINTCTL_DAEMON", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _emit_response(response: DaemonResponse) -> None:
    if response.stdout:
        typer.echo(response.stdout, nl=False)
    if response.stderr:
        typer.echo(response.stderr, err=True, nl=False)
    if response.error and not response.stderr:
        typer.echo(response.error, err=True)


def main() -> None:
    app()
