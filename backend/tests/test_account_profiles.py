"""Account/config-dir profiles: config parsing, merge, redaction, capabilities.

Phase 1 of the account-switching RFC. Profiles are agent-owned config
(claude_code / codex), merged field-by-field per launch target, and surfaced as
redacted ``{id, label, config_dir_key}`` metadata on the read APIs — never the
config-dir path, expected account key, or transcript policy.
"""

from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from waypoint.api import _backend_descriptors, create_app
from waypoint.backends.account_profiles import (
    backend_hosts_account_profiles,
    redacted_profile_metadata,
    resolve_account_profiles,
)
from waypoint.backends.bootstrap import build_default_registry
from waypoint.settings import Settings


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate(overrides)


def _codex_profiles() -> dict[str, object]:
    return {
        "personal": {"label": "Personal", "config_dir": "~/.codex-personal"},
        "work": {
            "label": "Work",
            "config_dir": "~/.codex-work",
            "transcript_policy": "copy_thread_on_switch",
            "expected_account_key": "user@company.com",
        },
    }


def _target(**plugin_configs: object) -> dict[str, object]:
    return {
        "id": "devbox",
        "name": "devbox",
        "ssh_destination": "user@devbox",
        "plugin_configs": plugin_configs,
    }


# ── Parsing ───────────────────────────────────────────────────────────────


def test_global_profiles_parse_for_agent_backends() -> None:
    s = _settings(plugin_configs={"codex": {"account_profiles": _codex_profiles()}})
    profiles = resolve_account_profiles(s, "codex")
    assert sorted(profiles) == ["personal", "work"]
    assert profiles["work"].transcript_policy == "copy_thread_on_switch"


def test_symlink_shared_requires_shared_transcript_dir() -> None:
    with pytest.raises(ValidationError, match="symlink_shared"):
        _settings(
            plugin_configs={
                "claude_code": {
                    "account_profiles": {
                        "team": {
                            "label": "Team",
                            "config_dir": "~/.claude-team",
                            "transcript_policy": "symlink_shared",
                        }
                    }
                }
            }
        )


def test_symlink_shared_accepts_when_shared_dir_present() -> None:
    s = _settings(
        plugin_configs={
            "claude_code": {
                "account_profiles": {
                    "team": {
                        "label": "Team",
                        "config_dir": "~/.claude-team",
                        "transcript_policy": "symlink_shared",
                        "shared_transcript_dir": "~/.waypoint/agent-state/claude-projects",
                    }
                }
            }
        }
    )
    assert resolve_account_profiles(s, "claude_code")["team"].transcript_policy == (
        "symlink_shared"
    )


@pytest.mark.parametrize("backend", ["opencode", "claude_tty", "tmux"])
def test_account_profiles_rejected_for_non_agent_backends_global(backend: str) -> None:
    with pytest.raises(ValidationError):
        _settings(
            plugin_configs={
                backend: {
                    "account_profiles": {"x": {"label": "X", "config_dir": "~/x"}}
                }
            }
        )


def test_account_profiles_rejected_for_opencode_target_level() -> None:
    with pytest.raises(ValidationError):
        _settings(
            ssh_targets=[
                _target(
                    opencode={
                        "account_profiles": {"x": {"label": "X", "config_dir": "~/x"}}
                    }
                )
            ]
        )


# ── Merge ─────────────────────────────────────────────────────────────────


def test_target_override_wins_field_by_field_and_inherits_unset() -> None:
    s = _settings(
        plugin_configs={"codex": {"account_profiles": _codex_profiles()}},
        ssh_targets=[
            _target(
                codex={
                    "account_profiles": {
                        "work": {
                            "label": "Work on devbox",
                            "config_dir": "~/.codex-work-devbox",
                        }
                    }
                }
            )
        ],
    )
    merged = resolve_account_profiles(s, "codex", s.ssh_targets[0])
    work = merged["work"]
    # Explicitly-set target fields win...
    assert work.label == "Work on devbox"
    assert work.config_dir == "~/.codex-work-devbox"
    # ...unset fields fall back to the global (not the field default).
    assert work.transcript_policy == "copy_thread_on_switch"
    assert work.expected_account_key == "user@company.com"


def test_target_only_profile_id_is_added() -> None:
    s = _settings(
        plugin_configs={"codex": {"account_profiles": _codex_profiles()}},
        ssh_targets=[
            _target(
                codex={
                    "account_profiles": {
                        "lab": {"label": "Lab", "config_dir": "~/.codex-lab"}
                    }
                }
            )
        ],
    )
    merged = resolve_account_profiles(s, "codex", s.ssh_targets[0])
    assert sorted(merged) == ["lab", "personal", "work"]
    assert merged["lab"].label == "Lab"


def test_merge_does_not_mutate_global_profiles() -> None:
    s = _settings(
        plugin_configs={"codex": {"account_profiles": _codex_profiles()}},
        ssh_targets=[
            _target(
                codex={
                    "account_profiles": {
                        "work": {"label": "Work on devbox", "config_dir": "~/w"}
                    }
                }
            )
        ],
    )
    resolve_account_profiles(s, "codex", s.ssh_targets[0])
    assert resolve_account_profiles(s, "codex")["work"].label == "Work"


def test_hosts_only_agent_backends() -> None:
    s = _settings()
    assert backend_hosts_account_profiles(s, "codex")
    assert backend_hosts_account_profiles(s, "claude_code")
    assert not backend_hosts_account_profiles(s, "opencode")
    assert not backend_hosts_account_profiles(s, "claude_tty")


# ── Redaction ───────────────────────────────────────────────────────────────


def test_redacted_metadata_exposes_only_id_label_key() -> None:
    s = _settings(plugin_configs={"codex": {"account_profiles": _codex_profiles()}})
    meta = redacted_profile_metadata(s, "codex")
    assert {m["id"] for m in meta} == {"personal", "work"}
    for entry in meta:
        assert set(entry) == {"id", "label", "config_dir_key"}
        assert entry["config_dir_key"] == "CODEX_HOME"
    # Nothing sensitive leaks into the public payload.
    blob = repr(meta)
    assert "~/.codex" not in blob
    assert "company.com" not in blob
    assert "copy_thread_on_switch" not in blob


def test_backends_payload_carries_redacted_global_profiles() -> None:
    s = _settings(plugin_configs={"codex": {"account_profiles": _codex_profiles()}})
    desc = {d["id"]: d for d in _backend_descriptors(build_default_registry(), s)}
    assert {m["id"] for m in desc["codex"]["account_profiles"]} == {"personal", "work"}
    # Agent ids only: transports/opencode host nothing.
    assert desc["claude_tty"]["account_profiles"] == []
    assert desc["opencode"]["account_profiles"] == []
    # No target merge on the global catalogue; paths never appear.
    assert "~/.codex" not in repr(desc["codex"]["account_profiles"])


# ── Capability descriptor ────────────────────────────────────────────────────


def test_capability_config_dir_fields() -> None:
    caps = {p.id: p.capabilities for p in build_default_registry().all()}
    assert caps["claude_code"].config_dir_env_var == "CLAUDE_CONFIG_DIR"
    assert caps["claude_code"].native_thread_store == "projects"
    assert caps["codex"].config_dir_env_var == "CODEX_HOME"
    assert caps["codex"].native_thread_store == "sessions"
    # Transport inherits the agent's config-dir contract, can't drift.
    assert caps["claude_tty"].config_dir_env_var == "CLAUDE_CONFIG_DIR"
    assert caps["opencode"].config_dir_env_var is None
    assert caps["tmux"].config_dir_env_var is None


def test_supports_account_profile_with_restart_is_derived() -> None:
    caps = {p.id: p.capabilities for p in build_default_registry().all()}
    # Derived from (config-dir var present) AND (restart-applied settings).
    assert caps["claude_code"].supports_account_profile_with_restart is True
    assert caps["claude_tty"].supports_account_profile_with_restart is True
    assert caps["codex"].supports_account_profile_with_restart is True
    assert caps["opencode"].supports_account_profile_with_restart is False
    assert caps["tmux"].supports_account_profile_with_restart is False


# ── Route-level (in-process ASGI) ────────────────────────────────────────────


def _app_and_token(tmp_path: Path, **settings_kw: Any) -> tuple[Any, str]:
    settings = Settings(data_dir=tmp_path / "data", **settings_kw)
    app = create_app(settings)
    token = app.state.context.tokens.issue().token
    return app, token


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_api_backends_exposes_redacted_global_profiles(tmp_path: Path) -> None:
    app, token = _app_and_token(
        tmp_path,
        plugin_configs={"codex": {"account_profiles": _codex_profiles()}},
    )
    async with _client(app) as client:
        resp = await client.get(
            "/api/backends", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200
    backends = {b["id"]: b for b in resp.json()["backends"]}
    assert {m["id"] for m in backends["codex"]["account_profiles"]} == {
        "personal",
        "work",
    }
    assert backends["codex"]["capabilities"]["config_dir_env_var"] == "CODEX_HOME"
    # No config-dir path, expected key, or transcript policy on the wire.
    assert "~/.codex" not in resp.text
    assert "company.com" not in resp.text
    assert "copy_thread_on_switch" not in resp.text


async def test_api_me_exposes_target_merged_profiles(tmp_path: Path) -> None:
    app, token = _app_and_token(
        tmp_path,
        password="pw",
        plugin_configs={"codex": {"account_profiles": _codex_profiles()}},
        ssh_targets=[
            {
                "id": "devbox",
                "name": "devbox",
                "ssh_destination": "user@devbox",
                "plugin_configs": {
                    "codex": {
                        "account_profiles": {
                            "work": {
                                "label": "Work on devbox",
                                "config_dir": "~/.codex-work-devbox",
                            },
                            "lab": {"label": "Lab", "config_dir": "~/.codex-lab"},
                        }
                    }
                },
            }
        ],
    )
    async with _client(app) as client:
        resp = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    target = {t["id"]: t for t in resp.json()["launch_targets"]}["devbox"]
    codex_profiles = {
        m["id"]: m for m in target["account_profiles_by_backend"]["codex"]
    }
    assert set(codex_profiles) == {"personal", "work", "lab"}
    # Target override wins on label; target-only id present.
    assert codex_profiles["work"]["label"] == "Work on devbox"
    assert codex_profiles["lab"]["label"] == "Lab"
    # Transports/opencode host nothing, so they're absent from the map.
    assert "opencode" not in target["account_profiles_by_backend"]
    assert "~/.codex" not in resp.text
