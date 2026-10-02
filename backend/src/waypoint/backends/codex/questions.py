"""Codex async questions mapped onto Waypoint's AskUserQuestion contract.

Codex's ``request_user_input_async`` tool surfaces as an ``agentMessage`` item
with ``delivery: "async"`` and a ``questions`` list; the turn keeps running.
The agent reads the human's reply from an ordinary user message carrying a
``<send_user_message_question_reply>`` envelope, so the Waypoint card is a
stand-in, like ``claude_tty``'s.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

from waypoint.backends.events import INTERACTION_METADATA_KEY, question_interaction
from waypoint.questions import ASK_USER_QUESTION_TOOL

ASYNC_DELIVERY = "async"
REPLY_TAG = "send_user_message_question_reply"
QUESTION_TOOL = "request_user_input_async"
# Codex bounds the quoted question in a reply to 512 bytes.
MAX_REPLY_QUESTION_BYTES = 512

_REPLY_PATTERN = re.compile(rf"\s*<{REPLY_TAG}>(.*)</{REPLY_TAG}>\s*", re.DOTALL)


@dataclass(frozen=True)
class ReplyEntry:
    tool_use_id: str
    index: int | None
    question: str
    answer: str


def is_async_message(item: dict[str, Any]) -> bool:
    return item.get("type") == "agentMessage" and item.get("delivery") == ASYNC_DELIVERY


def async_questions(item: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The item's questions in the canonical AskUserQuestion shape, or None."""
    if not is_async_message(item):
        return None
    raw = item.get("questions")
    if not isinstance(raw, list):
        return None
    questions: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = entry.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        options = entry.get("options")
        labels = (
            [label for label in options if isinstance(label, str) and label]
            if isinstance(options, list)
            else []
        )
        questions.append(
            {"question": title, "options": [{"label": label} for label in labels]}
        )
    return questions or None


def question_call_metadata(
    item_id: str, questions: list[dict[str, Any]]
) -> dict[str, Any]:
    """Metadata keys that make an event an open AskUserQuestion card."""
    metadata: dict[str, Any] = {
        "tool_name": ASK_USER_QUESTION_TOOL,
        "tool_use_id": item_id,
        "tool_input": {"questions": questions},
    }
    interaction = question_interaction(item_id, questions)
    if interaction is not None:
        metadata[INTERACTION_METADATA_KEY] = interaction.to_metadata()
    return metadata


def build_reply(
    item_id: str,
    questions: list[dict[str, Any]],
    answers: list[dict[str, Any]] | None,
    fallback_text: str,
) -> str:
    """Render the reply envelope Codex expects for ``item_id``'s questions.

    Structured ``answers`` (``[{question, answer, notes?}]``) map to their
    question by text; without any, the whole message answers the item.
    """
    entries: list[dict[str, str]] = []
    used: set[int] = set()
    for entry in answers or []:
        if not isinstance(entry, dict):
            continue
        text = _answer_text(entry)
        index = _question_index(questions, entry.get("question"), used)
        if not text or index is None:
            continue
        used.add(index)
        entries.append(
            {
                "questionItemId": _question_item_id(item_id, index),
                "question": _reply_question(questions[index]["question"]),
                "answer": text,
            }
        )
    if not entries:
        # One question takes the plain answer by index; for several, the bare
        # item id answers the whole message (Codex's resolver accepts both).
        first = questions[0]["question"] if questions else ""
        entries.append(
            {
                "questionItemId": (
                    _question_item_id(item_id, 0) if len(questions) == 1 else item_id
                ),
                "question": _reply_question(first),
                "answer": fallback_text.strip(),
            }
        )
    body = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    return f"<{REPLY_TAG}>\n{body}\n</{REPLY_TAG}>"


def parse_reply(text: str) -> list[ReplyEntry] | None:
    """Parse a message that is exactly a reply envelope; None for anything else."""
    match = _REPLY_PATTERN.fullmatch(text)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    raw = payload if isinstance(payload, list) else [payload]
    if not raw:
        return None
    entries: list[ReplyEntry] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return None
        item_ref = entry.get("questionItemId")
        question = entry.get("question")
        answer = entry.get("answer")
        if not (
            isinstance(item_ref, str)
            and isinstance(question, str)
            and isinstance(answer, str)
        ):
            return None
        tool_use_id, index = _parse_item_ref(item_ref)
        entries.append(ReplyEntry(tool_use_id, index, question, answer))
    return entries


def _question_item_id(item_id: str, index: int) -> str:
    return json.dumps([QUESTION_TOOL, item_id, index], separators=(",", ":"))


def _parse_item_ref(item_ref: str) -> tuple[str, int | None]:
    try:
        decoded = json.loads(item_ref)
    except json.JSONDecodeError:
        return item_ref, None
    if (
        isinstance(decoded, list)
        and len(decoded) == 3
        and isinstance(decoded[1], str)
        and isinstance(decoded[2], int)
    ):
        return decoded[1], decoded[2]
    return item_ref, None


def _answer_text(entry: dict[str, Any]) -> str:
    selection = entry.get("answer")
    notes = entry.get("notes")
    parts = [
        value.strip()
        for value in (selection, notes)
        if isinstance(value, str) and value.strip()
    ]
    return "\n\n".join(parts)


def _question_index(
    questions: list[dict[str, Any]], text: Any, used: set[int]
) -> int | None:
    for index, question in enumerate(questions):
        if index not in used and question.get("question") == text:
            return index
    return None


def _reply_question(title: str) -> str:
    flattened = title.replace("\n", " ")
    encoded = flattened.encode()
    if len(encoded) <= MAX_REPLY_QUESTION_BYTES:
        return flattened
    return encoded[:MAX_REPLY_QUESTION_BYTES].decode(errors="ignore")
