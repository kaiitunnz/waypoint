# Compaction

A compaction is a pair of messages: `/compact`, then a grounding message that starts the session working again (`Ground yourself comprehensively and proceed`, or `... and park`; with `--handoff`, it re-reads the notes first).

## Compacting another session

```
$W compact --target <sid> --grounding proceed|park
```

It returns at once. A detached helper waits until the target is idle and quiet for 30 seconds, sends `/compact`, waits for the compaction to end, then sends the grounding. `waiting_input` and Focus count as not ready. Use the grounding you recorded with `phase`.

- If the target's events show a compaction note since it parked, the user already compacted it: pass `--grounding-only`. The note reads `Context compacted` on Claude Code and Codex, and `Session compacted` on opencode.
- With `park`, wait until the target has run and gone idle again before sending follow-up work. With `proceed`, you are done.
- `compact` refusing because a helper is running means one is already on its way: leave it.
- `compact` refuses tmux sessions, whose status rarely reads idle. Send them the pair yourself with `waypoint sessions send`.

## When a pair does not finish

- **`pair-failed` before anything went out** → the phase is back to `normal`, and the alert names the status the helper saw. Run `compact` again once that clears.
- **`pair-failed` after `/compact`** → read the events as under `pair-stalled`.
- **`pair-held`** → the target is in Focus or on a dialog, and the message is queued for its human. Tell the user and leave the queue alone; a `pair-stalled` follows if the session stays idle afterwards.
- **`pair-stalled`** (the phase stayed `compacting` for 10 minutes with the target idle) → read `waypoint sessions events <sid> --compact --messages 30`.
  - No compaction note since the pair → `compact`.
  - A compaction but no turn after it → `compact --grounding-only`.
  - A second stall in one cycle → set `user-held` and tell the user.

## Compacting yourself

This needs the watcher on your own session (`start --target $WAYPOINT_SESSION_ID`). `$W status --target $WAYPOINT_SESSION_ID` prints the exact `self_compact_command`.

1. Finish the unit of work.
2. Update your notes, with any pending message or follow-up.
3. Make `self_compact_command`, exactly as printed, your final tool call. Use `--grounding park` instead of `proceed` when your next step is to wait for input.
4. End the turn with one short line.

The helper sends `/compact` once your turn has ended, then the grounding once the compaction has ended.

**Permission.** A session sending itself messages is the user's call. Claude Code's auto mode denies it unless the user has allowed this exact command: `status` also prints the `allow_rule` that goes in their settings. Show them the rule and let them decide. On a denial, tell the user and ask them to add the rule or run `/compact` themselves, then carry on with your work until they do. A denial stands whatever the route, so `compact` refuses your own session.
