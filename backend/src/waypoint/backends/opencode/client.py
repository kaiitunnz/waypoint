import asyncio
import json
import logging
import shlex
import urllib.parse
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any, Protocol

import aiohttp

from waypoint.launch_targets import SshLaunchTargetConfig

log = logging.getLogger("waypoint.opencode.client")

_CURL_SENTINEL = "__WP_JSON__"


class OpenCodeHttpClient(Protocol):
    async def get(self, path: str, params: dict[str, str] | None = None) -> Any: ...
    async def post(
        self,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any: ...
    async def patch(
        self, path: str, json_data: dict[str, Any] | None = None
    ) -> Any: ...
    def stream_events(self, path: str) -> AsyncGenerator[str, None]: ...
    async def close(self) -> None: ...


class LocalOpenCodeClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self._session: aiohttp.ClientSession | None = None

    def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                max_line_size=16 * 1024, max_field_size=16 * 1024
            )
        return self._session

    async def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        client = self._require_session()
        async with client.get(f"{self.base_url}{path}", params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def post(
        self,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        client = self._require_session()
        async with client.post(
            f"{self.base_url}{path}", json=json_data, params=params
        ) as resp:
            resp.raise_for_status()
            if resp.status == 204:
                return {}
            return await resp.json()

    async def patch(self, path: str, json_data: dict[str, Any] | None = None) -> Any:
        client = self._require_session()
        async with client.patch(f"{self.base_url}{path}", json=json_data) as resp:
            resp.raise_for_status()
            if resp.status == 204:
                return {}
            return await resp.json()

    async def stream_events(self, path: str) -> AsyncGenerator[str, None]:
        client = self._require_session()
        async with client.get(f"{self.base_url}{path}") as resp:
            resp.raise_for_status()
            async for line in resp.content:
                yield line.decode("utf-8")

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None


class RemoteOpenCodeClient:
    def __init__(
        self,
        target: SshLaunchTargetConfig,
        remote_port: int,
        cwd: str | None = None,
    ) -> None:
        self.target = target
        self.remote_port = remote_port
        self.cwd = cwd
        self.base_url = f"http://127.0.0.1:{remote_port}"
        # For SSE streaming, we need a long-lived subprocess
        self._sse_process: asyncio.subprocess.Process | None = None

    async def _run_curl(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None,
        params: dict[str, str] | None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        curl_args = ["curl", "-s", "-X", method]
        if json_data is not None:
            curl_args.extend(["-H", "Content-Type: application/json"])
            # Pass data via stdin to avoid command line length limits or escaping issues
            input_data = json.dumps(json_data).encode("utf-8")
            curl_args.extend(["-d", "@-"])
        else:
            input_data = None

        curl_args.append(url)

        # Echo the sentinel before curl so the Python consumer can discard
        # any rcfile noise that bash -ilc may have written ahead of the
        # actual response (same pattern as claude_thread_enumerator.sh).
        sentinel_cmd = f"echo {_CURL_SENTINEL} && {shlex.join(curl_args)}"
        args = self.target.build_remote_exec_args(
            ["bash", "-c", sentinel_cmd], self.cwd
        )

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if input_data else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate(input=input_data)

        if proc.returncode != 0:
            err_text = stderr.decode(errors="replace").strip()
            raise RuntimeError(f"curl failed ({proc.returncode}): {err_text}")

        stdout_str = stdout.decode("utf-8")
        marker = f"{_CURL_SENTINEL}\n"
        if marker in stdout_str:
            out_text = stdout_str[stdout_str.index(marker) + len(marker) :].strip()
        else:
            out_text = stdout_str.strip()

        if not out_text:
            return {}

        try:
            return json.loads(out_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON response from curl: {out_text}") from exc

    async def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return await self._run_curl("GET", path, None, params)

    async def post(
        self,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        return await self._run_curl("POST", path, json_data, params)

    async def patch(self, path: str, json_data: dict[str, Any] | None = None) -> Any:
        return await self._run_curl("PATCH", path, json_data, None)

    async def stream_events(self, path: str) -> AsyncGenerator[str, None]:
        url = f"{self.base_url}{path}"
        curl_args = ["curl", "-s", "-N", url]
        args = self.target.build_remote_exec_args(curl_args, self.cwd)

        self._sse_process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        assert self._sse_process.stdout is not None
        async for line in self._sse_process.stdout:
            yield line.decode("utf-8")

        returncode = await self._sse_process.wait()
        if returncode != 0:
            raise RuntimeError(f"sse stream ended with code {returncode}")

    async def close(self) -> None:
        if self._sse_process is not None:
            try:
                self._sse_process.terminate()
                with suppress(ProcessLookupError):
                    await asyncio.wait_for(self._sse_process.wait(), timeout=2.0)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    self._sse_process.kill()
            self._sse_process = None
