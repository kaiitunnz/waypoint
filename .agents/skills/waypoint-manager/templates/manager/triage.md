# Manager — triage

A ticket is in `intake` (a user posted it to `{{tickets_channel}}`, or you created
it). Triage assigns it a scale and a coarse footprint, then routes it. Serial:
only one ticket may be in `spec_pending` at a time, so `manager next` will not
recommend a second substantial-spec until the current one clears.

## Read the ticket

```bash
waypoint manager ticket show {{ticket_id}}
waypoint board read {{tickets_channel}} --key ticket:{{ticket_id}}   # the user's post + meta
```

Ticket: **{{ticket_title}}** — priority {{priority}}.
Body:

> {{ticket_body}}

## Assign scale and footprint

Apply the manifest `scale.substantial_when` rule. **Substantial** when the work
needs a schema/API/UX change, touches more than one module, or has ambiguous
intent; otherwise **trivial**. Estimate a **coarse footprint** — the path globs the
work will likely touch — from the body and a quick look at the repo; it need only
be good enough to order overlapping tickets, and the spec (if any) refines it.

```bash
waypoint manager ticket update {{ticket_id}} --scale {{scale}} \
  --footprint "{{footprint}}"   # repeat --footprint per glob; add --kind if useful
```

## Route

- **Substantial** → transition to `spec_pending` (only if no other ticket is
  already there — the server enforces ≤ 1). Then spawn the PRD writer
  (`prd-writer`) or RFC writer (`rfc-writer`) per the ticket's shape — a product
  problem gets a PRD, a technical/architectural change gets an RFC — and send it
  `templates/prd-writer/write.md` or `templates/rfc-writer/write.md`:
  ```bash
  waypoint manager ticket transition {{ticket_id}} --to spec_pending
  ```
- **Trivial** → transition straight to `ready`; no spec, footprint stays coarse:
  ```bash
  waypoint manager ticket transition {{ticket_id}} --to ready
  ```
- **Reject / duplicate** → `abandoned`, with a reason and a one-line note to the
  user on `{{tickets_channel}}`:
  ```bash
  waypoint manager ticket transition {{ticket_id}} --to abandoned --reason "duplicate of ticket-…"
  ```

## Spawn the writer (substantial only)

Spawn ephemeral, owner-scoped, titled for reconcile; it does **not** need a
worktree (it only writes a spec doc):

```bash
sid=$(waypoint sessions start --preset writer-opus \
  --cwd <repo-root> \
  --title "subagent:ticket-{{ticket_id}}:prd-writer" \
  --spawner-session-id {{manager_session_id}} | jq -r .session.id)
waypoint manager ticket update {{ticket_id}} --lead-session-id "$sid"
waypoint sessions send "$sid" "$(render templates/prd-writer/write.md)"
```

The writer posts the spec ref back and recommends an execution strategy; you then
move the ticket `spec_pending → spec_review` and open the human approval gate
(`templates/manager/monitor.md` covers the gate and the relay). Reap the writer
after the spec lands — it is ephemeral.
