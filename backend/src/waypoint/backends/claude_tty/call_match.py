"""Which pending tool call a permission dialog is asking about.

The dialog is only evidence: a call is named when everything the dialog shows of
it agrees, and only one call can be meant. Anything unread or unrecognized
matches nothing, since a wrong name gets another call approved.
"""

import json
import posixpath
import re
from collections.abc import Container, Sequence

from waypoint.backends.claude_tty.pane_dialog import ApprovalDialog, unboxed
from waypoint.backends.claude_tty.pending_calls import PendingCall

_FILE_HEADINGS: dict[str, frozenset[str]] = {
    "Create file": frozenset({"Write"}),
    "Write file": frozenset({"Write"}),
    "Overwrite file": frozenset({"Write"}),
    "Edit file": frozenset({"Edit", "MultiEdit"}),
    "Update file": frozenset({"Edit", "MultiEdit"}),
    "Edit notebook": frozenset({"NotebookEdit"}),
    "Read file": frozenset({"Read"}),
}
_NEW_FILE_HEADINGS = frozenset({"Create file", "Write file"})
_SKILL_HEADING_RE = re.compile(r'^Use skill "(.+)"\?$')
_NUMBERED_RE = re.compile(r"^ *(\d+)(?: (.*))?$")
_DIFF_LINE_RE = re.compile(r"^ *(\d+) ([-+ ])(.*)$")
_HUNK_BREAKS = frozenset({"...", "…", "⋮"})
_ARGUMENT_RE = re.compile(r"^\s*([\w-]+):(?:\s(.*))?$")


def match_call(
    dialog: ApprovalDialog,
    calls: Sequence[PendingCall],
    excluded: Container[str] = (),
) -> PendingCall | None:
    """The call ``dialog`` asks about, or None when that is not certain.

    ``calls`` are oldest first; ``excluded`` are ids that cannot be asking.
    Identical calls of one transcript prompt in order, so the oldest of them is
    meant. Differing calls, or calls from several transcripts, are ambiguous.
    """
    if not dialog.matchable:
        return None
    hits = [
        call
        for call in calls
        if call.tool_use_id not in excluded and _consistent(dialog, call)
    ]
    if not hits:
        return None
    first = hits[0]
    if any(
        (call.transcript, call.name, call.input)
        != (first.transcript, first.name, first.input)
        for call in hits
    ):
        return None
    return first


def match_plan_call(
    calls: Sequence[PendingCall], shown_path: str, excluded: Container[str] = ()
) -> PendingCall | None:
    """The one pending ExitPlanMode call for the plan file the dialog names."""
    hits = [
        call
        for call in calls
        if call.name == "ExitPlanMode"
        and call.tool_use_id not in excluded
        and _path_matches(shown_path, str(call.input.get("planFilePath")), call.cwd)
    ]
    return hits[0] if len(hits) == 1 else None


def _consistent(dialog: ApprovalDialog, call: PendingCall) -> bool:
    heading = dialog.heading or ""
    if heading == "Bash command":
        return call.name == "Bash" and _bash_matches(dialog, call)
    if names := _FILE_HEADINGS.get(heading):
        return call.name in names and _file_matches(dialog, heading, call)
    if heading == "Fetch":
        return call.name == "WebFetch" and _arguments_match(dialog, call)
    if heading == "Tool use":
        return _tool_use_matches(dialog, call)
    if skill := _SKILL_HEADING_RE.match(heading):
        return call.name == "Skill" and call.input.get("skill") == skill.group(1)
    return False


# ── wrapped text ──


def _words(text: str) -> str:
    return " ".join(text.split())


def _spells(text: str, lines: Sequence[str]) -> bool:
    """Whether ``lines`` are ``text`` wrapped: at a space, or inside a word too
    long for the line. Runs of whitespace count as one space."""
    target = _words(text)
    position = 0
    for line in lines:
        if not (segment := _words(line)):
            continue
        if target.startswith(" ", position):
            position += 1
        if not target.startswith(segment, position):
            return False
        position += len(segment)
    return position == len(target)


def _some_lines_spell(text: str, lines: Sequence[str]) -> bool:
    return any(
        _spells(text, lines[start:end])
        for start in range(len(lines))
        for end in range(start + 1, len(lines) + 1)
    )


# ── Bash ──


def _bash_matches(dialog: ApprovalDialog, call: PendingCall) -> bool:
    command = call.input.get("command")
    description = call.input.get("description")
    if not isinstance(command, str) or not command.strip():
        return False
    header = [
        line
        for line in dialog.header
        if line.strip() and not line.strip().startswith("Tip:")
    ]
    if dialog.body is not None:
        return _spells(command, [unboxed(line) for line in dialog.body]) and (
            not description or _some_lines_spell(str(description), header)
        )
    # Without dashed rules: the command's lines, then its description.
    for end in range(1, len(header) + 1):
        if _spells(command, header[:end]):
            rest = header[end:]
            return _spells(str(description), rest) if description else not rest
    return False


# ── files ──


def _file_matches(dialog: ApprovalDialog, heading: str, call: PendingCall) -> bool:
    key = "notebook_path" if call.name == "NotebookEdit" else "file_path"
    path = call.input.get(key)
    if not isinstance(path, str) or not path:
        return False
    if call.name == "Read":
        shown = (dialog.target or "").split(" · ")[0]
    elif call.name == "NotebookEdit":
        shown = dialog.target or ""
    else:
        shown = "".join(line.strip() for line in dialog.header)
    if not _path_matches(shown, path, call.cwd):
        return False
    body = dialog.body or []
    match call.name:
        case "Write":
            content = call.input.get("content")
            if not isinstance(content, str):
                return False
            if heading in _NEW_FILE_HEADINGS:
                return _new_file_matches(body, content)
            return _overwrite_matches(body, content)
        case "Edit":
            return _edits_match(body, [call.input])
        case "MultiEdit":
            edits = call.input.get("edits")
            return isinstance(edits, list) and _edits_match(body, edits)
        case "NotebookEdit":
            source = call.input.get("new_source")
            return _edits_match(body, [{"new_string": source}])
        case _:
            return True


def _path_matches(shown: str, path: str, cwd: str | None) -> bool:
    if not shown:
        return False
    forms = [path]
    if cwd:
        forms.append(posixpath.relpath(path, cwd))
    if shown.startswith("~/"):
        return path.endswith(shown[1:])
    if "…" in shown:
        # A tall dialog cuts the path's start or middle out.
        head, _, tail = shown.rpartition("…")
        return bool(tail) and any(
            form.startswith(head) and form.endswith(tail) for form in forms
        )
    return shown in forms


def _new_file_matches(body: list[str], content: str) -> bool:
    """Every line of the new file is shown under its number, and no other."""
    shown: dict[int, list[str]] = {}
    current: list[str] | None = None
    for line in body:
        if numbered := _NUMBERED_RE.match(line.rstrip()):
            current = shown[int(numbered.group(1))] = [numbered.group(2) or ""]
        elif current is not None:
            current.append(line)
        elif line.strip():
            return not content.strip()
    lines = content.splitlines() if content.strip() else []
    return sorted(shown) == list(range(1, len(lines) + 1)) and all(
        _spells(text, shown[number]) for number, text in enumerate(lines, 1)
    )


def _diff_entries(body: list[str]) -> list[tuple[int, str, list[str]]]:
    """``(line number, marker, wrapped segments)`` for each line of a diff."""
    entries: list[tuple[int, str, list[str]]] = []
    for line in body:
        if line.strip() in _HUNK_BREAKS:
            continue
        if match := _DIFF_LINE_RE.match(line.rstrip()):
            entries.append((int(match.group(1)), match.group(2), [match.group(3)]))
        elif numbered := _NUMBERED_RE.match(line.rstrip()):
            # An empty context line has nothing after its number.
            entries.append((int(numbered.group(1)), " ", [numbered.group(2) or ""]))
        elif entries:
            entries[-1][2].append(line)
    return entries


def _overwrite_matches(body: list[str], content: str) -> bool:
    lines = content.splitlines()
    new = [entry for entry in _diff_entries(body) if entry[1] != "-"]
    if not new:
        return not content.strip()
    return all(
        0 < number <= len(lines) and _spells(lines[number - 1], segments)
        for number, _, segments in new
    )


def _shown_in(text: str, side: list[list[str]]) -> bool:
    """Whether every line of ``text`` appears within a shown diff line."""
    shown = [
        joined
        for segments in side
        for joined in (
            _words(" ".join(segments)),
            "".join(_words(segment) for segment in segments),
        )
    ]
    return all(
        any(_words(line) in candidate for candidate in shown)
        for line in text.splitlines()
        if line.strip()
    )


def _edits_match(body: list[str], edits: list[object]) -> bool:
    entries = _diff_entries(body)
    removed = [segments for _, marker, segments in entries if marker != "+"]
    added = [segments for _, marker, segments in entries if marker != "-"]
    if not entries:
        return False
    for edit in edits:
        if not isinstance(edit, dict):
            return False
        old, new = edit.get("old_string") or "", edit.get("new_string") or ""
        if not isinstance(old, str) or not isinstance(new, str):
            return False
        if not (_shown_in(old, removed) and _shown_in(new, added)):
            return False
    return True


# ── other tools ──


def _arguments(body: list[str]) -> dict[str, list[str]] | None:
    """The ``key: value`` lines of a dialog body, or None when one repeats."""
    arguments: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in body:
        # Keys sit at the margin; their wrapped values are indented further.
        indent = len(line) - len(line.lstrip(" "))
        text = unboxed(line)
        match = _ARGUMENT_RE.match(text) if indent <= 1 else None
        if match is not None:
            if match.group(1) in arguments:
                return None
            current = arguments[match.group(1)] = [match.group(2) or ""]
        elif current is not None:
            current.append(text)
        elif text.strip():
            return None
    return arguments


def _arguments_match(dialog: ApprovalDialog, call: PendingCall) -> bool:
    shown = _arguments(dialog.body or [])
    if shown is None or set(shown) != set(call.input):
        return False
    for key, value in call.input.items():
        forms = [json.dumps(value, ensure_ascii=False)]
        if isinstance(value, str):
            forms.append(value)
        if not any(_spells(form, shown[key]) for form in forms):
            return False
    return True


def _alphanumeric(text: str) -> str:
    return "".join(char for char in text.lower() if char.isalnum())


def _mcp_matches(dialog: ApprovalDialog, call: PendingCall) -> bool:
    _, _, qualified = call.name.partition("mcp__")
    server, separator, tool = qualified.partition("__")
    if not separator or not dialog.header:
        return False
    title = "".join(dialog.header)
    shown_server, dash, shown_tool = title.partition("—")
    named = _alphanumeric(shown_tool.removesuffix("(MCP)"))
    return (
        bool(dash)
        and title.endswith("(MCP)")
        and _alphanumeric(shown_server) == _alphanumeric(server)
        and named in (_alphanumeric(tool), _alphanumeric(tool) + "tool")
        and _arguments_match(dialog, call)
    )


def _tool_use_matches(dialog: ApprovalDialog, call: PendingCall) -> bool:
    body = [unboxed(line) for line in dialog.body or []]
    match call.name:
        case "WebSearch":
            query = call.input.get("query")
            return _spells(f'Web Search("{query}")', body)
        case "Agent" | "Task":
            return _spells(f"Agent({call.input.get('description')})", body)
        case name if name.startswith("mcp__"):
            return _mcp_matches(dialog, call)
        case _:
            return False
