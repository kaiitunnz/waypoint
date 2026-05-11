from pathlib import Path

import typer

from waypointctl.client import DaemonUnavailableError, ensure_daemon
from waypointctl.legacy import stream_legacy_command
from waypointctl.paths import resolve_waypoint_home
from waypointctl.protocol import DaemonResponse

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
    _run_legacy(ctx, "logs", [service])


@app.command(hidden=True)
def daemon(ctx: typer.Context) -> None:
    from waypointctl.daemon import serve

    serve(_ctx_home(ctx))


@app.command()
def doctor(ctx: typer.Context) -> None:
    home = _ctx_home(ctx)
    typer.echo(f"WAYPOINT_HOME={home}")
    typer.echo(f"legacy script={home / 'scripts' / 'waypoint.sh'}")


def _run_control_command(ctx: typer.Context, command: str, args: list[str]) -> None:
    home = _ctx_home(ctx)
    try:
        response = ensure_daemon(home).request(command, args)
    except DaemonUnavailableError:
        _run_legacy(ctx, command, args)
        return

    _emit_response(response)
    raise typer.Exit(code=response.returncode)


def _run_legacy(ctx: typer.Context, command: str, args: list[str]) -> None:
    home = _ctx_home(ctx)
    try:
        returncode = stream_legacy_command(home, command, args)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=returncode)


def _emit_response(response: DaemonResponse) -> None:
    if response.stdout:
        typer.echo(response.stdout, nl=False)
    if response.stderr:
        typer.echo(response.stderr, err=True, nl=False)
    if response.error and not response.stderr:
        typer.echo(response.error, err=True)


def main() -> None:
    app()
