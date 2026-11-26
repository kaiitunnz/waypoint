"""Starting an OpenCode server: its password and which API it serves."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web

from opencode_stubs import fake_opencode, loopback_ssh, server_env, serving
from waypoint.backends.opencode.adapter import OpenCodeAdapter, OpenCodeError
from waypoint.backends.opencode.client import RemoteOpenCodeClient
from waypoint.backends.opencode.plugin import OpenCodePlugin, OpenCodePluginConfig
from waypoint.backends.opencode.server_auth import USERNAME, ServerAuth


def _adapter(**kwargs: Any) -> OpenCodeAdapter:
    async def emit(*_args: Any) -> None:
        return None

    return OpenCodeAdapter(emit_event=emit, **kwargs)


def _locked_v1(seen: Path) -> dict[str, Any]:
    """A 1.x /config that asks for the password the binary was started with."""

    async def config(request: web.Request) -> web.Response:
        expected = ServerAuth(server_env(seen)["OPENCODE_SERVER_PASSWORD"])
        if request.headers.get("Authorization") != f"Basic {expected.basic_token}":
            return web.json_response({}, status=401)
        return web.json_response({})

    return {"/config": config}


def _assert_secured(seen: Path) -> str:
    env = server_env(seen)
    assert env["OPENCODE_SERVER_USERNAME"] == USERNAME
    assert env["OPENCODE_SERVER_PASSWORD"] == env["OPENCODE_PASSWORD"]
    assert len(env["OPENCODE_PASSWORD"]) >= 32
    return env["OPENCODE_PASSWORD"]


@pytest.mark.asyncio
async def test_local_server_gets_a_password_and_the_client_sends_it(
    tmp_path: Path,
) -> None:
    seen = tmp_path / "server.env"
    passwords: set[str] = set()
    async with serving(_locked_v1(seen)) as url:
        binary, _ = fake_opencode(tmp_path, int(url.rsplit(":", 1)[1]))
        for _ in range(2):
            adapter = _adapter(
                binary=binary,
                workdir=str(tmp_path),
                launch_env={"OPENCODE_SERVER_PASSWORD": "from-the-session"},
            )
            await adapter.start()
            try:
                assert adapter.server_info is not None
                assert adapter.server_info.major == 1
                passwords.add(_assert_secured(seen))
            finally:
                await adapter.shutdown()
    assert len(passwords) == 2


@pytest.mark.asyncio
async def test_the_configured_local_binary_is_the_one_started(tmp_path: Path) -> None:
    seen = tmp_path / "server.env"
    async with serving(_locked_v1(seen)) as url:
        binary, _ = fake_opencode(tmp_path, int(url.rsplit(":", 1)[1]))
        config = OpenCodePluginConfig(local_bin=binary)
        runtime = SimpleNamespace(
            settings=SimpleNamespace(plugin_config=lambda _id: config),
            _emit_adapter_event=None,
        )
        adapter = await OpenCodePlugin()._get_or_create_adapter(
            cast(Any, runtime), None, str(tmp_path)
        )
        await adapter.start()
        try:
            assert seen.exists()
        finally:
            await adapter.shutdown()


@pytest.mark.asyncio
async def test_a_chatty_local_server_is_never_blocked_on_its_output(
    tmp_path: Path,
) -> None:
    seen = tmp_path / "server.env"
    written = tmp_path / "written"
    flood = (
        "head -c 3000000 /dev/zero\n"
        "head -c 3000000 /dev/zero >&2\n"
        f"touch '{written}'\n"
    )
    async with serving(_locked_v1(seen)) as url:
        binary, _ = fake_opencode(tmp_path, int(url.rsplit(":", 1)[1]), flood)
        adapter = _adapter(binary=binary, workdir=str(tmp_path))
        await adapter.start()
        try:
            async with asyncio.timeout(10):
                while not written.exists():
                    await asyncio.sleep(0.05)
        finally:
            await adapter.shutdown()


@pytest.mark.asyncio
async def test_remote_server_password_travels_on_stdin(tmp_path: Path) -> None:
    seen = tmp_path / "server.env"
    async with serving(_locked_v1(seen)) as url:
        binary, _ = fake_opencode(tmp_path, int(url.rsplit(":", 1)[1]))
        adapter = _adapter(launch_target=loopback_ssh(tmp_path, binary))
        await adapter.start()
        try:
            password = _assert_secured(seen)
            token = ServerAuth(password).basic_token
        finally:
            await adapter.shutdown()
    argv = (tmp_path / "ssh.argv").read_text()
    assert password not in argv
    assert token not in argv


@pytest.mark.asyncio
async def test_remote_requests_authenticate_and_keep_their_body(
    tmp_path: Path,
) -> None:
    auth = ServerAuth("s3cret-password")

    async def echo(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "auth": request.headers.get("Authorization"),
                "body": await request.text(),
            }
        )

    async def events(request: web.Request) -> web.Response:
        return web.Response(
            text=f"data: {request.headers.get('Authorization')}\n\n",
            content_type="text/event-stream",
        )

    expected = f"Basic {auth.basic_token}"
    body = {"text": 'line one\nline two "quoted" $HOME'}
    async with serving({"/echo": echo, "/event": events}) as url:
        client = RemoteOpenCodeClient(
            loopback_ssh(tmp_path, "opencode"), int(url.rsplit(":", 1)[1]), auth
        )
        try:
            got = await client.get("/echo")
            posted = await client.post("/echo", json_data=body)
            lines = [line async for line in client.stream_events("/event")]
        finally:
            await client.close()

    assert got == {"auth": expected, "body": ""}
    assert posted["auth"] == expected
    assert json.loads(posted["body"]) == body
    assert lines[0] == f"data: {expected}\n"
    argv = (tmp_path / "ssh.argv").read_text()
    assert auth.password not in argv
    assert auth.basic_token not in argv


async def _web_page(_request: web.Request) -> web.Response:
    return web.Response(text="<!doctype html>", content_type="text/html")


async def _unauthorized(_request: web.Request) -> web.Response:
    return web.json_response({"_tag": "UnauthorizedError"}, status=401)


def _info(payload: dict[str, Any]) -> Any:
    async def info(_request: web.Request) -> web.Response:
        return web.json_response(payload)

    return info


@pytest.mark.parametrize(
    ("routes", "detail"),
    [
        (
            {"/config": _web_page, "/api/info": _info({"version": "2.4.1"})},
            "OpenCode 2.4.1 is not supported",
        ),
        ({"/config": _web_page}, "200 text/html instead of JSON"),
        (
            {"/config": _web_page, "/api/info": _info({"name": "other"})},
            "200 text/html instead of JSON",
        ),
        ({"/config": _web_page, "/api/info": _web_page}, "200 text/html"),
        ({}, "200 text/html instead of JSON|HTTP 404"),
        ({"/config": _unauthorized}, "HTTP 401"),
    ],
)
@pytest.mark.asyncio
async def test_a_server_waypoint_cannot_drive_fails_at_once(
    tmp_path: Path, routes: dict[str, Any], detail: str
) -> None:
    async with serving(routes) as url:
        binary, _ = fake_opencode(tmp_path, int(url.rsplit(":", 1)[1]))
        adapter = _adapter(binary=binary, workdir=str(tmp_path))
        with pytest.raises(OpenCodeError, match=f"incompatible.*({detail})"):
            await asyncio.wait_for(adapter.start(), timeout=10)
