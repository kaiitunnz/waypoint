import json
from pathlib import Path

import httpx
import pytest

from waypoint.client import WaypointClient, WaypointError, cli_token_path
from waypoint.settings import Settings

VALID_TOKEN = "valid-token"


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(data_dir=tmp_path / "data", password="hunter2")
    settings.ensure_dirs()
    return settings


def _make_handler(state: dict) -> "httpx.MockTransport":
    state.setdefault("logins", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            body = json.loads(request.content)
            if body.get("password") != "hunter2":
                return httpx.Response(401, json={"detail": "invalid password"})
            state["logins"] += 1
            return httpx.Response(200, json={"token": VALID_TOKEN, "expires_at": "x"})
        if request.headers.get("Authorization") != f"Bearer {VALID_TOKEN}":
            return httpx.Response(401, json={"detail": "invalid token"})
        if request.url.path == "/api/sessions" and request.method == "GET":
            return httpx.Response(200, json={"sessions": [{"id": "s1"}]})
        if request.url.path == "/api/backends" and request.method == "GET":
            return httpx.Response(
                200, json={"backends": [{"id": "claude_code"}, {"id": "codex"}]}
            )
        if request.url.path == "/api/board" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "channels": [
                        {
                            "channel": "topic:x",
                            "entry_count": 2,
                            "last_created_at": "x",
                        }
                    ]
                },
            )
        if request.url.path.startswith("/api/board/"):
            rest = request.url.path[len("/api/board/") :]
            if rest.endswith("/clear") and request.method == "POST":
                channel = rest[: -len("/clear")]
                return httpx.Response(200, json={"channel": channel, "cleared": 3})
            channel = rest
            if request.method == "POST":
                payload = json.loads(request.content)
                state["board_post"] = {"channel": channel, **payload}
                return httpx.Response(
                    200, json={"entry": {"id": 1, "channel": channel, **payload}}
                )
            if request.method == "GET":
                state["board_read_params"] = dict(request.url.params)
                return httpx.Response(
                    200,
                    json={"channel": channel, "entries": [{"id": 1, "text": "hello"}]},
                )
            if request.method == "DELETE":
                return httpx.Response(200, json={"channel": channel, "deleted": 5})
        if request.url.path == "/api/sessions" and request.method == "POST":
            payload = json.loads(request.content)
            return httpx.Response(200, json={"session": {"id": "new", **payload}})
        if request.url.path == "/api/sessions/s1/input":
            return httpx.Response(200, json={"session": {"id": "s1"}})
        if request.url.path.startswith("/api/sessions/") and request.method == "DELETE":
            state["delete_force"] = request.url.params.get("force")
            return httpx.Response(
                200, json={"deleted": request.url.path.rsplit("/", 1)[-1]}
            )
        if request.url.path == "/api/sessions/missing":
            return httpx.Response(404, json={"detail": "session not found"})
        return httpx.Response(200, json={"session": {"id": "ok"}})

    return httpx.MockTransport(handler)


def _client(
    settings: Settings, state: dict, *, token: str | None = None
) -> WaypointClient:
    http = httpx.Client(transport=_make_handler(state), base_url="http://test")
    return WaypointClient(settings, token=token, client=http)


def test_uses_explicit_env_token_without_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        assert client.list_sessions() == [{"id": "s1"}]
    assert state["logins"] == 0


def test_list_backends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        assert client.list_backends() == [{"id": "claude_code"}, {"id": "codex"}]


def test_delete_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        assert client.delete("s1") == {"deleted": "s1"}
        assert state["delete_force"] is None
        assert client.delete("s1", force=True) == {"deleted": "s1"}
        assert state["delete_force"] == "true"


def test_reads_cached_token_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WAYPOINT_TOKEN", raising=False)
    settings = _settings(tmp_path)
    cli_token_path(settings).write_text(VALID_TOKEN, encoding="utf-8")
    state: dict = {}
    with _client(settings, state) as client:
        assert client.list_sessions() == [{"id": "s1"}]
    assert state["logins"] == 0


def test_logs_in_with_password_and_caches_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WAYPOINT_TOKEN", raising=False)
    monkeypatch.delenv("WAYPOINT_PASSWORD", raising=False)
    settings = _settings(tmp_path)
    state: dict = {}
    with _client(settings, state) as client:
        assert client.list_sessions() == [{"id": "s1"}]
    assert state["logins"] == 1
    cached = cli_token_path(settings)
    assert cached.read_text(encoding="utf-8").strip() == VALID_TOKEN
    assert oct(cached.stat().st_mode)[-3:] == "600"


def test_stale_token_triggers_relogin_and_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WAYPOINT_TOKEN", raising=False)
    monkeypatch.delenv("WAYPOINT_PASSWORD", raising=False)
    settings = _settings(tmp_path)
    state: dict = {}
    with _client(settings, state, token="stale") as client:
        assert client.list_sessions() == [{"id": "s1"}]
    assert state["logins"] == 1


def test_create_session_posts_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        session = client.create_session(backend="codex", cwd="/tmp", model="gpt-5")
    assert session["backend"] == "codex"
    assert session["model"] == "gpt-5"


def test_create_session_passes_spawner_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        session = client.create_session(
            backend="claude_code", cwd="/tmp", spawner_session_id="parent-1"
        )
    # The mock echoes the POST body back into the session payload.
    assert session["spawner_session_id"] == "parent-1"


def test_list_board_channels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        channels = client.list_board_channels()
    assert channels[0]["channel"] == "topic:x"
    assert channels[0]["entry_count"] == 2


def test_post_board_sends_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        entry = client.post_board(
            "topic:x",
            "hello",
            key="k1",
            author_session_id="s1",
            metadata={"a": "b"},
        )
    assert entry["text"] == "hello"
    assert state["board_post"]["key"] == "k1"
    assert state["board_post"]["author_session_id"] == "s1"
    assert state["board_post"]["metadata"] == {"a": "b"}


def test_read_board_passes_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        entries = client.read_board("topic:x", since=5, key="k1")
    assert entries == [{"id": 1, "text": "hello"}]
    assert state["board_read_params"]["since"] == "5"
    assert state["board_read_params"]["key"] == "k1"


def test_clear_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        assert client.clear_board("topic:x") == {"channel": "topic:x", "cleared": 3}


def test_delete_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        assert client.delete_board("topic:x") == {"channel": "topic:x", "deleted": 5}


def test_error_response_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        with pytest.raises(WaypointError):
            client.get_session("missing")
