from typing import Any

import pytest

from waypoint.backends.opencode.client import (
    OpenCodeHttpError,
    RemoteOpenCodeClient,
    _split_curl_status,
)
from waypoint.launch_targets import SshLaunchTargetConfig


class _FakeStream:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    def __aiter__(self) -> "_FakeStream":
        return self

    async def __anext__(self) -> bytes:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def read(self, n: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class _FakeProcess:
    def __init__(self) -> None:
        self.stdout = _FakeStream([b'data: {"ok": true}\n', b"\n"])
        self.stderr = _FakeStream([])
        self.returncode: int | None = 0
        self.terminated = False

    async def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


def _make_fake_subprocess(
    monkeypatch: pytest.MonkeyPatch, process: _FakeProcess
) -> None:
    monkeypatch.setattr(
        "waypoint.backends.opencode.client.asyncio.create_subprocess_exec",
        lambda *args, **kwargs: _coro(process),
    )
    monkeypatch.setattr(
        "waypoint.launch_targets._resolve_local_binary",
        lambda binary: binary,
    )


async def _coro(value: Any) -> Any:
    return value


@pytest.mark.asyncio
async def test_stream_events_clean_exit_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_process = _FakeProcess()  # returncode=0
    _make_fake_subprocess(monkeypatch, fake_process)

    target = SshLaunchTargetConfig(
        id="ssh-1", name="Remote", ssh_destination="user@example.com"
    )
    client = RemoteOpenCodeClient(target, 4096)

    events: list[str] = []
    async for line in client.stream_events("/event"):
        events.append(line)

    assert events == ['data: {"ok": true}\n', "\n"]


def test_split_curl_status_extracts_trailing_status() -> None:
    body, status = _split_curl_status('{"ok": true}\n200')
    assert body == '{"ok": true}'
    assert status == 200

    body, status = _split_curl_status('{"error": "boom"}\nmore body\n500')
    assert body == '{"error": "boom"}\nmore body'
    assert status == 500

    body, status = _split_curl_status("\n204")
    assert body == ""
    assert status == 204


class _FakeProcessFor:
    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        return self._stdout, b""


@pytest.mark.asyncio
async def test_remote_client_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_process = _FakeProcessFor(b'{"error": "session not found"}\n404')
    monkeypatch.setattr(
        "waypoint.backends.opencode.client.asyncio.create_subprocess_exec",
        lambda *args, **kwargs: _coro(fake_process),
    )
    monkeypatch.setattr(
        "waypoint.launch_targets._resolve_local_binary",
        lambda binary: binary,
    )

    target = SshLaunchTargetConfig(
        id="ssh-1", name="Remote", ssh_destination="user@example.com"
    )
    client = RemoteOpenCodeClient(target, 4096)

    with pytest.raises(OpenCodeHttpError) as excinfo:
        await client.get("/session/missing")

    assert excinfo.value.status == 404
    assert "session not found" in str(excinfo.value)


@pytest.mark.asyncio
async def test_stream_events_raises_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_process = _FakeProcess()
    fake_process.returncode = 1

    async def _wait() -> int:
        return 1

    fake_process.wait = _wait  # type: ignore[method-assign]
    _make_fake_subprocess(monkeypatch, fake_process)

    target = SshLaunchTargetConfig(
        id="ssh-1", name="Remote", ssh_destination="user@example.com"
    )
    client = RemoteOpenCodeClient(target, 4096)

    with pytest.raises(RuntimeError, match="sse stream ended with code 1"):
        async for _ in client.stream_events("/event"):
            pass
