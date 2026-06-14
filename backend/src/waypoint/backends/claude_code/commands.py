import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import yaml

from waypoint.backends.capabilities import SlashCommandSpec
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import CommandCompletion, CompletionDispatch

log = logging.getLogger("waypoint.backends.claude_code.commands")

# A static baseline of Claude's built-in slash commands. The structured backend
# normally learns these from the SDK's ``system.init`` stream, but a
# tmux-transport Claude session never emits it — without this list such a session
# would surface no built-ins at all. Plain-text dispatch: typed into the CLI/pane.
CLAUDE_BUILTIN_SLASH_COMMANDS = (
    SlashCommandSpec(
        name="compact", description="Compact the conversation to free up context"
    ),
    SlashCommandSpec(name="clear", description="Clear the conversation history"),
    SlashCommandSpec(name="context", description="Show context window usage"),
    SlashCommandSpec(name="cost", description="Show token usage and cost"),
    SlashCommandSpec(name="export", description="Export the conversation"),
    SlashCommandSpec(name="memory", description="Edit Claude memory files"),
    SlashCommandSpec(name="init", description="Generate a CLAUDE.md for the project"),
    SlashCommandSpec(name="config", description="Open the settings panel"),
    SlashCommandSpec(name="help", description="List available commands"),
)

_REMOTE_SCRIPT = r"""
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

cwd = sys.argv[1]
claude_bin = sys.argv[2]


def frontmatter(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    data = {}
    for raw in parts[1].splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key in {"name", "description", "argument-hint"} and value:
            data[key] = value
    return data


def command_name(root, path):
    rel = Path(path).relative_to(root).with_suffix("")
    return "/".join(rel.parts)


def custom_commands():
    roots = [Path(cwd) / ".claude" / "commands", Path.home() / ".claude" / "commands"]
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            name = command_name(root, path)
            if not name or name in seen:
                continue
            meta = frontmatter(path)
            seen.add(name)
            yield {
                "name": name,
                "description": meta.get("description") if isinstance(meta.get("description"), str) else None,
                "argument_hint": meta.get("argument-hint") if isinstance(meta.get("argument-hint"), str) else None,
                "source": "custom_command",
                "path": str(path),
            }


def user_skills():
    # Workspace skills win over user skills on name collision: order
    # matters here.
    roots = [Path(cwd) / ".claude" / "skills", Path.home() / ".claude" / "skills"]
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/SKILL.md")):
            meta = frontmatter(path)
            name = meta.get("name") if isinstance(meta.get("name"), str) else None
            if not name:
                name = path.parent.name
            if not name or name in seen:
                continue
            seen.add(name)
            yield {
                "name": name,
                "description": meta.get("description") if isinstance(meta.get("description"), str) else None,
                "argument_hint": meta.get("argument-hint") if isinstance(meta.get("argument-hint"), str) else None,
                "source": "user_skill",
                "path": str(path),
            }


def plugin_inventory():
    try:
        completed = subprocess.run(
            [claude_bin, "plugin", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return []
    if completed.returncode != 0:
        return []
    try:
        payload = json.loads(completed.stdout)
    except Exception:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("plugins", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def plugin_skills():
    seen = set()
    for plugin in plugin_inventory():
        if not isinstance(plugin, dict) or plugin.get("enabled") is not True:
            continue
        install_path = plugin.get("installPath")
        if not isinstance(install_path, str):
            continue
        for path in sorted(glob.glob(os.path.join(install_path, "skills", "*", "SKILL.md"))):
            meta = frontmatter(path)
            name = meta.get("name")
            if not isinstance(name, str) or not name:
                name = Path(path).parent.name
            if not name or name in seen:
                continue
            seen.add(name)
            description = meta.get("description")
            yield {
                "name": name,
                "description": description if isinstance(description, str) else None,
                "argument_hint": meta.get("argument-hint") if isinstance(meta.get("argument-hint"), str) else None,
                "source": "plugin_skill",
                "path": path,
            }


print(json.dumps([*custom_commands(), *user_skills(), *plugin_skills()]))
"""


async def list_claude_command_completions(
    *,
    cwd: str,
    claude_bin: str,
    prefix: str,
    launch_target: SshLaunchTargetConfig | None = None,
) -> list[CommandCompletion]:
    records = (
        await _list_remote_records(launch_target, cwd, claude_bin)
        if launch_target is not None
        else await _list_local_records(cwd, claude_bin)
    )
    return _records_to_completions(records, prefix)


async def _list_local_records(cwd: str, claude_bin: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    records.extend(_local_custom_commands(cwd))
    records.extend(_local_user_skills(cwd))
    records.extend(await _local_plugin_skills(claude_bin))
    return records


def _local_custom_commands(cwd: str) -> list[dict[str, Any]]:
    roots = [
        Path(cwd).expanduser() / ".claude" / "commands",
        Path.home() / ".claude" / "commands",
    ]
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            name = _command_name(root, path)
            if not name or name in seen:
                continue
            meta = _frontmatter(path)
            seen.add(name)
            records.append(
                {
                    "name": name,
                    "description": _string_or_none(meta.get("description")),
                    "argument_hint": _string_or_none(meta.get("argument-hint")),
                    "source": "custom_command",
                    "path": str(path),
                }
            )
    return records


def _local_user_skills(cwd: str) -> list[dict[str, Any]]:
    # Workspace `<cwd>/.claude/skills/` wins over `~/.claude/skills/` on
    # name collision, matching how the Claude CLI itself resolves
    # overlapping skill names.
    roots = [
        Path(cwd).expanduser() / ".claude" / "skills",
        Path.home() / ".claude" / "skills",
    ]
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/SKILL.md")):
            meta = _frontmatter(path)
            name = _string_or_none(meta.get("name")) or path.parent.name
            if not name or name in seen:
                continue
            seen.add(name)
            records.append(
                {
                    "name": name,
                    "description": _string_or_none(meta.get("description")),
                    "argument_hint": _string_or_none(meta.get("argument-hint")),
                    "source": "user_skill",
                    "path": str(path),
                }
            )
    return records


async def _local_plugin_skills(claude_bin: str) -> list[dict[str, Any]]:
    try:
        proc = await asyncio.create_subprocess_exec(
            claude_bin,
            "plugin",
            "list",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError:
        return []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for plugin in _plugin_items(payload):
        if plugin.get("enabled") is not True:
            continue
        install_path = plugin.get("installPath")
        if not isinstance(install_path, str):
            continue
        for path in sorted(Path(install_path).glob("skills/*/SKILL.md")):
            meta = _frontmatter(path)
            name = _string_or_none(meta.get("name")) or path.parent.name
            if not name or name in seen:
                continue
            seen.add(name)
            records.append(
                {
                    "name": name,
                    "description": _string_or_none(meta.get("description")),
                    "argument_hint": _string_or_none(meta.get("argument-hint")),
                    "source": "plugin_skill",
                    "path": str(path),
                }
            )
    return records


async def _list_remote_records(
    target: SshLaunchTargetConfig,
    cwd: str,
    claude_bin: str,
) -> list[dict[str, Any]]:
    try:
        args = target.build_remote_exec_args(
            ["python3", "-c", _REMOTE_SCRIPT, cwd or target.default_cwd, claude_bin]
        )
    except (FileNotFoundError, OSError) as exc:
        log.warning("failed to build Claude command discovery SSH argv: %s", exc)
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    except Exception as exc:
        log.warning("failed to run remote Claude command discovery: %s", exc)
        return []
    if proc.returncode != 0:
        log.warning(
            "remote Claude command discovery failed: %s",
            stderr.decode("utf-8", errors="replace").strip(),
        )
        return []
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError:
        return []
    return (
        [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, list)
        else []
    )


def _records_to_completions(
    records: list[dict[str, Any]],
    prefix: str,
) -> list[CommandCompletion]:
    normalized_prefix = prefix if prefix.startswith("/") else f"/{prefix}"
    completions: list[CommandCompletion] = []
    seen: set[str] = set()
    for record in records:
        name = _string_or_none(record.get("name"))
        if not name:
            continue
        command = f"/{name}"
        if normalized_prefix != "/" and not command.startswith(normalized_prefix):
            continue
        if command in seen:
            continue
        source = _string_or_none(record.get("source")) or "custom_command"
        kind = "skill" if source in {"plugin_skill", "user_skill"} else "command"
        completions.append(
            CommandCompletion(
                id=f"claude_code:{source}:{name}",
                trigger="/",
                replacement=f"{command} ",
                name=name,
                description=_string_or_none(record.get("description")),
                kind=kind,
                source=source,
                dispatch=CompletionDispatch.PLAIN_TEXT,
                argument_hint=_string_or_none(record.get("argument_hint")),
                metadata={"path": record.get("path")} if record.get("path") else {},
            )
        )
        seen.add(command)
    return completions


def _frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _command_name(root: Path, path: Path) -> str:
    try:
        rel = path.relative_to(root).with_suffix("")
    except ValueError:
        return path.stem
    return "/".join(part for part in rel.parts if part)


def _plugin_items(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("plugins", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
