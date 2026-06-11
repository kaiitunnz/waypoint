# Cleanup

Reap the children you spawned once their work is collected. Orphaned
`subagent:*` sessions left running are a bug.

```bash
waypoint sessions terminate <session-id>
```

Rules:

- Terminate **only** the session ids you spawned this turn and tracked. Do not
  terminate a session you cannot positively account for as your own.
- The personal-assistant session cannot be terminated — the server rejects it
  with `403`. Never target it.
- If a child has already `exited`, no termination is needed; just confirm its
  final state with `sessions show`.
- When in doubt about ownership, leave the session alone and ask the user.

A child that exited on its own still leaves a session record behind. Terminating
is about stopping a running child; it does not delete history. Leave history for
the user unless they ask otherwise.
