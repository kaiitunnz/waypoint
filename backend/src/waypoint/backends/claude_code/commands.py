import asyncio
import importlib.resources
import json
import logging
from functools import cache
from typing import Any

import yaml

from waypoint.backends.capabilities import SlashCommandSpec
from waypoint.backends.claude_code.command_candidates import list_candidates
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
        else await asyncio.to_thread(list_candidates, cwd, claude_bin, config_dir)
    )
    return _records_to_completions(_records_from_candidates(candidates), prefix)


async def _list_remote_candidates(
    target: SshLaunchTargetConfig,
    cwd: str,
    claude_bin: str,
    config_dir: str | None,
) -> list[dict[str, Any]]:
    try:
        args = target.build_remote_exec_args(
            [
                "python3",
                "-",
                cwd or target.default_cwd,
                claude_bin,
                config_dir or "",
            ]
        )
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(_candidates_script()), timeout=15
        )
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


@cache
def _candidates_script() -> bytes:
    return (
        importlib.resources.files("waypoint.backends.claude_code")
        .joinpath("command_candidates.py")
        .read_bytes()
    )


def _records_from_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # Custom commands are named by path; skills by frontmatter ``name``, else
    # the skill directory. The first candidate per (source, name) wins.
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


def _parse_frontmatter(block: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(block) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
