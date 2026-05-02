from typing import Any

import pytest

from waypoint.backends.opencode.client import RemoteOpenCodeClient
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


@pytest.mark.asyncio
async def test_stream_events_raises_when_stream_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_process = _FakeProcess()
    observed_args: list[tuple[Any, ...]] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        observed_args.append(args)
        return fake_process

    monkeypatch.setattr(
        "waypoint.backends.opencode.client.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        "waypoint.launch_targets._resolve_local_binary",
        lambda binary: binary,
    )

    target = SshLaunchTargetConfig(
        id="ssh-1",
        name="Remote",
        ssh_destination="user@example.com",
    )
    client = RemoteOpenCodeClient(target, 4096)

    events: list[str] = []
    with pytest.raises(RuntimeError, match="sse stream ended with code 0"):
        async for line in client.stream_events("/event"):
            events.append(line)

    assert events == ['data: {"ok": true}\n', "\n"]
    assert observed_args
