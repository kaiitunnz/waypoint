# Manager — triage

A ticket is in `intake` (a user posted it to `{{tickets_channel}}`, or you created
it). Triage classifies its **input type**, assigns a **scale** and a coarse
**footprint**, then routes it. Serial: only one ticket may be in `spec_pending` at
a time, so `manager next` will not recommend a second spec-authoring route until
the current one clears.

## Read the ticket

```bash
waypoint manager ticket show {{ticket_id}}
waypoint board read {{tickets_channel}} --key ticket:{{ticket_id}}   # the user's post + meta
```

Ticket: **{{ticket_title}}** — priority {{priority}}.
Body:

> {{ticket_body}}

## Classify the input type

Judge, from what the user actually posted, which of **five input types** it is —
this decides *which artifact (if any) gets written and by whom*, orthogonally to
scale. Classify from the content, not a label the user attached.

- **bug-report** — a reported defect or regression ("X is broken / wrong").
- **feature-request** — a concrete, bounded capability ask.
- **open-ended** — a broad or underspecified goal with no settled product shape
  ("improve onboarding"); needs product definition before any technical design.
- **prd** — the post already *is* a product requirements document.
- **rfc** — the post already *is* a technical design / RFC.

## Assign scale and footprint

Apply the manifest `scale.substantial_when` rule. **Substantial** when the work
needs a schema/API/UX change, touches more than one module, or has ambiguous
intent; otherwise **trivial**. Scale does not pick the artifact (input-type does)
and cannot skip a gate: every ticket routed to `spec_pending` to author a PRD/RFC
passes the `spec_review` human gate. What scale governs is the bug-report branch
below (a trivial fix takes the direct-instruction `triaged → ready` path with no
spec; a non-trivial one is specced) and, for a PRD input, whether it is small
enough to reduce to an RFC. Estimate a **coarse footprint** — the path globs the
work will likely touch — from the body and a quick look at the repo; it need only
be good enough to order overlapping tickets, and the spec (if any) refines it.

```bash
waypoint manager ticket update {{ticket_id}} --scale {{scale}} \
  --footprint "{{footprint}}"   # repeat --footprint per glob; add --kind if useful
```

## Route

Map input-type (and, for bugs and PRDs, a sub-decision) to a downstream and a
**legal `triaged →` edge**: `spec_pending` when a writer must author a *new*
artifact (still ≤ 1 ticket in `spec_pending` at a time — the server enforces it),
`ready` when the spec already exists (**pass-through**) or none is needed
(**trivial direct-instruction**).

| Input type | Sub-decision | Downstream | Artifact | `--to` |
|---|---|---|---|---|
| open-ended | — | prd-writer | new PRD | `spec_pending` |
| feature-request | — | rfc-writer | new RFC | `spec_pending` |
| bug-report | trivial fix | direct-instruction (no writer) | none | `ready` |
| bug-report | non-trivial, technical | rfc-writer | new RFC | `spec_pending` |
| bug-report | non-trivial, open *product* question | prd-writer | new PRD | `spec_pending` |
| prd | small enough to reduce to a design | rfc-writer (PRD in → RFC out) | new RFC | `spec_pending` |
| prd | problems only resolvable in implementation | tech-lead (pass-through) | input PRD as `spec_ref` | `ready` |
| prd | large & well-defined | tech-lead (pass-through) | input PRD as `spec_ref` | `ready` |
| rfc | — | tech-lead (pass-through) | input RFC as `spec_ref` | `ready` |
| any | reject / duplicate | — | — | `abandoned` |

- **→ spec_pending** (write a new artifact) — transition, then spawn the matching
  writer (next section). Its posted spec goes through the `spec_review` human
  approval gate (`templates/manager/monitor.md`) before `ready`.
  ```bash
  waypoint manager ticket transition {{ticket_id}} --to spec_pending
  ```
- **→ ready, pass-through** (input is already a usable spec) — record the input doc
  as the spec and go straight to `ready`: no writer, no `spec_review` gate (the
  human authored the doc, so there is nothing for them to re-approve).
  `templates/manager/delegate.md` spawns the tech-lead with this `spec_ref`. Point
  `--spec-ref` at where the doc lives — a repo path if the user gave one, else the
  `ticket:{{ticket_id}}` cell on `{{tickets_channel}}` (the body *is* the spec).
  ```bash
  waypoint manager ticket transition {{ticket_id}} --to ready --spec-ref <input-doc>
  ```
- **→ ready, trivial direct-instruction** (a trivial bug fix) — no spec; the
  tech-lead works from the ticket body. Footprint stays coarse.
  ```bash
  waypoint manager ticket transition {{ticket_id}} --to ready
  ```
- **→ abandoned, reject / duplicate** — with a reason and a one-line note to the
  user on `{{tickets_channel}}`:
  ```bash
  waypoint manager ticket transition {{ticket_id}} --to abandoned --reason "duplicate of ticket-…"
  ```

## Record the classification (observability)

Stamp the chosen input-type and route onto the ticket cell — without clobbering
the user's post — so the decision is visible on the board:

```bash
waypoint board set-meta {{tickets_channel}} --key ticket:{{ticket_id}} --merge \
  --meta input_type={{input_type}} --meta spec_route={{spec_route}}
```

`input_type` is one of `bug-report|feature-request|open-ended|prd|rfc`;
`spec_route` is one of `prd-writer|rfc-writer|passthrough|direct`.

## Spawn the writer (spec_pending routes only)

For a `prd-writer` or `rfc-writer` route, spawn the matching writer — ephemeral,
owner-scoped, titled for reconcile; it does **not** need a worktree (it only writes
a spec doc). The `role` (title suffix + template dir) is the `spec_route`:

```bash
role=<prd-writer|rfc-writer>          # from spec_route
# {{writer_launch}} expands from the matching writer role in the manifest
# (roles.prd_writer / roles.rfc_writer) — never hardcode a preset/model here.
sid=$(waypoint sessions start {{writer_launch}} \
  --cwd <repo-root> \
  --title "subagent:ticket-{{ticket_id}}:$role" \
  --spawner-session-id {{manager_session_id}} | jq -r .session.id)
waypoint manager ticket update {{ticket_id}} --lead-session-id "$sid"
waypoint sessions send "$sid" "$(render templates/$role/write.md)"
```

When the `rfc-writer` route is **converting an input PRD**, pass that PRD to the
writer as its primary input — it preserves the PRD's intent and reduces it to a
concrete technical design (`templates/rfc-writer/write.md`). The writer posts the
spec ref back and recommends an execution strategy; you then move the ticket
`spec_pending → spec_review` and open the human approval gate
(`templates/manager/monitor.md` covers the gate and the relay). Reap the writer
after the spec lands — it is ephemeral. Pass-through and trivial routes skip this
section; `templates/manager/delegate.md` picks them up when a slot frees.
