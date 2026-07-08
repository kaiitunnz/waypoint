"""Account-profile selection: persistence, launch-env overlay, preset carry.

Phase 2 of the account-switching RFC. A launched/scheduled/imported/forked
session records which account profile it ran under, and the profile's config-dir
is overlaid (profile-wins) into the private launch_env at launch time.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

from waypoint.backends.codex.schemas import CodexThreadImportRequest
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.presets import resolve_session_create_request
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    ScheduleCreateRequest,
    SessionLaunchRequest,
    SessionPresetRecord,
    SessionPresetSpec,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage


def _runtime(
    tmp_path: Path, **plugin_configs: object
) -> tuple[SessionRuntime, Storage]:
    settings = Settings(data_dir=tmp_path / "data", plugin_configs=plugin_configs)
    storage = Storage(settings.database_path)
    return SessionRuntime(settings, storage), storage


def _codex_profiles() -> dict[str, object]:
    return {
        "account_profiles": {
            "work": {
                "label": "Work",
                "config_dir": "~/.codex-work",
                "transcript_policy": "copy_thread_on_switch",
            }
        }
    }


def _session(**kw: object) -> SessionRecord:
    now = datetime.now(UTC)
    base: dict[str, object] = dict(
        id="s1",
        backend="codex",
        source=SessionSource.MANAGED,
        title="t",
        cwd="/tmp",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
    )
    base.update(kw)
    return SessionRecord(**base)


# ── Persistence ──────────────────────────────────────────────────────────


def test_session_record_round_trips_profile_selection(tmp_path: Path) -> None:
    _, storage = _runtime(tmp_path)
    storage.create_session(
        _session(account_profile_id="work", account_profile_label="Work")
    )
    got = storage.get_session("s1")
    assert got is not None
    assert got.account_profile_id == "work"
    assert got.account_profile_label == "Work"


def test_session_record_defaults_none_when_absent(tmp_path: Path) -> None:
    _, storage = _runtime(tmp_path)
    storage.create_session(_session())
    got = storage.get_session("s1")
    assert got is not None
    assert got.account_profile_id is None
    assert got.account_profile_label is None


# ── Launch-env overlay ─────────────────────────────────────────────────────


def test_overlay_sets_config_dir_and_returns_label(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path, codex=_codex_profiles())
    env, label = runtime._apply_account_profile_env(
        "codex", {"EXISTING": "1"}, "work", None
    )
    assert env["CODEX_HOME"] == os.path.expanduser("~/.codex-work")
    assert env["EXISTING"] == "1"
    assert label == "Work"


def test_overlay_strips_raw_config_dir_value(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path, codex=_codex_profiles())
    # A raw launch_env value for the config-dir key is overridden by the profile
    # (profile-wins), never honored and never a 400.
    env, _ = runtime._apply_account_profile_env(
        "codex", {"CODEX_HOME": "/wrong/path"}, "work", None
    )
    assert env["CODEX_HOME"] == os.path.expanduser("~/.codex-work")


def test_overlay_noop_without_profile(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path, codex=_codex_profiles())
    env, label = runtime._apply_account_profile_env("codex", {"A": "1"}, None, None)
    assert env == {"A": "1"}
    assert label is None


def test_overlay_rejects_unknown_profile(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path, codex=_codex_profiles())
    with pytest.raises(HTTPException) as exc:
        runtime._apply_account_profile_env("codex", {}, "nope", None)
    assert exc.value.status_code == 400


async def test_overlay_expands_tilde_config_dir_on_remote_with_warm_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A warm remote-home cache (populated by ``_ensure_remote_home_cached``,
    # normally called eagerly by an async launch path) lets the synchronous
    # overlay expand ``~`` against the remote home instead of rejecting it.
    runtime, _ = _runtime(tmp_path, codex=_codex_profiles())
    target = SshLaunchTargetConfig(id="d", name="d", ssh_destination="u@d")

    async def fake_ssh_capture(self: SshLaunchTargetConfig, remote_cmd: str) -> str:
        assert remote_cmd == "echo $HOME"
        return "/home/alice\n"

    monkeypatch.setattr(SshLaunchTargetConfig, "ssh_capture", fake_ssh_capture)
    await runtime._ensure_remote_home_cached(target)

    env, label = runtime._apply_account_profile_env("codex", {}, "work", target)
    assert env["CODEX_HOME"] == "/home/alice/.codex-work"
    assert label == "Work"


async def test_warm_remote_home_for_profile_lets_overlay_expand_tilde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The helper every non-switch entry point (create/import/probe) calls
    # before the synchronous overlay warms the profile's tilde prefix, so a
    # ``~``-relative remote config_dir resolves instead of 400ing on a cold
    # cache — the regression the review caught.
    runtime, _ = _runtime(tmp_path, codex=_codex_profiles())
    target = SshLaunchTargetConfig(id="d", name="d", ssh_destination="u@d")

    async def fake_ssh_capture(self: SshLaunchTargetConfig, remote_cmd: str) -> str:
        return "/home/alice\n"

    monkeypatch.setattr(SshLaunchTargetConfig, "ssh_capture", fake_ssh_capture)
    await runtime._warm_remote_home_for_profile(target, "codex", "work")

    env, _ = runtime._apply_account_profile_env("codex", {}, "work", target)
    assert env["CODEX_HOME"] == "/home/alice/.codex-work"


async def test_warm_remote_home_for_profile_noop_for_local_target(
    tmp_path: Path,
) -> None:
    runtime, _ = _runtime(tmp_path, codex=_codex_profiles())
    # No SSH, no cache write; a local target is a plain no-op.
    await runtime._warm_remote_home_for_profile(None, "codex", "work")
    assert runtime._remote_home_cache == {}


def test_overlay_rejects_tilde_config_dir_on_remote_with_cold_cache(
    tmp_path: Path,
) -> None:
    # No warm cache entry (target unreachable, or never resolved) still
    # rejects a ``~``-relative remote config_dir rather than launching under
    # a literal ``~`` dir.
    runtime, _ = _runtime(tmp_path, codex=_codex_profiles())
    target = SshLaunchTargetConfig(id="d", name="d", ssh_destination="u@d")
    with pytest.raises(HTTPException) as exc:
        runtime._apply_account_profile_env("codex", {}, "work", target)
    assert exc.value.status_code == 400


# ── Preset carry ────────────────────────────────────────────────────────────


def _preset(account_profile_id: str | None) -> SessionPresetRecord:
    now = datetime.now(UTC)
    return SessionPresetRecord(
        id="p1",
        name="p",
        spec=SessionPresetSpec(backend="codex", account_profile_id=account_profile_id),
        created_at=now,
        updated_at=now,
    )


def test_preset_supplies_profile_when_request_omits_it(tmp_path: Path) -> None:
    _, storage = _runtime(tmp_path)
    storage.create_session_preset(_preset("work"))
    resolved, _ = resolve_session_create_request(
        storage, SessionLaunchRequest(backend="codex", cwd="/tmp", preset_id="p1")
    )
    assert resolved.account_profile_id == "work"


def test_explicit_request_profile_wins_over_preset(tmp_path: Path) -> None:
    _, storage = _runtime(tmp_path)
    storage.create_session_preset(_preset("work"))
    resolved, _ = resolve_session_create_request(
        storage,
        SessionLaunchRequest(
            backend="codex", cwd="/tmp", preset_id="p1", account_profile_id="personal"
        ),
    )
    assert resolved.account_profile_id == "personal"


# ── Import request ──────────────────────────────────────────────────────────


def test_import_request_accepts_profile() -> None:
    req = CodexThreadImportRequest(thread_id="t", account_profile_id="work")
    assert req.account_profile_id == "work"


# ── Schedule ────────────────────────────────────────────────────────────────


async def test_schedule_persists_and_validates_profile(tmp_path: Path) -> None:
    # create_schedule arms an asyncio fire-timer, so it needs a running loop.
    runtime, storage = _runtime(tmp_path, codex=_codex_profiles())
    record = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend="codex", cwd="/tmp", delay_seconds=60, account_profile_id="work"
        )
    )
    stored = storage.get_schedule(record.id)
    assert stored is not None
    # Selection persisted; label resolved at create time. The config-dir itself
    # is resolved from the profile at fire time, not snapshotted here.
    assert stored.account_profile_id == "work"
    assert stored.account_profile_label == "Work"


def test_schedule_rejects_unknown_profile(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path, codex=_codex_profiles())
    with pytest.raises(HTTPException) as exc:
        runtime.scheduler.create_schedule(
            ScheduleCreateRequest(
                backend="codex", cwd="/tmp", delay_seconds=60, account_profile_id="nope"
            )
        )
    assert exc.value.status_code == 400
