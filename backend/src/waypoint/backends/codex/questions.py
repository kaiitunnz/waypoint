"""Codex async questions mapped onto Waypoint's AskUserQuestion contract.

Codex's ``request_user_input_async`` tool surfaces as an ``agentMessage`` item
with ``delivery: "async"`` and a ``questions`` list; the turn keeps running.
The agent reads the human's reply from an ordinary user message carrying a
``<send_user_message_question_reply>`` envelope, so the Waypoint card is a
stand-in.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

from waypoint.backends.events import INTERACTION_METADATA_KEY, question_interaction
from waypoint.questions import ASK_USER_QUESTION_TOOL

_ASYNC_DELIVERY = "async"
_REPLY_TAG = "send_user_message_question_reply"
_QUESTION_TOOL = "request_user_input_async"
# Codex bounds the quoted question in a reply to 512 bytes.
_MAX_REPLY_QUESTION_BYTES = 512

_REPLY_PATTERN = re.compile(rf"\s*<{_REPLY_TAG}>(.*)</{_REPLY_TAG}>\s*", re.DOTALL)


@dataclass(frozen=True)
class ReplyEntry:
    tool_use_id: str
    question: str
    answer: str


def is_async_message(item: dict[str, Any]) -> bool:
    return (
        item.get("type") == "agentMessage" and item.get("delivery") == _ASYNC_DELIVERY
    )


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


def apply_async_question(metadata: dict[str, Any], item: dict[str, Any]) -> bool:
    """Mark ``metadata`` as an AskUserQuestion card when ``item`` is an async
    question, copying the questions to ``payload["input"]`` where the frontend
    reads them. Returns whether it was one."""
    item_id = item.get("id")
    questions = async_questions(item)
    if questions is None or not isinstance(item_id, str) or not item_id:
        return False
    metadata["payload"]["input"] = {"questions": questions}
    metadata.update(
        tool_name=ASK_USER_QUESTION_TOOL,
        tool_use_id=item_id,
        tool_input={"questions": questions},
    )
    interaction = question_interaction(item_id, questions)
    if interaction is not None:
        metadata[INTERACTION_METADATA_KEY] = interaction.to_metadata()
    return True


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
    return f"<{_REPLY_TAG}>\n{body}\n</{_REPLY_TAG}>"


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
        entries.append(ReplyEntry(_parse_item_ref(item_ref), question, answer))
    return entries


def _question_item_id(item_id: str, index: int) -> str:
    return json.dumps([_QUESTION_TOOL, item_id, index], separators=(",", ":"))


def _parse_item_ref(item_ref: str) -> str:
    try:
        decoded = json.loads(item_ref)
    except json.JSONDecodeError:
        return item_ref
    if (
        isinstance(decoded, list)
        and len(decoded) == 3
        and isinstance(decoded[1], str)
        and isinstance(decoded[2], int)
    ):
        return decoded[1]
    return item_ref


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
    if len(encoded) <= _MAX_REPLY_QUESTION_BYTES:
        return flattened
    return encoded[:_MAX_REPLY_QUESTION_BYTES].decode(errors="ignore")
