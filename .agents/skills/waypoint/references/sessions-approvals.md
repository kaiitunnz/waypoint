# Approvals

Use approvals when a coding backend is waiting for a tool decision.

```bash
waypoint sessions approve <session-id> <decision>
waypoint sessions approve <session-id> <decision> --approval-id <id>
waypoint sessions approve <session-id> <decision> --text <message>
```

Read the pending request from
`waypoint sessions events <session-id> --messages 20 --compact` before
approving. If multiple approvals are pending, pass `--approval-id` when the
transcript exposes one. Use `events --coalesce` or raw `events` only if you need
full metadata, exact event ordering, duplicate diagnosis, or per-chunk metadata.

Do not approve destructive, privileged, or unclear requests without user
confirmation.

## Plans

A plan-mode session (backends with `supports_plan_approval`, e.g. codex) ends
its turn with a plan: an `item_type: plan` event in `events --compact`. Decide
it with `approve-plan`, passing that event's `item_id`; `approve` does not
release it.

```bash
waypoint sessions approve-plan <session-id> --plan-item-id <item_id>
waypoint sessions approve-plan <session-id> --plan-item-id <item_id> --decision decline --text "<why>"
```

`accept` (default) and `acceptForSession` leave plan mode and run the plan;
`decline` and `cancel` keep the session in plan mode. Decided plans show
`plan_item_id` and `plan_decision` on the following system event.

## Questions

A session can also block on a question (an `AskUserQuestion` prompt), which is
not an approval. It surfaces as a `tool_call` with
`tool_name: AskUserQuestion`, so `approve` does not release it — answer it
instead:

```bash
waypoint sessions answer-question <session-id> --answer "<your answer>"
waypoint sessions answer-question <session-id> --answer "<text>" --tool-use-id <id>
waypoint sessions answer-question <session-id> --answers-json '[{"question": "...", "answer": "..."}]'
```

`sessions send` injects a normal message and will **not** satisfy the blocking
question; only `answer-question` does. Pass `--tool-use-id` when several
questions are pending.

To dismiss a question without answering it, cancel it. The agent sees it
declined and may end its turn (Claude does), so send a follow-up if it should
carry on:

```bash
waypoint sessions cancel-question <session-id> [--tool-use-id <id>]
```
