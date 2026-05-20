import os
import signal
import subprocess
from pathlib import Path

import typer

from waypointctl.ancestry import is_descendant_of
from waypointctl.client import (
    DaemonClient,
    DaemonUnavailableError,
    daemon_available,
    ensure_daemon,
)
from waypointctl.config import apply_dotenv, load_stack_config
from waypointctl.paths import (
    pid_file_for,
    resolve_state_dir,
    resolve_waypoint_home,
    waypoint_pid_path,
    waypoint_socket_path,
)
from waypointctl.process import is_pid_running, read_pid_file, running_pid
from waypointctl.protocol import DaemonResult
from waypointctl.stack import WaypointStack
from waypointctl.tailscale import preflight_tailscale_command, run_tailscale_helper

app = typer.Typer(
    add_completion=False, no_args_is_help=True, help="Waypoint control plane"
)

daemon_app = typer.Typer(
    add_completion=False, no_args_is_help=True, help="Manage the local waypointd daemon"
)
app.add_typer(daemon_app, name="daemon")

tailscale_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Manage Docker-backed tailnet sidecars",
)
app.add_typer(tailscale_app, name="tailscale")


def _ctx_home(ctx: typer.Context) -> Path:
    obj = ctx.obj or {}
    home = obj["home"]
    assert isinstance(home, Path)
    return home


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
    home_path = resolve_waypoint_home(home)
    apply_dotenv(home_path)
    ctx.obj = {"home": home_path}


@app.command()
def start(ctx: typer.Context, service: str = typer.Argument("all")) -> None:
    _run_control_command(ctx, "start", [service])


@app.command()
def stop(
    ctx: typer.Context,
    service: str = typer.Argument("all"),
    wait: bool = typer.Option(
        False,
        "-w",
        "--wait",
        help="Block until the daemon finishes stopping (streams logs).",
    ),
) -> None:
    _check_agent_restart_safety(ctx, [service], wait=wait)
    _run_control_command(ctx, "stop", [service], wait=wait)


@app.command()
def restart(
    ctx: typer.Context,
    service: str = typer.Argument("all"),
    wait: bool = typer.Option(
        False,
        "-w",
        "--wait",
        help="Block until the daemon finishes restarting (streams logs).",
    ),
) -> None:
    _check_agent_restart_safety(ctx, [service], wait=wait)
    _run_control_command(ctx, "restart", [service], wait=wait)


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


@app.command()
def doctor(ctx: typer.Context) -> None:
    home = _ctx_home(ctx)
    typer.echo(f"WAYPOINT_HOME={home}")
    typer.echo(f"WAYPOINTCTL_STATE_DIR={resolve_state_dir()}")
    typer.echo(f"daemon socket={waypoint_socket_path()}")
    typer.echo(f"daemon for this home={'yes' if daemon_available(home) else 'no'}")


@tailscale_app.command("up")
def tailscale_up(
    ctx: typer.Context,
    profile: str = typer.Argument(..., help="Tailnet profile name."),
) -> None:
    _run_tailscale_command(ctx, "up", profile)


@tailscale_app.command("down")
def tailscale_down(
    ctx: typer.Context,
    profile: str = typer.Argument(..., help="Tailnet profile name."),
) -> None:
    _run_tailscale_command(ctx, "down", profile)


@tailscale_app.command("status")
def tailscale_status(
    ctx: typer.Context,
    profile: str = typer.Argument(..., help="Tailnet profile name."),
) -> None:
    _run_tailscale_command(ctx, "status", profile)


@tailscale_app.command("logs")
def tailscale_logs(
    ctx: typer.Context,
    profile: str = typer.Argument(..., help="Tailnet profile name."),
) -> None:
    _run_tailscale_command(ctx, "logs", profile)


@daemon_app.command("start")
def daemon_start(ctx: typer.Context) -> None:
    home = _ctx_home(ctx)
    if daemon_available(home):
        typer.echo("waypointd already running")
        return
    try:
        ensure_daemon(home)
    except DaemonUnavailableError as exc:
        typer.echo(f"failed to start waypointd: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("waypointd started")


@daemon_app.command("stop")
def daemon_stop(ctx: typer.Context) -> None:
    pid_path = waypoint_pid_path()
    pid = read_pid_file(pid_path)
    if pid is None or not is_pid_running(pid):
        typer.echo("waypointd not running")
        pid_path.unlink(missing_ok=True)
        waypoint_socket_path().unlink(missing_ok=True)
        _warn_if_services_running()
        return
    os.kill(pid, signal.SIGTERM)
    typer.echo(f"waypointd stopped (pid {pid})")
    _warn_if_services_running()


def _warn_if_services_running() -> None:
    survivors = [
        name for name in ("backend", "frontend") if running_pid(pid_file_for(name))
    ]
    if not survivors:
        return
    typer.echo(
        f"note: {', '.join(survivors)} still running; "
        "use `waypointctl stop` to shut them down.",
        err=True,
    )


@daemon_app.command("status")
def daemon_status(ctx: typer.Context) -> None:
    home = _ctx_home(ctx)
    pid = read_pid_file(waypoint_pid_path())
    if pid and is_pid_running(pid):
        responsive = daemon_available()
        for_this_home = daemon_available(home)
        typer.echo(
            f"waypointd: running pid={pid} "
            f"responsive={'yes' if responsive else 'no'} "
            f"for_this_home={'yes' if for_this_home else 'no'}"
        )
        return
    typer.echo("waypointd: stopped")


@daemon_app.command("serve", hidden=True)
def daemon_serve(ctx: typer.Context) -> None:
    from waypointctl.daemon import serve

    serve(_ctx_home(ctx))


def _run_control_command(
    ctx: typer.Context, command: str, args: list[str], wait: bool = False
) -> None:
    home = _ctx_home(ctx)
    if _should_use_daemon(home):
        client = _daemon_client(home)
        if client is not None:
            _run_via_daemon(client, command, args, wait=wait)
            return

    _run_in_process(home, command, args)


def _run_via_daemon(
    client: DaemonClient, command: str, args: list[str], wait: bool = False
) -> None:
    def log(stream: str, line: str) -> None:
        typer.echo(line, err=(stream == "stderr"))

    try:
        result = client.request(command, args, log=log, wait=wait)
    except DaemonUnavailableError as exc:
        typer.echo(f"waypointd unavailable: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    _exit_with_result(result)


def _exit_with_result(result: DaemonResult) -> None:
    if not result.ok and result.error:
        typer.echo(result.error, err=True)
    raise typer.Exit(code=result.returncode)


def _run_in_process(home: Path, command: str, args: list[str]) -> None:
    stack = WaypointStack(load_stack_config(home))

    def log(stream: str, line: str) -> None:
        typer.echo(line, err=(stream == "stderr"))

    if command == "start":
        target = args[0] if args else "all"
        result = stack.start(log, target)
    elif command == "stop":
        target = args[0] if args else "all"
        result = stack.stop(log, target)
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


def _run_tailscale_command(ctx: typer.Context, command: str, profile: str) -> None:
    home = _ctx_home(ctx)
    preflight_tailscale_command(command)
    run_tailscale_helper(home, command, profile)


def _should_use_daemon(home: Path) -> bool:
    if _env_flag("WAYPOINTCTL_DAEMON"):
        return True
    return daemon_available(home)


def _daemon_client(home: Path) -> DaemonClient | None:
    if daemon_available(home):
        return DaemonClient(home)
    if not _env_flag("WAYPOINTCTL_DAEMON"):
        return None
    try:
        return ensure_daemon(home)
    except DaemonUnavailableError as exc:
        typer.echo(f"failed to start waypointd: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def _check_agent_restart_safety(
    ctx: typer.Context, args: list[str], wait: bool = False
) -> None:
    home = _ctx_home(ctx)
    # In deferred daemon mode the CLI returns before the kill, so being
    # inside the target's tree is fine. --wait puts us back on the kill
    # path; the check is unconditional in that case.
    if _should_use_daemon(home) and not wait:
        return
    config = load_stack_config(home)
    stack = WaypointStack(config)

    target = (args[0] if args else "all").lower()
    self_pid = os.getpid()
    services = []
    if target in {"backend", "all"}:
        services.append(("backend", stack.backend.pid_path))
    if target in {"frontend", "all"}:
        services.append(("frontend", stack.frontend.pid_path))

    for name, pid_path in services:
        pid = running_pid(pid_path)
        if pid is None:
            continue
        if is_descendant_of(self_pid, pid):
            typer.echo(
                f"refusing to restart {name} from inside its own process tree "
                f"(pid {self_pid} descends from {name} pid {pid}); "
                "run `waypointctl daemon start` or set WAYPOINTCTL_DAEMON=1",
                err=True,
            )
            raise typer.Exit(code=1)


def main() -> None:
    app()
