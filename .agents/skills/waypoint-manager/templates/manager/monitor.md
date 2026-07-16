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
| `partial` | A subset delivered (status text lists the deferred goals) | → `review_requested` (`--is-partial`) |

## Blockers → the inbox (with escalation policy)

Apply the escalation policy: settle blockers of kind {{self_decide}} yourself;
escalate {{always_escalate}} to the human inbox. The lead posted the full question and
its options as a keyless `kind=<error|decision|attention>` log entry — read it (`board
log {{ticket_channel}} --since <last-seen>`) and lift its question and options
**verbatim** into the inbox item. Transition, then post an inbox item and record
`awaiting_since` is now stamped (the server does it):

```bash
waypoint manager ticket transition {{ticket_id}} --to blocked --reason "<the blocker>"
item=$(waypoint inbox list --status open --q "{{ticket_channel}}" \
  | jq -r '[.items[] | select((.subject|startswith("{{ticket_channel}}: ")) and (.subject|endswith("— decision needed"))) | .id] | first // empty')   # adopt an open gate a crash left behind
[ -n "$item" ] || item=$(waypoint inbox post --json - <<'JSON' | jq -r '.item.id'
{ "subject": "{{ticket_channel}}: {{ticket_title}} — decision needed",
  "blocks": [
    { "type": "markdown", "text": "<the lead's question, verbatim from its entry>" },
    { "type": "question", "question": "How should it proceed?",
      "options": [{"label": "<lead's option a>"}, {"label": "<lead's option b>"}],
      "multi": false, "required": true } ] }
JSON
)
waypoint manager ticket update {{ticket_id}} --inbox-item "$item"
```

Each gate post is idempotent: it first adopts an existing open item for this
ticket+phase (matched by subject) and posts fresh only when none exists, so a crash
between the transition and the post re-opens the same gate rather than double-posting
or stranding the ticket (the `stale_gates` reconcile signal surfaces one to re-open).
It records the item id on the ticket (`--inbox-item`); the answer read looks that id up
and acts once it resolves. The server clears the id on the next non-self transition, so
a later gate never resolves this one's answer.

Frame an open question when the lead left the decision open. You are
`--wake-on-inbox`-subscribed, so the human's answer wakes you.

## The substantial-spec gate

When the writer posts back, branch on its `kind`.

**`spec_ready`** — move `spec_pending → spec_review` (recording `{{spec_ref}}`) and
post an **approval** inbox item with the spec. On the answer: **approve** → `ready`;
**request-changes** → `spec_pending` (re-spec — see **Re-spec** below); **reject** →
`abandoned`. A silent latency-timeout is abandoned by the `latency_timeouts` reconcile
path in `{{templates_dir}}/manager/loop-cycle.md`.

```bash
waypoint manager ticket transition {{ticket_id}} --to spec_review --spec-ref {{spec_ref}}
item=$(waypoint inbox list --status open --q "{{ticket_channel}}" \
  | jq -r '[.items[] | select((.subject|startswith("{{ticket_channel}}: ")) and (.subject|endswith("— spec review"))) | .id] | first // empty')   # adopt an open gate a crash left behind
[ -n "$item" ] || item=$(waypoint inbox post --json - <<'JSON' | jq -r '.item.id'
{ "subject": "{{ticket_channel}}: {{ticket_title}} — spec review",
  "blocks": [
    { "type": "markdown", "text": "<spec summary; ref {{spec_ref}}>" },
    { "type": "approval", "prompt": "Approve this spec?",
      "options": ["approve", "request-changes", "reject"], "required": true } ] }
JSON
)
waypoint manager ticket update {{ticket_id}} --inbox-item "$item"
```

**`infeasible`** — the writer determined the request cannot be specced. Move
`spec_pending → blocked` and post a **decision** inbox item carrying the writer's
reason; the branch-less-blocker relay below drives the human's answer (`blocked →
ready` to proceed on a human-supplied spec, `blocked → spec_pending` to re-spec,
`blocked → abandoned`).

```bash
waypoint manager ticket transition {{ticket_id}} --to blocked --reason "infeasible spec: <writer's reason>"
item=$(waypoint inbox list --status open --q "{{ticket_channel}}" \
  | jq -r '[.items[] | select((.subject|startswith("{{ticket_channel}}: ")) and (.subject|endswith("— decision needed"))) | .id] | first // empty')   # adopt an open gate a crash left behind
[ -n "$item" ] || item=$(waypoint inbox post --json - <<'JSON' | jq -r '.item.id'
{ "subject": "{{ticket_channel}}: {{ticket_title}} — decision needed",
  "blocks": [
    { "type": "markdown", "text": "<the writer's infeasibility reason, verbatim>" },
    { "type": "question", "question": "How should it proceed?",
      "options": [{"label": "proceed on a human-supplied spec"},
                  {"label": "re-spec"}, {"label": "abandon"}],
      "multi": false, "required": true } ] }
JSON
)
waypoint manager ticket update {{ticket_id}} --inbox-item "$item"
```

## Relay a human answer back to the lead — durably

The answer lands on a later wake, so recover the gate item from the ticket's recorded
`inbox_item_id`. Act only when that item has resolved — an empty id or an open item
means this wake carried no answer for the current gate. Read the answer (never injected
— pull it); when a lead holds the branch, post it to the durable log and nudge:

```bash
info=$(waypoint manager ticket show {{ticket_id}})
item=$(echo "$info" | jq -r '.ticket.inbox_item_id // empty')
if [ -n "$item" ]; then
  answer=$(waypoint inbox get "$item")                 # {"item": {...}}
  if [ "$(echo "$answer" | jq -r '.item.status')" = resolved ]; then
    decision=$(echo "$answer" | jq -r '.item.blocks[] | select(.type=="approval") | .answer.decision // empty')                                        # spec gate: approve|request-changes|reject
    selected=$(echo "$answer" | jq -r '[.item.blocks[] | select(.type=="question").answer | .selected[]?, (.other // empty)] | map(select(. != "")) | join("; ")')   # blocker: the chosen option(s) + free-text
    branch=$(echo "$info" | jq -r '.ticket.branch // empty')     # set for a mid-build blocker; empty for a branch-less block or the spec gate
    lead=$(echo "$info" | jq -r '.ticket.lead_session_id // empty')
    if [ -n "$branch" ]; then                          # a lead holds a real branch — relay durably and nudge
      waypoint board post {{ticket_channel}} "$selected" --meta kind=relay
      waypoint sessions send "$lead" \
        "[wp-msg from={{manager_session_id}}] Relay posted on {{ticket_channel}}; read owed relays and act."
    fi
  fi
fi
```

Then, once the gate item has resolved (the same `.item.status` check above gates every
shape), transition out of the awaiting state by the block's shape:

- **mid-build blocker** (a live lead on its branch) — relay `$selected` to the lead
  (above), then resume `blocked → building`;
- **branch-less blocker** (an infeasible `spec_pending → blocked`, with no lead to
  relay to) — `$selected` is your transition directly: `proceed on a human-supplied
  spec` → `blocked → ready` (record a supplied spec with `--spec-ref <ref>`), `re-spec`
  → `blocked → spec_pending` (see **Re-spec** below), `abandon` → `blocked → abandoned`;
- **spec gate** — branch on `$decision`: `approve` → `spec_review → ready`;
  `request-changes` → `spec_review → spec_pending` (see **Re-spec**); `reject` →
  `spec_review → abandoned`.

`awaiting_since` clears automatically on exit. For the relayed cases, each relay is a
`kind=relay` post the lead consumes in board-entry-`id` order, applying each once;
post the relay before the exit transition (a re-post lands under a higher id and the
lead re-applies it once).

## Re-spec — a request-changes or a blocked re-spec

`spec_review → spec_pending` (request-changes) and `blocked → spec_pending` both send
the ticket back for a fresh spec. Lift the human's requested changes from the resolved
gate item's `reply.notes` into a durable `kind=respec` note **before** the transition,
then re-spawn the writer per `{{templates_dir}}/manager/triage.md` (Spawn the writer),
which re-derives the writer role from the ticket cell's `spec_route`:

```bash
notes=$(waypoint inbox get "$item" | jq -r '[.item.blocks[].reply.notes // empty] | join("\n")')
waypoint board post {{ticket_channel}} "${notes:-revise per the review}" --meta kind=respec
waypoint manager ticket transition {{ticket_id}} --to spec_pending --reason respec
# then re-spawn the writer: {{templates_dir}}/manager/triage.md, "Spawn the writer"
```

The re-spawned writer reads the newest `kind=respec` note and revises the prior
`{{spec_ref}}`. A crash before the re-spawn leaves a `spec_pending` ticket whose reaped
writer the `dead_leads` reconcile re-spawns; the note is durable, so the revision still
runs.

## Done / partial

On `done`/`partial`, move to `review_requested`. `review_requested` is reachable only
from `building`, so a coalesced wake that finds the ticket still `delegated` (the
strategy post and the `done` arrived together) hops `delegated → building` first:

```bash
[ "$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.state')" = delegated ] \
  && waypoint manager ticket transition {{ticket_id}} --to building --reason "build observed with done"
```

The lead parks alive and idle; the ticket keeps holding the shared tree until it lands
or is abandoned:

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
