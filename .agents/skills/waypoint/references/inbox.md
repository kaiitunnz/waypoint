# Inbox — durable human checkpoints

The inbox is the one channel that reaches the **human**, not another session:
post a message the user triages in the inbox UI, and optionally block until they
answer. Use it to gate on a human decision — a crew/workqueue lead pausing at a
phase boundary, or any session that needs the user to choose before it proceeds.
Answers live on the item and are read back over the API; they are never injected
into an agent's input. Run `waypoint help` for exact flags.

## Post an item

`waypoint inbox post --json <file|->` — the body is JSON because an item is an
ordered list of typed **blocks**:

```json
{
  "subject": "Approve the PRD?",
  "from_session_id": "<your session id>",
  "blocks": [
    { "type": "markdown", "text": "## Scope\n- ..." },
    { "type": "question", "question": "Which stack?",
      "options": [{ "label": "Next.js" }, { "label": "SvelteKit" }],
      "multi": false, "required": true },
    { "type": "approval", "prompt": "Approve and build?",
      "options": ["approve", "request changes"], "required": true },
    { "type": "attachment", "ref": { "session_id": "<sid>", "attachment_id": "<id>" } }
  ]
}
```

An item **resolves** once every required question/approval block is answered; an
item with no required blocks is a pure FYI that resolves when the user reads it.
Post prints the created item (its `id` and per-block `id`s).

## Block until the user decides

`waypoint inbox wait <item-id> [--until resolved|update] [--timeout 30m]` blocks
and prints `{"outcome", "item"}`. Exit codes let a shell chain branch:

- `0` — `resolved` (all required blocks answered) or `update` (first change past `--since`)
- `124` — `timeout`
- `3` — `gone` (the item was deleted)

`--until resolved` is the default (wait for the decision); `--until update` wakes
on the first change. It prefers the live stream and falls back to polling.

## Read the answer back

`waypoint inbox get <item-id>` returns the item with each block's `answer`
(`{"selected": [...]}` for a question, `{"decision": "..."}` for an approval) and
any `reply`. The lead reads the decision here and acts on it (e.g. records a
crew's `approved=` cell). `waypoint inbox list` (status filter, search,
load-more) enumerates items; `answer`, `read`, and `delete` are the remaining
scripting verbs — the UI is the primary answer path.

## The pattern

Post → wait → get: `id=$(… inbox post --json body.json | jq -r .item.id)`, then
`waypoint inbox wait "$id" --until resolved`, then `waypoint inbox get "$id"` to
branch on the answer. Reserve it for genuine human gates — between them, run
autonomously.
