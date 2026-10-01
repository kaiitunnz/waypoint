"""Durable AskUserQuestion lifecycle markers shared by storage, runtime, and plugins.

A question is an ``AskUserQuestion`` tool call keyed by its ``tool_use_id``. It
stays open until a correlated event resolves it: a paired ``tool_result``, an
``ask_user_question_answer`` user event, or one of the system notes below.
"""

from enum import StrEnum

ASK_USER_QUESTION_TOOL = "AskUserQuestion"
ASK_QUESTION_ANSWER = "ask_user_question_answer"
# The human declined the question through Waypoint.
ASK_QUESTION_CANCELLED = "ask_user_question_cancelled"
# The provider or the agent's lifecycle ended the question without an answer
# Waypoint recorded and without a paired tool_result.
ASK_QUESTION_CLOSED = "ask_user_question_closed"

QUESTION_RESOLUTION_NOTE_KINDS = (ASK_QUESTION_CANCELLED, ASK_QUESTION_CLOSED)


class QuestionLiveness(StrEnum):
    """Whether an open question can take an answer or a cancel right now."""

    ACTIONABLE = "actionable"
    # The agent asked but cannot accept the reply yet (e.g. the tool call
    # streamed before its permission request registered).
    STARTING = "starting"
    # The agent is not reachable now but the question may become answerable
    # again (detached pane, adapter not restored yet).
    UNAVAILABLE = "unavailable"
    # The provider has definitively ended the request; reconciliation records
    # a closure note.
    CLOSED = "closed"


class QuestionCloseReason(StrEnum):
    PROVIDER_CLOSED = "provider_closed"
    PROVIDER_REJECTED = "provider_rejected"
    PROVIDER_REPLIED = "provider_replied"
