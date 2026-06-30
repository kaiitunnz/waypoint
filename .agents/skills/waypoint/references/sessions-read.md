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

Use `events --coalesce` for settled structured JSON reads, including tool
context, approvals, and questions. Coalescing only merges streaming
`agent_output` and `tool_result` deltas with the same `item_id`;
`approval_request` and `tool_call` records stay visible.

Use raw `events` or `--follow` only when exact stream mechanics matter: delta
boundaries, original sequence/timestamps, overwritten metadata, duplicate or
paging diagnosis, or transport artifacts such as `raw_terminal_chunk`.

When summarizing, distinguish status (`running`, `waiting_input`, `idle`,
`exited`, `error`) from your interpretation of progress. Quote only the minimum
transcript text needed to justify the conclusion.
