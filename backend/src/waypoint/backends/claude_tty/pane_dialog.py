"""Pure detection/parsing of Claude TUI dialogs from a captured pane screen.

The TUI resolves tool permissions, model choice, and effort in on-screen popups
with no stdio control channel, so a `claude_tty` session that wants to surface
approvals (or drive `/model` / `/effort`) must read them off the rendered pane.
These functions are deliberately side-effect free: they take the text of a
`tmux capture-pane` snapshot and return structured descriptions, so the brittle,
layout-coupled parsing is unit-testable against captured fixtures without a live
TUI. Keystroke delivery and pane polling live in the (not-yet-wired) caller.

Verified against fixtures captured from Claude Code 2.1.177 in
``tests/fixtures/claude_tty_pane/``.
"""

import re
from dataclasses import dataclass
from enum import StrEnum


class PaneScreen(StrEnum):
    APPROVAL = "approval"
    QUESTION = "question"
    TRUST = "trust"
    MODEL_SELECTOR = "model_selector"
    EFFORT_POPUP = "effort_popup"
    OTHER = "other"


# Footer signatures uniquely identify each popup. The approval and trust dialogs
# both end in "Esc to cancel", so the distinguishing token is "Tab to amend"
# (approval) vs "Enter to confirm" anchored to the trust question.
#
# Anchors are matched in both their literal form and a whitespace-stripped
# ("compact") form, so a dialog that wraps or pads differently at the rendered
# width still classifies — box-drawn popups reflow and a raw substring match is
# brittle to that.
_APPROVAL_FOOTER = "Esc to cancel · Tab to amend"
_APPROVAL_QUESTION_MARKER = "Do you want to"
# The AskUserQuestion popup carries its own navigation footer, distinct from the
# permission dialog's "Tab to amend". We only need to recognize it: the dialog
# is dismissed with Esc so the TUI flushes the structured questions to the
# transcript, which is surfaced from there rather than parsed off the pane.
_QUESTION_FOOTER = "Tab/Arrow keys to navigate"
_MODEL_FOOTER = "Enter to set as default"
_MODEL_MARKER = "Select model"
_EFFORT_FOOTER = "←/→ to adjust · Enter to confirm"
_EFFORT_MARKER = "Effort"
_TRUST_MARKER = "Is this a project you created or one you trust?"

_OPTION_RE = re.compile(r"^\s*(❯)?\s*(\d+)\.\s+(.*\S)\s*$")
_TOOL_HEADER_RE = re.compile(r"^\s*●\s*([A-Za-z][\w-]*)\((.*)\)\s*$")
_QUESTION_RE = re.compile(r"^\s*(Do you want to .*\?)\s*$", re.MULTILINE)
_WHITESPACE_RE = re.compile(r"\s+")
# CSI / OSC / two-byte escape sequences. A pane captured with `tmux capture-pane
# -e` carries colour and cursor codes interleaved with the text, which would
# shred the substring/regex anchors; strip them before any matching.
_ANSI_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))"
)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _compact(text: str) -> str:
    return _WHITESPACE_RE.sub("", text)


def _contains(screen: str, screen_compact: str, marker: str) -> bool:
    return marker in screen or _compact(marker) in screen_compact


@dataclass
class DialogOption:
    number: int
    label: str
    selected: bool


@dataclass
class ApprovalDialog:
    tool_name: str | None
    target: str | None
    question: str
    options: list[DialogOption]

    @property
    def approve_option(self) -> DialogOption | None:
        """Option 1 ("Yes") — the unconditional approve."""
        for opt in self.options:
            if opt.number == 1 and opt.label.lower().startswith("yes"):
                return opt
        return None

    @property
    def decline_option(self) -> DialogOption | None:
        """The "No" option — the decline that does not widen permissions."""
        for opt in self.options:
            if opt.label.lower().startswith("no"):
                return opt
        return None


def classify(screen: str) -> PaneScreen:
    screen = _strip_ansi(screen)
    compact = _compact(screen)
    if _contains(screen, compact, _APPROVAL_FOOTER) and _contains(
        screen, compact, _APPROVAL_QUESTION_MARKER
    ):
        return PaneScreen.APPROVAL
    if _contains(screen, compact, _QUESTION_FOOTER):
        return PaneScreen.QUESTION
    if _contains(screen, compact, _TRUST_MARKER):
        return PaneScreen.TRUST
    if _contains(screen, compact, _MODEL_FOOTER) and _contains(
        screen, compact, _MODEL_MARKER
    ):
        return PaneScreen.MODEL_SELECTOR
    if _contains(screen, compact, _EFFORT_FOOTER) and _contains(
        screen, compact, _EFFORT_MARKER
    ):
        return PaneScreen.EFFORT_POPUP
    return PaneScreen.OTHER


def _parse_options(lines: list[str]) -> list[DialogOption]:
    options: list[DialogOption] = []
    for line in lines:
        match = _OPTION_RE.match(line)
        if match is None:
            continue
        options.append(
            DialogOption(
                number=int(match.group(2)),
                label=match.group(3),
                selected=match.group(1) is not None,
            )
        )
    return options


def parse_approval(screen: str) -> ApprovalDialog | None:
    """Parse a tool-permission dialog, or None if the screen is not one.

    The question, options, and footer form the dialog; the ``● Tool(arg)`` line
    nearest above it carries the tool name and target.
    """
    screen = _strip_ansi(screen)
    if classify(screen) is not PaneScreen.APPROVAL:
        return None
    lines = screen.splitlines()
    question_idx: int | None = None
    question = ""
    for i, line in enumerate(lines):
        match = _QUESTION_RE.match(line)
        if match is not None:
            question_idx, question = i, match.group(1)
            break
    if question_idx is None:
        return None

    # Options live between the question and the footer; bounding the slice keeps
    # diff-preview lines like "  1 waypoint-probe" (no dot) and numbered list
    # content out of the option set.
    footer_idx = next(
        (
            i
            for i, line in enumerate(lines[question_idx:], question_idx)
            if _APPROVAL_FOOTER in line
        ),
        len(lines),
    )
    options = _parse_options(lines[question_idx + 1 : footer_idx])
    tool_name, target = _extract_tool_target(lines[:question_idx], question)

    return ApprovalDialog(
        tool_name=tool_name,
        target=target or None,
        question=question,
        options=options,
    )


# Dialog-body labels Claude renders above the question. These are the reliable
# source of the tool + target: the `● Tool(arg)` header line is often absent
# (e.g. a Bash dialog shows a "Running…" spinner there instead), so it is only
# a fallback.
_BODY_TOOL_LABELS: dict[str, str] = {
    "Bash command": "Bash",
    "Create file": "Write",
    "Write file": "Write",
    "Edit file": "Edit",
    "Update file": "Edit",
}


def _extract_tool_target(
    head_lines: list[str], question: str
) -> tuple[str | None, str | None]:
    stripped = [line.strip() for line in head_lines]

    # 1. Body label → tool; the next non-empty line is the command/path. Scan
    # bottom-up: the active dialog is the lowest block on the pane, so an
    # identical label string sitting earlier in the scrollback can't win. The
    # target is taken as a single line — at the fixed 120-col launch width with
    # `capture-pane -J` joining wraps this holds; a label rendered without its
    # body is the only degenerate case and yields no worse than a None target.
    for i in range(len(stripped) - 1, -1, -1):
        tool = _BODY_TOOL_LABELS.get(stripped[i])
        if tool is not None:
            target = next((s for s in stripped[i + 1 :] if s), None)
            return tool, target

    # 2. File operations name the path in the question itself.
    qmatch = re.match(
        r"Do you want to (create|write to|edit|update) (.+?)\?$", question
    )
    if qmatch is not None:
        verb = qmatch.group(1)
        tool = "Write" if verb in {"create", "write to"} else "Edit"
        return tool, qmatch.group(2)

    # 3. Fallback: the `● Tool(arg)` header line, if present.
    header_tool: str | None = None
    header_target: str | None = None
    for line in head_lines:
        header = _TOOL_HEADER_RE.match(line)
        if header is not None:
            header_tool, header_target = header.group(1), header.group(2)
    return header_tool, header_target


def parse_model_selector(screen: str) -> list[DialogOption] | None:
    """Parse the ``/model`` selector's choices, or None if not on that screen."""
    screen = _strip_ansi(screen)
    if classify(screen) is not PaneScreen.MODEL_SELECTOR:
        return None
    lines = screen.splitlines()
    start = next((i for i, line in enumerate(lines) if "Select model" in line), 0)
    end = next((i for i, line in enumerate(lines) if _MODEL_FOOTER in line), len(lines))
    return _parse_options(lines[start:end])
