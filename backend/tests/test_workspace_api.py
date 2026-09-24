"""Route-level tests for the workspace preview endpoints.

These exercise the auth, gating, and denylist behavior through the real
FastAPI app (over an in-process ASGI transport) rather than the helper layer.
The routes only read storage/tokens, so the runtime lifespan is not started.
"""

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from waypoint.api import create_app
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus
from waypoint.settings import Settings


@pytest.fixture(params=["local", "remote"])
def target(
    request: pytest.FixtureRequest, loopback_target: SshLaunchTargetConfig
) -> SshLaunchTargetConfig | None:
    return None if request.param == "local" else loopback_target


def _app(
    tmp_path: Path,
    cwd: Path,
    target: SshLaunchTargetConfig | None,
    settings_kw: dict[str, Any] | None = None,
    session_kw: dict[str, Any] | None = None,
) -> tuple[Any, str]:
    settings_kw = dict(settings_kw or {})
    session_kw = dict(session_kw or {})
    session_kw.setdefault("cwd", str(cwd))
    if target is not None:
        settings_kw.setdefault("ssh_targets", [target])
        session_kw.setdefault("launch_target_id", target.id)
    settings = Settings(data_dir=tmp_path / "data", **settings_kw)
    app = create_app(settings)
    context = app.state.context
    now = datetime.now(UTC)
    session = SessionRecord(
        id="s1",
        backend="codex",
        source=SessionSource.MANAGED,
        title="t",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=str(tmp_path / "raw.log"),
        structured_log_path=str(tmp_path / "events.jsonl"),
        **session_kw,
    )
    context.storage.create_session(session)
    token = context.tokens.issue().token
    return app, token


def _build(
    tmp_path: Path,
    target: SshLaunchTargetConfig | None,
    settings_kw: dict[str, Any] | None = None,
    session_kw: dict[str, Any] | None = None,
) -> tuple[Any, str]:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "notes.md").write_text("hello", encoding="utf-8")
    (workspace / ".env").write_text("SECRET=1", encoding="utf-8")
    git_dir = workspace / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n", encoding="utf-8")
    return _app(tmp_path, workspace, target, settings_kw, session_kw)


def _init_repo(workspace: Path) -> None:
    for args in (
        ("init", "-q"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "T"),
        ("config", "commit.gpgsign", "false"),
    ):
        _git(workspace, *args)


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(workspace), *args], check=True, capture_output=True
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_tree_lists_dotfiles_but_hides_git(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    app, token = _build(tmp_path, target)
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/tree",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    names = [entry["name"] for entry in resp.json()["entries"]]
    assert "notes.md" in names
    assert ".env" in names  # ordinary dotfiles preview by default
    assert ".git" not in names  # VCS internals stay hidden


async def test_file_reads_text(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    app, token = _build(tmp_path, target)
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": "notes.md"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "hello"
    assert body["binary"] is False


async def test_dotdir_is_denied(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    app, token = _build(tmp_path, target)
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": ".git/config"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


async def test_traversal_and_missing(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    app, token = _build(tmp_path, target)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(app) as client:
        escape = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": "../../../../etc/passwd"},
            headers=headers,
        )
        missing = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": "nope.txt"},
            headers=headers,
        )
    assert escape.status_code == 403
    assert missing.status_code == 404


async def test_requires_token(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    app, _ = _build(tmp_path, target)
    async with _client(app) as client:
        resp = await client.get("/api/sessions/s1/workspace/tree")
    assert resp.status_code == 401


async def test_disabled_returns_404(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    app, token = _build(
        tmp_path, target, settings_kw={"workspace_preview_enabled": False}
    )
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/tree",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "disabled"  # the gated branch, not a routing miss


async def test_raw_validates_query_token(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    app, token = _build(tmp_path, target)
    async with _client(app) as client:
        bad = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": "notes.md", "raw": "1", "token": "bogus"},
        )
        good = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": "notes.md", "raw": "1", "token": token},
        )
    assert bad.status_code == 401
    assert good.status_code == 200
    assert good.text == "hello"


async def test_resolve_relative_file(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    app, token = _build(tmp_path, target)
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/resolve",
            params={"path": "notes.md"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"path": "notes.md", "kind": "file"}


async def test_resolve_absolute_path_within_base(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    # The transcript hands the backend the agent-printed absolute path verbatim;
    # it must resolve to the same canonical relative path as the bare name.
    app, token = _build(tmp_path, target)
    absolute = str(tmp_path / "ws" / "notes.md")
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/resolve",
            params={"path": absolute},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"path": "notes.md", "kind": "file"}


async def test_resolve_directory(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    app, token = _build(tmp_path, target)
    (tmp_path / "ws" / "docs").mkdir()
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(app) as client:
        rel = await client.get(
            "/api/sessions/s1/workspace/resolve",
            params={"path": "docs"},
            headers=headers,
        )
        absolute = await client.get(
            "/api/sessions/s1/workspace/resolve",
            params={"path": str(tmp_path / "ws" / "docs")},
            headers=headers,
        )
    assert rel.json() == {"path": "docs", "kind": "dir"}
    assert absolute.json() == {"path": "docs", "kind": "dir"}


async def test_resolve_outside_base_is_rejected(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    app, token = _build(tmp_path, target)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(app) as client:
        absolute = await client.get(
            "/api/sessions/s1/workspace/resolve",
            params={"path": "/etc/passwd"},
            headers=headers,
        )
        relative = await client.get(
            "/api/sessions/s1/workspace/resolve",
            params={"path": "../../../../etc/passwd"},
            headers=headers,
        )
        denied = await client.get(
            "/api/sessions/s1/workspace/resolve",
            params={"path": ".git/config"},
            headers=headers,
        )
        denied_absolute = await client.get(
            "/api/sessions/s1/workspace/resolve",
            params={"path": str(tmp_path / "ws" / ".git" / "config")},
            headers=headers,
        )
        missing = await client.get(
            "/api/sessions/s1/workspace/resolve",
            params={"path": "nope.txt"},
            headers=headers,
        )
    assert absolute.status_code == 403
    assert relative.status_code == 403
    assert denied.status_code == 403
    assert denied_absolute.status_code == 403  # denylist runs on the raw absolute path
    assert missing.status_code == 404


async def test_empty_denylist_disables_filtering(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    app, token = _build(tmp_path, target, settings_kw={"workspace_denylist": []})
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": ".git/config"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["content"] == "[core]\n"


async def test_git_status_hides_denied_paths(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()

    _init_repo(workspace)
    (workspace / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-q", "-m", "init")
    (workspace / "app.py").write_text("x = 2\n", encoding="utf-8")  # changed, shown
    (workspace / "secret.pem").write_text("KEY\n", encoding="utf-8")  # denied, hidden

    app, token = _app(
        tmp_path, workspace, target, settings_kw={"workspace_denylist": ["*.pem"]}
    )
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/git/status",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    paths = [entry["path"] for entry in body["files"]]
    assert "app.py" in paths
    assert "secret.pem" not in paths


async def test_find_returns_ranked_matches(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()

    _init_repo(workspace)
    (workspace / "explorer.py").write_text("x\n", encoding="utf-8")
    (workspace / "secret.pem").write_text("KEY\n", encoding="utf-8")  # denied
    (workspace / "readme.md").write_text("x\n", encoding="utf-8")  # no match

    app, token = _app(
        tmp_path, workspace, target, settings_kw={"workspace_denylist": ["*.pem"]}
    )
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/find",
            params={"q": "explorer"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    matches = [entry["path"] for entry in resp.json()["matches"]]
    assert "explorer.py" in matches
    assert "secret.pem" not in matches  # denied paths never surface
    assert "readme.md" not in matches  # non-matching paths are dropped


async def test_unknown_or_disabled_target_is_unavailable_not_local(
    tmp_path: Path,
) -> None:
    disabled = SshLaunchTargetConfig(
        id="devbox", name="devbox", ssh_destination="devbox", enabled=False
    )
    app, token = _build(
        tmp_path,
        None,
        settings_kw={"ssh_targets": [disabled]},
        session_kw={"launch_target_id": "devbox"},
    )
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/tree",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 503


async def test_remote_transport_failure_is_503(
    tmp_path: Path, loopback_target: SshLaunchTargetConfig, monkeypatch
) -> None:
    app, token = _build(tmp_path, loopback_target)
    monkeypatch.setattr(
        SshLaunchTargetConfig,
        "build_remote_exec_args",
        lambda self, command, *a, **kw: ("false",),
    )
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/tree",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "workspace unavailable"


async def test_tilde_cwd_resolves_on_the_session_host(
    tmp_path: Path, target: SshLaunchTargetConfig | None, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    app, token = _build(tmp_path, target, session_kw={"cwd": "~/ws"})
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": "notes.md"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["content"] == "hello"


async def test_raw_serves_image_bytes(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    app, token = _build(tmp_path, target)
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
    (tmp_path / "ws" / "pic.png").write_bytes(png)
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": "pic.png", "raw": "1", "token": token},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["content-disposition"].startswith("inline")
    assert resp.content == png


async def test_git_diff_of_deleted_file(
    tmp_path: Path, target: SshLaunchTargetConfig | None
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _init_repo(workspace)
    (workspace / "gone.txt").write_text("bye\n", encoding="utf-8")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-q", "-m", "init")
    (workspace / "gone.txt").unlink()
    app, token = _app(tmp_path, workspace, target)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(app) as client:
        deleted = await client.get(
            "/api/sessions/s1/workspace/git/diff",
            params={"path": "gone.txt"},
            headers=headers,
        )
        escape = await client.get(
            "/api/sessions/s1/workspace/git/diff",
            params={"path": "../outside.txt"},
            headers=headers,
        )
    assert deleted.status_code == 200
    assert deleted.json()["total_deletions"] == 1
    assert escape.status_code == 403


async def test_directories_complete_on_the_target(
    tmp_path: Path, target: SshLaunchTargetConfig | None, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("Projects", "Proto", "Papers", ".hidden", ".ssh"):
        (tmp_path / name).mkdir()
    (tmp_path / "Profile.txt").write_text("x", encoding="utf-8")
    app, token = _build(tmp_path, target)
    params: dict[str, str] = {"launch_target_id": target.id} if target else {}
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(app) as client:
        pro = await client.get(
            "/api/directories", params={**params, "prefix": "~/Pro"}, headers=headers
        )
        hidden = await client.get(
            "/api/directories", params={**params, "prefix": "~/."}, headers=headers
        )
        absolute = await client.get(
            "/api/directories",
            params={**params, "prefix": f"{tmp_path}/Pa"},
            headers=headers,
        )
        relative = await client.get(
            "/api/directories", params={**params, "prefix": "Pro"}, headers=headers
        )
    assert pro.json() == {"directories": ["~/Projects", "~/Proto"]}
    assert hidden.json() == {"directories": ["~/.hidden"]}  # .ssh is denylisted
    assert absolute.json() == {"directories": [f"{tmp_path}/Papers"]}
    assert relative.json() == {"directories": []}


async def test_directories_unknown_target_and_disabled(tmp_path: Path) -> None:
    app, token = _build(tmp_path, None)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(app) as client:
        unknown = await client.get(
            "/api/directories",
            params={"prefix": "/", "launch_target_id": "nope"},
            headers=headers,
        )
    assert unknown.status_code == 404

    other = tmp_path / "other"
    other.mkdir()
    app, token = _app(
        other, other, None, settings_kw={"workspace_preview_enabled": False}
    )
    async with _client(app) as client:
        disabled = await client.get(
            "/api/directories",
            params={"prefix": "/"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert disabled.status_code == 404
