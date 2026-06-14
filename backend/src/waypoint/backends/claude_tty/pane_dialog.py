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
    TRUST = "trust"
    MODEL_SELECTOR = "model_selector"
    EFFORT_POPUP = "effort_popup"
    OTHER = "other"


# Footer signatures uniquely identify each popup. The approval and trust dialogs
# both end in "Esc to cancel", so the distinguishing token is "Tab to amend"
# (approval) vs "Enter to confirm" anchored to the trust question.
_APPROVAL_FOOTER = "Esc to cancel · Tab to amend"
_MODEL_FOOTER = "Enter to set as default"
_EFFORT_FOOTER = "←/→ to adjust · Enter to confirm"
_TRUST_MARKER = "Is this a project you created or one you trust?"

_OPTION_RE = re.compile(r"^\s*(❯)?\s*(\d+)\.\s+(.*\S)\s*$")
_TOOL_HEADER_RE = re.compile(r"^\s*●\s*([A-Za-z][\w-]*)\((.*)\)\s*$")
_QUESTION_RE = re.compile(r"^\s*(Do you want to .*\?)\s*$", re.MULTILINE)


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
    if _APPROVAL_FOOTER in screen and _QUESTION_RE.search(screen) is not None:
        return PaneScreen.APPROVAL
    if _TRUST_MARKER in screen:
        return PaneScreen.TRUST
    if _MODEL_FOOTER in screen and "Select model" in screen:
        return PaneScreen.MODEL_SELECTOR
    if _EFFORT_FOOTER in screen and "Effort" in screen:
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

    tool_name: str | None = None
    target: str | None = None
    for line in lines[:question_idx]:
        header = _TOOL_HEADER_RE.match(line)
        if header is not None:
            tool_name, target = header.group(1), header.group(2)

    return ApprovalDialog(
        tool_name=tool_name,
        target=target or None,
        question=question,
        options=options,
    )


def parse_model_selector(screen: str) -> list[DialogOption] | None:
    """Parse the ``/model`` selector's choices, or None if not on that screen."""
    if classify(screen) is not PaneScreen.MODEL_SELECTOR:
        return None
    lines = screen.splitlines()
    start = next((i for i, line in enumerate(lines) if "Select model" in line), 0)
    end = next((i for i, line in enumerate(lines) if _MODEL_FOOTER in line), len(lines))
    return _parse_options(lines[start:end])
