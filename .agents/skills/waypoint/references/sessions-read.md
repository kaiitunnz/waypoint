# Reading Sessions

Use these commands to ground answers in live Waypoint state:

```bash
waypoint sessions list
waypoint sessions show <session-id>
waypoint sessions output <session-id> --messages 40
waypoint sessions output <session-id> --messages 40 --text
waypoint sessions events <session-id> --messages 40 --coalesce
waypoint sessions events <session-id> --before-sequence <sequence> --coalesce
waypoint sessions events <session-id> --messages 40              # raw events
waypoint sessions events <session-id> --follow
```

`list` is the starting point for broad questions. Use `show` for one session's
metadata. Use `output` for the conversational transcript or the agent's answer;
`--text` is best when you want only the concatenated assistant text for shell
piping or quick reading.

Use `events --coalesce` for settled structured JSON inspection. Coalescing
combines streaming `agent_output` and `tool_result` deltas that share an
`item_id`; it leaves other event kinds, including `approval_request`,
`tool_call`, and question events, as their own records. That makes coalesced
events the right default for checking what happened in a finished turn,
including normal tool-call/result context or pending approvals/questions.

Switch to raw `events` only when exact event-stream mechanics matter: delta
boundaries, original per-chunk `sequence`/`ts`, intermediate metadata that a
merged event may overwrite, paging or duplicate-event diagnosis, transport/TUI
artifacts such as `raw_terminal_chunk`, or live `--follow` streams.

When summarizing, distinguish status (`running`, `waiting_input`, `idle`,
`exited`, `error`) from your interpretation of progress. Quote only the minimum
transcript text needed to justify the conclusion.
