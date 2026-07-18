"""Intent construction and deterministic Telegram-HTML rendering.

Two pure mappers turn a source record into a :class:`NotificationIntent`
(``intent_from_inbox_item``, ``intent_from_event``), and ``render_message``
turns an intent plus the validated origin into a bounded HTML
:class:`OutboundMessage` (bold title, blockquote preview, bulleted choices).
Every content string is HTML-escaped, so agent-provided strings cannot inject
tags, break rendering, or error the send; visible length is bounded so the
escaped result stays well under Telegram's message limit.
"""

import html
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
                blocks.append(TextBlock(text=block.text, quote=True))
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
        blocks.append(TextBlock(text=envelope.body, quote=True))
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


def _esc(text: str) -> str:
    """Escape text for Telegram HTML parse mode (`&`, `<`, `>`).

    Applied to every content string so agent-provided text cannot inject tags,
    break rendering, or error the send.
    """
    return html.escape(text, quote=False)


def _truncate(text: str, budget: int) -> tuple[str, int]:
    """Take up to ``budget`` visible chars, appending an ellipsis when cut.

    Truncation is on the *visible* text before HTML escaping/tagging, so a
    multi-char entity or a tag is never split.
    """
    if budget <= 0:
        return "", 0
    if len(text) <= budget:
        return text, budget - len(text)
    return text[: max(budget - 1, 0)].rstrip() + "…", 0


def _render_blocks_html(blocks: list[PreviewBlock], budget: int) -> list[str]:
    parts: list[str] = []
    for block in blocks:
        if budget <= 0:
            break
        if isinstance(block, TextBlock):
            visible, budget = _truncate(_sanitize(block.text), budget)
            if not visible:
                continue
            parts.append(
                f"<blockquote>{_esc(visible)}</blockquote>"
                if block.quote
                else _esc(visible)
            )
        elif isinstance(block, ChoiceListBlock):
            lines: list[str] = []
            if block.label:
                label_visible, budget = _truncate(_sanitize(block.label), budget)
                if label_visible:
                    lines.append(_esc(label_visible))
            for choice in block.choices:
                if budget <= 0:
                    break
                choice_visible, budget = _truncate(_sanitize(choice.label), budget)
                if not choice_visible:
                    continue
                line = f"• <b>{_esc(choice_visible)}</b>"
                if choice.description and budget > 0:
                    desc_visible, budget = _truncate(
                        _sanitize(choice.description), budget
                    )
                    if desc_visible:
                        line += f" — {_esc(desc_visible)}"
                lines.append(line)
            if lines:
                parts.append("\n".join(lines))
    return parts


def render_message(
    intent: NotificationIntent,
    *,
    public_base_url: str,
    preview_chars: int,
    title_chars: int,
) -> OutboundMessage:
    """Render an intent into a bounded Telegram-HTML message.

    The subject is a bold title, the content preview a blockquote, and choices a
    bulleted list. Every content string is escaped; visible length is bounded so
    the escaped result stays well under Telegram's message limit.
    """
    label = _KIND_LABEL.get(intent.kind, "Notification")
    title_visible, _ = _truncate(_sanitize(f"{label}: {intent.subject}"), title_chars)
    parts = [f"<b>{_esc(title_visible)}</b>"]
    parts.extend(_render_blocks_html(intent.preview_blocks, preview_chars))
    text = "\n\n".join(part for part in parts if part)
    button_label = "Open inbox item" if intent.kind == "inbox" else "Open session"
    return OutboundMessage(
        intent_id=intent.dedupe_key,
        text=text,
        url=f"{public_base_url}{intent.target_path}",
        button_label=button_label,
    )
