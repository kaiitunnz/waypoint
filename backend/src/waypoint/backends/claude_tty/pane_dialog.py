"""Pure detection/parsing of Claude TUI dialogs from a captured pane screen.

The TUI resolves tool permissions, model choice, and effort in on-screen popups
with no stdio control channel, so a `claude_tty` session that wants to surface
approvals (or drive `/model` / `/effort`) must read them off the rendered pane.
These functions are deliberately side-effect free: they take the text of a
`tmux capture-pane` snapshot and return structured descriptions, so the brittle,
layout-coupled parsing is unit-testable against captured fixtures without a live
TUI.

A permission dialog is a ruled block: a solid rule, a heading naming the tool
(with `` · from the <name> agent`` for a subagent's), then the call between dashed
rules, a "Do you want to …?" question, numbered options and, usually, a footer.

Verified against fixtures captured from Claude Code 2.1.177 to 2.1.332 in
``tests/fixtures/claude_tty_pane/``.
"""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from textwrap import dedent

from waypoint.backends.pane_text import strip_ansi, strip_whitespace


class PaneScreen(StrEnum):
    APPROVAL = "approval"
    PLAN = "plan"
    QUESTION = "question"
    TRUST = "trust"
    MODEL_SELECTOR = "model_selector"
    EFFORT_POPUP = "effort_popup"
    AUTO_MODE_TEACHING = "auto_mode_teaching"
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
# The first-run auto-mode consent modal. Title and full footer are both required
# to classify: the footer's "Esc to cancel" is shared with other dialogs, so the
# title is what keeps an adjacent screen from being cancelled by a partial match.
_AUTO_MODE_TEACHING_TITLE = "Teach auto mode about your environment?"
_AUTO_MODE_TEACHING_FOOTER = "←/→ to change usage · Enter to continue · Esc to cancel"
_TRUST_MARKER = "Is this a project you created or one you trust?"
# Claude Code 2.1.331 lists "No, exit" first, preselected and unnumbered. Its
# ``❯`` row reads as a composer prompt, so the active region starts below the
# question; the trust option and footer under it still identify the prompt.
_TRUST_OPTION = "Yes, I trust this folder"
_TRUST_FOOTER = "Enter to confirm"

_OPTION_RE = re.compile(r"^\s*(❯)?\s*(\d+)\.\s+(.*\S)\s*$")
_QUESTION_RE = re.compile(r"^\s*(Do you want to .*\?)\s*$", re.MULTILINE)
_PLAN_QUESTION_RE = re.compile(r"Would you like to proceed\?")
# The plan file the dialog footer names ("… · ~/.claude/plans/<slug>.md"), in
# whichever config dir the session runs with.
_PLAN_PATH_RE = re.compile(r"(?:^|·)\s*((?:~|/)[^·]*/plans/[^/·]+\.md)")
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

# Footer tokens marking the bottom edge of a live inline dialog. A trailing run of
# blank / leading-❯ lines resting on one is queued-message or free-text-field
# output, not the composer. "Esc to cancel" ends every inline dialog `classify`
# recognizes but the fetch one, which has no footer: its last option ends "(esc)".
# The plan dialog is a full-pane overlay with nothing below its footer, so it
# needs no token.
_DIALOG_FOOTER_TOKENS = ("Esc to cancel", "(esc)")
# Key hints an interactive popup shows, for popups no marker above names.
_INTERACTIVE_HINTS = (
    "Esc to cancel",
    "Enter to confirm",
    "Enter to select",
    "Enter to continue",
    "(esc)",
)
# The composer's top and bottom borders: full-width rules of this character.
_RULE_CHAR = "─"
_MIN_RULE_WIDTH = 20
_DASHED_RULE_RE = re.compile(r"[╌─]{20,}")
_AGENT_SUFFIX_RE = re.compile(r"^(.*?)\s+· from the (.+?) agent$")
SKILL_HEADING_RE = re.compile(r'^Use skill "(.+)"\?$')
# The heading whose tool is named by the lines below it.
TOOL_USE_HEADING = "Tool use"
# A line the options block ends with when the dialog is scrolled.
_MORE_LINES_PREFIX = "↑"
# The indent of an option's wrapped label or description.
_OPTION_DETAIL_INDENT = " " * 5
# The most lines a wrapped heading takes.
_HEADING_MAX_LINES = 3


def digest(text: str) -> str:
    """A short fingerprint of what was on screen or on disk."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _strip_ansi(text: str) -> str:
    return strip_ansi(text)


def _plain_lines(screen: str) -> list[str]:
    return _strip_ansi(screen).replace("\xa0", " ").splitlines()


def _compact(text: str) -> str:
    return strip_whitespace(text)


def _contains(screen: str, screen_compact: str, marker: str) -> bool:
    return marker in screen or _compact(marker) in screen_compact


def joined(lines: list[str]) -> str:
    """Text a dialog broke across lines without a space."""
    return "".join(line.strip() for line in lines)


def unboxed(line: str) -> str:
    """A dialog line without the bar that marks wrapped or multi-line text."""
    return re.sub(r"^\s*│", "", line)


@dataclass
class DialogOption:
    number: int
    label: str
    selected: bool


@dataclass
class ApprovalDialog:
    """A permission dialog as drawn.

    ``tool_name`` and ``target`` are what the dialog itself reads as, which is
    not proof of the call behind it.
    """

    tool_name: str | None
    target: str | None
    question: str
    options: list[DialogOption]
    # The heading naming the tool; None when the top of the dialog is cut off.
    heading: str | None
    # The subagent the heading says is asking.
    agent: str | None
    # Lines above the first dashed rule.
    header: list[str]
    # Lines between the dashed rules; None without them.
    body: list[str] | None
    # Lines between the last dashed rule and the question.
    extra: list[str]
    # False when the dialog does not show enough to say which call it asks
    # about: its top is cut off, or its heading cannot be read.
    matchable: bool

    @property
    def described_header(self) -> list[str]:
        """The header without the auto-mode tip and blank lines."""
        return [
            line
            for line in self.header
            if line.strip() and not line.strip().startswith("Tip:")
        ]

    @property
    def identity(self) -> str:
        """A digest of the lines on screen, apart from the highlight and the
        auto-mode tip. Spacing counts: ``rm -rf a /`` is not ``rm -rf a/``."""
        parts = [
            self.heading or "",
            self.agent or "",
            *self.described_header,
            "|",
            *(self.body or []),
            "|",
            *self.extra,
            self.question,
            *(f"{option.number}.{option.label}" for option in self.options),
        ]
        return digest("\n".join(part.rstrip() for part in parts))

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


def _rests_on_dialog_footer(lines: list[str], run_end: int) -> int | None:
    """Index of the dialog footer the trailing run ending at ``run_end`` rests on.

    Walks up over the contiguous run of blank and composer (leading-``❯``) lines;
    returns the index of the line below it when that line is a dialog footer, else
    None.
    """
    j = run_end
    while j >= 0 and (not lines[j].strip() or _is_composer_line(lines[j])):
        j -= 1
    if j >= 0 and any(token in lines[j] for token in _DIALOG_FOOTER_TOKENS):
        return j
    return None


def _is_column0_rule(line: str) -> bool:
    stripped = line.rstrip()
    return len(stripped) >= _MIN_RULE_WIDTH and set(stripped) == {_RULE_CHAR}


def _live_composer_frame(lines: list[str]) -> tuple[int, int] | None:
    """Indices of the live composer's top and bottom borders, when it is drawn.

    The composer is framed by two full-width rules starting at column 0, with
    the ``❯`` prompt at column 0 directly under the top one. Its text wraps onto
    indented lines, so a message quoting a dialog (borders, footers, ``❯``
    rows) can never reproduce that frame.
    """
    for top in range(len(lines) - 3, -1, -1):
        prompt = lines[top + 1]
        if not (
            _is_column0_rule(lines[top])
            and prompt.startswith(_COMPOSER_PROMPT)
            and _is_composer_line(prompt)
        ):
            continue
        width = len(lines[top].rstrip())
        for bottom in range(top + 2, len(lines)):
            if _is_column0_rule(lines[bottom]) and len(lines[bottom].rstrip()) == width:
                return top, bottom
        return None
    return None


def _active_region(lines: list[str]) -> list[str]:
    """The pane text below the live composer, where a live dialog or popup draws.

    Scoping here drops dialog signatures quoted higher in settled transcript.
    When the composer frame is drawn the region starts at its bottom border, so a
    typed message quoting a dialog is excluded. Otherwise it starts at the
    bottom-most composer prompt; a trailing run of blank / leading-``❯`` lines
    resting on a dialog footer is queued messages or a free-text answer field,
    so the scan skips it and continues above the footer.
    """
    if (frame := _live_composer_frame(lines)) is not None:
        return lines[frame[1] :]
    i = len(lines) - 1
    while i >= 0:
        if not _is_composer_line(lines[i]):
            i -= 1
            continue
        footer_idx = _rests_on_dialog_footer(lines, i)
        if footer_idx is None:
            return lines[i:]
        i = footer_idx - 1
    return lines


def composer_ready(screen: str) -> bool:
    """Whether the Claude TUI composer prompt is drawn and able to take input.

    True once the ``❯`` prompt line exists. A freshly relaunched pane (reattach
    or restart) shows the boot screen with no prompt for a beat; the tmux
    transport waits on this before pasting so the keystrokes are not dropped.
    """
    lines = _plain_lines(screen)
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
    lines = _plain_lines(screen)
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
    # Read by structure before any other marker: the fetch dialog has no footer,
    # and the prompt it shows may quote another dialog's text.
    if _is_footerless_approval(region.split("\n")):
        return PaneScreen.APPROVAL
    if all(_contains(region, compact, marker) for marker in _PLAN_MARKERS):
        return PaneScreen.PLAN
    if all(_contains(region, compact, marker) for marker in _QUESTION_MARKERS):
        return PaneScreen.QUESTION
    if _contains(region, compact, _TRUST_MARKER) or (
        _contains(region, compact, _TRUST_OPTION)
        and _contains(region, compact, _TRUST_FOOTER)
    ):
        return PaneScreen.TRUST
    if _contains(region, compact, _MODEL_FOOTER) and _contains(
        region, compact, _MODEL_MARKER
    ):
        return PaneScreen.MODEL_SELECTOR
    if _contains(region, compact, _EFFORT_FOOTER) and _contains(
        region, compact, _EFFORT_MARKER
    ):
        return PaneScreen.EFFORT_POPUP
    if _contains(region, compact, _AUTO_MODE_TEACHING_TITLE) and _contains(
        region, compact, _AUTO_MODE_TEACHING_FOOTER
    ):
        return PaneScreen.AUTO_MODE_TEACHING
    return PaneScreen.OTHER


def _question_index(lines: list[str]) -> int | None:
    # The live dialog is the lowest block, so a question quoted higher in the
    # transcript must not be parsed in place of the real prompt.
    return next(
        (i for i in range(len(lines) - 1, -1, -1) if _QUESTION_RE.match(lines[i])), None
    )


def _is_footerless_approval(lines: list[str]) -> bool:
    """A ruled block ending in a question and its Yes/No options, with nothing
    else below them but queued messages: the fetch dialog has no footer."""
    if (question := _question_index(lines)) is None:
        return False
    if not any(_is_column0_rule(line) for line in lines[:question]):
        return False
    below = [line for line in lines[question + 1 :] if line.strip()]
    options = _parse_options(below)
    labels = [option.label.lower() for option in options]
    return (
        any(option.selected for option in options)
        and any(label.startswith("yes") for label in labels)
        and any(label.startswith("no") for label in labels)
        and all(
            _OPTION_RE.match(line)
            or _is_composer_line(line)
            or line.startswith(_OPTION_DETAIL_INDENT)
            or line.strip().startswith(_MORE_LINES_PREFIX)
            for line in below
        )
    )


def composer_drawn(screen: str) -> bool:
    """Whether the composer's bordered input box is on screen.

    A dialog or panel replaces it, so no composer means keystrokes would land in
    something else. Borders may carry a label, and shell mode prompts with ``!``.
    """
    lines = _plain_lines(screen)
    for top in range(len(lines) - 3, -1, -1):
        if not (
            _is_border(lines[top])
            and lines[top + 1].startswith((_COMPOSER_PROMPT, "!"))
        ):
            continue
        # The typed text wraps onto indented lines until the bottom border.
        bottom = top + 2
        while bottom < len(lines) and (
            not lines[bottom].strip() or lines[bottom].startswith(" ")
        ):
            bottom += 1
        if bottom < len(lines) and _is_border(lines[bottom]):
            # A popup below the box has taken its place: the box is left over
            # from earlier in the scrollback.
            return not any(
                _is_border(line) or _is_interactive_line(line)
                for line in lines[bottom + 1 :]
            )
    return False


def _is_interactive_line(line: str) -> bool:
    return bool(
        _OPTION_PROMPT_RE.match(line)
        or _QUESTION_RE.match(line)
        or any(hint in line for hint in _INTERACTIVE_HINTS)
    )


def _is_border(line: str) -> bool:
    stripped = line.rstrip()
    return (
        len(stripped) >= _MIN_RULE_WIDTH
        and stripped.startswith(_RULE_CHAR * 2)
        and stripped.endswith(_RULE_CHAR)
    )


def _looks_interactive(screen: str) -> bool:
    return any(
        _is_interactive_line(line)
        for line in _active_region(_strip_ansi(screen).splitlines())
    )


def trust_option_offset(screen: str) -> int | None:
    """Rows from the trust prompt's highlighted option to its trust option.

    Positive means the trust option is below the highlight. None when either
    row is missing. Callers classify the screen as the trust prompt first.
    """
    rows = [line for line in _strip_ansi(screen).splitlines() if line.strip()]
    selected = max(
        (i for i, row in enumerate(rows) if _COMPOSER_PROMPT_RE.match(row)),
        default=None,
    )
    trust = max((i for i, row in enumerate(rows) if _TRUST_OPTION in row), default=None)
    if selected is None or trust is None:
        return None
    return trust - selected


def composer_holds_input(screen: str) -> bool:
    """Whether the live composer holds an unsent message that ``Enter`` submits.

    Read from the framed composer only: a panel or shell-mode prompt replaces
    it, and earlier prompts echo above with the same ``❯`` glyph.
    """
    lines = _plain_lines(screen)
    frame = _live_composer_frame(lines)
    if frame is None or shows_blocking_dialog(screen):
        return False
    top, bottom = frame
    text = "\n".join(lines[top + 1 : bottom]).removeprefix(_COMPOSER_PROMPT).strip()
    return bool(text) and not text.startswith(_COMPOSER_PLACEHOLDER_PREFIX)


def shows_blocking_dialog(screen: str) -> bool:
    """Whether a modal popup is on screen that would capture ``Enter``.

    The composer prompt glyph ``❯`` also marks the selected option of an
    approval/trust dialog, so a snapshot showing a dialog looks "ready" to the
    composer probes. The tmux transport consults this to avoid pasting or
    submitting a message into a dialog (which would select an option — e.g.
    auto-approve a tool). A popup `classify` does not know counts too, by its
    options or key hints standing where the composer should be.
    """
    if classify(screen) is not PaneScreen.OTHER:
        return True
    return not composer_drawn(screen) and _looks_interactive(screen)


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
    """Parse a tool-permission dialog, or None if the screen is not one."""
    screen = _strip_ansi(screen)
    if classify(screen) is not PaneScreen.APPROVAL:
        return None
    lines = _active_region(screen.splitlines())
    if (question_idx := _question_index(lines)) is None:
        return None
    match = _QUESTION_RE.match(lines[question_idx])
    assert match is not None
    question = match.group(1)

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
    heading, agent, matchable, header, body, extra = _split_dialog(lines[:question_idx])
    tool_name, target = _read_tool_target(heading, header, body, question)

    return ApprovalDialog(
        tool_name=tool_name,
        target=target or None,
        question=question,
        options=options,
        heading=heading,
        agent=agent,
        header=header,
        body=body,
        extra=extra,
        matchable=matchable,
    )


def _read_heading(lines: list[str], start: int) -> tuple[str, str | None, int, bool]:
    """The heading at ``start``, the agent it names, the line after it, and
    whether it was fully read."""
    text = lines[start].strip()
    for end in range(start + 1, min(start + _HEADING_MAX_LINES, len(lines)) + 1):
        if suffixed := _AGENT_SUFFIX_RE.match(text):
            return suffixed.group(1), suffixed.group(2), end, True
        if end < len(lines):
            # A narrow pane wraps the agent suffix onto the following lines.
            text = f"{text} {lines[end].strip()}"
    near = " ".join(line.strip() for line in lines[start : start + _HEADING_MAX_LINES])
    return lines[start].strip(), None, start + 1, "· from" not in near


def _split_dialog(
    lines: list[str],
) -> tuple[str | None, str | None, bool, list[str], list[str] | None, list[str]]:
    """Split the lines above a dialog's question into its parts."""
    top = next(
        (i for i in range(len(lines) - 1, -1, -1) if _is_column0_rule(lines[i])), None
    )
    heading: str | None = None
    agent: str | None = None
    matchable = False
    if top is not None:
        start = next(
            (i for i in range(top + 1, len(lines)) if lines[i].strip()), len(lines)
        )
        if start < len(lines):
            heading, agent, end, matchable = _read_heading(lines, start)
            lines = lines[end:]
    rules = [
        i for i, line in enumerate(lines) if _DASHED_RULE_RE.fullmatch(line.strip())
    ]
    header, body, extra = lines, None, []
    if len(rules) >= 2:
        first, last = rules[0], rules[-1]
        header, body, extra = lines[:first], lines[first + 1 : last], lines[last + 1 :]
    elif rules and heading is None:
        # Only the bottom rule is left of a dialog whose top scrolled away.
        header, body, extra = [], lines[: rules[0]], lines[rules[0] + 1 :]
    return heading, agent, matchable, _trim(header), body, _trim(extra)


def _trim(lines: list[str]) -> list[str]:
    kept = [i for i, line in enumerate(lines) if line.strip()]
    return lines[kept[0] : kept[-1] + 1] if kept else []


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

    # Below the sub-hint, where a long path wraps onto the following lines.
    below = lines[footer_idx + 1 if footer_idx < len(lines) else question_idx + 1 :]
    paths = _PLAN_PATH_RE.findall(joined(below))

    return PlanDialog(options=options, plan_path=paths[-1] if paths else None)


# Dialog headings that name the tool by themselves.
_HEADING_TOOLS: dict[str, str] = {
    "Bash command": "Bash",
    "Create file": "Write",
    "Write file": "Write",
    "Overwrite file": "Write",
    "Edit file": "Edit",
    "Update file": "Edit",
    "Edit notebook": "NotebookEdit",
    "Read file": "Read",
    "Fetch": "WebFetch",
}
_FILE_QUESTION_RE = re.compile(
    r"Do you want to (create|overwrite|write to|make this edit to|edit|update)"
    r" (.+?)\?$"
)
_NOTEBOOK_CHANGE_RE = re.compile(r"^\s*(Replace|Insert|Delete) ")


def _extract_bash_command(header: list[str], body: list[str] | None) -> str | None:
    if body is not None:
        # Hints and descriptions sit outside the ruled command body.
        end = next(
            (
                i
                for i, line in enumerate(body)
                if _DASHED_RULE_RE.fullmatch(line.strip())
            ),
            len(body),
        )
        return dedent("\n".join(unboxed(line) for line in body[:end])).strip() or None
    # Without a ruled body the command is the first line, unless a lone rule or
    # the tip shows the dialog was read short.
    if any(_DASHED_RULE_RE.fullmatch(line.strip()) for line in header):
        return None
    if _compact("\n".join(header)).startswith(
        _compact("Tip: auto mode handles these prompts for you")
    ):
        return None
    return next((line.strip() for line in header if line.strip()), None)


def _read_tool_target(
    heading: str | None, header: list[str], body: list[str] | None, question: str
) -> tuple[str | None, str | None]:
    """The tool and target the dialog reads as. Nothing above the dialog is
    consulted: a call echoed there may be another one."""
    shown = [*header, *(body or [])]
    text = "\n".join(unboxed(line).strip() for line in shown if line.strip())
    if heading is not None:
        if skill := SKILL_HEADING_RE.match(heading):
            return "Skill", skill.group(1)
        match _HEADING_TOOLS.get(heading):
            case "Bash":
                return "Bash", _extract_bash_command(header, body)
            case "Write" | "Edit" as tool:
                return tool, next((s.strip() for s in header if s.strip()), None)
            case "NotebookEdit":
                path = next(
                    (
                        i
                        for i, line in enumerate(header)
                        if _NOTEBOOK_CHANGE_RE.match(line)
                    ),
                    len(header),
                )
                return "NotebookEdit", joined(header[:path])
            case "Read":
                call = joined(body or [])
                return "Read", call.removeprefix("Read(").removesuffix(")")
            case "WebFetch":
                return "WebFetch", text
        if heading == TOOL_USE_HEADING:
            first = joined(body or [])
            if first.startswith("Web Search("):
                return "WebSearch", text
            if first.startswith("Agent("):
                return "Agent", text
            return None, text
    # File operations name the path in the question itself.
    if file_question := _FILE_QUESTION_RE.match(question):
        verb = file_question.group(1)
        tool = "Write" if verb in {"create", "overwrite", "write to"} else "Edit"
        return tool, file_question.group(2)
    return None, text if heading is not None else None


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
