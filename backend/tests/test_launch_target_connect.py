"""Route-level tests for password-auth launch-target connect + the launch gate.

Exercised through the real FastAPI app over an in-process ASGI transport;
``asyncio.create_subprocess_exec`` is monkeypatched so no real ``ssh`` runs.
"""

from pathlib import Path
from typing import Any

import httpx

from waypoint.api import create_app
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.settings import Settings

_MULTIPLEX_ARGS = [
    "-o",
    "ControlMaster=auto",
    "-o",
    "ControlPath=~/.ssh/cm-%C",
    "-o",
    "ControlPersist=600s",
]


class _FakeProc:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""

    async def wait(self) -> int:
        return self.returncode


def _make_recorder(connected: bool):
    async def recorder(*argv: str, **kwargs: Any) -> _FakeProc:
        if "-O" in argv and "check" in argv:
            return _FakeProc(0 if connected else 1)
        return _FakeProc(0)

    return recorder


def _build(tmp_path: Path) -> tuple[Any, str]:
    settings = Settings(
        data_dir=tmp_path / "data",
        ssh_targets=[
            SshLaunchTargetConfig(
                id="pw",
                name="pw",
                ssh_destination="host",
                ssh_auth="password",
                ssh_args=_MULTIPLEX_ARGS,
            ),
            SshLaunchTargetConfig(id="keyed", name="keyed", ssh_destination="host"),
        ],
    )
    app = create_app(settings)
    token = app.state.context.tokens.issue().token
    return app, token


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_connect_unknown_target_404(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/launch-targets/nope/connect",
            json={"password": "x"},
            headers=_auth(token),
        )
    assert resp.status_code == 404


async def test_connect_rejects_key_auth_target_400(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/launch-targets/keyed/connect",
            json={"password": "x"},
            headers=_auth(token),
        )
    assert resp.status_code == 400


async def test_connect_success_reports_connected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    monkeypatch.setattr(
        "waypoint.ssh_master.asyncio.create_subprocess_exec",
        _make_recorder(connected=True),
    )
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/launch-targets/pw/connect",
            json={"password": "s3cret"},
            headers=_auth(token),
        )
    assert resp.status_code == 200
    assert resp.json()["connected"] is True


async def test_threads_probe_skipped_when_master_down(
    tmp_path: Path, monkeypatch
) -> None:
    # No SSH must be attempted; if the gate misses, this would raise loudly.
    async def boom(*argv: str, **kwargs: Any):
        raise AssertionError(f"unexpected ssh probe: {argv}")

    monkeypatch.setattr("waypoint.ssh_master.asyncio.create_subprocess_exec", boom)
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get(
            "/api/backends/codex/threads",
            params={"launch_target_id": "pw"},
            headers=_auth(token),
        )
    assert resp.status_code == 200
    assert resp.json()["threads"] == []


async def test_create_session_requires_live_master_409(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    monkeypatch.setattr(
        "waypoint.ssh_master.asyncio.create_subprocess_exec",
        _make_recorder(connected=False),
    )
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/sessions",
            json={
                "backend": "codex",
                "cwd": "~/work",
                "launch_target_id": "pw",
                "source_mode": "managed",
            },
            headers=_auth(token),
        )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ssh-master-required"
