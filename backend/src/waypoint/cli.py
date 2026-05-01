import argparse
import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import uvicorn

from waypoint.api import AppContext, create_app
from waypoint.backends.registry import get_registry
from waypoint.schemas import SessionAttachRequest, SessionCreateRequest
from waypoint.settings import Settings, load_settings


def _backend_choices() -> list[str]:
    """Backend ids accepted by ``session start`` / ``session attach``.

    Excludes managed-launch fallback wrappers (capabilities flag
    ``is_fallback_for_managed_launch``) — those are routed to via the
    registry, not selected as a real backend.
    """
    return [
        plugin.id
        for plugin in get_registry().all()
        if not plugin.capabilities.is_fallback_for_managed_launch
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="waypoint")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--config", default=None)

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--config", default=None)

    reset = subparsers.add_parser(
        "reset",
        help="Wipe runtime data (sessions, events, tokens, schedules, logs). Config is untouched.",
    )
    reset.add_argument("--config", default=None)
    reset.add_argument(
        "--yes",
        action="store_true",
        help="Confirm destruction. Without this flag the command is a dry run.",
    )

    session = subparsers.add_parser("session")
    session.add_argument("--config", default=None)
    session_subparsers = session.add_subparsers(dest="session_command", required=True)

    backend_choices = _backend_choices()
    start = session_subparsers.add_parser("start")
    start.add_argument("--backend", choices=backend_choices, required=True)
    start.add_argument("--cwd", required=True)
    start.add_argument("--launch-target-id")
    start.add_argument("--title")
    start.add_argument("args", nargs="*")

    attach = session_subparsers.add_parser("attach")
    attach.add_argument("--tmux", required=True)
    attach.add_argument("--backend-hint", choices=backend_choices)
    attach.add_argument("--title")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "serve":
        app = create_app(_settings_from_arg(args.config))
        host = args.host or app.state.context.settings.host
        port = args.port or app.state.context.settings.port
        # Cap graceful-shutdown so a stuck websocket can never hold uvicorn
        # past Ctrl+C. The first SIGINT triggers shutdown; any in-flight ws
        # connection that doesn't close on cancel within this window gets
        # force-closed.
        uvicorn.run(app, host=host, port=port, timeout_graceful_shutdown=5)
        return
    if args.command == "doctor":
        run_doctor(_settings_from_arg(args.config))
        return
    if args.command == "reset":
        run_reset(_settings_from_arg(args.config), confirmed=args.yes)
        return
    if args.command == "session":
        asyncio.run(run_session_command(args))
        return
    parser.error("unknown command")


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
    # come from each registered plugin's ``capabilities.cli_binary`` so a
    # new backend shows up here automatically without editing this file.
    checks: dict[str, Any] = {
        "tmux": shutil.which("tmux"),
        "ssh": shutil.which("ssh"),
        "tailscale": shutil.which("tailscale"),
    }
    for plugin in get_registry().all():
        binary = plugin.capabilities.cli_binary
        if binary is None or binary in checks:
            continue
        checks[binary] = shutil.which(binary)
    checks["config_path"] = str(settings.config_path) if settings.config_path else None
    print(json.dumps(checks, indent=2))


async def run_session_command(args: argparse.Namespace) -> None:
    context = AppContext(_settings_from_arg(args.config))
    context.settings.ensure_dirs()
    try:
        if args.session_command == "start":
            session = await context.runtime.create_session(
                SessionCreateRequest(
                    backend=args.backend,
                    cwd=args.cwd,
                    launch_target_id=args.launch_target_id,
                    title=args.title,
                    args=list(args.args),
                )
            )
            print(json.dumps({"session": session.model_dump(mode="json")}, indent=2))
        elif args.session_command == "attach":
            payload: dict[str, Any] = {"tmux_target": args.tmux, "title": args.title}
            if args.backend_hint:
                payload["backend_hint"] = args.backend_hint
            session = await context.runtime.attach_tmux(
                SessionAttachRequest.model_validate(payload)
            )
            print(json.dumps({"session": session.model_dump(mode="json")}, indent=2))
    finally:
        await context.runtime.stop()


def _settings_from_arg(raw: str | None) -> Settings:
    return load_settings(Path(raw).expanduser() if raw else None)
