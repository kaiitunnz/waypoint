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
