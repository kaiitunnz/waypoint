# Compaction

A compaction is a pair of messages: `/compact`, then a grounding message that starts the session working again (`Ground yourself comprehensively and proceed`, or `... and park`; with `--handoff`, it re-reads the notes first).

## Compacting another session

```
$W compact --target <sid> --grounding proceed|park
```

Use the grounding you recorded with `phase`. If the target's events show a compaction note since it parked, the user already compacted it: pass `--grounding-only`. The note reads `Context compacted` on Claude Code and Codex, and `Session compacted` on opencode.

`compact` refuses a target at `waiting_input`, `interrupted` or `error`. Otherwise the pair goes out by backend:

- **Claude Code** queues `/compact` until its turn ends, so `compact` sends it at once, and a refusal fails the command. An idle target gets the grounding right after it. A running turn would consume plain text, so a detached helper sends that target's grounding once the turn has ended.
- **Codex and opencode** can't queue it, so `compact` returns at once and a detached helper sends both: `/compact` once the target is idle and quiet (2 s on Codex, 5 s on opencode), then the grounding once the compaction has ended. Focus counts as not ready.

With `park`, wait until the target has run a turn after the compaction note before sending follow-up work. With `proceed`, you are done.

A refusal that names seconds left means a pair has just gone out: wait, then re-run only if it is still needed. `/compact not confirmed (send: unknown)` may still have landed, so read the events before re-running.

## When a pair does not finish

A busy target may still have `/compact` queued behind its turn: wait for the turn to end before acting on any of these.

- **`pair-failed`, nothing went out** → act on the reason it names.
  - The phase changed: you decided otherwise, or the target compacted on its own and the watcher re-armed. Compact again only if `status` still shows usage at the first threshold and you still want to.
  - Any other reason: run the same `compact` once the target is idle.
- **`pair-failed`, `/compact` went out** → read the events as under `pair-stalled`.
- **`pair-held`** → the target is in Focus or on a dialog, and the messages are queued for its human. Tell the user to release them once its turn has ended, and leave the queue alone.
- **`pair-stalled`** (the phase stayed `compacting` for 10 minutes with the target idle) → read `waypoint sessions events <sid> --compact --messages 30`.
  - `status` shows `pair_running: true` → the grounding is on its way. Leave it.
  - A `pair-held` earlier in this cycle → its message may still be queued. Tell the user and send nothing.
  - No compaction note since the pair → `compact`.
  - A compaction but no turn after it → `compact --grounding-only`.
  - A second stall in one cycle → set `user-held` and tell the user.

## Compacting yourself

This needs the watcher on your own session (`start --target $WAYPOINT_SESSION_ID`). `$W status --target $WAYPOINT_SESSION_ID` prints the exact `self_compact_command`.

1. Finish the unit of work.
2. Update your notes, with any pending message or follow-up.
3. Make `self_compact_command`, exactly as printed, your final tool call. Use `--grounding park` instead of `proceed` when your next step is to wait for input.
4. End the turn with one short line.

On Claude Code the command sends `/compact` at once, so a refusal fails the tool call: read the error and carry on working. Elsewhere the helper sends the pair after your turn.

**Permission.** A session sending itself messages is the user's call. Claude Code's auto mode denies it unless the user has allowed this exact command: `status` also prints the `allow_rule` that goes in their settings. Show them the rule and let them decide. On a denial, tell the user and ask them to add the rule or run `/compact` themselves, then carry on with your work until they do. A denial stands whatever the route, so `compact` refuses your own session.
