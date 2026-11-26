"""OpenCode forms as Waypoint questions, and answers back as form replies."""

from dataclasses import dataclass
from typing import Any

_NUMBERS: dict[str, type[int] | type[float]] = {"integer": int, "number": float}
_BOOLEANS = {"yes": True, "true": True, "no": False, "false": False}


@dataclass(frozen=True)
class Option:
    label: str
    value: str
    description: str | None = None


@dataclass(frozen=True)
class Field:
    key: str
    type: str
    question: str
    header: str | None
    options: tuple[Option, ...]
    # Whether text outside ``options`` is accepted.
    custom: bool
    # Conditions on other fields' values under which this one is answered.
    when: tuple[tuple[str, str, Any], ...] = ()

    def applies(self, reply: dict[str, Any]) -> bool:
        return all(
            (reply.get(key) == value) is (op == "eq") for key, op, value in self.when
        )


@dataclass(frozen=True)
class Form:
    id: str
    # The OpenCode session that asked, which the reply goes to.
    session_id: str
    fields: tuple[Field, ...]

    def questions(self) -> list[dict[str, Any]]:
        """The ``questions`` of Waypoint's question card."""
        questions: list[dict[str, Any]] = []
        for field in self.fields:
            entry: dict[str, Any] = {
                "question": field.question,
                "options": [
                    {"label": option.label}
                    | (
                        {"description": option.description}
                        if option.description
                        else {}
                    )
                    for option in field.options
                ],
                "multiSelect": field.type == "multiselect",
            }
            if field.header:
                entry["header"] = field.header
            questions.append(entry)
        return questions

    def reply(
        self, answer: str, answers: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        """The reply's ``{key: value}`` from Waypoint's answer. ``answers`` has
        one entry per question; ``answer`` alone fills the first."""
        entries = answers or [{"answer": answer}]
        by_question = {field.question: field for field in self.fields}
        reply: dict[str, Any] = {}
        for index, entry in enumerate(entries):
            field = self.fields[index] if index < len(self.fields) else None
            question = entry.get("question")
            if field is None or question not in (None, field.question):
                field = by_question.get(str(question), field)
            if field is None:
                continue
            value = _value(field, _text(entry.get("answer")), _text(entry.get("notes")))
            if value is not None:
                reply[field.key] = value
        return {
            field.key: reply[field.key]
            for field in self.fields
            if field.key in reply and field.applies(reply)
        }


def parse_form(payload: dict[str, Any]) -> Form | None:
    form_id, session_id = payload.get("id"), payload.get("sessionID")
    if not isinstance(form_id, str) or not isinstance(session_id, str):
        return None
    fields = tuple(
        field
        for raw in payload.get("fields") or ()
        if isinstance(raw, dict) and (field := _parse_field(raw)) is not None
    )
    return Form(id=form_id, session_id=session_id, fields=fields)


def _parse_field(raw: dict[str, Any]) -> Field | None:
    key = raw.get("key")
    if not isinstance(key, str) or raw.get("hidden"):
        return None
    kind = str(raw.get("type") or "string")
    title, description = _text(raw.get("title")), _text(raw.get("description"))
    question = description or title or key
    if kind == "external" and (url := _text(raw.get("url"))):
        question = f"{question}: {url}"
    options = tuple(
        Option(
            label=str(option.get("label") or option.get("value")),
            value=str(option.get("value")),
            description=_text(option.get("description")) or None,
        )
        for option in raw.get("options") or ()
        if isinstance(option, dict) and option.get("value") is not None
    )
    if kind == "boolean":
        options = (Option("Yes", "yes"), Option("No", "no"))
    return Field(
        key=key,
        type=kind,
        question=question,
        header=title if description and title else None,
        options=options,
        custom=bool(raw.get("custom")) or not options,
        when=tuple(
            (str(rule.get("key")), str(rule.get("op")), rule.get("value"))
            for rule in raw.get("when") or ()
            if isinstance(rule, dict)
        ),
    )


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _value(field: Field, answer: str, notes: str) -> Any:
    values = {option.label: option.value for option in field.options}
    if field.type == "external":
        return None
    if field.type == "boolean":
        return _BOOLEANS.get((answer or notes).lower())
    if number := _NUMBERS.get(field.type):
        try:
            return number(answer or notes)
        except ValueError:
            return None
    if field.type == "multiselect":
        # Picks arrive joined with commas, which a label may also contain.
        picks = [answer] if answer in values else answer.split(",")
        chosen = [values.get(pick.strip(), pick.strip()) for pick in picks if pick]
        if not field.custom:
            chosen = [pick for pick in chosen if pick in values.values()]
        elif notes:
            chosen.append(notes)
        return chosen or None
    picked = values.get(answer)
    if not field.custom:
        return picked
    return " ".join(part for part in (picked or answer, notes) if part) or None
