from typing import Any


def read_item_id(event: dict[str, Any]) -> str | None:
    """Extract item_id from event metadata."""
    meta = event.get("metadata", {})
    item_id = meta.get("item_id")
    if isinstance(item_id, str) and item_id:
        return item_id
    return None


def is_tool_result_delta(event: dict[str, Any]) -> bool:
    """Check if event is a tool result delta."""
    if event.get("kind") != "tool_result":
        return False
    meta = event.get("metadata", {})
    method = meta.get("method")
    return method in (
        "item/commandExecution/outputDelta",
        "item/fileChange/outputDelta",
    )


def is_todo_list_event(event: dict[str, Any]) -> bool:
    """Check if event is a todo list."""
    meta = event.get("metadata", {})
    if meta.get("item_type") == "todo_list":
        return True
    tool_name = meta.get("tool_name")
    if isinstance(tool_name, str):
        if tool_name.startswith("default_api:"):
            tool_name = tool_name[len("default_api:") :]
        if tool_name.lower() == "todowrite":
            return True
    return False


def merge_event_text(existing: dict[str, Any], incoming: dict[str, Any]) -> str:
    """Port of frontend mergeEventText."""
    if incoming.get("kind") == "agent_output":
        return existing.get("text", "") + incoming.get("text", "")

    if incoming.get("kind") != "tool_result":
        return incoming.get("text", "")

    if is_todo_list_event(existing) or is_todo_list_event(incoming):
        return incoming.get("text", "") or existing.get("text", "")

    if is_tool_result_delta(incoming):
        return existing.get("text", "") + incoming.get("text", "")

    if is_tool_result_delta(existing):
        return existing.get("text", "") or incoming.get("text", "")

    existing_text = existing.get("text", "")
    incoming_text = incoming.get("text", "")

    if not existing_text:
        return incoming_text

    if not incoming_text or existing_text == incoming_text:
        return existing_text

    separator = (
        "" if existing_text.endswith("\n") or incoming_text.startswith("\n") else "\n"
    )
    return existing_text + separator + incoming_text


def coalesce_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk events in ascending sequence order, one pass."""
    result: list[dict[str, Any]] = []
    lookup: dict[tuple[str, str], int] = {}
    seen_ids: set[int] = set()
    seen_sequences: set[int] = set()

    for event in events:
        event_id = event.get("id")
        seq = event.get("sequence")

        if event_id is not None:
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)

        if seq is not None:
            if seq in seen_sequences:
                continue
            seen_sequences.add(seq)

        kind = event.get("kind")
        item_id = read_item_id(event)

        if kind in ("agent_output", "tool_result") and item_id:
            key = (kind, item_id)
            if key in lookup:
                idx = lookup[key]
                existing = result[idx]

                # Merge text
                existing["text"] = merge_event_text(existing, event)

                # Update ts, sequence, metadata
                existing["ts"] = event.get("ts", existing.get("ts"))
                existing["sequence"] = event.get("sequence", existing.get("sequence"))
                existing["metadata"] = {
                    **existing.get("metadata", {}),
                    **event.get("metadata", {}),
                }
            else:
                # new entry
                new_event = dict(event)
                idx = len(result)
                result.append(new_event)
                lookup[key] = idx
        else:
            # Everything else
            result.append(dict(event))

    return result
