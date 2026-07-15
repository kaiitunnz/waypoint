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
escalate {{always_escalate}} to the human inbox. Transition, then post an inbox item
and record `awaiting_since` is now stamped (the server does it):

```bash
waypoint manager ticket transition {{ticket_id}} --to blocked --reason "<the blocker>"
waypoint inbox post --json - <<'JSON'
{ "subject": "{{ticket_channel}}: {{ticket_title}} — decision needed",
  "blocks": [
    { "type": "markdown", "text": "The lead reports: <blocker detail>." },
    { "type": "question", "question": "How should it proceed?",
      "options": [{"label": "…"}, {"label": "…"}], "multi": false, "required": true } ] }
JSON
```

You are `--wake-on-inbox`-subscribed, so the human's answer wakes you.

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

Then transition out of the awaiting state — `blocked → building` (answer relayed),
or `spec_review → ready`. `awaiting_since` clears automatically on exit. Each relay
is a `kind=relay` post the lead consumes in board-entry-`id` order, applying each
once. Post the relay before the exit transition (a re-post lands under a higher id
and the lead re-applies it once).

## Done / partial

On `done`/`partial`, record the PR and move to `review_requested`. The lead parks
alive and idle; the ticket keeps holding the shared tree until it lands or is
abandoned:

```bash
waypoint manager ticket transition {{ticket_id}} --to review_requested \
  --pr-url {{pr_url}} --not-partial      # or --is-partial for a partial delivery
```

Then open the per-PR review gate — see `{{templates_dir}}/manager/integrate.md`.
