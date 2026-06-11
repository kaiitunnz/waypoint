# Cleanup

Reap the children you spawned once their work is collected. Leaving orphaned
`subagent:*` sessions behind — running *or* as stale `exited` records — clutters
the user's session list and is a bug.

There are two operations:

- `waypoint sessions terminate <id>` — stops a running child; the record
  remains (status `exited`), so the user can still review it.
- `waypoint sessions delete <id>` — terminates if needed, then **removes the
  record** entirely. Add `--force` only for a wedged adapter that won't
  terminate gracefully.

## Disposition: delete by default, keep deliberately

When you are done with a child, decide its fate. **Default to deleting it** —
most children do internal, throwaway work whose result you have already consumed
or relayed, and a left-behind record is just clutter.

```bash
waypoint sessions delete <child-id>
```

**Keep** a child (terminate only, or leave it as-is) when:

- It produced output the **user should review**.
- It ended in `error` — deleting it would hide the failure. Leave it and surface
  it to the user.
- It is **pinned** by the user (`pinned_at` set) — never delete a pinned session.

When you keep children, tell the user which ones and why, and quote their ids so
they can find them.

## Rules

- Delete or terminate **only** the session ids you spawned this turn and tracked.
  Never touch a session you cannot positively account for as your own; when in
  doubt, leave it and ask the user.
- The personal-assistant session cannot be terminated or deleted — the server
  rejects it with `403`. Never target it.
- If a child has already `exited` and is not worth keeping, `delete` it to clear
  the record; `terminate` alone would leave the clutter behind.
- Throwaway children — tests, quick one-shot fan-out — should always be deleted.
