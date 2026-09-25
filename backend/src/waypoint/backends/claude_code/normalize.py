"""Claude Code stream-json → canonical-event normalisation helpers.

Pure functions extracted from the Claude CLI adapter so the wire-shape
contract (status events, compact boundaries, rate-limit messages,
approval prompts, content-block normalisation) is testable without
spinning up the streaming pipeline. The adapter still owns the
session-state mutations (terminal fragments, streamed_tool_result_ids,
pending control requests) — those aren't normalisation, they're per
session bookkeeping.
"""

import hashlib
import html
import json
import os
import re
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from waypoint.schemas import AttachmentOrigin, SessionStatus


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


# ─── Task notifications (Claude Code native transcript) ──────────────────────
#
# Claude records subagent/Agent completion, Monitor events and terminal state,
# and background-command completion as a synthetic ``user`` record with
# ``origin.kind == "task-notification"`` and a ``<task-notification>…</…>``
# string payload. These are not human turns. Both the live tailer and history
# import normalize them into a standalone SYSTEM_NOTE event carrying a versioned,
# backend-private metadata contract.

TASK_NOTIFICATION_METHOD = "claude.task_notification"
TASK_NOTIFICATION_ITEM_TYPE = "task_notification"
TASK_NOTIFICATION_VERSION = 1
# Largest body field (result/event/note) kept verbatim in event metadata, which
# every client receives whether or not the card is ever expanded. A larger body
# spills to a session attachment and is read back on demand.
TASK_NOTIFICATION_INLINE_LIMIT = 4 * 1024
# A summary is a headline, not a body: bound it hard, and never spill it.
TASK_NOTIFICATION_SUMMARY_LIMIT = 4 * 1024


@dataclass
class ParsedTaskNotification:
    task_id: str | None = None
    tool_use_id: str | None = None
    status: str | None = None
    summary: str | None = None
    event: str | None = None
    note: str | None = None
    result: str | None = None
    output_file: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


def classify_injected_user_turn(record: dict[str, Any], content: Any) -> str:
    """Classify a harness-injected user turn: ``task_notification`` (normalize
    it), ``continuation`` (a /compact summary — still dropped), or ``none``.

    ``origin.kind`` is authoritative when present; otherwise the trimmed string
    content is matched. Only a plain-string user turn is ever injected.
    """
    # A task notification is always a string payload. Requiring string content
    # even when ``origin.kind`` matches means an anomalous record with list
    # (tool_result) content still flows through normal tool-result handling
    # rather than being dropped.
    if isinstance(content, str):
        origin = record.get("origin")
        origin_says_notification = (
            isinstance(origin, dict) and origin.get("kind") == "task-notification"
        )
        stripped = content.lstrip()
        if origin_says_notification or stripped.startswith("<task-notification>"):
            return "task_notification"
        if stripped.startswith("This session is being continued"):
            return "continuation"
    return "none"


def _excise_block(text: str, tag: str, *, greedy_close: bool) -> tuple[str | None, str]:
    """Cut the first ``<tag>…</tag>`` span out of ``text`` and return
    ``(inner, remainder)``. ``greedy_close`` matches the *last* ``</tag>`` so a
    body that itself quotes ``</tag>`` is captured whole. A missing close cuts to
    the next ``<usage>``/``</task-notification>`` boundary (fail-safe: drop the
    field, never scan the body for scalars)."""
    open_tag, close_tag = f"<{tag}>", f"</{tag}>"
    start = text.find(open_tag)
    if start == -1:
        return None, text
    body_start = start + len(open_tag)
    end = text.rfind(close_tag) if greedy_close else text.find(close_tag, body_start)
    if end == -1 or end < body_start:
        rest = text[body_start:]
        boundary = re.search(r"<usage>|</task-notification>", rest)
        cut = boundary.start() if boundary else len(rest)
        inner = rest[:cut]
        remainder = text[:start] + rest[cut:]
        return html.unescape(inner.strip()) or None, remainder
    inner = text[body_start:end]
    remainder = text[:start] + text[end + len(close_tag) :]
    return html.unescape(inner.strip()) or None, remainder


def _scan_scalar(text: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return html.unescape(match.group(1).strip()) or None if match else None


def _parse_usage(inner: str | None) -> dict[str, int]:
    if not inner:
        return {}
    usage: dict[str, int] = {}
    for key in ("subagent_tokens", "tool_uses", "duration_ms"):
        match = re.search(rf"<{key}>(.*?)</{key}>", inner, re.DOTALL)
        if match:
            try:
                usage[key] = int(match.group(1).strip())
            except ValueError:
                continue
    return usage


def parse_task_notification(content: Any) -> ParsedTaskNotification | None:
    """Parse a ``<task-notification>`` payload into known fields, stdlib-only and
    non-throwing (NFR1: no XML parser, no external-entity resolution).

    Returns ``None`` for a missing wrapper or one with no recognized field, so a
    contentless or malformed record stays suppressed rather than becoming an
    empty card.
    """
    if not isinstance(content, str) or "<task-notification>" not in content:
        return None
    # Excise every free-text field body first — greedily, to its last close, so a
    # body that quotes its own close tag is captured whole — then scan the
    # leftover structured remainder for ``output-file`` and the short scalars.
    # This is a security boundary: ``output-file`` feeds the host-file capture
    # sink, so a tag quoted inside a report/event/summary/note body must never be
    # hoisted into a real path (which would read an arbitrary host file). The
    # remainder that reaches the output-file scan holds only the wrapper's own
    # short fields (task-id, tool-use-id, status).
    result, remainder = _excise_block(content, "result", greedy_close=True)
    summary, remainder = _excise_block(remainder, "summary", greedy_close=True)
    event, remainder = _excise_block(remainder, "event", greedy_close=True)
    note, remainder = _excise_block(remainder, "note", greedy_close=True)
    usage_inner, remainder = _excise_block(remainder, "usage", greedy_close=False)
    output_file, remainder = _excise_block(remainder, "output-file", greedy_close=False)
    parsed = ParsedTaskNotification(
        task_id=_scan_scalar(remainder, "task-id"),
        tool_use_id=_scan_scalar(remainder, "tool-use-id"),
        status=_scan_scalar(remainder, "status"),
        summary=summary,
        event=event,
        note=note,
        result=result,
        output_file=output_file,
        usage=_parse_usage(usage_inner),
    )
    if not (parsed.summary or parsed.event or parsed.status or parsed.result):
        return None
    return parsed


def infer_task_notification_kind(parsed: ParsedTaskNotification) -> str:
    """Infer a presentation kind from the summary. A verbose summary can mention
    several roles incidentally (e.g. a background-command note that explains an
    "agent teardown"), so anchor on the reliable leading phrases first, then the
    distinctive background phrasing, and only then fall back to a bare mention."""
    summary = (parsed.summary or "").lower()
    if summary.startswith(('agent "', "agent '")):
        return "agent"
    if summary.startswith("monitor"):
        return "monitor"
    if (
        "background command" in summary
        or "background shell command" in summary
        or summary.startswith("background")
    ):
        return "background_command"
    if "monitor" in summary:
        return "monitor"
    if "agent" in summary:
        return "agent"
    return "unknown"


def _truncate_utf8(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _bounded(
    text: str | None, limit: int = TASK_NOTIFICATION_INLINE_LIMIT
) -> tuple[str | None, bool]:
    """Cap ``text`` to ``limit`` bytes, reporting whether anything was cut."""
    if text is None or len(text.encode("utf-8")) <= limit:
        return text, False
    return _truncate_utf8(text, limit), True


def _stable_task_notification_id(
    parsed: ParsedTaskNotification, ts: datetime | None
) -> str:
    basis = "|".join(
        (
            ts.isoformat() if ts is not None else "",
            parsed.task_id or "",
            parsed.summary or "",
            parsed.event or (parsed.result or "")[:256],
        )
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


_AGENT_MESSAGE_RE = re.compile(
    r'<agent-message\s+from="([^"]+)"\s*>\n?(.*?)</agent-message>', re.DOTALL
)
_HANDBACK_MARKER = "[Subagent hand-back]"
_REPORT_FOLLOWS = "The report follows:\n"


def parse_agent_handback(content: str) -> tuple[str, str] | None:
    """Extract ``(sender_id, report)`` from a subagent hand-back message.

    Inter-agent messages are wrapped in ``<agent-message from="id">…</agent-message>``;
    a hand-back carries the ``[Subagent hand-back]`` preamble, which is stripped
    down to the report body. Returns ``None`` for a non-hand-back peer message or
    a record that carries no agent-message block.
    """
    match = _AGENT_MESSAGE_RE.search(content)
    if match is None:
        return None
    inner = match.group(2)
    if _HANDBACK_MARKER not in inner:
        return None
    report = inner.split(_REPORT_FOLLOWS, 1)[-1]
    return match.group(1), textwrap.dedent(report).strip()


def task_notification_dedup_key(content: str) -> str:
    """A content-derived key identifying one task notification across record forms.

    The CLI can persist the same notification twice — as a ``queue-operation``
    enqueue and as a later ``user`` turn — with byte-identical wrapper content, so
    a hash of the stripped content collapses those twins while keeping genuinely
    distinct notifications (different body) apart.
    """
    return hashlib.sha1(content.strip().encode("utf-8")).hexdigest()


def _compact_task_notification_text(summary: str | None, event: str | None) -> str:
    # Takes the bounded values: this becomes ``EventRecord.text``.
    parts = [part for part in (summary, event) if part]
    return " — ".join(parts) if parts else "Task notification"


def build_task_notification_metadata(
    parsed: ParsedTaskNotification,
    *,
    record_uuid: str | None,
    allow_output_capture: bool,
    capture_enabled: bool,
    ts: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build the ``(text, metadata)`` for a task-notification SYSTEM_NOTE event.

    ``allow_output_capture`` is True for the live tailer and False for history
    import, whose temp file is long gone; ``capture_enabled`` is the operator's
    ``task_output_capture_enabled`` switch. Both must hold to capture, and each
    explains itself differently on the card.

    When capture is allowed, an absolute ``output-file`` rides the transient
    ``capture_host_text`` key and any over-long body spills its full text on
    ``capture_inline_blobs``.
    """
    capture_allowed = allow_output_capture and capture_enabled
    # An imported transcript never had the file, so that explanation wins over
    # the operator switch, which was irrelevant at capture time.
    no_capture_reason = (
        "full output not captured on import"
        if not allow_output_capture
        else "output capture is disabled"
    )
    notification_id = record_uuid or _stable_task_notification_id(parsed, ts)
    kind = infer_task_notification_kind(parsed)

    result_preview, result_truncated = _bounded(parsed.result)
    event_text, event_truncated = _bounded(parsed.event)
    note_text, note_truncated = _bounded(parsed.note)
    summary_text, _ = _bounded(parsed.summary, TASK_NOTIFICATION_SUMMARY_LIMIT)

    # An Agent's ``output-file`` is its sidechain transcript; its report is the
    # last record, already inline on ``result``.
    report_is_inline = kind == "agent" and parsed.result is not None
    output_file = parsed.output_file
    captures_output = bool(
        output_file and os.path.isabs(output_file) and not report_is_inline
    )

    spills: list[dict[str, Any]] = []
    if capture_allowed:
        for name, text, truncated in (
            ("result", parsed.result, result_truncated),
            # ``event`` samples the stream the ``output-file`` records, so
            # capturing that file already keeps the text a spill would store.
            ("event", parsed.event, event_truncated and not captures_output),
            ("note", parsed.note, note_truncated),
        ):
            if truncated and text is not None:
                spills.append(
                    {
                        "filename": f"task-{notification_id}-{name}.txt",
                        "text": text,
                        "mime": "text/plain; charset=utf-8",
                    }
                )

    output_available = False
    output_unavailable_reason: str | None = None
    capture_path: str | None = None
    if captures_output:
        if capture_allowed:
            capture_path = output_file
            output_available = True
        else:
            output_unavailable_reason = no_capture_reason
    elif spills:
        output_available = True
    elif not capture_allowed and (
        result_truncated or event_truncated or note_truncated
    ):
        output_unavailable_reason = no_capture_reason

    payload: dict[str, Any] = {
        "version": TASK_NOTIFICATION_VERSION,
        "id": notification_id,
        "task_id": parsed.task_id,
        "tool_use_id": parsed.tool_use_id,
        "kind": kind,
        "status": parsed.status,
        "summary": summary_text,
        "event": event_text,
        "note": note_text,
        "result_preview": result_preview,
        "result_truncated": result_truncated,
        "event_truncated": event_truncated,
        "note_truncated": note_truncated,
        "output_available": output_available,
        "output_unavailable_reason": output_unavailable_reason,
    }
    if parsed.usage:
        payload["usage"] = parsed.usage

    metadata: dict[str, Any] = {
        "method": TASK_NOTIFICATION_METHOD,
        "item_type": TASK_NOTIFICATION_ITEM_TYPE,
        "task_notification": payload,
        "status": SessionStatus.RUNNING,
    }
    if capture_path is not None:
        # Captured as *text*: a report small enough to render whole rides in
        # the event itself, so the card needs no attachment and no fetch. Only
        # an oversized one becomes a pinned attachment with a bounded preview.
        metadata["capture_host_text"] = [capture_path]
    if spills:
        metadata["capture_inline_blobs"] = spills
    if capture_path is not None or spills:
        metadata["capture_origin"] = AttachmentOrigin.TASK_OUTPUT
    return _compact_task_notification_text(summary_text, event_text), metadata


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

# Claude Code's tool for sending local files to the human.
SEND_USER_FILE_TOOL = "SendUserFile"

_VALID_TASK_STATUSES = frozenset({"pending", "in_progress", "completed"})


def sent_user_file_paths(tool_input: dict[str, Any]) -> list[str]:
    """The non-empty ``str`` entries of a ``SendUserFile`` input's ``files``."""
    raw = tool_input.get("files")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item]


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
