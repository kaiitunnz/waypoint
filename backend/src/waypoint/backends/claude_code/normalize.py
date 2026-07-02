"""Claude Code stream-json → canonical-event normalisation helpers.

Pure functions extracted from the Claude CLI adapter so the wire-shape
contract (status events, compact boundaries, rate-limit messages,
approval prompts, content-block normalisation) is testable without
spinning up the streaming pipeline. The adapter still owns the
session-state mutations (terminal fragments, streamed_tool_result_ids,
pending control requests) — those aren't normalisation, they're per
session bookkeeping.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from waypoint.schemas import SessionStatus


def format_status_event(event: dict[str, Any]) -> tuple[str, SessionStatus]:
    status_label = event.get("status")
    compact_result = event.get("compact_result")
    if status_label == "compacting":
        return "Compacting context…", SessionStatus.RUNNING
    if compact_result is not None:
        return (
            f"Context compaction {compact_result}",
            (
                SessionStatus.IDLE
                if compact_result == "success"
                else SessionStatus.ERROR
            ),
        )
    return "", SessionStatus.RUNNING


def format_compact_boundary(metadata: dict[str, Any]) -> str:
    pre = metadata.get("pre_tokens")
    post = metadata.get("post_tokens")
    duration_ms = metadata.get("duration_ms")
    trigger = metadata.get("trigger") or "manual"
    parts = [f"Context compacted ({trigger})"]
    if pre is not None and post is not None:
        parts.append(f"{pre} → {post} tokens")
    if duration_ms is not None:
        parts.append(f"{duration_ms} ms")
    return " · ".join(parts)


def format_rate_limit(info: dict[str, Any]) -> str:
    status = info.get("status", "unknown")
    rl_type = info.get("rate_limit_type", "")
    return f"Rate limit ({rl_type}): {status}".strip()


def format_approval_text(payload: dict[str, Any]) -> str:
    tool_name = payload.get("tool_name") or "tool"
    tool_input = payload.get("tool_input") or {}
    if tool_name == "Bash":
        command = tool_input.get("command") or ""
        return f"Approve Bash command:\n{command}"
    if tool_name in {"Edit", "Write", "MultiEdit"}:
        path = tool_input.get("file_path") or tool_input.get("path") or ""
        return f"Approve {tool_name} on {path}"
    if tool_name == "ExitPlanMode":
        # Plan text is already rendered as a markdown agent_output
        # above this card — keep this prompt compact to avoid
        # duplication.
        return "Approve plan and exit plan mode"
    if tool_name in {"Task", "Agent"}:
        # The prompt body can be many kilobytes; the frontend
        # renders it as markdown from metadata.tool_input.prompt
        # instead of dumping JSON here.
        description = str(tool_input.get("description") or "").strip()
        subagent = str(tool_input.get("subagent_type") or "").strip()
        label = description or "subagent task"
        if subagent:
            label = f"{label} (via {subagent})"
        return f"Approve subagent task: {label}"
    if tool_name == "WebFetch":
        url = str(tool_input.get("url") or "").strip()
        return f"Approve WebFetch: {url}" if url else "Approve WebFetch"
    if tool_name == "WebSearch":
        query = str(tool_input.get("query") or "").strip()
        return f"Approve WebSearch: {query}" if query else "Approve WebSearch"
    if tool_name == "NotebookEdit":
        path = str(tool_input.get("notebook_path") or "").strip()
        return f"Approve NotebookEdit on {path}" if path else "Approve NotebookEdit"
    if tool_name in {"Workflow", "RunWorkflow"}:
        # The workflow script is rendered in the approval card body; keep this
        # prompt compact to avoid duplicating it.
        return "Approve dynamic workflow"
    return f"Approve {tool_name}: {json.dumps(tool_input)[:240]}"


def iter_content_blocks(content: Any) -> list[dict[str, Any]]:
    # Claude Code normally streams message content as a list of typed
    # blocks, but synthetic turns (notably the user echo after
    # /compact) can arrive as a bare string or a list mixing strings
    # and dicts. Coerce everything to a list of dicts so callers can
    # rely on .get().
    if not content:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    blocks: list[dict[str, Any]] = []
    for entry in content:
        if isinstance(entry, dict):
            blocks.append(entry)
        elif isinstance(entry, str):
            blocks.append({"type": "text", "text": entry})
    return blocks


def is_injected_user_turn(content: Any) -> bool:
    """Return True for harness-injected user turns that must not surface as chat.

    Shared by the live tty-tail normalizer and the historical thread-import
    converter, both of which read the same on-disk transcript records.
    """
    if isinstance(content, str):
        stripped = content.lstrip()
        return stripped.startswith("<task-notification>") or stripped.startswith(
            "This session is being continued"
        )
    return False


def stringify_tool_result(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for entry in content:
            if isinstance(entry, dict):
                if entry.get("type") == "text" and isinstance(entry.get("text"), str):
                    parts.append(entry["text"])
                elif "text" in entry:
                    parts.append(str(entry["text"]))
                else:
                    parts.append(json.dumps(entry))
            else:
                parts.append(str(entry))
        return "\n".join(parts)
    return json.dumps(content)


# ─── Task tools (Claude Code >= v2.1.142) ───────────────────────────────────
#
# Newer Claude Code tracks todos through structured Task tools instead of the
# single TodoWrite call. Where TodoWrite rewrote the whole `todos` array on
# every invocation, the Task tools split it up: TaskCreate adds one item,
# TaskUpdate patches one item by id, and TaskGet/TaskList read the list back.
# The frontend's todo card still expects a full snapshot per event, so we fold
# the incremental stream back into one here. See docs/coding_agent_plugins.md
# and https://code.claude.com/docs/en/agent-sdk/todo-tracking.

TASK_TOOL_NAMES = frozenset({"TaskCreate", "TaskUpdate", "TaskGet", "TaskList"})

_VALID_TASK_STATUSES = frozenset({"pending", "in_progress", "completed"})


def normalize_task_status(value: Any) -> str:
    return value if value in _VALID_TASK_STATUSES else "pending"


@dataclass
class TaskItem:
    content: str
    status: str = "pending"
    active_form: str | None = None
    description: str | None = None


@dataclass
class TaskListTracker:
    """Reconstructs a TodoWrite-style snapshot from the incremental Task stream.

    ``TaskCreate`` learns its assigned id only from the matching tool_result,
    so the adapter stitches the create input to that id before calling
    :meth:`create`. ``TaskUpdate`` carries the id in its input and maps onto
    :meth:`update`; ``status == "deleted"`` removes the item. Insertion order is
    preserved so the rendered list stays stable across updates.
    """

    tasks: dict[str, TaskItem] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.tasks

    def create(
        self,
        task_id: str,
        *,
        content: str,
        active_form: str | None = None,
        description: str | None = None,
        status: str = "pending",
    ) -> None:
        self.tasks[task_id] = TaskItem(
            content=content,
            status=normalize_task_status(status),
            active_form=active_form,
            description=description,
        )

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        content: str | None = None,
        active_form: str | None = None,
        description: str | None = None,
    ) -> None:
        if status == "deleted":
            self.tasks.pop(task_id, None)
            return
        task = self.tasks.get(task_id)
        if task is None:
            # An update for a task we never saw created — e.g. a resumed
            # session whose creates predate this process. Materialise a stub so
            # the item still appears, but only when the patch carries something
            # to show.
            if content is None and status is None:
                return
            task = TaskItem(content=content or "")
            self.tasks[task_id] = task
        if status is not None:
            task.status = normalize_task_status(status)
        if content is not None:
            task.content = content
        if active_form is not None:
            task.active_form = active_form
        if description is not None:
            task.description = description

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "content": task.content,
                "status": task.status,
                "activeForm": task.active_form,
                "description": task.description,
            }
            for task in self.tasks.values()
        ]


_TASK_CREATED_RE = re.compile(r"[Tt]ask #(\d+)")


def extract_created_task_id(block: dict[str, Any]) -> str | None:
    """Pull the assigned task id out of a ``TaskCreate`` tool_result.

    Real Claude Code returns a plain string ``"Task #N created successfully:
    <subject>"`` whose ``N`` matches the ``taskId`` later passed to
    ``TaskUpdate``. The Agent SDK docs instead describe a structured
    ``{"task": {"id": ...}}`` payload. Try the structured form first (so we
    track newer shapes if CC adopts them), then fall back to parsing ``#N`` out
    of the result text.
    """
    for payload in _iter_result_payloads(block.get("content")):
        task = payload.get("task")
        if isinstance(task, dict) and task.get("id"):
            return str(task["id"])
        task_id = payload.get("id") or payload.get("taskId")
        if task_id:
            return str(task_id)
    match = _TASK_CREATED_RE.search(stringify_tool_result(block.get("content")))
    return match.group(1) if match else None


def _iter_result_payloads(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, dict):
        return [content]
    if isinstance(content, str):
        parsed = _try_json(content)
        return [parsed] if isinstance(parsed, dict) else []
    if isinstance(content, list):
        payloads: list[dict[str, Any]] = []
        for entry in content:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "text" and isinstance(entry.get("text"), str):
                parsed = _try_json(entry["text"])
                if isinstance(parsed, dict):
                    payloads.append(parsed)
            else:
                payloads.append(entry)
        return payloads
    return []


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def format_task_snapshot(todos: list[dict[str, Any]]) -> str:
    if not todos:
        return "Todos cleared"
    glyphs = {"completed": "[x]", "in_progress": "[~]", "pending": "[ ]"}
    lines: list[str] = []
    for todo in todos:
        status = todo.get("status", "pending")
        active_form = todo.get("activeForm")
        text = (
            active_form
            if status == "in_progress" and active_form
            else todo.get("content", "")
        )
        lines.append(f"{glyphs.get(status, '[ ]')} {text}")
    return "\n".join(lines)
