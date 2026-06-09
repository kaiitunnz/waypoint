import hashlib
import json
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ASSISTANT_ASSET_MANIFEST = ".waypoint-assistant-assets.json"
ASSISTANT_SYMLINK_TARGET = "../.agents/skills"
ASSET_SCHEMA_VERSION = 1


class AssistantAssetError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssistantAssetSource:
    root: Path
    assistant_dir: Path
    skills_dir: Path


def ensure_assistant_assets(
    workspace: Path,
    *,
    config_path: Path | None = None,
    source_root: Path | None = None,
    prefer_symlinks: bool = True,
) -> Path:
    source = resolve_assistant_asset_source(
        config_path=config_path,
        source_root=source_root,
    )
    validate_assistant_asset_source(source)
    workspace.mkdir(parents=True, exist_ok=True)
    if prefer_symlinks:
        try:
            _ensure_symlink_assets(workspace, source)
            _write_manifest_if_changed(
                workspace,
                {
                    "schema_version": ASSET_SCHEMA_VERSION,
                    "mode": "symlink",
                    "source_root": str(source.root),
                },
            )
            return workspace
        except OSError:
            pass
    _ensure_copied_assets(workspace, source)
    return workspace


def resolve_assistant_asset_source(
    *,
    config_path: Path | None = None,
    source_root: Path | None = None,
) -> AssistantAssetSource:
    candidates: list[Path] = []
    if source_root is not None:
        candidates.append(source_root)
    env_root = os.environ.get("WAYPOINT_ASSISTANT_ASSETS_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    waypoint_home = os.environ.get("WAYPOINT_HOME")
    if waypoint_home:
        candidates.append(Path(waypoint_home))
    candidates.append(Path(__file__).resolve().parents[3])
    if config_path is not None:
        expanded = config_path.expanduser().resolve()
        candidates.extend([expanded.parent, *expanded.parents])

    seen: set[Path] = set()
    for candidate in candidates:
        root = candidate.expanduser().resolve()
        if root in seen:
            continue
        seen.add(root)
        source = AssistantAssetSource(
            root=root,
            assistant_dir=root / ".agents" / "assistant",
            skills_dir=root / ".agents" / "skills",
        )
        if source.assistant_dir.is_dir() and source.skills_dir.is_dir():
            return source
    tried = ", ".join(str(path.expanduser()) for path in candidates)
    raise AssistantAssetError(f"assistant asset source not found; tried: {tried}")


def validate_assistant_asset_source(source: AssistantAssetSource) -> None:
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = source.assistant_dir / name
        if not path.is_file():
            raise AssistantAssetError(f"missing assistant bootstrap file: {path}")
    if not source.skills_dir.is_dir():
        raise AssistantAssetError(
            f"missing assistant skills directory: {source.skills_dir}"
        )
    skill_names: set[str] = set()
    skill_dirs = [
        path
        for path in sorted(source.skills_dir.iterdir())
        if path.is_dir() and not path.name.startswith(".")
    ]
    if not skill_dirs:
        raise AssistantAssetError(f"no assistant skills found in {source.skills_dir}")
    for skill_dir in skill_dirs:
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            raise AssistantAssetError(f"missing skill file: {skill_file}")
        metadata = _skill_frontmatter(skill_file)
        name = _required_metadata_string(metadata, "name", skill_file)
        _required_metadata_string(metadata, "description", skill_file)
        if name in skill_names:
            raise AssistantAssetError(f"duplicate assistant skill name: {name}")
        skill_names.add(name)
    repo_claude_skills = source.root / ".claude" / "skills"
    if not repo_claude_skills.is_symlink():
        raise AssistantAssetError(
            f"repo .claude/skills must be a symlink: {repo_claude_skills}"
        )
    if os.readlink(repo_claude_skills) != ASSISTANT_SYMLINK_TARGET:
        raise AssistantAssetError(
            f"repo .claude/skills must point to {ASSISTANT_SYMLINK_TARGET!r}"
        )


def _ensure_symlink_assets(workspace: Path, source: AssistantAssetSource) -> None:
    _ensure_symlink(
        workspace / "AGENTS.md",
        source.assistant_dir / "AGENTS.md",
    )
    _ensure_symlink(
        workspace / "CLAUDE.md",
        source.assistant_dir / "CLAUDE.md",
    )
    _ensure_symlink(
        workspace / ".agents" / "skills",
        source.skills_dir,
    )
    _ensure_symlink(
        workspace / ".claude" / "skills",
        Path(ASSISTANT_SYMLINK_TARGET),
    )
    _ensure_symlink(
        workspace / ".codex" / "skills",
        Path(ASSISTANT_SYMLINK_TARGET),
    )


def _ensure_copied_assets(workspace: Path, source: AssistantAssetSource) -> None:
    signature = _asset_signature(
        [
            source.assistant_dir / "AGENTS.md",
            source.assistant_dir / "CLAUDE.md",
            source.skills_dir,
        ]
    )
    manifest = _read_manifest(workspace)
    if (
        manifest.get("schema_version") == ASSET_SCHEMA_VERSION
        and manifest.get("mode") == "copy"
        and manifest.get("source_root") == str(source.root)
        and manifest.get("signature") == signature
        and (workspace / "AGENTS.md").is_file()
        and (workspace / "CLAUDE.md").is_file()
        and (workspace / ".agents" / "skills").is_dir()
        and _skill_entry_exists(workspace / ".claude" / "skills")
        and _skill_entry_exists(workspace / ".codex" / "skills")
    ):
        return
    _replace_file(workspace / "AGENTS.md", source.assistant_dir / "AGENTS.md")
    _replace_file(workspace / "CLAUDE.md", source.assistant_dir / "CLAUDE.md")
    _replace_tree(workspace / ".agents" / "skills", source.skills_dir)
    _ensure_skill_entry(workspace / ".claude" / "skills", source.skills_dir)
    _ensure_skill_entry(workspace / ".codex" / "skills", source.skills_dir)
    _write_manifest_if_changed(
        workspace,
        {
            "schema_version": ASSET_SCHEMA_VERSION,
            "mode": "copy",
            "source_root": str(source.root),
            "signature": signature,
            "written_at": datetime.now(UTC).isoformat(),
        },
    )


def _skill_entry_exists(path: Path) -> bool:
    return path.is_symlink() or path.is_dir()


def _ensure_skill_entry(path: Path, source: Path) -> None:
    try:
        _ensure_symlink(path, Path(ASSISTANT_SYMLINK_TARGET))
    except OSError:
        _replace_tree(path, source)


def _ensure_symlink(path: Path, target: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() and Path(os.readlink(path)) == target:
        return
    _remove_path(path)
    path.symlink_to(
        target,
        target_is_directory=target.name != "AGENTS.md" and target.name != "CLAUDE.md",
    )


def _replace_file(destination: Path, source: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _remove_path(destination)
    shutil.copy2(source, destination)


def _replace_tree(destination: Path, source: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    _remove_path(tmp)
    shutil.copytree(source, tmp, symlinks=True)
    _remove_path(destination)
    tmp.replace(destination)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _read_manifest(workspace: Path) -> dict[str, Any]:
    path = workspace / ASSISTANT_ASSET_MANIFEST
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_manifest_if_changed(workspace: Path, payload: dict[str, Any]) -> None:
    path = workspace / ASSISTANT_ASSET_MANIFEST
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")


def _asset_signature(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        if path.is_dir():
            files = sorted(item for item in path.rglob("*") if item.is_file())
        else:
            files = [path]
        for file_path in files:
            digest.update(str(file_path).encode("utf-8"))
            digest.update(b"\0")
            digest.update(file_path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _skill_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise AssistantAssetError(f"missing skill frontmatter: {path}")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise AssistantAssetError(f"invalid skill frontmatter: {path}")
    try:
        payload = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise AssistantAssetError(f"invalid skill frontmatter: {path}") from exc
    if not isinstance(payload, dict):
        raise AssistantAssetError(f"invalid skill frontmatter: {path}")
    return payload


def _required_metadata_string(
    metadata: dict[str, Any],
    key: str,
    path: Path,
) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AssistantAssetError(f"skill {path} missing required {key!r}")
    return value.strip()
