import asyncio
import json
import logging
import os
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

# Lists candidate command/skill files on a remote target. It returns each file's
# raw frontmatter block and leaves parsing, naming, and de-duplication to
# ``_records_from_candidates`` so remote results match local ones exactly.
_REMOTE_SCRIPT = r"""
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

cwd = os.path.expanduser(sys.argv[1])
claude_bin = sys.argv[2]
# A profile-scoped session's user commands, skills, and plugins live under its
# CLAUDE_CONFIG_DIR; `claude plugin list` below inherits the same variable.
user_root = Path(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"))


def frontmatter_block(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    return parts[1] if len(parts) == 3 else None


def candidate(source, name_hint, path):
    return {
        "source": source,
        "name_hint": name_hint,
        "path": str(path),
        "frontmatter": frontmatter_block(path),
    }


def custom_commands():
    for root in (Path(cwd) / ".claude" / "commands", user_root / "commands"):
        if root.is_dir():
            for path in sorted(root.rglob("*.md")):
                name = "/".join(path.relative_to(root).with_suffix("").parts)
                yield candidate("custom_command", name, path)


def user_skills():
    for root in (Path(cwd) / ".claude" / "skills", user_root / "skills"):
        if root.is_dir():
            for path in sorted(root.glob("*/SKILL.md")):
                yield candidate("user_skill", path.parent.name, path)


def plugin_inventory():
    try:
        completed = subprocess.run(
            [claude_bin, "plugin", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return json.loads(completed.stdout) if completed.returncode == 0 else []
    except Exception:
        return []


def plugin_skills():
    payload = plugin_inventory()
    if isinstance(payload, dict):
        payload = next(
            (payload[k] for k in ("plugins", "items", "data") if isinstance(payload.get(k), list)),
            [],
        )
    for plugin in payload if isinstance(payload, list) else []:
        if not isinstance(plugin, dict) or plugin.get("enabled") is not True:
            continue
        install_path = plugin.get("installPath")
        if not isinstance(install_path, str):
            continue
        for path in sorted(glob.glob(os.path.join(install_path, "skills", "*", "SKILL.md"))):
            yield candidate("plugin_skill", Path(path).parent.name, path)


print(json.dumps([*custom_commands(), *user_skills(), *plugin_skills()]))
"""


def _user_config_root(config_dir: str | None) -> Path:
    """The session's account config dir (a profile's CLAUDE_CONFIG_DIR), else ~/.claude.

    User commands/skills live under it, so completion for a profile-scoped
    session must scan the profile's dir, not the default account's.
    """
    return Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"


async def list_claude_command_completions(
    *,
    cwd: str,
    claude_bin: str,
    prefix: str,
    launch_target: SshLaunchTargetConfig | None = None,
    config_dir: str | None = None,
) -> list[CommandCompletion]:
    candidates = (
        await _list_remote_candidates(launch_target, cwd, claude_bin, config_dir)
        if launch_target is not None
        else await _list_local_candidates(cwd, claude_bin, config_dir)
    )
    return _records_to_completions(_records_from_candidates(candidates), prefix)


async def _list_local_candidates(
    cwd: str, claude_bin: str, config_dir: str | None = None
) -> list[dict[str, Any]]:
    user_root = _user_config_root(config_dir)
    project_root = Path(cwd).expanduser() / ".claude"
    candidates: list[dict[str, Any]] = []
    for root in (project_root / "commands", user_root / "commands"):
        if root.is_dir():
            candidates.extend(
                _candidate("custom_command", _command_name(root, path), path)
                for path in sorted(root.rglob("*.md"))
            )
    # Workspace `<cwd>/.claude/skills/` wins over the account `skills/` on
    # name collision, matching how the Claude CLI itself resolves
    # overlapping skill names.
    for root in (project_root / "skills", user_root / "skills"):
        if root.is_dir():
            candidates.extend(
                _candidate("user_skill", path.parent.name, path)
                for path in sorted(root.glob("*/SKILL.md"))
            )
    candidates.extend(await _local_plugin_skill_candidates(claude_bin, config_dir))
    return candidates


async def _local_plugin_skill_candidates(
    claude_bin: str, config_dir: str | None = None
) -> list[dict[str, Any]]:
    # `claude plugin list` reports the plugins enabled for a config dir, so run
    # it under the session's CLAUDE_CONFIG_DIR (a profile's) rather than the
    # backend's default account.
    env = {**os.environ, "CLAUDE_CONFIG_DIR": config_dir} if config_dir else None
    try:
        proc = await asyncio.create_subprocess_exec(
            claude_bin,
            "plugin",
            "list",
            "--json",
            env=env,
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
    candidates: list[dict[str, Any]] = []
    for plugin in _plugin_items(payload):
        if plugin.get("enabled") is not True:
            continue
        install_path = plugin.get("installPath")
        if not isinstance(install_path, str):
            continue
        candidates.extend(
            _candidate("plugin_skill", path.parent.name, path)
            for path in sorted(Path(install_path).glob("skills/*/SKILL.md"))
        )
    return candidates


async def _list_remote_candidates(
    target: SshLaunchTargetConfig,
    cwd: str,
    claude_bin: str,
    config_dir: str | None = None,
) -> list[dict[str, Any]]:
    try:
        args = target.build_remote_exec_args(
            ["python3", "-c", _REMOTE_SCRIPT, cwd or target.default_cwd, claude_bin],
            extra_env={"CLAUDE_CONFIG_DIR": config_dir} if config_dir else None,
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


def _candidate(source: str, name_hint: str, path: Path) -> dict[str, Any]:
    return {
        "source": source,
        "name_hint": name_hint,
        "path": str(path),
        "frontmatter": _frontmatter_block(path),
    }


def _records_from_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # Custom commands are named by their path; skills by their frontmatter
    # ``name``, falling back to the skill directory. Within a source the first
    # candidate wins, so candidate order encodes root precedence.
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        source = _string_or_none(item.get("source"))
        name_hint = _string_or_none(item.get("name_hint"))
        if source is None or name_hint is None:
            continue
        block = item.get("frontmatter")
        meta = _parse_frontmatter(block) if isinstance(block, str) else {}
        name = (
            name_hint
            if source == "custom_command"
            else _string_or_none(meta.get("name")) or name_hint
        )
        if (source, name) in seen:
            continue
        seen.add((source, name))
        records.append(
            {
                "name": name,
                "description": _string_or_none(meta.get("description")),
                "argument_hint": _string_or_none(meta.get("argument-hint")),
                "source": source,
                "path": item.get("path"),
            }
        )
    return records


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


def _frontmatter_block(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    return parts[1] if len(parts) == 3 else None


def _parse_frontmatter(block: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(block) or {}
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
