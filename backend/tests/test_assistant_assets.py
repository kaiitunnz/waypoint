import os
from pathlib import Path

import pytest

from waypoint.assistant_assets import (
    ASSISTANT_ASSET_MANIFEST,
    AssistantAssetError,
    ensure_assistant_assets,
    resolve_assistant_asset_source,
    validate_assistant_asset_source,
)


def _write_source(root: Path) -> Path:
    assistant = root / ".agents" / "assistant"
    skills = root / ".agents" / "skills"
    assistant.mkdir(parents=True)
    (assistant / "AGENTS.md").write_text("# Assistant\n", encoding="utf-8")
    (assistant / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    waypoint = skills / "waypoint"
    waypoint.mkdir(parents=True)
    (waypoint / "SKILL.md").write_text(
        "---\nname: waypoint\ndescription: Manage sessions\n---\n",
        encoding="utf-8",
    )
    waypointctl = skills / "waypointctl"
    waypointctl.mkdir()
    (waypointctl / "SKILL.md").write_text(
        "---\nname: waypointctl\ndescription: Manage stack\n---\n",
        encoding="utf-8",
    )
    (root / ".claude").mkdir()
    (root / ".claude" / "skills").symlink_to("../.agents/skills")
    return root


def test_ensure_assistant_assets_prefers_symlinks(tmp_path) -> None:
    source_root = _write_source(tmp_path / "repo")
    workspace = tmp_path / "data" / "assistant"

    ensure_assistant_assets(workspace, source_root=source_root)

    assert (workspace / "AGENTS.md").is_symlink()
    assert (workspace / "AGENTS.md").resolve() == (
        source_root / ".agents" / "assistant" / "AGENTS.md"
    )
    assert (workspace / ".agents" / "skills").is_symlink()
    assert (workspace / ".agents" / "skills").resolve() == (
        source_root / ".agents" / "skills"
    )
    assert os.readlink(workspace / ".claude" / "skills") == "../.agents/skills"
    assert os.readlink(workspace / ".codex" / "skills") == "../.agents/skills"


def test_ensure_assistant_assets_repairs_stale_workspace_files(tmp_path) -> None:
    source_root = _write_source(tmp_path / "repo")
    workspace = tmp_path / "data" / "assistant"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("stale", encoding="utf-8")

    ensure_assistant_assets(workspace, source_root=source_root)

    assert (workspace / "AGENTS.md").is_symlink()
    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == "# Assistant\n"


def test_ensure_assistant_assets_copy_fallback_is_idempotent(tmp_path) -> None:
    source_root = _write_source(tmp_path / "repo")
    workspace = tmp_path / "data" / "assistant"

    ensure_assistant_assets(workspace, source_root=source_root, prefer_symlinks=False)
    manifest = workspace / ASSISTANT_ASSET_MANIFEST
    first_manifest = manifest.read_text(encoding="utf-8")

    ensure_assistant_assets(workspace, source_root=source_root, prefer_symlinks=False)

    assert (workspace / "AGENTS.md").is_file()
    assert not (workspace / "AGENTS.md").is_symlink()
    assert (workspace / ".agents" / "skills" / "waypoint" / "SKILL.md").is_file()
    assert manifest.read_text(encoding="utf-8") == first_manifest


def test_validate_assistant_asset_source_requires_skill_frontmatter(tmp_path) -> None:
    source_root = _write_source(tmp_path / "repo")
    (source_root / ".agents" / "skills" / "waypoint" / "SKILL.md").write_text(
        "---\nname: waypoint\n---\n",
        encoding="utf-8",
    )
    source = resolve_assistant_asset_source(source_root=source_root)

    with pytest.raises(AssistantAssetError, match="description"):
        validate_assistant_asset_source(source)


def test_validate_assistant_asset_source_requires_repo_claude_symlink(
    tmp_path,
) -> None:
    source_root = _write_source(tmp_path / "repo")
    (source_root / ".claude" / "skills").unlink()
    (source_root / ".claude" / "skills").write_text("not a link", encoding="utf-8")
    source = resolve_assistant_asset_source(source_root=source_root)

    with pytest.raises(AssistantAssetError, match="must be a symlink"):
        validate_assistant_asset_source(source)
