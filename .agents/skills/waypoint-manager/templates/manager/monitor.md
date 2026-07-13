# Manager — monitor

A `ticket-{{ticket_id}}` post or an inbox answer woke you. Read the lead's typed
feedback, drive the state, and relay any human answer back **durably**. This step
covers the substantial-spec gate, mid-build blockers, and the done/partial signal;
the merge itself is `templates/manager/integrate.md`.

## Read the feedback

```bash
waypoint board read ticket-{{ticket_id}} --key status        # the status cell (by key — --since misses cell overwrites)
waypoint board log ticket-{{ticket_id}} --since <last-seen>  # append-log narrative + relay acks
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

Apply the manifest policy: settle a `retryable-error` / `unambiguous-clarification`
yourself; escalate a `product-decision` / `scope-change` / `irreversible` /
`spec-ambiguity`. Transition, then post an inbox item and record `awaiting_since`
is now stamped (the server does it):

```bash
waypoint manager ticket transition {{ticket_id}} --to blocked --reason "<the blocker>"
id=$(waypoint inbox post --json - <<'JSON' | jq -r .item.id
{ "subject": "ticket-{{ticket_id}}: {{ticket_title}} — decision needed",
  "blocks": [
    { "type": "markdown", "text": "The lead reports: <blocker detail>." },
    { "type": "question", "question": "How should it proceed?",
      "options": [{"label": "…"}, {"label": "…"}], "multi": false, "required": true } ] }
JSON
)
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

Read the answer (never injected — pull it), then post it to the durable versioned
log and nudge:

```bash
answer=$(waypoint inbox get "$id")                     # emits {"item": {...}} — branch on the block's answer
ver=$(echo "$answer" | jq -r '.item.version')
waypoint board post ticket-{{ticket_id}} "<the human's decision, verbatim enough to act on>" \
  --meta relay_version="$ver" --meta kind=relay
lead=$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.lead_session_id')
waypoint sessions send "$lead" \
  "[wp-msg from={{manager_session_id}}] Relay posted on ticket-{{ticket_id}} (v$ver); read owed relays and act."
```

Then transition out of the awaiting state — `blocked → building` (answer relayed),
or `spec_review → ready`. `awaiting_since` clears automatically on exit. The lead
consumes the relay by version and is idempotent, so a duplicate nudge is harmless;
if the lead is dead, the relay is still on the log for its replacement to read
(`references/loop.md`).

## Done / partial

On `done`/`partial`, record the PR and move to `review_requested` — this **frees
the slot** (the lead parks alive):

```bash
waypoint manager ticket transition {{ticket_id}} --to review_requested \
  --pr-url {{pr_url}} --not-partial      # or --is-partial for a partial delivery
```

Then open the per-PR review gate — see `templates/manager/integrate.md`.
