from datetime import UTC, datetime

from waypoint.backends.events import InteractionChoice, InteractionEnvelope
from waypoint.notifications.render import (
    intent_from_event,
    intent_from_inbox_item,
    render_message,
)
from waypoint.schemas import (
    EventKind,
    EventRecord,
    InboxApprovalBlock,
    InboxAttachmentBlock,
    InboxAttachmentRef,
    InboxItem,
    InboxMarkdownBlock,
    InboxQuestionBlock,
    InboxQuestionOption,
)

BASE_URL = "https://wp.example.ts.net"


def _now() -> datetime:
    return datetime(2026, 7, 18, tzinfo=UTC)


def _inbox_item(
    blocks: list, subject: str = "Subject", from_label: str | None = "S"
) -> InboxItem:
    return InboxItem(
        id="item123",
        from_session_id="sess1",
        from_label=from_label,
        subject=subject,
        created_at=_now(),
        updated_at=_now(),
        blocks=blocks,
    )


def _render(intent, preview_chars: int = 900) -> str:
    return render_message(
        intent, public_base_url=BASE_URL, preview_chars=preview_chars, title_chars=160
    ).text


def test_inbox_markdown_question_approval_attachment() -> None:
    item = _inbox_item(
        [
            InboxMarkdownBlock(id="a", text="Please review"),
            InboxQuestionBlock(
                id="b",
                question="Ship it?",
                options=[
                    InboxQuestionOption(label="yes", description="go"),
                    InboxQuestionOption(label="no"),
                ],
            ),
            InboxApprovalBlock(id="c", prompt="Approve deploy", options=["approve"]),
            InboxAttachmentBlock(
                id="d",
                ref=InboxAttachmentRef(
                    session_id="s", attachment_id="x", filename="log.txt"
                ),
            ),
        ]
    )
    intent = intent_from_inbox_item(item)
    assert intent.dedupe_key == "inbox:item123"
    text = _render(intent)
    assert "Inbox: Subject" in text
    assert "From: S" in text
    assert "Ship it?" in text
    assert "• yes — go" in text
    assert "Approve deploy" in text
    assert "Attachment: log.txt" in text
    msg = render_message(
        intent, public_base_url=BASE_URL, preview_chars=900, title_chars=160
    )
    assert msg.url == f"{BASE_URL}/inbox/item123"
    assert msg.button_label == "Open inbox item"


def test_attachment_content_never_embedded() -> None:
    item = _inbox_item(
        [
            InboxAttachmentBlock(
                id="d",
                ref=InboxAttachmentRef(
                    session_id="s", attachment_id="secret", filename="private.pdf"
                ),
            )
        ]
    )
    text = _render(intent_from_inbox_item(item))
    assert "Attachment: private.pdf" in text
    assert "secret" not in text  # the attachment id/handle is never leaked


def test_control_characters_and_markup_are_plaintext() -> None:
    item = _inbox_item(
        [
            InboxMarkdownBlock(
                id="a", text="line\x07one\x00\n\n\n\ntwo <b>bold</b> `code`"
            )
        ]
    )
    text = _render(intent_from_inbox_item(item))
    assert "\x07" not in text and "\x00" not in text
    assert "\n\n\n" not in text
    # Markup passes through as literal characters (no parse_mode interpretation).
    assert "<b>bold</b>" in text


def test_unicode_preserved() -> None:
    item = _inbox_item([InboxMarkdownBlock(id="a", text="日本語 — émoji 🚀")])
    text = _render(intent_from_inbox_item(item))
    assert "日本語" in text and "🚀" in text


def test_truncation_appends_ellipsis() -> None:
    item = _inbox_item([InboxMarkdownBlock(id="a", text="x" * 5000)])
    text = _render(intent_from_inbox_item(item), preview_chars=100)
    assert text.endswith("…")
    assert len(text) < 400


def test_title_truncated_to_limit() -> None:
    item = _inbox_item([], subject="S" * 500)
    msg = render_message(
        intent_from_inbox_item(item),
        public_base_url=BASE_URL,
        preview_chars=900,
        title_chars=50,
    )
    first_line = msg.text.splitlines()[0]
    assert len(first_line) <= 50
    assert first_line.endswith("…")


def _event_with_interaction(envelope: InteractionEnvelope) -> EventRecord:
    return EventRecord(
        session_id="sessX",
        ts=_now(),
        kind=EventKind.APPROVAL_REQUEST,
        text="x",
        metadata={"interaction": envelope.to_metadata()},
        sequence=1,
    )


def test_event_intent_for_approval() -> None:
    env = InteractionEnvelope(
        kind="approval",
        request_id="req9",
        title="Approve Bash",
        body="pytest -q",
        choices=[
            InteractionChoice(label="approve"),
            InteractionChoice(label="decline"),
        ],
    )
    intent = intent_from_event(_event_with_interaction(env), session_title="My Session")
    assert intent is not None
    assert intent.dedupe_key == "event:sessX:approval:req9"
    msg = render_message(
        intent, public_base_url=BASE_URL, preview_chars=900, title_chars=160
    )
    assert msg.url == f"{BASE_URL}/session/sessX"
    assert msg.button_label == "Open session"
    assert "Approval needed: Approve Bash" in msg.text
    assert "Session: My Session" in msg.text
    assert "• approve" in msg.text


def test_event_intent_for_plan_uses_plan_item_id() -> None:
    env = InteractionEnvelope(
        kind="plan_approval",
        request_id="tool9",
        title="Approve plan",
        body="Step 1\nStep 2",
        plan_item_id="plan42",
    )
    intent = intent_from_event(_event_with_interaction(env), session_title=None)
    assert intent is not None
    assert intent.dedupe_key == "event:sessX:plan_approval:plan42"


def test_no_interaction_returns_none() -> None:
    event = EventRecord(
        session_id="s",
        ts=_now(),
        kind=EventKind.AGENT_OUTPUT,
        text="hi",
        metadata={},
        sequence=1,
    )
    assert intent_from_event(event, None) is None


def test_malformed_interaction_returns_none() -> None:
    event = EventRecord(
        session_id="s",
        ts=_now(),
        kind=EventKind.APPROVAL_REQUEST,
        text="x",
        metadata={"interaction": {"kind": "bogus"}},
        sequence=1,
    )
    assert intent_from_event(event, None) is None


def test_link_never_contains_credentials() -> None:
    env = InteractionEnvelope(kind="approval", request_id="r", title="t")
    intent = intent_from_event(_event_with_interaction(env), session_title="Sess")
    assert intent is not None
    msg = render_message(
        intent, public_base_url=BASE_URL, preview_chars=900, title_chars=160
    )
    assert msg.url == f"{BASE_URL}/session/sessX"
    assert "?" not in msg.url and "token" not in msg.url.lower()
