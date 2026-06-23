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

from waypoint.backends.pane_text import strip_ansi, strip_whitespace


class PaneScreen(StrEnum):
    APPROVAL = "approval"
    PLAN = "plan"
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
# The AskUserQuestion popup. We only need to recognize it — the dialog is
# dismissed with Esc so the TUI flushes the structured questions to the
# transcript, which is surfaced from there rather than parsed off the pane. The
# only footer tokens stable across every layout are "Enter to select" (unique
# to this popup among the screens we classify, as of the fixture-pinned Claude
# Code version) and "Esc to cancel". Everything else varies: the navigation
# hint reads "↑/↓ to navigate" / "Tab/Arrow keys to navigate" by arity, and a
# dialog with option previews offers free-text via "n to add notes" instead of
# an inline "Type something" option — so requiring "Type something" (as cctty
# does for its form parsing) misses the preview/notes variant entirely.
_QUESTION_MARKERS = ("Enter to select", "Esc to cancel")
# The ExitPlanMode dialog the TUI raises in plan mode. It carries neither the
# approval footer ("Tab to amend") nor the question footer ("Enter to select"),
# so it falls through to OTHER unless recognized here. Two co-located substrings
# of its prompt anchor it — "ready to execute" guards against a bare "Would you
# like to proceed?" an agent merely quoted. Verified against
# ``tests/fixtures/claude_tty_pane/plan_approval.txt`` (Claude Code 2.1.186).
_PLAN_MARKERS = ("ready to execute", "Would you like to proceed")
_MODEL_FOOTER = "Enter to set as default"
_MODEL_MARKER = "Select model"
_EFFORT_FOOTER = "←/→ to adjust · Enter to confirm"
_EFFORT_MARKER = "Effort"
_TRUST_MARKER = "Is this a project you created or one you trust?"

_OPTION_RE = re.compile(r"^\s*(❯)?\s*(\d+)\.\s+(.*\S)\s*$")
_TOOL_HEADER_RE = re.compile(r"^\s*●\s*([A-Za-z][\w-]*)\((.*)\)\s*$")
_QUESTION_RE = re.compile(r"^\s*(Do you want to .*\?)\s*$", re.MULTILINE)
_PLAN_QUESTION_RE = re.compile(r"Would you like to proceed\?")
# The saved-plan path the dialog footer names ("… · ~/.claude/plans/<slug>.md").
# Only a fallback for the ``planFilePath`` echoed on the approval card; the
# canonical (absolute) path and body come from the plan-file Write the transcript
# normalizer captured. The footer renders it tilde-unexpanded, so this yields
# the literal "~/…" form — display/echo only, not a path to open on disk.
_PLAN_PATH_RE = re.compile(r"(\S*/\.claude/plans/\S+\.md)")
# Sub-hint line that sits directly below the plan options; bounds the option
# slice so it is not scanned past the interactive rows.
_PLAN_FOOTER = "to approve with this feedback"
# The live composer is a ``❯`` prompt at the start of the line (after any pad).
# A ``❯`` embedded mid-line is content — a diff-preview row, a quoted glyph, a
# command — not a prompt, and must not be read as one. A dialog's selected
# option also leads with ``❯`` but as ``❯ 1.``; ``_OPTION_PROMPT_RE`` separates
# those so option rows are not mistaken for the composer either.
_COMPOSER_PROMPT_RE = re.compile(r"^\s*❯(?:\s|$)")
_OPTION_PROMPT_RE = re.compile(r"^\s*❯\s*\d+\.")


def _strip_ansi(text: str) -> str:
    return strip_ansi(text)


def _compact(text: str) -> str:
    return strip_whitespace(text)


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


@dataclass
class PlanDialog:
    """The ExitPlanMode "ready to proceed" dialog raised in plan mode.

    The accept options vary by Claude subscription plan, so they are selected by
    label, never by position: ``manual_option`` exits to ``default`` (edits still
    prompt) and ``auto_option`` exits to the ``auto`` permission mode. The auto
    option is absent on some plans, hence optional.
    """

    options: list[DialogOption]
    plan_path: str | None

    @property
    def manual_option(self) -> DialogOption | None:
        """The "Yes, manually approve edits" option → exits to ``default``.

        Falls back to the first "Yes" that is not the auto option, so if the
        manual label shifts in a future build a default-target approval still
        never presses "use auto mode" (which would widen permissions).
        """
        yes = [opt for opt in self.options if opt.label.lower().startswith("yes")]
        manual = next((opt for opt in yes if "manual" in opt.label.lower()), None)
        if manual is not None:
            return manual
        auto = self.auto_option
        return next((opt for opt in yes if opt is not auto), None)

    @property
    def auto_option(self) -> DialogOption | None:
        """The "Yes, and use auto mode" option → exits to ``auto`` (may be absent)."""
        return next(
            (
                opt
                for opt in self.options
                if opt.label.lower().startswith("yes") and "auto" in opt.label.lower()
            ),
            None,
        )


_COMPOSER_PROMPT = "❯"
# An idle composer shows dim ghost placeholder text (``Try "…"``) after the
# prompt rather than a bare prompt; it is empty for submit-confirmation
# purposes. A pending message never collides with this — an attachment turn
# leads with the ``[Image #N]`` chip / ``Attached files:`` block.
_COMPOSER_PLACEHOLDER_PREFIX = 'Try "'


def _is_composer_line(line: str) -> bool:
    """Whether a line is the live composer input prompt rather than a dialog row.

    The composer is a leading ``❯`` prompt followed by free text — a typed
    message, a slash command, the idle placeholder, or nothing. A ``❯`` embedded
    mid-line is dialog body or transcript content and is not a prompt. A dialog's
    selected option leads with the glyph but as ``❯ 1.`` (handled by
    `_OPTION_PROMPT_RE`); option rows are excluded too.
    """
    return (
        _COMPOSER_PROMPT_RE.match(line) is not None
        and _OPTION_PROMPT_RE.match(line) is None
    )


def _active_region(lines: list[str]) -> list[str]:
    """The pane text at and below the bottom-most live composer prompt.

    Settled transcript sits above the live composer, so scoping here drops a
    dialog-signature string an agent merely quoted or pasted into the
    conversation. A real dialog's interactive rows are options (``❯ 1.``), never
    a leading free-text prompt, so the boundary never slices an actual dialog.
    """
    last_composer = next(
        (i for i in range(len(lines) - 1, -1, -1) if _is_composer_line(lines[i])),
        None,
    )
    return lines if last_composer is None else lines[last_composer:]


def composer_ready(screen: str) -> bool:
    """Whether the Claude TUI composer prompt is drawn and able to take input.

    True once the ``❯`` prompt line exists. A freshly relaunched pane (reattach
    or restart) shows the boot screen with no prompt for a beat; the tmux
    transport waits on this before pasting so the keystrokes are not dropped.
    """
    lines = _strip_ansi(screen).replace("\xa0", " ").splitlines()
    return any(_COMPOSER_PROMPT in line for line in lines)


def composer_is_empty(screen: str) -> bool:
    """Whether the Claude TUI composer (input box) is empty.

    The composer prompt (``❯``) is the bottom-most prompt line; a submitted user
    turn echoes *above* it in the transcript with the same glyph, so the last
    ``❯`` line is the live input. It holds the typed message before submission
    and returns to a bare prompt (while a turn runs) or the idle placeholder
    (once idle) after — which is how the tmux submit-confirm loop tells an
    absorbed ``Enter`` (composer still populated) from a real submission. If no
    prompt line is found the composer is not drawn (a booting or dead pane), so
    nothing has been submitted — return False so the bounded confirm loop keeps
    retrying rather than declaring a phantom success.
    """
    lines = _strip_ansi(screen).replace("\xa0", " ").splitlines()
    prompt_lines = [line for line in lines if _COMPOSER_PROMPT in line]
    if not prompt_lines:
        return False
    after_prompt = prompt_lines[-1].split(_COMPOSER_PROMPT, 1)[1].strip()
    return after_prompt == "" or after_prompt.startswith(_COMPOSER_PLACEHOLDER_PREFIX)


def classify(screen: str) -> PaneScreen:
    # Match only within the active region (the bottom-most dialog/composer block),
    # not the whole pane: substring/compact matching against scrollback would let
    # a marker quoted in transcript content read as a live dialog.
    region = "\n".join(_active_region(_strip_ansi(screen).splitlines()))
    compact = _compact(region)
    if _contains(region, compact, _APPROVAL_FOOTER) and _contains(
        region, compact, _APPROVAL_QUESTION_MARKER
    ):
        return PaneScreen.APPROVAL
    if all(_contains(region, compact, marker) for marker in _PLAN_MARKERS):
        return PaneScreen.PLAN
    if all(_contains(region, compact, marker) for marker in _QUESTION_MARKERS):
        return PaneScreen.QUESTION
    if _contains(region, compact, _TRUST_MARKER):
        return PaneScreen.TRUST
    if _contains(region, compact, _MODEL_FOOTER) and _contains(
        region, compact, _MODEL_MARKER
    ):
        return PaneScreen.MODEL_SELECTOR
    if _contains(region, compact, _EFFORT_FOOTER) and _contains(
        region, compact, _EFFORT_MARKER
    ):
        return PaneScreen.EFFORT_POPUP
    return PaneScreen.OTHER


def shows_blocking_dialog(screen: str) -> bool:
    """Whether a modal popup is on screen that would capture ``Enter``.

    The composer prompt glyph ``❯`` also marks the selected option of an
    approval/trust dialog, so a snapshot showing a dialog looks "ready" to the
    composer probes. The tmux transport consults this to avoid pasting or
    submitting a message into a dialog (which would select an option — e.g.
    auto-approve a tool).
    """
    return classify(screen) is not PaneScreen.OTHER


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
    # Scope to the active region and take the bottom-most question: the live
    # dialog is the lowest block, so a "Do you want to …?" an agent quoted higher
    # in the transcript must not be parsed in place of the real prompt.
    lines = _active_region(screen.splitlines())
    question_idx: int | None = None
    question = ""
    for i, line in enumerate(lines):
        match = _QUESTION_RE.match(line)
        if match is not None:
            question_idx, question = i, match.group(1)
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


def parse_plan_dialog(screen: str) -> PlanDialog | None:
    """Parse the ExitPlanMode dialog, or None if the screen is not one.

    The interactive options follow the "Would you like to proceed?" question; the
    saved-plan path is named in the footer. Both are scoped to the active region
    and anchored to the bottom-most question so a plan an agent quoted earlier in
    the transcript cannot be parsed in its place.
    """
    screen = _strip_ansi(screen)
    if classify(screen) is not PaneScreen.PLAN:
        return None
    lines = _active_region(screen.splitlines())
    question_idx: int | None = None
    for i, line in enumerate(lines):
        if _PLAN_QUESTION_RE.search(line):
            question_idx = i
    if question_idx is None:
        return None

    # Bound the option slice on the sub-hint that sits directly below the
    # options, not on the plan-path line: the path is collected in its own pass
    # below, and keying the bound on it would slice the options away if a future
    # build rendered the path above them.
    footer_idx = next(
        (
            i
            for i, line in enumerate(lines[question_idx:], question_idx)
            if _PLAN_FOOTER in line
        ),
        len(lines),
    )
    options = _parse_options(lines[question_idx + 1 : footer_idx])

    plan_path: str | None = None
    for line in lines[question_idx:]:
        match = _PLAN_PATH_RE.search(line)
        if match is not None:
            plan_path = match.group(1)

    return PlanDialog(options=options, plan_path=plan_path)


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
    # Scope to the active region and anchor to the bottom-most "Select model" so
    # the live popup's options win over any the transcript quoted above it.
    lines = _active_region(screen.splitlines())
    start = next(
        (i for i in range(len(lines) - 1, -1, -1) if "Select model" in lines[i]), 0
    )
    end = next(
        (i for i, line in enumerate(lines[start:], start) if _MODEL_FOOTER in line),
        len(lines),
    )
    return _parse_options(lines[start:end])
