from typing import Any, Literal

from pydantic import BaseModel, Field

from waypoint.schemas import EventKind, SessionStatus


class ItemPayload(BaseModel):
    item_id: str | None = None
    item_type: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_use_id: str | None = None
    payload: dict[str, Any] | None = None


class ApprovalPayload(BaseModel):
    approval_id: str
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    decisions: list[str] = Field(default_factory=lambda: ["approve", "decline"])


class EventEnvelope(BaseModel):
    """Canonical normalized event shape every plugin emits.

    Step 9 of the refactor moves all per-plugin normalization into
    `to_envelope(raw)` functions and persists `metadata.version=1`. Until
    then the envelope is opt-in; runtime keeps reading the raw `metadata`
    dict for back-compat.
    """

    kind: EventKind
    text: str
    status: SessionStatus | None = None
    item: ItemPayload | None = None
    approval: ApprovalPayload | None = None
    metadata_version: Literal[1] = 1
    extra: dict[str, Any] = Field(default_factory=dict)


InteractionKind = Literal["approval", "question", "plan_approval"]


class InteractionChoice(BaseModel):
    label: str
    description: str | None = None


class InteractionEnvelope(BaseModel):
    """Backend-neutral description of a human-actionable session interaction.

    A backend attaches this to an event's ``metadata["interaction"]`` at the
    point the event makes a session actionable (a tool/plan approval or an
    ``AskUserQuestion``). It is the *only* shape the notification center reads,
    so protocol-specific parsing stays inside ``backends/<id>/`` — the runtime
    and notification path never branch on backend id or raw provider payloads.
    """

    version: Literal[1] = 1
    kind: InteractionKind
    # Provider-stable id for the request, so replays/retries dedupe.
    request_id: str
    title: str
    body: str | None = None
    choices: list[InteractionChoice] = Field(default_factory=list)
    plan_item_id: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


INTERACTION_METADATA_KEY = "interaction"


def question_interaction(
    request_id: str, questions: Any, *, title: str = "Answer question"
) -> InteractionEnvelope | None:
    """Build a ``question`` interaction from an ``AskUserQuestion`` tool input.

    ``questions`` is the canonical ``[{question, header?, options: [{label,
    description?}]}]`` list every backend normalizes to. Returns ``None`` when
    it is empty or malformed so the caller emits no interaction.
    """
    if not isinstance(questions, list) or not questions:
        return None
    lines: list[str] = []
    choices: list[InteractionChoice] = []
    resolved_title: str | None = None
    for entry in questions:
        if not isinstance(entry, dict):
            continue
        prompt = entry.get("question") or entry.get("header")
        if isinstance(prompt, str) and prompt:
            if resolved_title is None:
                resolved_title = prompt
            lines.append(prompt)
        for option in entry.get("options") or []:
            if not isinstance(option, dict):
                continue
            label = option.get("label")
            if not isinstance(label, str) or not label:
                continue
            description = option.get("description")
            choices.append(
                InteractionChoice(
                    label=label,
                    description=description if isinstance(description, str) else None,
                )
            )
    body = "\n".join(lines) or None
    return InteractionEnvelope(
        kind="question",
        request_id=request_id,
        title=resolved_title or title,
        body=body,
        choices=choices,
    )
