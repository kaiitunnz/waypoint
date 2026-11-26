"""OpenCode 2.x forms and model catalog as Waypoint sees them."""

from typing import Any

import pytest

from waypoint.backends.opencode.catalog import model_id, model_ref, parse_models
from waypoint.backends.opencode.forms import Form, parse_form


def _form(*fields: dict[str, Any]) -> Form:
    form = parse_form({"id": "frm_1", "sessionID": "ses_1", "fields": list(fields)})
    assert form is not None
    return form


def _choice(key: str, kind: str = "string", **extra: Any) -> dict[str, Any]:
    return {
        "key": key,
        "type": kind,
        "title": f"{key} title",
        "description": f"{key}?",
        "options": [
            {"value": "r", "label": "Red"},
            {"value": "b", "label": "Blue, dark", "description": "navy"},
        ],
    } | extra


def test_fields_become_question_cards() -> None:
    form = _form(
        _choice("colour", custom=True),
        {"key": "name", "type": "string", "title": "Name"},
        {"key": "ok", "type": "boolean", "description": "Proceed?"},
        {"key": "secret", "type": "string", "hidden": True},
    )

    colour, name, ok = form.questions()

    assert colour == {
        "question": "colour?",
        "header": "colour title",
        "options": [
            {"label": "Red"},
            {"label": "Blue, dark", "description": "navy"},
        ],
        "multiSelect": False,
    }
    assert name == {"question": "Name", "options": [], "multiSelect": False}
    assert [option["label"] for option in ok["options"]] == ["Yes", "No"]


@pytest.mark.parametrize(
    ("field", "answer", "notes", "expected"),
    [
        (_choice("k"), "Red", "", "r"),
        (_choice("k"), "Blue, dark", "", "b"),
        (_choice("k"), "Green", "", None),
        (_choice("k", custom=True), "Green", "", "Green"),
        (_choice("k", custom=True), "Red", "and bright", "r and bright"),
        (_choice("k", "multiselect"), "Blue, dark", "", ["b"]),
        (_choice("k", "multiselect"), "Red,Red", "ignored", ["r", "r"]),
        (_choice("k", "multiselect", custom=True), "Red", "more", ["r", "more"]),
        ({"key": "k", "type": "string"}, "", "typed note", "typed note"),
        ({"key": "k", "type": "integer"}, "7", "", 7),
        ({"key": "k", "type": "number"}, "no", "", None),
        ({"key": "k", "type": "boolean"}, "No", "", False),
    ],
)
def test_an_answer_becomes_the_field_value(
    field: dict[str, Any], answer: str, notes: str, expected: Any
) -> None:
    reply = _form(field).reply(answer, [{"answer": answer, "notes": notes}])

    assert reply == ({} if expected is None else {"k": expected})


def test_answers_match_fields_by_question_then_position() -> None:
    form = _form(_choice("first"), _choice("second"))

    assert form.reply(
        "", [{"question": "second?", "answer": "Red"}, {"answer": "Blue, dark"}]
    ) == {"second": "b"}
    assert form.reply(
        "", [{"question": "first?", "answer": "Red"}, {"answer": "Blue, dark"}]
    ) == {"first": "r", "second": "b"}
    assert form.reply("Red", None) == {"first": "r"}


def test_a_field_whose_condition_fails_is_not_answered() -> None:
    form = _form(
        {"key": "more", "type": "boolean"},
        {
            "key": "detail",
            "type": "string",
            "when": [{"key": "more", "op": "eq", "value": True}],
        },
    )

    assert form.reply("", [{"answer": "No"}, {"answer": "x"}]) == {"more": False}
    assert form.reply("", [{"answer": "Yes"}, {"answer": "x"}]) == {
        "more": True,
        "detail": "x",
    }


def test_the_catalog_lists_selectable_chat_models() -> None:
    base = {"providerID": "p", "enabled": True, "status": "active"}
    models = parse_models(
        [
            base
            | {
                "id": "a/b",
                "name": "A",
                "variants": [{"id": "high"}],
                "limit": {"context": 100},
            },
            base | {"id": "old", "status": "deprecated"},
            base | {"id": "off", "enabled": False},
            base | {"id": "pic", "type": "image"},
        ]
    )

    [model] = models
    assert (model.id, model.name, model.variants, model.context_window) == (
        "p/a/b",
        "A",
        ("high",),
        100,
    )
    assert model_ref(model.id, "high") == {
        "providerID": "p",
        "id": "a/b",
        "variant": "high",
    }
    assert model_id(model_ref(model.id)) == model.id
    assert model_ref("nonsense") is None
