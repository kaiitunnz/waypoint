# Reading Sessions

Use these commands to ground answers in live Waypoint state:

```bash
waypoint sessions list
waypoint sessions show <session-id>
waypoint sessions events <session-id>
waypoint sessions events <session-id> --messages 40
waypoint sessions events <session-id> --before-sequence <sequence>
```

`list` is the starting point for broad questions. Use `show` for one session's
metadata and `events` for transcript details.

When summarizing, distinguish status (`running`, `waiting_input`, `idle`,
`exited`, `error`) from your interpretation of progress. Quote only the minimum
transcript text needed to justify the conclusion.
