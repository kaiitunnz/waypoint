"""Claude command and skill files visible to a session.

Local discovery calls ``list_candidates`` in-process; remote discovery pipes
this file to ``python3 -`` on the SSH target, which prints the candidates as
one JSON line. The module is stdlib-only and Python 3.8-compatible.
"""

# Required for PEP 585/604 annotations on a remote Python 3.8 interpreter.
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PLUGIN_LIST_TIMEOUT_SECONDS = 10


def list_candidates(
    cwd: str, claude_bin: str, config_dir: str | None
) -> list[dict[str, Any]]:
    # The workspace ``.claude`` dir precedes the account config dir.
    project_root = Path(os.path.expanduser(cwd)) / ".claude"
    user_root = Path(os.path.expanduser(config_dir or "~/.claude"))
    candidates: list[dict[str, Any]] = []
    for root in (project_root / "commands", user_root / "commands"):
        if root.is_dir():
            candidates.extend(
                _candidate("custom_command", _command_name(root, path), path)
                for path in sorted(root.rglob("*.md"))
            )
    for root in (project_root / "skills", user_root / "skills"):
        if root.is_dir():
            candidates.extend(
                _candidate("user_skill", path.parent.name, path)
                for path in sorted(root.glob("*/SKILL.md"))
            )
    for install_path in _enabled_plugin_paths(claude_bin, config_dir):
        candidates.extend(
            _candidate("plugin_skill", path.parent.name, path)
            for path in sorted(Path(install_path).glob("skills/*/SKILL.md"))
        )
    return candidates


def _candidate(source: str, name_hint: str, path: Path) -> dict[str, Any]:
    return {
        "source": source,
        "name_hint": name_hint,
        "path": str(path),
        "frontmatter": _frontmatter_block(path),
    }


def _frontmatter_block(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    return parts[1] if len(parts) == 3 else None


def _command_name(root: Path, path: Path) -> str:
    try:
        rel = path.relative_to(root).with_suffix("")
    except ValueError:
        return path.stem
    return "/".join(part for part in rel.parts if part)


def _enabled_plugin_paths(claude_bin: str, config_dir: str | None) -> list[str]:
    # `claude plugin list` reports the plugins enabled for its CLAUDE_CONFIG_DIR.
    env = {**os.environ, "CLAUDE_CONFIG_DIR": config_dir} if config_dir else None
    try:
        completed = subprocess.run(
            [claude_bin, "plugin", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=PLUGIN_LIST_TIMEOUT_SECONDS,
            env=env,
            check=False,
        )
        payload = json.loads(completed.stdout) if completed.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    if isinstance(payload, dict):
        payload = next(
            (
                payload[key]
                for key in ("plugins", "items", "data")
                if isinstance(payload.get(key), list)
            ),
            [],
        )
    if not isinstance(payload, list):
        return []
    return [
        plugin["installPath"]
        for plugin in payload
        if isinstance(plugin, dict)
        and plugin.get("enabled") is True
        and isinstance(plugin.get("installPath"), str)
    ]


if __name__ == "__main__":
    print(json.dumps(list_candidates(sys.argv[1], sys.argv[2], sys.argv[3] or None)))
