import asyncio
import json
import logging
import urllib.parse
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any, Protocol

import aiohttp

from waypoint.launch_targets import SshLaunchTargetConfig

log = logging.getLogger("waypoint.opencode.client")

# Large `tool.output` SSE frames (file diffs, multi-file searches) routinely
# exceed aiohttp's 64 KiB defaults. 256 KiB matches what OpenCode itself
# tolerates upstream.
_SSE_BUFFER_BYTES = 256 * 1024
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
# SSE is intentionally long-lived; only bound the initial connect.
_STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)


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
                max_line_size=_SSE_BUFFER_BYTES,
                max_field_size=_SSE_BUFFER_BYTES,
                read_bufsize=_SSE_BUFFER_BYTES,
                timeout=_REQUEST_TIMEOUT,
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
        async with client.get(
            f"{self.base_url}{path}", timeout=_STREAM_TIMEOUT
        ) as resp:
            resp.raise_for_status()
            buffer = b""
            async for chunk in resp.content.iter_any():
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    yield (line + b"\n").decode("utf-8", errors="replace")
            if buffer:
                yield buffer.decode("utf-8", errors="replace")

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
        self._sse_stderr_task: asyncio.Task[None] | None = None

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

        args = self.target.build_remote_exec_args(curl_args, self.cwd)

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if input_data else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate(input=input_data)

        if proc.returncode != 0:
            err_lines = [
                line
                for line in stderr.decode(errors="replace").strip().splitlines()
                if "cannot set terminal process group" not in line
                and "no job control in this shell" not in line
            ]
            err_text = "\n".join(err_lines).strip()
            raise RuntimeError(f"curl failed ({proc.returncode}): {err_text}")

        out_text = stdout.decode("utf-8").strip()

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

        # `limit=` raises the StreamReader's per-read cap so a single
        # tool.output frame larger than 64 KiB doesn't bring the SSE
        # connection down with `LimitOverrunError`.
        self._sse_process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_SSE_BUFFER_BYTES,
        )

        assert self._sse_process.stdout is not None
        assert self._sse_process.stderr is not None
        self._sse_stderr_task = asyncio.create_task(
            _drain_stream(self._sse_process.stderr, "opencode sse stderr")
        )

        try:
            buffer = b""
            while True:
                chunk = await self._sse_process.stdout.read(_SSE_BUFFER_BYTES)
                if not chunk:
                    if buffer:
                        yield buffer.decode("utf-8", errors="replace")
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    yield (line + b"\n").decode("utf-8", errors="replace")
        finally:
            if self._sse_stderr_task is not None:
                self._sse_stderr_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._sse_stderr_task
                self._sse_stderr_task = None

        returncode = await self._sse_process.wait()
        # Negative returncodes mean the child was killed by a signal; that's
        # the expected exit path when `close()` terminates the process.
        if returncode > 0:
            raise RuntimeError(f"sse stream ended with code {returncode}")

    async def close(self) -> None:
        if self._sse_stderr_task is not None:
            self._sse_stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._sse_stderr_task
            self._sse_stderr_task = None
        if self._sse_process is not None:
            try:
                self._sse_process.terminate()
                with suppress(ProcessLookupError):
                    await asyncio.wait_for(self._sse_process.wait(), timeout=2.0)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    self._sse_process.kill()
            self._sse_process = None


async def _drain_stream(reader: asyncio.StreamReader, label: str) -> None:
    try:
        async for line in reader:
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                log.debug("%s: %s", label, text)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("error draining %s", label)
