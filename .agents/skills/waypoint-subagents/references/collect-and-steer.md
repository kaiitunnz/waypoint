# Collect and Steer

Once a child reaches `idle` / `waiting_input` / `exited`, read its work and
decide whether it is done or needs another turn.

## Read output

```bash
waypoint sessions output <session-id> --messages 40
waypoint sessions output <session-id> --messages 40 --text
waypoint sessions events <session-id> --messages 40 --coalesce       # structured JSON
waypoint sessions events <session-id> --before-sequence <sequence>   # page back JSON
```

Use `sessions output` for the child's normal answer; it coalesces streaming
deltas into readable assistant messages. Use `--text` when you only need the
concatenated assistant text. Use `events --coalesce` for settled structured JSON
inspection, including normal tool-call/result context and pending
approvals/questions: coalescing leaves `approval_request`, `tool_call`, and
question records intact. Drop to raw `events` only for exact stream debugging,
such as delta boundaries, per-chunk sequence/timestamp checks, overwritten
metadata, paging/duplicate diagnosis, or transport/TUI artifacts. For sessions
on the `tmux` (Terminal) transport, event kinds are inferred heuristically —
also check `raw_terminal_chunk` if structured output is incomplete.

Quote only the minimum transcript text needed to justify your conclusion;
summarize the rest. Distinguish the reported `status` from your interpretation of
whether the task actually succeeded.

## Continue a child

```bash
waypoint sessions send <session-id> "<follow-up instructions>"
```

After sending, poll again (see `spawn-and-poll.md`) — `send` returns
immediately; it does not wait for the child to finish the new turn.

## Answer an approval

A child sitting in `waiting_input` may be blocked on an approval rather than
asking you for more work:

```bash
waypoint sessions approve <session-id> <decision> [--approval-id <id>] [--text <message>]
```

See `references/permissions.md` for reading the pending request, the
backend-specific `<decision>` values, tool-use vs. plan approvals, and choosing a
child's permission mode at spawn time so it stalls less often.

## Answer a question

A child can instead be blocked on a question (an `AskUserQuestion`, surfacing as
a `tool_call` with `tool_name: AskUserQuestion`, not an `approval_request`).
Answer it with `answer-question`, not `send` or `approve`:

```bash
waypoint sessions answer-question <session-id> --answer "<your answer>"
```

`send` injects a message and leaves the question blocking; only `answer-question`
releases it. See `references/permissions.md` for the structured `--answers-json`
shape and `--tool-use-id`.

## Interrupt a stuck child

```bash
waypoint sessions interrupt <session-id>
```

Only for a child you spawned that is clearly stuck. After interrupting, re-check
its status and events before deciding the next step.
