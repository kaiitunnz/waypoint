"""Intent construction and deterministic plain-text rendering.

Two pure mappers turn a source record into a :class:`NotificationIntent`
(``intent_from_inbox_item``, ``intent_from_event``), and ``render_message``
turns an intent plus the validated origin into a bounded, plaintext
:class:`OutboundMessage`. No markup is interpreted, so agent-provided strings
cannot inject message-service formatting or oversized payloads.
"""

import re

from waypoint.backends.events import (
    INTERACTION_METADATA_KEY,
    InteractionEnvelope,
)
from waypoint.notifications.contracts import (
    ChoiceItem,
    ChoiceListBlock,
    NotificationIntent,
    OutboundMessage,
    PreviewBlock,
    TextBlock,
)
from waypoint.schemas import (
    EventRecord,
    InboxApprovalBlock,
    InboxAttachmentBlock,
    InboxItem,
    InboxMarkdownBlock,
    InboxQuestionBlock,
)

# Telegram accepts up to 4096 UTF-16 code units per message; stay well under it
# even after a title and formatting. The channel re-checks defensively.
HARD_TEXT_LIMIT = 3500

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_WHITESPACE_RUN = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")

_KIND_LABEL = {
    "inbox": "Inbox",
    "approval": "Approval needed",
    "question": "Question",
    "plan_approval": "Plan approval",
}


def _sanitize(text: str) -> str:
    """Strip control characters and collapse whitespace to plaintext."""
    cleaned = _CONTROL_CHARS.sub("", text)
    cleaned = _WHITESPACE_RUN.sub(" ", cleaned)
    cleaned = _BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip()


def intent_from_inbox_item(item: InboxItem) -> NotificationIntent:
    """Map a newly created inbox item to a notification intent.

    Never downloads or embeds attachment data; an attachment becomes an
    ``Attachment: <filename>`` marker only.
    """
    blocks: list[PreviewBlock] = []
    if item.from_label:
        blocks.append(TextBlock(text=f"From: {item.from_label}"))
    for block in item.blocks:
        if isinstance(block, InboxMarkdownBlock):
            if block.text.strip():
                blocks.append(TextBlock(text=block.text))
        elif isinstance(block, InboxQuestionBlock):
            blocks.append(
                ChoiceListBlock(
                    label=block.question,
                    choices=[
                        ChoiceItem(label=option.label, description=option.description)
                        for option in block.options
                    ],
                )
            )
        elif isinstance(block, InboxApprovalBlock):
            blocks.append(
                ChoiceListBlock(
                    label=block.prompt,
                    choices=[ChoiceItem(label=option) for option in block.options],
                )
            )
        elif isinstance(block, InboxAttachmentBlock):
            filename = block.ref.filename or "attachment"
            blocks.append(TextBlock(text=f"Attachment: {filename}"))
    return NotificationIntent(
        dedupe_key=f"inbox:{item.id}",
        kind="inbox",
        subject=item.subject,
        target_path=f"/inbox/{item.id}",
        source_session_id=item.from_session_id or None,
        preview_blocks=blocks,
        created_at=item.created_at,
    )


def intent_from_event(
    event: EventRecord, session_title: str | None
) -> NotificationIntent | None:
    """Map a normalized session event to a notification intent, or ``None``.

    Reads only the backend-neutral ``metadata["interaction"]`` envelope, so a
    backend that emits no interaction (or an event that is not actionable)
    produces no notification.
    """
    raw = event.metadata.get(INTERACTION_METADATA_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        envelope = InteractionEnvelope.model_validate(raw)
    except ValueError:
        return None
    blocks: list[PreviewBlock] = []
    if session_title:
        blocks.append(TextBlock(text=f"Session: {session_title}"))
    if envelope.body and envelope.body.strip():
        blocks.append(TextBlock(text=envelope.body))
    if envelope.choices:
        blocks.append(
            ChoiceListBlock(
                choices=[
                    ChoiceItem(label=choice.label, description=choice.description)
                    for choice in envelope.choices
                ]
            )
        )
    request_ref = envelope.plan_item_id or envelope.request_id
    return NotificationIntent(
        dedupe_key=f"event:{event.session_id}:{envelope.kind}:{request_ref}",
        kind=envelope.kind,
        subject=envelope.title,
        target_path=f"/session/{event.session_id}",
        source_session_id=event.session_id,
        preview_blocks=blocks,
        created_at=event.ts,
    )


def _render_blocks(blocks: list[PreviewBlock], budget: int) -> str:
    parts: list[str] = []
    for block in blocks:
        if budget <= 0:
            break
        if isinstance(block, TextBlock):
            parts.append(_sanitize(block.text))
        elif isinstance(block, ChoiceListBlock):
            lines: list[str] = []
            if block.label:
                lines.append(_sanitize(block.label))
            for choice in block.choices:
                line = f"• {_sanitize(choice.label)}"
                if choice.description:
                    line += f" — {_sanitize(choice.description)}"
                lines.append(line)
            parts.append("\n".join(lines))
    body = "\n\n".join(part for part in parts if part)
    if len(body) > budget:
        body = body[: max(budget - 1, 0)].rstrip() + "…"
    return body


def render_message(
    intent: NotificationIntent,
    *,
    public_base_url: str,
    preview_chars: int,
    title_chars: int,
) -> OutboundMessage:
    """Render an intent into a bounded plaintext message for a URL-only channel."""
    label = _KIND_LABEL.get(intent.kind, "Notification")
    title = _sanitize(f"{label}: {intent.subject}")
    if len(title) > title_chars:
        title = title[: max(title_chars - 1, 0)].rstrip() + "…"
    body = _render_blocks(intent.preview_blocks, preview_chars)
    text = f"{title}\n\n{body}" if body else title
    if len(text) > HARD_TEXT_LIMIT:
        text = text[: HARD_TEXT_LIMIT - 1].rstrip() + "…"
    button_label = "Open inbox item" if intent.kind == "inbox" else "Open session"
    return OutboundMessage(
        intent_id=intent.dedupe_key,
        text=text,
        url=f"{public_base_url}{intent.target_path}",
        button_label=button_label,
    )
