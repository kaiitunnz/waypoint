# On an alert

Alerts read `[ctx-watch target=<sid> ... cycle=<id> level=<level> [refill-fast]] ...`. First run `$W status --target <sid>`. Ignore the alert if its cycle is not the current one or the phase is `user-held`. Also ignore a threshold or reminder alert while `pair_running` is true: a compaction is already on its way.

Then act on the target's **current** state; the first matching bullet wins:

- **Target at `waiting_input`, `interrupted` or `error`** → it needs a person. Tell the user and do not compact it.
- **`pair-failed`, `pair-held` or `pair-stalled`** → follow "When a pair does not finish" in [`compaction.md`](compaction.md).
- **Idle target, phase `parking`** → it parked: run `compact` with the recorded grounding.
- **`refill-fast`** → read `waypoint sessions events <sid> --compact --messages 30` for a compaction note.
  - No note: the compaction failed. Run `compact` again.
  - A note: grounding alone refills the window, so compacting again would loop. At `second`, ask the target to stop and park. Set `user-held` and tell the user the usage and the choice: raise the thresholds or slim the notes.
- **Idle target, otherwise** → a natural stop. Set `deferred` and handle the stop normally. Compact only when you re-drive it (below).
- **`first` (or its reminder), target running** → read its recent work (`waypoint sessions output <sid> --messages 20 --text`) and its notes.
  - Finishing very soon, with the last step of a unit that ends in a stop it will report → set `deferred` and leave it.
  - Otherwise → send `[wp-msg from=<lead> reply-to=<lead>] Your context is at N%. Find a good checkpoint, update your notes, and park with nothing still running; I will compact your context. Ping me once parked.` Set `parking --grounding proceed`.
- **`second` (or its reminder), target running** → send `[wp-msg …] Stop the current work, update your notes, and park for context compaction, with nothing still running. Ping me once parked.` Set `parking --grounding proceed`.

Name the notes in those messages when `config.handoff` is set.

## Re-drive

Before sending a watched target follow-up work, check its usage in `status`. At or above the first threshold:

1. Write the follow-up into your own notes.
2. Send `[wp-msg …] Update your notes and park for context compaction, with nothing still running. Ping me once parked.`
3. Set `parking --grounding park`.
4. Run `compact --grounding park` on its ping.
5. Send the follow-up once the target has run and gone idle again.

## Your own session

The same decisions apply when you are the target (`start --target $WAYPOINT_SESSION_ID`). Compacting yourself is in [`compaction.md`](compaction.md). A `deferred` lead whose new turn starts at or above the first threshold records the incoming message in its notes and compacts itself with `proceed`.
