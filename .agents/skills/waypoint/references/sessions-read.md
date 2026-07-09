# Reading Sessions

Use these commands to ground answers in live Waypoint state:

```bash
waypoint sessions list
waypoint sessions show <session-id>
waypoint sessions output <session-id> --messages 40 --compact
waypoint sessions output <session-id> --messages 40 --text
waypoint sessions events <session-id> --messages 40 --compact
waypoint sessions events <session-id> --before-sequence <sequence> --compact
waypoint sessions events <session-id> --messages 40 --coalesce  # full metadata
waypoint sessions events <session-id> --messages 40              # raw events
waypoint sessions events <session-id> --follow
```

`list` is the starting point for broad questions. Use `show` for one session's
metadata. Use `output` for the conversational transcript or the agent's answer;
`--compact` is the routine agent-readable transcript view, including user and
assistant turns without raw backend metadata. `--text` is best when you want only
the concatenated assistant text for shell piping or quick reading.

Use `events --compact` for routine settled structured reads, including tool
context, approvals, and questions without raw backend payloads. Compact events
coalesce streaming deltas into logical events. Use `events --coalesce` when you
need the full event envelope and metadata. Coalescing only merges streaming
`agent_output` and `tool_result` deltas with the same `item_id`;
`approval_request` and `tool_call` records stay visible.

Use raw `events` or `--follow` only when exact stream mechanics matter: delta
boundaries, original sequence/timestamps, overwritten metadata, duplicate or
paging diagnosis, or transport artifacts such as `raw_terminal_chunk`.

When summarizing, distinguish status (`running`, `waiting_input`, `idle`,
`exited`, `error`) from your interpretation of progress. Quote only the minimum
transcript text needed to justify the conclusion.
