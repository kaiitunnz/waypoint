"""Shared approval-decision vocabulary.

The frontend and CLI send a decision string for a tool approval; several
distinct strings ("accept", "approve", "acceptForSession", …) all mean "let the
tool run". Each backend maps that to its own mechanism (claude_code → an "allow"
control response, claude_tty/tmux → the dialog's Yes keystroke). Centralising
the vocabulary keeps those backends from drifting apart — a backend that
recognises a narrower set silently turns an unrecognised approval into a
decline.
"""

APPROVE_DECISIONS = frozenset(
    {
        "approve",
        "accept",
        "yes",
        "y",
        "allow",
        "acceptforsession",
        "acceptalways",
    }
)


def is_approve_decision(decision: str) -> bool:
    """Return True when ``decision`` means the tool should be allowed to run."""
    return decision.strip().lower() in APPROVE_DECISIONS
