# Reading Sessions

Use these commands to ground answers in live Waypoint state:

```bash
waypoint sessions list
waypoint sessions show <session-id>
waypoint sessions output <session-id> --messages 40
waypoint sessions output <session-id> --messages 40 --text
waypoint sessions events <session-id>
waypoint sessions events <session-id> --coalesce
waypoint sessions events <session-id> --messages 40
waypoint sessions events <session-id> --before-sequence <sequence>
```

`list` is the starting point for broad questions. Use `show` for one session's
metadata. Use `output` for the conversational transcript or the agent's answer;
`--text` is best when you want only the concatenated assistant text for shell
piping or quick reading.

Use `events --coalesce` when you need structured JSON transcript inspection
without streaming deltas split across many events. Use raw `events` for
control-plane details such as approvals, tool calls/results, questions,
debugging missing output, or live `--follow` streams.

When summarizing, distinguish status (`running`, `waiting_input`, `idle`,
`exited`, `error`) from your interpretation of progress. Quote only the minimum
transcript text needed to justify the conclusion.
