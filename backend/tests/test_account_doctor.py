"""Account probe / doctor / setup-transcripts: checklist, endpoints, CLI.

Phase 2 of the account-switching RFC. Covers the server-free static checklist,
the per-agent readiness verdicts, the HTTP endpoints (redaction + guards), and
the CLI verbs (including doctor's non-zero exit).
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

import waypoint.cli as cli
import waypoint.runtime as runtime_module
from waypoint.api import create_app
from waypoint.backends.account_profiles import (
    account_profile_static_checks,
    resolve_account_profiles,
)
from waypoint.backends.base import ConfigDirReadinessReporting
from waypoint.backends.bootstrap import build_default_registry
from waypoint.backends.transcript_fs_remote import RemoteTranscriptFilesystem
from waypoint.cli import app
from waypoint.client import WaypointClient
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.runtime import SessionRuntime
from waypoint.schemas import AccountProbeResult
from waypoint.settings import Settings
from waypoint.storage import Storage

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The test host may run Waypoint itself, whose WAYPOINT_* env vars would
    # otherwise override the per-test config file (notably data_dir).
    for var in (
        "WAYPOINT_DATA_DIR",
        "WAYPOINT_CONFIG_PATH",
        "WAYPOINT_HOST",
        "WAYPOINT_PORT",
        "WAYPOINT_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)


def _onboarded_claude_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True}))
    return path


def _settings(tmp_path: Path, profiles: dict[str, Any]) -> Settings:
    return Settings.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "plugin_configs": {"claude_code": {"account_profiles": profiles}},
        }
    )


# ── per-agent readiness verdicts ────────────────────────────────────────────


def _readiness_reporter(backend: str) -> ConfigDirReadinessReporting:
    plugin = build_default_registry().get(backend)
    assert isinstance(plugin, ConfigDirReadinessReporting)
    return plugin


def test_claude_readiness_true_when_onboarded(tmp_path: Path) -> None:
    cfg = _onboarded_claude_dir(tmp_path / "c")
    verdict = _readiness_reporter("claude_code").config_dir_readiness(str(cfg))
    assert verdict.ready is True
    assert verdict.reason is None


def test_claude_readiness_false_when_unonboarded(tmp_path: Path) -> None:
    (tmp_path / "c").mkdir()
    verdict = _readiness_reporter("claude_code").config_dir_readiness(
        str(tmp_path / "c")
    )
    assert verdict.ready is False
    assert "onboarding" in (verdict.reason or "")


def test_codex_readiness_tracks_auth_json(tmp_path: Path) -> None:
    plugin = _readiness_reporter("codex")
    home = tmp_path / "codex"
    home.mkdir()
    assert plugin.config_dir_readiness(str(home)).ready is False
    (home / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-live"}))
    assert plugin.config_dir_readiness(str(home)).ready is True


# ── static checklist ────────────────────────────────────────────────────────


def _check(checks: list[Any], name: str) -> Any:
    return next(c for c in checks if c.name == name)


def test_static_checks_pass_for_ready_profile(tmp_path: Path) -> None:
    cfg = _onboarded_claude_dir(tmp_path / "work")
    settings = _settings(tmp_path, {"work": {"label": "Work", "config_dir": str(cfg)}})
    profile = resolve_account_profiles(settings, "claude_code")["work"]
    checks = account_profile_static_checks(
        settings, "claude_code", "work", profile, local=True
    )
    assert all(c.ok for c in checks)
    assert _check(checks, "supported").ok


def test_static_checks_flag_missing_dir_and_unonboarded(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, {"gone": {"label": "Gone", "config_dir": str(tmp_path / "nope")}}
    )
    profile = resolve_account_profiles(settings, "claude_code")["gone"]
    checks = account_profile_static_checks(
        settings, "claude_code", "gone", profile, local=True
    )
    assert _check(checks, "config_dir_exists").ok is False
    assert _check(checks, "ready").ok is False


def test_static_checks_transcript_setup_requires_symlink(tmp_path: Path) -> None:
    cfg = _onboarded_claude_dir(tmp_path / "work")
    shared = tmp_path / "shared"
    shared.mkdir()
    settings = _settings(
        tmp_path,
        {
            "work": {
                "label": "Work",
                "config_dir": str(cfg),
                "transcript_policy": "symlink_shared",
                "shared_transcript_dir": str(shared),
            }
        },
    )
    profile = resolve_account_profiles(settings, "claude_code")["work"]
    checks = account_profile_static_checks(
        settings, "claude_code", "work", profile, local=True
    )
    # projects/ isn't a symlink yet -> transcript_setup fails.
    assert _check(checks, "transcript_setup").ok is False


def test_static_checks_redact_paths_by_default(tmp_path: Path) -> None:
    cfg = _onboarded_claude_dir(tmp_path / "work")
    settings = _settings(tmp_path, {"work": {"label": "Work", "config_dir": str(cfg)}})
    profile = resolve_account_profiles(settings, "claude_code")["work"]
    checks = account_profile_static_checks(
        settings, "claude_code", "work", profile, local=True
    )
    detail = _check(checks, "config_dir_exists").detail or ""
    assert str(cfg) not in detail
    shown = account_profile_static_checks(
        settings, "claude_code", "work", profile, local=True, show_paths=True
    )
    assert str(cfg) in (_check(shown, "config_dir_exists").detail or "")


def test_static_checks_skip_filesystem_when_remote(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, {"work": {"label": "Work", "config_dir": "/whatever"}}
    )
    profile = resolve_account_profiles(settings, "claude_code")["work"]
    checks = account_profile_static_checks(
        settings, "claude_code", "work", profile, local=False
    )
    assert _check(checks, "config_dir_exists").detail == (
        "skipped: unsupported on a remote launch target"
    )


# ── remote doctor: do-not-regress readiness surface ─────────────────────────
#
# ``account_profile_static_checks`` can't stat a remote dir (no launch
# target), so it skips its filesystem checks entirely for a remote profile.
# ``SessionRuntime.account_doctor`` fills that gap with a real, best-effort
# remote existence check (see ``_remote_config_dir_check``) — the do-not-
# regress surface for a remote switch, while the interactive-onboarding
# readiness verdict stays a documented follow-up (no remote implementation).


def _runtime_with_target(
    tmp_path: Path, profiles: dict[str, Any], target: SshLaunchTargetConfig
) -> SessionRuntime:
    settings = Settings.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "plugin_configs": {"claude_code": {"account_profiles": profiles}},
            "ssh_targets": [target.model_dump()],
        }
    )
    return SessionRuntime(settings, Storage(settings.database_path))


def _target(target_id: str = "d") -> SshLaunchTargetConfig:
    return SshLaunchTargetConfig(id=target_id, name=target_id, ssh_destination="u@d")


async def test_remote_doctor_reports_existing_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _target()
    runtime = _runtime_with_target(
        tmp_path,
        {"work": {"label": "Work", "config_dir": "/remote/.claude-work"}},
        target,
    )
    monkeypatch.setattr(RemoteTranscriptFilesystem, "exists", lambda self, path: True)

    reports = await runtime.account_doctor(backend="claude_code", launch_target_id="d")
    check = _check(reports[0].checks, "remote_config_dir_exists")
    assert check.ok is True
    assert "exists" in (check.detail or "")


async def test_remote_doctor_flags_missing_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _target()
    runtime = _runtime_with_target(
        tmp_path,
        {"work": {"label": "Work", "config_dir": "/remote/.claude-work"}},
        target,
    )
    monkeypatch.setattr(RemoteTranscriptFilesystem, "exists", lambda self, path: False)

    reports = await runtime.account_doctor(backend="claude_code", launch_target_id="d")
    check = _check(reports[0].checks, "remote_config_dir_exists")
    assert check.ok is False
    assert "missing" in (check.detail or "")


async def test_remote_doctor_flags_unresolved_tilde_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cache is never warmed here (no call to ``_ensure_remote_home_cached``),
    # mirroring an unreachable target — the check must fail closed rather than
    # guess at a literal ``~`` path, and must not even attempt the remote stat.
    target = _target()
    runtime = _runtime_with_target(
        tmp_path, {"work": {"label": "Work", "config_dir": "~/.claude-work"}}, target
    )
    called = False

    def _unexpected_exists(self: RemoteTranscriptFilesystem, path: str) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(RemoteTranscriptFilesystem, "exists", _unexpected_exists)

    reports = await runtime.account_doctor(backend="claude_code", launch_target_id="d")
    check = _check(reports[0].checks, "remote_config_dir_exists")
    assert check.ok is False
    assert "resolve remote home" in (check.detail or "")
    assert called is False


# ── HTTP endpoints ──────────────────────────────────────────────────────────


@pytest.fixture
def app_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Any, dict[str, str]]]:
    cfg = _onboarded_claude_dir(tmp_path / "work")
    settings = _settings(
        tmp_path,
        {
            "work": {
                "label": "Work",
                "config_dir": str(cfg),
                "expected_account_key": "claude_code:acme",
            }
        },
    )
    app = create_app(settings)
    token = app.state.context.tokens.issue().token
    yield app, {"Authorization": f"Bearer {token}"}


async def _get(app: Any, path: str, headers: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path, headers=headers)


async def _post(
    app: Any, path: str, headers: dict[str, str], body: dict[str, Any]
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(path, headers=headers, json=body)


@pytest.mark.asyncio
async def test_doctor_endpoint_reports_profiles(
    app_client: tuple[Any, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, headers = app_client

    async def fake_probe(*args: Any, **kwargs: Any) -> AccountProbeResult:
        return AccountProbeResult(account_key="claude_code:acme", account_label="Acme")

    monkeypatch.setattr(runtime_module, "probe_account", fake_probe)
    resp = await _get(app, "/api/backends/claude_code/accounts/doctor", headers)
    assert resp.status_code == 200
    report = resp.json()[0]
    assert report["profile"] == "work"
    names = {c["name"] for c in report["checks"]}
    assert {"supported", "config_dir_exists", "ready", "transcript_setup"} <= names
    match = next(c for c in report["checks"] if c["name"] == "account_matches_expected")
    assert match["ok"] is True
    # The matched key stays out of the detail without --show-key.
    assert "claude_code:acme" not in (match["detail"] or "")


@pytest.mark.asyncio
async def test_doctor_endpoint_redacts_account_key_by_default(
    app_client: tuple[Any, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, headers = app_client

    async def wrong_probe(*args: Any, **kwargs: Any) -> AccountProbeResult:
        return AccountProbeResult(
            account_key="claude_code:other", account_label="Other"
        )

    monkeypatch.setattr(runtime_module, "probe_account", wrong_probe)

    def _match(resp: httpx.Response) -> dict[str, Any]:
        checks = resp.json()[0]["checks"]
        return next(c for c in checks if c["name"] == "account_matches_expected")

    redacted = await _get(app, "/api/backends/claude_code/accounts/doctor", headers)
    detail = _match(redacted)["detail"]
    assert _match(redacted)["ok"] is False
    assert "claude_code:other" not in detail
    assert "claude_code:acme" not in detail

    shown = await _get(
        app, "/api/backends/claude_code/accounts/doctor?show_key=true", headers
    )
    assert "claude_code:acme" in _match(shown)["detail"]


@pytest.mark.asyncio
async def test_doctor_endpoint_rejects_non_hosting_backend(
    app_client: tuple[Any, dict[str, str]],
) -> None:
    app, headers = app_client
    resp = await _get(app, "/api/backends/opencode/accounts/doctor", headers)
    assert resp.status_code == 400
    assert "does not host account profiles" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_probe_endpoint_redacts_key_by_default(
    app_client: tuple[Any, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, headers = app_client

    async def fake_probe(*args: Any, **kwargs: Any) -> AccountProbeResult:
        return AccountProbeResult(account_key="claude_code:acme", account_label="Acme")

    monkeypatch.setattr(runtime_module, "probe_account", fake_probe)
    redacted = await _get(app, "/api/backends/claude_code/accounts/work/probe", headers)
    assert redacted.json()["account_key"] == ""
    assert redacted.json()["account_label"] == "Acme"
    shown = await _get(
        app, "/api/backends/claude_code/accounts/work/probe?show_key=true", headers
    )
    assert shown.json()["account_key"] == "claude_code:acme"


@pytest.mark.asyncio
async def test_setup_transcripts_endpoint_migrates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _onboarded_claude_dir(tmp_path / "work")
    (cfg / "projects" / "p").mkdir(parents=True)
    (cfg / "projects" / "p" / "t.jsonl").write_text("thread")
    shared = tmp_path / "shared"
    settings = _settings(
        tmp_path,
        {
            "work": {
                "label": "Work",
                "config_dir": str(cfg),
                "transcript_policy": "symlink_shared",
                "shared_transcript_dir": str(shared),
            }
        },
    )
    app = create_app(settings)
    headers = {"Authorization": f"Bearer {app.state.context.tokens.issue().token}"}
    resp = await _post(
        app, "/api/backends/claude_code/accounts/work/setup-transcripts", headers, {}
    )
    assert resp.status_code == 200
    assert any("linked" in a for a in resp.json()["actions"])
    assert (cfg / "projects").is_symlink()
    assert (shared / "p" / "t.jsonl").read_text() == "thread"


# ── CLI verbs ───────────────────────────────────────────────────────────────


def _cli_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "waypoint.yaml"
    cfg.write_text(f"data_dir: {tmp_path / 'data'}\n", encoding="utf-8")
    return cfg


def _mock_cli_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    def fake_client(settings: Settings, **_: object) -> WaypointClient:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
        return WaypointClient(settings, token="t", client=http)

    monkeypatch.setattr(cli, "WaypointClient", fake_client)


def test_cli_doctor_exits_nonzero_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/backends":
            return httpx.Response(
                200,
                json={
                    "backends": [
                        {"id": "claude_code", "account_profiles": [{"id": "work"}]}
                    ]
                },
            )
        if path == "/api/backends/claude_code/accounts/doctor":
            return httpx.Response(
                200,
                json=[
                    {
                        "backend": "claude_code",
                        "profile": "work",
                        "label": "Work",
                        "ok": False,
                        "checks": [
                            {"name": "ready", "ok": False, "detail": "not onboarded"}
                        ],
                    }
                ],
            )
        return httpx.Response(404, json={"detail": f"unexpected {path}"})

    _mock_cli_client(monkeypatch, handler)
    result = runner.invoke(
        app, ["--config", str(_cli_config(tmp_path)), "accounts", "doctor"]
    )
    assert result.exit_code == 1
    assert "FAIL" in result.stdout


def test_cli_doctor_json_ok_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/backends":
            return httpx.Response(
                200,
                json={
                    "backends": [
                        {"id": "claude_code", "account_profiles": [{"id": "work"}]}
                    ]
                },
            )
        if path == "/api/backends/claude_code/accounts/doctor":
            return httpx.Response(
                200,
                json=[
                    {
                        "backend": "claude_code",
                        "profile": "work",
                        "label": "Work",
                        "ok": True,
                        "checks": [{"name": "ready", "ok": True, "detail": "ready"}],
                    }
                ],
            )
        return httpx.Response(404, json={"detail": f"unexpected {path}"})

    _mock_cli_client(monkeypatch, handler)
    result = runner.invoke(
        app, ["--config", str(_cli_config(tmp_path)), "accounts", "doctor", "--json"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)[0]["ok"] is True


def test_cli_probe_emits_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/backends/claude_code/accounts/work/probe":
            assert request.url.params.get("show_key") == "true"
            return httpx.Response(
                200, json={"account_key": "claude_code:acme", "account_label": "Acme"}
            )
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    _mock_cli_client(monkeypatch, handler)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "accounts",
            "probe",
            "claude_code",
            "work",
            "--show-key",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["account_key"] == "claude_code:acme"


def test_cli_setup_transcripts_emits_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accounts/work/setup-transcripts"):
            return httpx.Response(200, json={"actions": ["linked X -> Y"]})
        return httpx.Response(404, json={"detail": f"unexpected {request.url.path}"})

    _mock_cli_client(monkeypatch, handler)
    result = runner.invoke(
        app,
        [
            "--config",
            str(_cli_config(tmp_path)),
            "accounts",
            "setup-transcripts",
            "claude_code",
            "work",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["actions"] == ["linked X -> Y"]
