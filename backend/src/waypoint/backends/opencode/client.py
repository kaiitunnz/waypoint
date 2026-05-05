import asyncio
import json
import logging
import urllib.parse
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any, Protocol

import aiohttp

from waypoint.backends.opencode.connection_config import (
    http_control_timeout,
    with_ssh_keepalive,
)
from waypoint.launch_targets import SshLaunchTargetConfig

log = logging.getLogger("waypoint.opencode.client")

# Large `tool.output` SSE frames (file diffs, multi-file searches) routinely
# exceed aiohttp's 64 KiB defaults. 256 KiB matches what OpenCode itself
# tolerates upstream.
_SSE_BUFFER_BYTES = 256 * 1024
# SSE is intentionally long-lived; only bound the initial connect.
_STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
# Long-running HTTP calls (summarize, prompt) are exempt from any
# application-layer ceiling; the connect handshake still needs a bound so
# a frozen DNS lookup or syn-loss doesn't wedge forever.
_LONG_RUNNING_TIMEOUT = aiohttp.ClientTimeout(
    total=None, sock_connect=10, sock_read=None
)


def _control_timeout() -> aiohttp.ClientTimeout:
    seconds = http_control_timeout()
    if seconds <= 0:
        return aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
    return aiohttp.ClientTimeout(total=seconds)


class OpenCodeHttpClient(Protocol):
    async def get(self, path: str, params: dict[str, str] | None = None) -> Any: ...
    async def post(
        self,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        long_running: bool = False,
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
                timeout=_control_timeout(),
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
        long_running: bool = False,
    ) -> Any:
        client = self._require_session()
        timeout = _LONG_RUNNING_TIMEOUT if long_running else _control_timeout()
        async with client.post(
            f"{self.base_url}{path}",
            json=json_data,
            params=params,
            timeout=timeout,
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

    def _build_ssh_argv(self, command: list[str]) -> tuple[str, ...]:
        return with_ssh_keepalive(self.target.build_remote_exec_args(command, self.cwd))

    async def _run_curl(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None,
        params: dict[str, str] | None,
        long_running: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        # `-w "\n%{http_code}"` appends a newline + numeric status after the
        # response body so we can surface 4xx/5xx as failures (curl's own
        # exit code is 0 for any HTTP response when `-f` is not set, and
        # `-f` would swallow the response body we want to log).
        curl_args = ["curl", "-s", "-X", method, "-w", "\\n%{http_code}"]
        if json_data is not None:
            curl_args.extend(["-H", "Content-Type: application/json"])
            # Pass data via stdin to avoid command line length limits or escaping issues
            input_data = json.dumps(json_data).encode("utf-8")
            curl_args.extend(["-d", "@-"])
        else:
            input_data = None

        curl_args.append(url)

        args = self._build_ssh_argv(curl_args)

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if input_data else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Long-running calls (summarize, etc.) skip the app-layer ceiling
        # entirely; SSH ServerAlive surfaces a dead link as a clean exit.
        # Tiny control-plane calls cap at HTTP_CONTROL_TIMEOUT as a
        # belt-and-suspenders against truly stuck subprocesses.
        timeout: float | None
        if long_running:
            timeout = None
        else:
            seconds = http_control_timeout()
            timeout = float(seconds) if seconds > 0 else None

        try:
            if timeout is None:
                stdout, stderr = await proc.communicate(input=input_data)
            else:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=input_data), timeout=timeout
                )
        except TimeoutError:
            with suppress(ProcessLookupError):
                proc.kill()
            with suppress(Exception):
                await proc.wait()
            raise RuntimeError(
                f"curl exceeded control timeout ({timeout}s): {method} {url}"
            ) from None

        if proc.returncode != 0:
            err_lines = [
                line
                for line in stderr.decode(errors="replace").strip().splitlines()
                if "cannot set terminal process group" not in line
                and "no job control in this shell" not in line
            ]
            err_text = "\n".join(err_lines).strip()
            raise RuntimeError(f"curl failed ({proc.returncode}): {err_text}")

        out_text = stdout.decode("utf-8")
        body, status_code = _split_curl_status(out_text)

        if status_code >= 400:
            raise OpenCodeHttpError(status_code, body, method, url)

        body_text = body.strip()
        if not body_text:
            return {}

        try:
            return json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON response from curl: {body_text}") from exc

    async def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return await self._run_curl("GET", path, None, params)

    async def post(
        self,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        long_running: bool = False,
    ) -> Any:
        return await self._run_curl(
            "POST", path, json_data, params, long_running=long_running
        )

    async def patch(self, path: str, json_data: dict[str, Any] | None = None) -> Any:
        return await self._run_curl("PATCH", path, json_data, None)

    async def stream_events(self, path: str) -> AsyncGenerator[str, None]:
        url = f"{self.base_url}{path}"
        curl_args = ["curl", "-s", "-N", url]
        args = self._build_ssh_argv(curl_args)

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


class OpenCodeHttpError(RuntimeError):
    """Raised when a remote OpenCode HTTP call returns a non-2xx response."""

    def __init__(self, status: int, body: str, method: str, url: str) -> None:
        snippet = body.strip()[:500]
        super().__init__(f"{method} {url} returned HTTP {status}: {snippet}")
        self.status = status
        self.body = body


def _split_curl_status(out_text: str) -> tuple[str, int]:
    # The trailing status line is appended by `-w "\n%{http_code}"`. Anything
    # before the final newline is the response body (which may itself contain
    # newlines).
    idx = out_text.rfind("\n")
    if idx < 0:
        status_text = out_text.strip()
        body = ""
    else:
        status_text = out_text[idx + 1 :].strip()
        body = out_text[:idx]
    try:
        status_code = int(status_text)
    except ValueError:
        status_code = 0
    return body, status_code


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
