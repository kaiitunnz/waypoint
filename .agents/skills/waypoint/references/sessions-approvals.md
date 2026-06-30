# Approvals

Use approvals when a coding backend is waiting for a plan or tool decision.

```bash
waypoint sessions approve <session-id> <decision>
waypoint sessions approve <session-id> <decision> --approval-id <id>
waypoint sessions approve <session-id> <decision> --text <message>
```

Read the pending request from
`waypoint sessions events <session-id> --messages 20 --coalesce` before
approving. Coalescing preserves `approval_request` records and keeps surrounding
assistant/tool output readable. If multiple approvals are pending, pass
`--approval-id` when the transcript exposes one. Use raw `events` only if you
need exact event ordering, duplicate diagnosis, or per-chunk metadata.

Do not approve destructive, privileged, or unclear requests without user
confirmation.

## Questions

A session can also block on a question (an `AskUserQuestion` prompt), which is
not an approval. It surfaces in coalesced or raw events as a `tool_call` event
with `tool_name: AskUserQuestion` rather than an `approval_request`, so
`approve` does not release it — answer it instead:

```bash
waypoint sessions answer-question <session-id> --answer "<your answer>"
waypoint sessions answer-question <session-id> --answer "<text>" --tool-use-id <id>
waypoint sessions answer-question <session-id> --answers-json '[{"question": "...", "answer": "..."}]'
```

`sessions send` injects a normal message and will **not** satisfy the blocking
question; only `answer-question` does. Pass `--tool-use-id` when several
questions are pending.
