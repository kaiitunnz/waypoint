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
| `done` | Work complete, delivered | → `review_requested` (`--not-partial`) |
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

Every gate item's subject leads with `{{ticket_channel}}:`; the answer read matches
that prefix.

Frame an open question when the lead left the decision open. You are
`--wake-on-inbox`-subscribed, so the human's answer wakes you.

## The substantial-spec gate

When the writer posts back, branch on its `kind`.

**`spec_ready`** — move `spec_pending → spec_review` (recording `{{spec_ref}}`) and
post an **approval** inbox item with the spec. On the answer: **approve** → `ready`;
**request-changes** → `spec_pending` (relay the notes to a fresh writer); **reject** →
`abandoned`. A silent latency-timeout is abandoned by the `latency_timeouts` reconcile
path in `{{templates_dir}}/manager/loop-cycle.md`.

```bash
waypoint manager ticket transition {{ticket_id}} --to spec_review --spec-ref {{spec_ref}}
waypoint inbox post --json - <<'JSON'
{ "subject": "{{ticket_channel}}: {{ticket_title}} — spec review",
  "blocks": [
    { "type": "markdown", "text": "<spec summary; ref {{spec_ref}}>" },
    { "type": "approval", "prompt": "Approve this spec?", "required": true } ] }
JSON
```

**`infeasible`** — the writer determined the request cannot be specced. Move
`spec_pending → blocked` and post a **decision** inbox item carrying the writer's
reason; the branch-less-blocker relay below drives the human's answer (`blocked →
ready` to proceed on a human-supplied spec, `blocked → spec_pending` to re-spec,
`blocked → abandoned`).

```bash
waypoint manager ticket transition {{ticket_id}} --to blocked --reason "infeasible spec: <writer's reason>"
waypoint inbox post --json - <<'JSON'
{ "subject": "{{ticket_channel}}: {{ticket_title}} — decision needed",
  "blocks": [
    { "type": "markdown", "text": "<the writer's infeasibility reason, verbatim>" },
    { "type": "question", "question": "How should it proceed?",
      "options": [{"label": "proceed on a human-supplied spec"},
                  {"label": "re-spec"}, {"label": "abandon"}],
      "multi": false, "required": true } ] }
JSON
```

## Relay a human answer back to the lead — durably

The answer lands on a later wake, so recover the gate item from the inbox by its
`{{ticket_channel}}:` subject. Act only when a resolved gate item exists — a board-post
or liveness wake leaves `item` empty and carries no answer to relay. Read the answer
(never injected — pull it); when a lead holds the branch, post it to the durable log
and nudge:

```bash
item=$(waypoint inbox list --status resolved --q "{{ticket_channel}}:" \
  | jq -r --arg p "{{ticket_channel}}:" '[.items[] | select(.subject | startswith($p))][0].id')  # newest answered gate; empty if this wake carried no answer
if [ -n "$item" ] && [ "$item" != "null" ]; then
  answer=$(waypoint inbox get "$item")                 # {"item": {...}} — branch on the block's answer
  info=$(waypoint manager ticket show {{ticket_id}})
  branch=$(echo "$info" | jq -r '.ticket.branch // empty')       # set only for a mid-build blocker; null for a branch-less block or the spec gate
  lead=$(echo "$info" | jq -r '.ticket.lead_session_id // empty')
  if [ -n "$branch" ]; then                            # a lead holds a real branch — relay durably and nudge
    waypoint board post {{ticket_channel}} "<the human's decision, verbatim enough to act on>" --meta kind=relay
    waypoint sessions send "$lead" \
      "[wp-msg from={{manager_session_id}}] Relay posted on {{ticket_channel}}; read owed relays and act."
  fi
fi
```

Then transition out of the awaiting state by the block's shape:

- **mid-build blocker** (a live lead on its branch) — relay the answer to the lead
  (above), then resume `blocked → building`;
- **branch-less blocker** (an infeasible `spec_pending → blocked`, with no lead to
  relay to) — the human's answer is your transition directly: `blocked → ready`
  (proceed — as direct-instruction, or against a human-supplied spec recorded with
  `--spec-ref <ref>`), `blocked → spec_pending` (re-spec via a fresh writer), or
  `blocked → abandoned`;
- **spec gate** — `spec_review → ready` on approve.

`awaiting_since` clears automatically on exit. For the relayed cases, each relay is a
`kind=relay` post the lead consumes in board-entry-`id` order, applying each once;
post the relay before the exit transition (a re-post lands under a higher id and the
lead re-applies it once).

## Done / partial

On `done`/`partial`, move to `review_requested`. The lead parks alive and idle; the
ticket keeps holding the shared tree until it lands or is abandoned:

{{#if integration_mode == pr}}
```bash
waypoint manager ticket transition {{ticket_id}} --to review_requested \
  --pr-url {{pr_url}} --not-partial      # or --is-partial for a partial delivery
```
{{/if}}
{{#if integration_mode == local}}
```bash
waypoint manager ticket transition {{ticket_id}} --to review_requested \
  --not-partial      # or --is-partial for a partial delivery
```
{{/if}}

Then open the review gate — see `{{templates_dir}}/manager/integrate.md`.
