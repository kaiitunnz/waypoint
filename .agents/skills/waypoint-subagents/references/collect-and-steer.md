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

Use `sessions output` for the child's normal answer, and add `--text` when you
only need assistant text. Use `events --coalesce` for structured JSON reads,
including tool context and pending approvals/questions. Use raw `events` only
for exact stream debugging or transport/TUI artifacts such as
`raw_terminal_chunk`.

Quote only the minimum transcript text needed to justify your conclusion;
summarize the rest. Distinguish the reported `status` from your interpretation of
whether the task actually succeeded.

## Continue a child

```bash
waypoint sessions send <session-id> "<follow-up instructions>"
```

After sending, poll again (see `spawn-and-poll.md`) — `send` returns
immediately; it does not wait for the child to finish the new turn.

When the work is likely to need more turns, **keep the child parked (idle and
alive) between them and continue it with `sessions send`** — this is far cheaper
than reaping it and reimporting the thread later (which spins a new session,
replays history, and loses live state). See `references/cleanup.md` for park vs.
terminate vs. delete.

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
