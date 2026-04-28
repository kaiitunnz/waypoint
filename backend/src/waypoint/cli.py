import argparse
import asyncio
import json
import shutil
from typing import Any

import uvicorn

from waypoint.api import AppContext, create_app
from waypoint.schemas import Backend, SessionAttachRequest, SessionCreateRequest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="waypoint")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    doctor = subparsers.add_parser("doctor")

    session = subparsers.add_parser("session")
    session_subparsers = session.add_subparsers(dest="session_command", required=True)

    start = session_subparsers.add_parser("start")
    start.add_argument("--backend", choices=[backend.value for backend in Backend], required=True)
    start.add_argument("--cwd", required=True)
    start.add_argument("--title")
    start.add_argument("args", nargs="*")

    attach = session_subparsers.add_parser("attach")
    attach.add_argument("--tmux", required=True)
    attach.add_argument("--backend-hint", choices=[backend.value for backend in Backend])
    attach.add_argument("--title")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "serve":
        app = create_app()
        host = args.host or app.state.context.settings.host
        port = args.port or app.state.context.settings.port
        uvicorn.run(app, host=host, port=port)
        return
    if args.command == "doctor":
        run_doctor()
        return
    if args.command == "session":
        asyncio.run(run_session_command(args))
        return
    parser.error("unknown command")


def run_doctor() -> None:
    checks = {
        "tmux": shutil.which("tmux"),
        "codex": shutil.which("codex"),
        "claude": shutil.which("claude"),
        "tailscale": shutil.which("tailscale"),
    }
    print(json.dumps(checks, indent=2))


async def run_session_command(args: argparse.Namespace) -> None:
    context = AppContext()
    context.settings.ensure_dirs()
    try:
        if args.session_command == "start":
            session = await context.runtime.create_session(
                SessionCreateRequest(
                    backend=Backend(args.backend),
                    cwd=args.cwd,
                    title=args.title,
                    args=list(args.args),
                )
            )
            print(json.dumps({"session": session.model_dump(mode="json")}, indent=2))
        elif args.session_command == "attach":
            payload: dict[str, Any] = {"tmux_target": args.tmux, "title": args.title}
            if args.backend_hint:
                payload["backend_hint"] = Backend(args.backend_hint)
            session = await context.runtime.attach_tmux(SessionAttachRequest.model_validate(payload))
            print(json.dumps({"session": session.model_dump(mode="json")}, indent=2))
    finally:
        await context.runtime.stop()
