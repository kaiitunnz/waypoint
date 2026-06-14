import json
from pathlib import Path

import httpx
import pytest

from waypoint.client import (
    WaypointClient,
    WaypointError,
    cli_token_path,
    is_event_envelope,
    session_status_from_envelope,
    websocket_url,
)
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
        if request.url.path == "/api/backends/claude_code/threads":
            state["threads_params"] = dict(request.url.params)
            return httpx.Response(
                200,
                json={
                    "threads": [
                        {"id": "thread-1", "title": "Alpha", "cwd": "/tmp/repo"}
                    ]
                },
            )
        if request.url.path == "/api/backends/claude_code/sessions/import":
            state["import_body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"session": {"id": "imported", **state["import_body"]}},
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
                state["clear_params"] = dict(request.url.params)
                return httpx.Response(200, json={"channel": channel, "cleared": 3})
            if "/entries/" in rest:
                channel, entry_id = rest.split("/entries/", 1)
                if request.method == "DELETE":
                    return httpx.Response(
                        200,
                        json={
                            "channel": channel,
                            "entry_id": int(entry_id),
                            "deleted": True,
                        },
                    )
                if request.method == "PATCH":
                    payload = json.loads(request.content)
                    state["board_update"] = {
                        "channel": channel,
                        "entry_id": int(entry_id),
                        **payload,
                    }
                    return httpx.Response(
                        200,
                        json={
                            "entry": {
                                "id": int(entry_id),
                                "channel": channel,
                                **payload,
                            }
                        },
                    )
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
        if (
            request.url.path == "/api/sessions/s1/attachments"
            and request.method == "POST"
        ):
            state["uploads"] = state.get("uploads", 0) + 1
            return httpx.Response(
                200,
                json={
                    "id": f"att{state['uploads']:032x}",
                    "filename": "f",
                    "mime": "text/plain",
                    "size": 1,
                    "kind": "file",
                },
            )
        if request.url.path == "/api/sessions/s1/input":
            state["input_body"] = json.loads(request.content)
            return httpx.Response(200, json={"session": {"id": "s1"}})
        if request.url.path.startswith("/api/sessions/") and request.method == "DELETE":
            state["delete_force"] = request.url.params.get("force")
            return httpx.Response(
                200, json={"deleted": request.url.path.rsplit("/", 1)[-1]}
            )
        if request.url.path == "/api/sessions/s1/events" and request.method == "GET":
            state["events_params"] = dict(request.url.params)
            return httpx.Response(
                200,
                json={
                    "events": [
                        {"kind": "user_input", "text": "hi", "sequence": 1},
                        {"kind": "agent_output", "text": "hello", "sequence": 2},
                    ],
                    "has_more": False,
                },
            )
        if request.url.path.startswith("/api/backends/") and request.url.path.endswith(
            "/models"
        ):
            backend = request.url.path[len("/api/backends/") : -len("/models")]
            state["models_params"] = dict(request.url.params)
            return httpx.Response(
                200,
                json={
                    "backend": backend,
                    "models": [{"id": "opus", "label": "Opus"}],
                    "default_model_id": "opus",
                },
            )
        if request.url.path.endswith("/answer-question") and request.method == "POST":
            state["answer_body"] = json.loads(request.content)
            return httpx.Response(200, json={"session": {"id": "s1"}})
        if (
            request.url.path == "/api/schedules/clear-history"
            and request.method == "POST"
        ):
            return httpx.Response(200, json={"removed": 3})
        if request.url.path == "/api/schedules" and request.method == "GET":
            return httpx.Response(200, json={"schedules": [{"id": "sc1"}]})
        if request.url.path == "/api/schedules" and request.method == "POST":
            payload = json.loads(request.content)
            state["schedule_create"] = payload
            return httpx.Response(200, json={"schedule": {"id": "sc1", **payload}})
        if (
            request.url.path.startswith("/api/schedules/")
            and request.method == "DELETE"
        ):
            schedule_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"schedule": {"id": schedule_id}})
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


def test_list_models_passes_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        payload = client.list_models(
            "claude_code", launch_target_id="lt1", include_hidden=True
        )
    assert payload["backend"] == "claude_code"
    assert payload["default_model_id"] == "opus"
    assert state["models_params"]["launch_target_id"] == "lt1"
    assert state["models_params"]["include_hidden"] == "true"


def test_list_models_omits_default_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        client.list_models("claude_code")
    assert state["models_params"] == {}


def test_list_threads_passes_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        threads = client.list_threads("claude_code", launch_target_id="lt1")
    assert threads == [{"id": "thread-1", "title": "Alpha", "cwd": "/tmp/repo"}]
    assert state["threads_params"] == {"launch_target_id": "lt1"}


def test_import_thread_sends_raw_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        session = client.import_thread(
            "claude_code", {"thread_id": "thread-1", "cwd": "/tmp/repo"}
        )
    assert session["id"] == "imported"
    assert state["import_body"] == {"thread_id": "thread-1", "cwd": "/tmp/repo"}


def test_get_events_passes_messages_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        page = client.get_events("s1", messages=5)
    assert page["events"][1]["text"] == "hello"
    assert state["events_params"] == {"messages": "5"}


def test_answer_question_sends_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        session = client.answer_question(
            "s1",
            "use opus",
            tool_use_id="tu1",
            answers=[{"question": "which model?", "answer": "opus"}],
        )
    assert session == {"id": "s1"}
    assert state["answer_body"]["answer"] == "use opus"
    assert state["answer_body"]["tool_use_id"] == "tu1"
    assert state["answer_body"]["answers"] == [
        {"question": "which model?", "answer": "opus"}
    ]


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


def test_send_input_uploads_attachments_and_sends_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    one = tmp_path / "a.txt"
    two = tmp_path / "b.png"
    one.write_text("x")
    two.write_bytes(b"y")
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        ids = [client.upload_attachment("s1", p)["id"] for p in (one, two)]
        client.send_input("s1", "hi", attachments=ids)
    assert state["uploads"] == 2
    assert state["input_body"]["attachments"] == ids


def test_send_input_omits_attachments_when_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        client.send_input("s1", "hi")
    assert "attachments" not in state["input_body"]


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


def test_create_session_passes_worktree_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        session = client.create_session(
            backend="claude_code",
            cwd="/repos/myrepo-feat",
            worktree_path="/repos/myrepo-feat",
        )
    assert session["worktree_path"] == "/repos/myrepo-feat"


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


def test_delete_board_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        assert client.delete_board_entry("topic:x", 7) == {
            "channel": "topic:x",
            "entry_id": 7,
            "deleted": True,
        }


def test_update_board_entry_sends_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        entry = client.update_board_entry("topic:x", 7, "new", metadata={"a": "b"})
    assert entry["text"] == "new"
    assert state["board_update"]["entry_id"] == 7
    assert state["board_update"]["metadata"] == {"a": "b"}


def test_update_board_entry_meta_only_omits_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        entry = client.update_board_entry("topic:x", 7, metadata={"x": "y"})
    assert state["board_update"]["entry_id"] == 7
    assert state["board_update"]["metadata"] == {"x": "y"}
    assert "text" not in state["board_update"]
    # Server echoes back existing text unchanged.
    assert entry["id"] == 7


def test_error_response_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        with pytest.raises(WaypointError):
            client.get_session("missing")


def test_list_schedules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        schedules = client.list_schedules()
    assert schedules == [{"id": "sc1"}]


def test_create_schedule_posts_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        schedule = client.create_schedule(
            backend="claude_code",
            cwd="/tmp",
            initial_prompt="do the thing",
            delay_seconds=30,
        )
    assert schedule["id"] == "sc1"
    assert state["schedule_create"]["backend"] == "claude_code"
    assert state["schedule_create"]["initial_prompt"] == "do the thing"
    assert state["schedule_create"]["delay_seconds"] == 30
    # Unset optionals must be omitted, not sent as null: the server validates
    # launch_mode against its enum even though it has a default.
    assert "launch_mode" not in state["schedule_create"]


def test_delete_schedule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        schedule = client.delete_schedule("sc1")
    assert schedule == {"id": "sc1"}


def test_clear_schedule_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        result = client.clear_schedule_history()
    assert result == {"removed": 3}


def test_websocket_url_derives_scheme_and_encodes_token() -> None:
    assert (
        websocket_url("http://127.0.0.1:8787", "/ws/sessions/s1", token="a b")
        == "ws://127.0.0.1:8787/ws/sessions/s1?token=a%20b"
    )
    assert (
        websocket_url("https://host:443/", "/ws/sessions/s1", token="t")
        == "wss://host:443/ws/sessions/s1?token=t"
    )


def test_session_status_from_envelope() -> None:
    assert (
        session_status_from_envelope(
            {"type": "session_state", "payload": {"session": {"status": "idle"}}}
        )
        == "idle"
    )
    # Non-state envelopes and malformed payloads carry no status.
    assert (
        session_status_from_envelope({"type": "event", "payload": {"event": {}}})
        is None
    )
    assert (
        session_status_from_envelope({"type": "session_state", "payload": {}}) is None
    )


def test_is_event_envelope() -> None:
    assert is_event_envelope({"type": "event", "payload": {"event": {}}})
    assert not is_event_envelope({"type": "session_state", "payload": {"session": {}}})


def test_list_sessions_passes_spawned_by(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"token": VALID_TOKEN, "expires_at": "x"})
        if request.url.path == "/api/sessions" and request.method == "GET":
            state["params"] = dict(request.url.params)
            spawned_by = request.url.params.get("spawned_by")
            if spawned_by == "parent-1":
                return httpx.Response(200, json={"sessions": [{"id": "child-1"}]})
            return httpx.Response(200, json={"sessions": [{"id": "s1"}, {"id": "s2"}]})
        return httpx.Response(404, json={"detail": "unexpected"})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    settings = _settings(tmp_path)
    with WaypointClient(settings, token=VALID_TOKEN, client=http) as client:
        all_sessions = client.list_sessions()
        filtered = client.list_sessions(spawned_by="parent-1")
    assert all_sessions == [{"id": "s1"}, {"id": "s2"}]
    assert (
        "spawned_by" not in state.get("params", {})
        or state["params"].get("spawned_by") == "parent-1"
    )
    assert filtered == [{"id": "child-1"}]
    assert state["params"]["spawned_by"] == "parent-1"


def test_list_sessions_omits_spawned_by_when_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions" and request.method == "GET":
            state["params"] = dict(request.url.params)
            return httpx.Response(200, json={"sessions": []})
        return httpx.Response(404, json={"detail": "unexpected"})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    settings = _settings(tmp_path)
    with WaypointClient(settings, token=VALID_TOKEN, client=http) as client:
        client.list_sessions()
    assert "spawned_by" not in state["params"]


def test_get_usage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/usage" and request.method == "GET":
            return httpx.Response(
                200,
                json={"buckets": [], "total_cost_usd": 0.0},
            )
        return httpx.Response(404, json={"detail": "unexpected"})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
    with WaypointClient(_settings(tmp_path), token=VALID_TOKEN, client=http) as c:
        result = c.get_usage()
    assert result["buckets"] == []
    assert result["total_cost_usd"] == 0.0


def test_refresh_usage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/usage/refresh" and request.method == "POST":
            called.append("refresh")
            return httpx.Response(
                200,
                json={"buckets": [{"id": "b1"}], "total_cost_usd": 1.5},
            )
        return httpx.Response(404, json={"detail": "unexpected"})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
    with WaypointClient(_settings(tmp_path), token=VALID_TOKEN, client=http) as c:
        result = c.refresh_usage()
    assert called == ["refresh"]
    assert result["total_cost_usd"] == 1.5


def test_send_input_timeout_session_running_reports_delivered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/input":
            raise httpx.ReadTimeout("timed out", request=request)
        if request.url.path == "/api/sessions/s1" and request.method == "GET":
            return httpx.Response(
                200, json={"session": {"id": "s1", "status": "running"}}
            )
        return httpx.Response(404, json={"detail": "unexpected"})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
    with WaypointClient(_settings(tmp_path), token=VALID_TOKEN, client=http) as c:
        result = c.send_input("s1", "hello")
    assert result["send"] == "delivered"
    assert result["status"] == "running"


def test_send_input_timeout_session_idle_reports_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/input":
            raise httpx.ReadTimeout("timed out", request=request)
        if request.url.path == "/api/sessions/s1" and request.method == "GET":
            return httpx.Response(200, json={"session": {"id": "s1", "status": "idle"}})
        return httpx.Response(404, json={"detail": "unexpected"})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
    with WaypointClient(_settings(tmp_path), token=VALID_TOKEN, client=http) as c:
        result = c.send_input("s1", "hello")
    assert result["send"] == "unknown"


def test_send_input_timeout_get_fails_reports_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/input":
            raise httpx.ReadTimeout("timed out", request=request)
        # GET also fails
        return httpx.Response(503, json={"detail": "unavailable"})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
    with WaypointClient(_settings(tmp_path), token=VALID_TOKEN, client=http) as c:
        result = c.send_input("s1", "hello")
    assert result["send"] == "unknown"
    assert result["id"] == "s1"


def test_send_input_non_timeout_error_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions/s1/input":
            return httpx.Response(400, json={"detail": "bad input"})
        return httpx.Response(404, json={"detail": "unexpected"})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
    with WaypointClient(_settings(tmp_path), token=VALID_TOKEN, client=http) as c:
        with pytest.raises(WaypointError):
            c.send_input("s1", "hello")


def test_clear_board_keep_last_passes_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        result = client.clear_board("topic:x", keep_last=5)
    assert result == {"channel": "topic:x", "cleared": 3}
    assert state["clear_params"].get("keep_last") == "5"


def test_clear_board_no_keep_last_omits_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYPOINT_TOKEN", VALID_TOKEN)
    state: dict = {}
    with _client(_settings(tmp_path), state) as client:
        client.clear_board("topic:x")
    assert state.get("clear_params", {}).get("keep_last") is None
