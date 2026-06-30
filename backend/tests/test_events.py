from waypoint.events import (
    coalesce_events,
    is_todo_list_event,
    is_tool_result_delta,
    merge_event_text,
    read_item_id,
)


def test_read_item_id():
    assert read_item_id({}) is None
    assert read_item_id({"metadata": {}}) is None
    assert read_item_id({"metadata": {"item_id": "123"}}) == "123"
    assert read_item_id({"metadata": {"item_id": ""}}) is None
    assert read_item_id({"metadata": {"item_id": 123}}) is None


def test_is_tool_result_delta():
    assert not is_tool_result_delta({})
    assert not is_tool_result_delta(
        {
            "kind": "agent_output",
            "metadata": {"method": "item/commandExecution/outputDelta"},
        }
    )
    assert is_tool_result_delta(
        {
            "kind": "tool_result",
            "metadata": {"method": "item/commandExecution/outputDelta"},
        }
    )
    assert is_tool_result_delta(
        {"kind": "tool_result", "metadata": {"method": "item/fileChange/outputDelta"}}
    )


def test_is_todo_list_event():
    assert not is_todo_list_event({})
    assert is_todo_list_event({"metadata": {"item_type": "todo_list"}})
    assert is_todo_list_event({"metadata": {"tool_name": "TodoWrite"}})
    assert is_todo_list_event({"metadata": {"tool_name": "default_api:todowrite"}})
    assert is_todo_list_event({"metadata": {"tool_name": "default_api:TodoWrite"}})


def test_merge_event_text():
    # agent_output concatenates
    assert (
        merge_event_text(
            {"kind": "agent_output", "text": "Hello "},
            {"kind": "agent_output", "text": "World"},
        )
        == "Hello World"
    )

    # tool_result todo list replaces
    assert (
        merge_event_text(
            {
                "kind": "tool_result",
                "text": "Old",
                "metadata": {"tool_name": "TodoWrite"},
            },
            {
                "kind": "tool_result",
                "text": "New",
                "metadata": {"tool_name": "TodoWrite"},
            },
        )
        == "New"
    )
    assert (
        merge_event_text(
            {
                "kind": "tool_result",
                "text": "Old",
                "metadata": {"tool_name": "TodoWrite"},
            },
            {"kind": "tool_result", "text": "", "metadata": {"tool_name": "TodoWrite"}},
        )
        == "Old"
    )

    # tool_result delta concatenates
    assert (
        merge_event_text(
            {"kind": "tool_result", "text": "Line 1"},
            {
                "kind": "tool_result",
                "text": "Line 2",
                "metadata": {"method": "item/commandExecution/outputDelta"},
            },
        )
        == "Line 1Line 2"
    )

    # a final non-delta tool result keeps streamed delta text
    assert (
        merge_event_text(
            {
                "kind": "tool_result",
                "text": "Delta",
                "metadata": {"method": "item/commandExecution/outputDelta"},
            },
            {"kind": "tool_result", "text": "Final"},
        )
        == "Delta"
    )


def test_coalesce_events():
    # Dedup by id
    events = [
        {"id": 1, "kind": "user_input", "text": "test", "sequence": 1},
        {"id": 1, "kind": "user_input", "text": "test2", "sequence": 2},
    ]
    assert len(coalesce_events(events)) == 1

    # Dedup by sequence
    events = [
        {"kind": "user_input", "text": "test", "sequence": 1},
        {"kind": "user_input", "text": "test2", "sequence": 1},
    ]
    assert len(coalesce_events(events)) == 1

    # agent_output concat
    events = [
        {
            "kind": "agent_output",
            "text": "A",
            "sequence": 1,
            "metadata": {"item_id": "1"},
        },
        {
            "kind": "agent_output",
            "text": "B",
            "sequence": 2,
            "metadata": {"item_id": "1", "extra": "data"},
        },
        {
            "kind": "agent_output",
            "text": "C",
            "sequence": 3,
            "metadata": {"item_id": "1", "version": 2},
        },
    ]
    res = coalesce_events(events)
    assert len(res) == 1
    assert res[0]["text"] == "ABC"
    assert res[0]["sequence"] == 3
    assert res[0]["metadata"]["version"] == 2
    assert res[0]["metadata"]["extra"] == "data"

    # tool_result concat
    events = [
        {
            "kind": "tool_result",
            "text": "A\n",
            "sequence": 1,
            "metadata": {"item_id": "2"},
        },
        {
            "kind": "tool_result",
            "text": "B",
            "sequence": 2,
            "metadata": {"item_id": "2"},
        },
    ]
    res = coalesce_events(events)
    assert len(res) == 1
    assert res[0]["text"] == "A\nB"

    # non-mergeable passes through
    events = [
        {"kind": "user_input", "text": "test", "sequence": 1},
        {
            "kind": "tool_call",
            "text": "call",
            "sequence": 2,
            "metadata": {"item_id": "1"},
        },
    ]
    assert len(coalesce_events(events)) == 2
