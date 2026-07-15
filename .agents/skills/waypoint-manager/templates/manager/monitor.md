# Manager — monitor

A `{{ticket_channel}}` post or an inbox answer woke you. Read the lead's typed
feedback, drive the state, and relay any human answer back **durably**. This step
covers the substantial-spec gate, mid-build blockers, and the done/partial signal;
the merge itself is `{{templates_dir}}/manager/integrate.md`.

## Read the feedback

```bash
waypoint board read {{ticket_channel}} --key status        # the status cell (by key — --since misses cell overwrites)
waypoint board log {{ticket_channel}} --since <last-seen>  # append-log narrative + relay acks
```

The `status` cell's `kind=` is the feedback vocabulary:

| `kind=` | Meaning | Drive |
|---|---|---|
| `progress` | Working, no action needed | none — keep monitoring |
| `error` | A failure the lead can't resolve | → `blocked`, escalate to inbox |
| `decision` | Needs a product/scope call | → `blocked`, escalate to inbox |
| `attention` | Ambiguity — needs a look | → `blocked`, escalate to inbox |
| `done` | Work complete, PR open (`pr=`/`commit=`) | → `review_requested` (`--not-partial`) |
| `partial` | A subset delivered (`detail` lists deferred goals) | → `review_requested` (`--is-partial`) |

## Blockers → the inbox (with escalation policy)

Apply the escalation policy: settle blockers of kind {{self_decide}} yourself;
escalate {{always_escalate}} to the human inbox. The lead posted the full question and
its options as a keyless `kind=<error|decision|attention>` log entry — read it (`board
log {{ticket_channel}} --since <last-seen>`) and lift its question and options
**verbatim** into the inbox item. Transition, then post an inbox item and record
`awaiting_since` is now stamped (the server does it):

```bash
waypoint manager ticket transition {{ticket_id}} --to blocked --reason "<the blocker>"
waypoint inbox post --json - <<'JSON'
{ "subject": "{{ticket_channel}}: {{ticket_title}} — decision needed",
  "blocks": [
    { "type": "markdown", "text": "<the lead's question, verbatim from its entry>" },
    { "type": "question", "question": "How should it proceed?",
      "options": [{"label": "<lead's option a>"}, {"label": "<lead's option b>"}],
      "multi": false, "required": true } ] }
JSON
```

Frame an open question when the lead left the decision open. You are
`--wake-on-inbox`-subscribed, so the human's answer wakes you.

## The substantial-spec gate

When the writer posts the spec ref, move `spec_pending → spec_review` (recording
`{{spec_ref}}`) and post an **approval** inbox item with the spec. On the answer:
**approve** → `ready`; **request-changes** → `spec_pending` (relay the notes to a
fresh writer); **reject / latency-timeout** → `abandoned`.

```bash
waypoint manager ticket transition {{ticket_id}} --to spec_review --spec-ref {{spec_ref}}
```

## Relay a human answer back to the lead — durably

The answer lands on a later wake, so recover the item from the ticket rather than a
prior turn's variable — the item's subject carries `{{ticket_channel}}`. Read the
answer (never injected — pull it), then post it to the durable log and nudge:

```bash
item=$(waypoint inbox list --status open --q "{{ticket_channel}}" | jq -r '.items[0].id')   # newest unanswered item for this ticket
answer=$(waypoint inbox get "$item")                   # {"item": {...}} — branch on the block's answer
waypoint board post {{ticket_channel}} "<the human's decision, verbatim enough to act on>" --meta kind=relay
lead=$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.lead_session_id')
waypoint sessions send "$lead" \
  "[wp-msg from={{manager_session_id}}] Relay posted on {{ticket_channel}}; read owed relays and act."
```

Then transition out of the awaiting state by the block's shape:

- **mid-build blocker** (a live lead on its branch) — relay the answer to the lead
  (above), then resume `blocked → building`;
- **branch-less blocker** (an infeasible `spec_pending → blocked`, with no lead to
  relay to) — the human's answer is your transition directly: `blocked → ready`
  (proceed — build from a human-supplied spec or as direct-instruction),
  `blocked → spec_pending` (re-spec via a fresh writer), or `blocked → abandoned`;
- **spec gate** — `spec_review → ready` on approve.

`awaiting_since` clears automatically on exit. For the relayed cases, each relay is a
`kind=relay` post the lead consumes in board-entry-`id` order, applying each once;
post the relay before the exit transition (a re-post lands under a higher id and the
lead re-applies it once).

## Done / partial

On `done`/`partial`, record the PR and move to `review_requested`. The lead parks
alive and idle; the ticket keeps holding the shared tree until it lands or is
abandoned:

```bash
waypoint manager ticket transition {{ticket_id}} --to review_requested \
  --pr-url {{pr_url}} --not-partial      # or --is-partial for a partial delivery
```

Then open the per-PR review gate — see `{{templates_dir}}/manager/integrate.md`.
