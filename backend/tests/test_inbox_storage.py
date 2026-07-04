import pytest

from waypoint.schemas import (
    InboxApprovalBlockInput,
    InboxAttachmentBlockInput,
    InboxAttachmentRef,
    InboxItem,
    InboxMarkdownBlockInput,
    InboxQuestionBlock,
    InboxQuestionBlockInput,
    InboxQuestionOption,
    InboxReplyInput,
    InboxStatus,
)
from waypoint.storage import (
    InboxBlockNotFoundError,
    InboxBlockTypeError,
    Storage,
)


def _storage(tmp_path) -> Storage:
    return Storage(tmp_path / "waypoint.db")


def _question(required: bool = True) -> InboxQuestionBlockInput:
    return InboxQuestionBlockInput(
        question="Ship it?",
        options=[InboxQuestionOption(label="yes"), InboxQuestionOption(label="no")],
        required=required,
    )


def _approval(required: bool = True) -> InboxApprovalBlockInput:
    return InboxApprovalBlockInput(
        prompt="Approve the plan?", options=["approve", "reject"], required=required
    )


def _submit_ok(storage: Storage, item_id: str, block_id: str, **kwargs) -> InboxItem:
    # submit_inbox_block returns (item, changed) on success; most tests only
    # care about the resulting item, so unwrap and assert it wasn't gone.
    result = storage.submit_inbox_block(item_id, block_id, **kwargs)
    assert result is not None
    item, _ = result
    return item


def test_create_assigns_ids_and_starts_open(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item(
        from_session_id="s1",
        from_label="Lead",
        subject="PRD ready",
        blocks=[InboxMarkdownBlockInput(text="# summary"), _question()],
    )
    assert item.status is InboxStatus.OPEN
    assert item.version == 0
    assert item.read_at is None
    assert all(block.id for block in item.blocks)
    # Distinct server-assigned ids.
    assert len({block.id for block in item.blocks}) == 2

    reloaded = storage.get_inbox_item(item.id)
    assert reloaded is not None
    assert reloaded.model_dump() == item.model_dump()


def test_answering_required_block_resolves_and_bumps_version(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item("s1", None, "gate", [_question()])
    qid = item.blocks[0].id

    updated = _submit_ok(storage, item.id, qid, answer={"selected": ["yes"]})
    assert updated.status is InboxStatus.RESOLVED
    assert updated.version == 1
    block = updated.blocks[0]
    assert isinstance(block, InboxQuestionBlock)
    assert block.answer is not None
    assert block.answer.selected == ["yes"]
    assert block.answered_at is not None


def test_optional_only_item_does_not_resolve_on_answer(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item("s1", None, "fyi+opt", [_question(required=False)])
    qid = item.blocks[0].id

    updated = _submit_ok(storage, item.id, qid, answer={"selected": ["no"]})
    # No required interactive block gates it: stays open, resolves only on read.
    assert updated.status is InboxStatus.OPEN
    assert updated.version == 1


def test_no_action_item_resolves_on_read(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item(
        "s1", None, "fyi", [InboxMarkdownBlockInput(text="hi")]
    )
    assert item.status is InboxStatus.OPEN

    result = storage.mark_inbox_read(item.id)
    assert result is not None
    read, changed = result
    assert changed is True
    assert read.status is InboxStatus.RESOLVED
    assert read.read_at is not None
    assert read.version == 1  # resolve-on-read is a real state change


def test_read_is_idempotent_and_no_rebump(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item(
        "s1", None, "fyi", [InboxMarkdownBlockInput(text="hi")]
    )
    first_result = storage.mark_inbox_read(item.id)
    assert first_result is not None
    first, first_changed = first_result
    assert first_changed is True and first.version == 1

    second_result = storage.mark_inbox_read(item.id)
    assert second_result is not None
    second, second_changed = second_result
    assert second_changed is False  # no-op re-read → no broadcast
    assert second.version == 1  # no re-bump on repeat read
    assert second.read_at == first.read_at


def test_read_of_interactive_item_does_not_bump_version(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item("s1", None, "gate", [_question()])
    result = storage.mark_inbox_read(item.id)
    assert result is not None
    read, changed = result
    assert changed is True  # first read stamps read_at
    assert read.status is InboxStatus.OPEN
    assert read.read_at is not None
    assert read.version == 0  # plain read never bumps version


def test_resolved_is_terminal_no_demotion_on_later_reply(tmp_path) -> None:
    storage = _storage(tmp_path)
    # No-action item, resolved on read.
    item = storage.create_inbox_item(
        "s1", None, "fyi", [InboxMarkdownBlockInput(text="hi")]
    )
    result = storage.mark_inbox_read(item.id)
    assert result is not None
    read, _ = result
    assert read.status is InboxStatus.RESOLVED
    assert storage.unresolved_inbox_count() == 0

    bid = item.blocks[0].id
    replied = _submit_ok(storage, item.id, bid, reply=InboxReplyInput(notes="thanks"))
    assert replied.status is InboxStatus.RESOLVED  # not demoted back to open
    assert replied.version == read.version + 1  # reply still bumps version
    assert storage.unresolved_inbox_count() == 0


def test_multi_block_resolution_gates_on_all_required(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item("s1", None, "two-gate", [_question(), _approval()])
    qid, aid = item.blocks[0].id, item.blocks[1].id

    after_q = _submit_ok(storage, item.id, qid, answer={"selected": ["yes"]})
    assert after_q.status is InboxStatus.OPEN

    after_a = _submit_ok(storage, item.id, aid, answer={"decision": "approve"})
    assert after_a.status is InboxStatus.RESOLVED


def test_reply_allowed_on_any_block_type(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item(
        "s1", None, "m", [InboxMarkdownBlockInput(text="prose")]
    )
    bid = item.blocks[0].id
    ref = InboxAttachmentRef(session_id="s1", attachment_id="a" * 32)
    updated = _submit_ok(
        storage,
        item.id,
        bid,
        reply=InboxReplyInput(notes="see file", attachments=[ref]),
    )
    assert updated.blocks[0].reply is not None
    assert updated.blocks[0].reply.notes == "see file"
    assert updated.blocks[0].reply.attachments[0].attachment_id == "a" * 32


def test_answer_on_wrong_block_type_raises(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item(
        "s1", None, "m", [InboxMarkdownBlockInput(text="prose")]
    )
    bid = item.blocks[0].id
    with pytest.raises(InboxBlockTypeError):
        storage.submit_inbox_block(item.id, bid, answer={"selected": ["x"]})


def test_mismatched_answer_shape_raises(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item("s1", None, "gate", [_approval()])
    bid = item.blocks[0].id
    # A question-shaped answer on an approval block is rejected.
    with pytest.raises(InboxBlockTypeError):
        storage.submit_inbox_block(item.id, bid, answer={"selected": ["yes"]})


def test_answer_with_unknown_option_raises(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item("s1", None, "gate", [_question()])
    bid = item.blocks[0].id
    with pytest.raises(InboxBlockTypeError):
        storage.submit_inbox_block(item.id, bid, answer={"selected": ["maybe"]})


def test_single_select_question_rejects_multiple(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item("s1", None, "gate", [_question()])  # multi False
    bid = item.blocks[0].id
    with pytest.raises(InboxBlockTypeError):
        storage.submit_inbox_block(item.id, bid, answer={"selected": ["yes", "no"]})


def test_multi_select_question_accepts_multiple(tmp_path) -> None:
    storage = _storage(tmp_path)
    multi = InboxQuestionBlockInput(
        question="Pick any",
        options=[InboxQuestionOption(label="a"), InboxQuestionOption(label="b")],
        multi=True,
        required=True,
    )
    item = storage.create_inbox_item("s1", None, "gate", [multi])
    bid = item.blocks[0].id
    updated = _submit_ok(storage, item.id, bid, answer={"selected": ["a", "b"]})
    assert updated.status is InboxStatus.RESOLVED


def test_required_question_rejects_empty_answer(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item("s1", None, "gate", [_question()])
    bid = item.blocks[0].id
    # A content-free answer must not silently satisfy a required gate.
    with pytest.raises(InboxBlockTypeError):
        storage.submit_inbox_block(item.id, bid, answer={"selected": []})
    assert storage.unresolved_inbox_count() == 1


def test_required_question_accepts_other_only_answer(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item("s1", None, "gate", [_question()])
    bid = item.blocks[0].id
    updated = _submit_ok(
        storage, item.id, bid, answer={"selected": [], "other": "something else"}
    )
    assert updated.status is InboxStatus.RESOLVED


def test_optional_question_accepts_empty_answer(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item("s1", None, "fyi", [_question(required=False)])
    bid = item.blocks[0].id
    updated = _submit_ok(storage, item.id, bid, answer={"selected": []})
    assert updated.status is InboxStatus.OPEN  # lenient: optional blocks don't gate


def test_approval_with_off_menu_decision_raises(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item("s1", None, "gate", [_approval()])
    bid = item.blocks[0].id
    with pytest.raises(InboxBlockTypeError):
        storage.submit_inbox_block(item.id, bid, answer={"decision": "maybe"})


def test_missing_block_raises_not_found(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item("s1", None, "gate", [_question()])
    with pytest.raises(InboxBlockNotFoundError):
        storage.submit_inbox_block(item.id, "nope", answer={"selected": ["yes"]})


def test_submit_on_missing_item_returns_none(tmp_path) -> None:
    storage = _storage(tmp_path)
    assert storage.submit_inbox_block("ghost", "b", answer={"selected": ["y"]}) is None
    assert storage.mark_inbox_read("ghost") is None
    assert storage.get_inbox_item("ghost") is None


def test_noop_submit_reports_unchanged_without_bump(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item("s1", None, "gate", [_question()])
    bid = item.blocks[0].id
    # Neither answer nor reply: nothing changes, so the runtime can skip the
    # broadcast and the version must not bump.
    result = storage.submit_inbox_block(item.id, bid)
    assert result is not None
    unchanged, changed = result
    assert changed is False
    assert unchanged.version == item.version


def test_delete_removes_row(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item("s1", None, "gate", [_question()])
    assert storage.delete_inbox_item(item.id) is True
    assert storage.get_inbox_item(item.id) is None
    assert storage.delete_inbox_item(item.id) is False


def test_delete_items_removes_only_known_ids(tmp_path) -> None:
    storage = _storage(tmp_path)
    a = storage.create_inbox_item("s1", None, "a", [_question()])
    b = storage.create_inbox_item("s1", None, "b", [_question()])
    c = storage.create_inbox_item("s1", None, "c", [_question()])

    deleted = storage.delete_inbox_items([a.id, c.id, "nope", a.id])
    assert set(deleted) == {a.id, c.id}
    assert storage.get_inbox_item(a.id) is None
    assert storage.get_inbox_item(c.id) is None
    assert storage.get_inbox_item(b.id) is not None


def test_delete_items_empty_list_is_noop(tmp_path) -> None:
    storage = _storage(tmp_path)
    storage.create_inbox_item("s1", None, "a", [_question()])
    assert storage.delete_inbox_items([]) == []
    assert storage.unresolved_inbox_count() == 1


def test_delete_resolved_leaves_open_items(tmp_path) -> None:
    storage = _storage(tmp_path)
    open_item = storage.create_inbox_item("s1", None, "open", [_question()])
    resolved_a = storage.create_inbox_item("s1", None, "res-a", [_question()])
    resolved_b = storage.create_inbox_item("s1", None, "res-b", [_question()])
    for item in (resolved_a, resolved_b):
        storage.submit_inbox_block(
            item.id, item.blocks[0].id, answer={"selected": ["yes"]}
        )

    deleted = storage.delete_resolved_inbox_items()
    assert set(deleted) == {resolved_a.id, resolved_b.id}
    assert storage.get_inbox_item(open_item.id) is not None
    assert storage.get_inbox_item(resolved_a.id) is None
    # Idempotent once the resolved folder is empty.
    assert storage.delete_resolved_inbox_items() == []


def test_unresolved_count_tracks_open_items(tmp_path) -> None:
    storage = _storage(tmp_path)
    storage.create_inbox_item("s1", None, "a", [_question()])
    storage.create_inbox_item("s1", None, "b", [_question()])
    assert storage.unresolved_inbox_count() == 2
    item_c = storage.create_inbox_item("s1", None, "c", [_question()])
    storage.submit_inbox_block(
        item_c.id, item_c.blocks[0].id, answer={"selected": ["yes"]}
    )
    assert storage.unresolved_inbox_count() == 2


def test_list_filter_search_and_pagination(tmp_path) -> None:
    storage = _storage(tmp_path)
    created = []
    for i in range(5):
        created.append(
            storage.create_inbox_item("s1", "Lead", f"item {i}", [_question()])
        )
    # Resolve the first two so the status filter has something to exclude.
    for item in created[:2]:
        storage.submit_inbox_block(
            item.id, item.blocks[0].id, answer={"selected": ["yes"]}
        )

    open_items, has_more, cursor = storage.list_inbox_items(
        status=InboxStatus.OPEN, limit=2
    )
    assert len(open_items) == 2
    assert has_more is True
    assert cursor is not None

    page2, has_more2, _ = storage.list_inbox_items(
        status=InboxStatus.OPEN, limit=2, cursor=cursor
    )
    assert len(page2) == 1
    assert has_more2 is False
    # No overlap between pages.
    assert {i.id for i in open_items}.isdisjoint({i.id for i in page2})

    # Search over subject.
    hits, _, _ = storage.list_inbox_items(query="item 3")
    assert [i.subject for i in hits] == ["item 3"]

    # Search over from_label.
    label_hits, _, _ = storage.list_inbox_items(query="Lead")
    assert len(label_hits) == 5


def test_blocks_round_trip_through_reopen(tmp_path) -> None:
    db = tmp_path / "waypoint.db"
    storage = Storage(db)
    item = storage.create_inbox_item(
        "s1",
        "Lead",
        "mixed",
        [
            InboxMarkdownBlockInput(text="# hi"),
            InboxAttachmentBlockInput(
                ref=InboxAttachmentRef(session_id="s1", attachment_id="b" * 32)
            ),
            _question(),
            _approval(),
        ],
    )
    storage.submit_inbox_block(item.id, item.blocks[2].id, answer={"selected": ["yes"]})
    storage.close()

    reopened = Storage(db)
    got = reopened.get_inbox_item(item.id)
    assert got is not None
    assert [b.type for b in got.blocks] == [b.type for b in item.blocks]
    question = got.blocks[2]
    assert isinstance(question, InboxQuestionBlock)
    assert question.answer is not None
