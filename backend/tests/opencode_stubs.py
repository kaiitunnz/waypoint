"""Stand-ins for starting an OpenCode server: a binary, an ssh and its API."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from aiohttp import web

from waypoint.launch_targets import SshLaunchTargetConfig

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@asynccontextmanager
async def serving(routes: dict[str, Handler]) -> AsyncIterator[str]:
    """Serve ``routes``, each for GET and POST, and yield the base URL."""
    app = web.Application()
    for path, handler in routes.items():
        app.router.add_get(path, handler)
        app.router.add_post(path, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{runner.addresses[0][1]}"
    finally:
        await runner.cleanup()


def script(path: Path, body: str) -> str:
    path.write_text(f"#!/usr/bin/env bash\n{body}")
    path.chmod(0o755)
    return str(path)


def fake_opencode(directory: Path, port: int, then: str = "") -> tuple[str, Path]:
    """A binary that records the server variables it got, announces ``port``,
    runs the shell lines in ``then`` and stays up."""
    seen = directory / "server.env"
    binary = script(
        directory / "opencode",
        f"env | grep '^OPENCODE_' | sort > '{seen}'\n"
        f'echo "server listening on http://127.0.0.1:{port}"\n'
        f"{then}"
        "exec sleep 30\n",
    )
    return binary, seen


def server_env(seen: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in seen.read_text().splitlines())


def loopback_ssh(directory: Path, opencode_bin: str) -> SshLaunchTargetConfig:
    """A target whose ssh runs the remote command here, logging its argv."""
    ssh = script(
        directory / "ssh",
        f"printf '%s\\n' \"$@\" >> '{directory / 'ssh.argv'}'\n"
        'exec bash -c "${@: -1}"\n',
    )
    return SshLaunchTargetConfig(
        id="ssh-1",
        name="Remote",
        ssh_destination="user@example.com",
        ssh_bin=ssh,
        remote_shell="",
        plugin_configs={"opencode": {"remote_bin": opencode_bin}},
    )
