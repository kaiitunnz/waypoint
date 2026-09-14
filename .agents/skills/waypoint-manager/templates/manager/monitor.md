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

## Observe the strategy post

The lead posts its chosen strategy to the `strategy` cell before it starts building. On
a wake where the ticket is still `delegated` and that cell is present, the build has
started — move `delegated → building`:

```bash
strategy=$(waypoint board read {{ticket_channel}} --key strategy --json | jq -r '.cells[0].text // empty')
state=$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.state')
[ -n "$strategy" ] && [ "$state" = delegated ] \
  && waypoint manager ticket transition {{ticket_id}} --to building --reason "strategy: $strategy"
```

## Blockers → the inbox (with escalation policy)

Apply the escalation policy: settle blockers of kind {{self_decide}} yourself;
escalate {{always_escalate}} to the human inbox. The lead posted the full question and
its options as a keyless `kind=<error|decision|attention>` log entry — read it (`board
log {{ticket_channel}} --since <last-seen>`) and lift its question and options
**verbatim** into the inbox item. Transition, then post an inbox item and record
`awaiting_since` is now stamped (the server does it):

```bash
[ "$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.state')" = blocked ] \
  || waypoint manager ticket transition {{ticket_id}} --to blocked --reason "<the blocker>"
item=$(waypoint inbox list --status open --q "{{ticket_channel}}: " \
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

Each gate section is idempotent: the leading transition is guarded on the ticket's
state (skipped when already in the awaiting state), and the post first adopts an
existing open item for this ticket+phase (matched by subject), posting fresh only when
none exists. So re-running the whole section — a `stale_gates` re-open after a crash
between the transition and the post — re-opens the same gate without re-transitioning
or double-posting. It records the item id on the ticket (`--inbox-item`); the answer
read looks that id up and acts once it resolves. The server clears the id on the next
non-self transition, so a later gate never resolves this one's answer.

Frame an open question when the lead left the decision open. You are
`--wake-on-inbox`-subscribed, so the human's answer wakes you.

## The substantial-spec gate

When the writer posts back, branch on its `kind`. Act on the newest `spec_ready`/
`infeasible` post above the newest `respec` note — a re-spec round accumulates older
posts in the log.

**`spec_ready`** — move `spec_pending → spec_review` (recording `{{spec_ref}}`) and
post an **approval** inbox item that **attaches the spec**. On the answer: **approve**
→ `ready`; **request-changes** →
`spec_pending` (re-spec — see **Re-spec** below); **reject** → `abandoned`. A silent
latency-timeout is abandoned by the `latency_timeouts` reconcile path in
`{{templates_dir}}/manager/loop-cycle.md`.

`{{spec_ref}}` must be a local readable regular file for this gate; a symbolic board
ref (`ticket:<id>`) is not attachable. `--attach` uploads it and pins it against the
inbox item for the life of the gate. On an attach/post failure, leave `--inbox-item`
unset so the next drain reports the failure rather than gating on an unopenable spec.

```bash
[ "$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.state')" = spec_review ] \
  || waypoint manager ticket transition {{ticket_id}} --to spec_review --spec-ref {{spec_ref}}
item=$(waypoint inbox list --status open --q "{{ticket_channel}}: " \
  | jq -r '[.items[] | select((.subject|startswith("{{ticket_channel}}: ")) and (.subject|endswith("— spec review"))) | .id] | first // empty')   # adopt an open gate a crash left behind
if [ -z "$item" ]; then
  [ -f "{{spec_ref}}" ] || { echo "spec_ref is not a local file: {{spec_ref}}" >&2; exit 1; }   # fail closed; no path-only gate
  item=$(waypoint inbox post --json - --attach "{{spec_ref}}" <<'JSON' | jq -r '.item.id'
{ "subject": "{{ticket_channel}}: {{ticket_title}} — spec review",
  "blocks": [
    { "type": "markdown", "text": "<spec summary; the attached file is the full RFC/PRD>" },
    { "type": "approval", "prompt": "Approve this spec?",
      "options": ["approve", "request-changes", "reject"], "required": true } ] }
JSON
)
fi
[ -n "$item" ] && waypoint manager ticket update {{ticket_id}} --inbox-item "$item"   # only record a gate that was actually posted
```

**`infeasible`** — the writer determined the request cannot be specced. Move
`spec_pending → blocked` and post a **decision** inbox item carrying the writer's
reason; the branch-less-blocker relay below drives the human's answer (`blocked →
ready` to proceed on a human-supplied spec, `blocked → spec_pending` to re-spec,
`blocked → abandoned`).

```bash
[ "$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.state')" = blocked ] \
  || waypoint manager ticket transition {{ticket_id}} --to blocked --reason "infeasible spec: <writer's reason>"
item=$(waypoint inbox list --status open --q "{{ticket_channel}}: " \
  | jq -r '[.items[] | select((.subject|startswith("{{ticket_channel}}: ")) and (.subject|endswith("— decision needed"))) | .id] | first // empty')   # adopt an open gate a crash left behind
[ -n "$item" ] || item=$(waypoint inbox post --json - <<'JSON' | jq -r '.item.id'
{ "subject": "{{ticket_channel}}: {{ticket_title}} — decision needed",
  "blocks": [
    { "type": "markdown", "text": "<the writer's infeasibility reason, verbatim>. To proceed on your own spec, add its path or ref as a comment." },
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
— pull it), then drive the state by the gate's shape. A branch-present ticket is a
mid-build blocker (a resumable lead) or a restart-exhausted block (its `lead_restarts`
budget is spent); the two split on that budget:

```bash
info=$(waypoint manager ticket show {{ticket_id}})
item=$(echo "$info" | jq -r '.ticket.inbox_item_id // empty')
if [ -n "$item" ]; then
  answer=$(waypoint inbox get "$item")                 # {"item": {...}}
  if [ "$(echo "$answer" | jq -r '.item.status')" = resolved ]; then
    decision=$(echo "$answer" | jq -r '.item.blocks[] | select(.type=="approval") | .answer.decision // empty')                                        # spec gate: approve|request-changes|reject
    selected=$(echo "$answer" | jq -r '[.item.blocks[] | select(.type=="question").answer | .selected[]?, (.other // empty)] | map(select(. != "")) | join("; ")')   # blocker: the chosen option(s) + free-text
    notes=$(echo "$answer" | jq -r '[.item.blocks[].reply.notes // empty] | map(select(. != "")) | join("; ")')   # any block-level comment the human added
    branch=$(echo "$info" | jq -r '.ticket.branch // empty')     # set for a mid-build or restart-exhausted block; empty for a branch-less block or the spec gate
    restarts=$(echo "$info" | jq -r '.ticket.lead_restarts')
    maxr=$(waypoint manager state --json | jq -r '.config.max_lead_restarts')
    if [ -n "$branch" ] && [ "$restarts" -ge "$maxr" ] && [ "$selected" = retry ]; then      # restart-exhausted: fresh budget, resume the branch
      waypoint manager ticket update {{ticket_id}} --reset-lead-restarts
      waypoint manager ticket transition {{ticket_id}} --to building --reason "retry after restart exhaustion"
    elif [ -n "$branch" ] && [ "$restarts" -ge "$maxr" ] && [ "$selected" = abandon ]; then
      waypoint manager ticket transition {{ticket_id}} --to abandoned --reason "abandoned after restart exhaustion"   # then Finalize ({{templates_dir}}/manager/integrate.md)
    elif [ -n "$branch" ]; then                        # mid-build blocker: relay durably, resume
      waypoint board post {{ticket_channel}} "${selected}${notes:+ (notes: $notes)}" --meta kind=relay
      waypoint manager ticket transition {{ticket_id}} --to building --reason "resume after blocker"
    fi
  fi
fi
```

Then, once the gate item has resolved (the same `.item.status` check above gates every
shape), a branch-less blocker or the spec gate transitions out of the awaiting state by
the block's shape. Both may still carry the ephemeral writer as `lead_session_id` — a
spec-gate ticket always does (it stayed alive through `spec_review`), and a branch-less
`blocked` infeasible ticket does too. Any disposition that **ends** writer ownership —
approval-for-handoff, rejection, abandonment, or the human taking over with their own
spec — reaps that writer and clears the ref **before** the `ready`/terminal transition,
so a later `{{templates_dir}}/manager/delegate.md` records the tech-lead without
overwriting the only reference to a live session. Re-spec dispositions **keep** the writer
(see **Re-spec** below). The reap is idempotent, so a crash between the delete and the
transition replays safely:

```bash
# reap the parked writer before a handoff/terminal transition (idempotent)
writer=$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.lead_session_id // empty')
if [ -n "$writer" ]; then
  waypoint sessions delete "$writer" --force || true   # tolerate an already-deleted session
  waypoint manager ticket update {{ticket_id}} --lead-session-id ""
fi
```

- **branch-less blocker** (an infeasible or writer-restart-exhausted `spec_pending →
  blocked`, or a delegate-budget-exhausted `delegated → blocked`, with no lead to relay
  to) — for these, `$selected` is your transition directly: `proceed on a human-supplied
  spec` → reap the writer (above), then `blocked → ready`, recording `--spec-ref` from
  the ref the human gave in `reply.notes`, else the `ticket:{{ticket_id}}` cell (the body
  as the spec); `re-spec` → `blocked → spec_pending` (see **Re-spec** below — keeps the
  writer); `abandon` → reap the writer (above), then `blocked → abandoned`.
  `retry` splits on `attempts` (a writer/spec ticket never delegated, `attempts == 0`; a
  delegate-exhaustion block has `attempts >= 1`): for `attempts == 0` (writer-restart
  exhaustion, whose writer already died — nothing to reap), when the spec slot is free
  (`waypoint manager state --json | jq '[.tickets[]|select(.state=="spec_pending")]|length'`
  is `0`) reset the writer budget
  (`waypoint manager ticket update {{ticket_id}} --reset-lead-restarts`) and return
  `blocked → spec_pending` with no respec note — the next drain's `dead_leads` re-spawns
  the writer, and a busy slot defers to a later drain; for `attempts >=
  1` (delegate exhaustion), reset the delegate budget
  (`waypoint manager ticket update {{ticket_id}} --reset-attempts`) and return
  `blocked → ready`;
- **spec gate** — branch on `$decision`: `approve` → reap the writer (above), then
  `spec_review → ready`; `request-changes` → `spec_review → spec_pending` (see
  **Re-spec** — keeps the writer); `reject` → reap the writer (above), then
  `spec_review → abandoned`.

`awaiting_since` clears automatically on exit. For the relayed cases, each relay is a
`kind=relay` post the lead consumes in board-entry-`id` order, applying each once;
post the relay before the exit transition (a re-post lands under a higher id and the
lead re-applies it once).

## Re-spec — a request-changes or a blocked re-spec

`spec_review → spec_pending` (request-changes) and `blocked → spec_pending` both send
the ticket back for a fresh spec. Do **not** reap the writer on this path — it is retained
as `lead_session_id` and reused; the authoring session best interprets the human's review
notes. `spec_pending` holds the single
spec slot (≤1 at a time), so re-spec only when the slot is free; another ticket holding it
defers this one to a later drain — the resolved gate item keeps the human's notes until
then, so nothing is lost. When the slot is free, lift the human's requested changes from
the resolved gate item's `reply.notes` into a durable `kind=respec` note **before** the
transition, then hand off to `{{templates_dir}}/manager/triage.md` (Spawn the writer),
which re-derives the writer role from the ticket cell's `spec_route` and **re-sends the
`write` step to the retained live writer** (spawning a replacement only if it has died):

```bash
if [ "$(waypoint manager state --json | jq '[.tickets[] | select(.state == "spec_pending")] | length')" = 0 ]; then
  notes=$(waypoint inbox get "$item" | jq -r '[.item.blocks[].reply.notes // empty] | join("\n")')
  waypoint board post {{ticket_channel}} "${notes:-revise per the review}" --meta kind=respec
  waypoint manager ticket transition {{ticket_id}} --to spec_pending --reason respec
  # then re-send `write` to the retained writer (re-spawns only if it died):
  # {{templates_dir}}/manager/triage.md, "Spawn the writer"
fi
```

The writer reads the newest `kind=respec` note and revises the prior `{{spec_ref}}` from
its own conversation context. If that writer has died while parked, the `dead_leads`
reconcile path re-spawns a replacement that reads the same durable note; the note is the
recovery source, so the revision runs either way.

## Done / partial

On `done`/`partial`, move to `review_requested` — from `building` on the first done, or
from `revising` on a re-report. Read the state once: a coalesced wake that finds the
ticket still `delegated` (the strategy post and the `done` arrived together) hops
`delegated → building` first, and a re-read `done` while already `review_requested` is a
no-op:

```bash
state=$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.state')
[ "$state" = delegated ] \
  && waypoint manager ticket transition {{ticket_id}} --to building --reason "build observed with done" \
  && state=building
```

The lead parks alive and idle; the ticket keeps holding the shared tree until it lands
or is abandoned. Transition to `review_requested` only from `building` or `revising`:

{{#if integration_mode == pr}}
The lead's `done` cell carries the PR url in its `pr=` meta; read it and pass it as
`--pr-url` on this transition:

```bash
if [ "$state" = building ] || [ "$state" = revising ]; then
  pr=$(waypoint board read {{ticket_channel}} --key status --json | jq -r '.cells[0].metadata.pr')
  waypoint manager ticket transition {{ticket_id}} --to review_requested \
    --pr-url "$pr" --not-partial      # or --is-partial for a partial delivery
fi
```
{{/if}}
{{#if integration_mode == local}}
```bash
if [ "$state" = building ] || [ "$state" = revising ]; then
  waypoint manager ticket transition {{ticket_id}} --to review_requested \
    --not-partial      # or --is-partial for a partial delivery
fi
```
{{/if}}

Then open the review gate — see `{{templates_dir}}/manager/integrate.md`.
