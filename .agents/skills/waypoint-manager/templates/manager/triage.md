# Manager — triage

A ticket is in `intake` (a user posted it to `{{tickets_channel}}`, or you created
it). Triage classifies its **input type**, assigns a **scale** and a coarse
**footprint**, then routes it.

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

Apply the scale rule — **substantial** when the work {{substantial_when}}. Scale
does not pick the artifact (input-type does)
and cannot skip a gate: every ticket routed to `spec_pending` to author a PRD/RFC
passes the `spec_review` human gate. What scale governs is the bug-report branch
below (a trivial fix takes the direct-instruction `triaged → ready` path with no
spec; a non-trivial one is specced) and, for a PRD input, whether it is small
enough to reduce to an RFC. Estimate a **coarse footprint** — the path globs the work
will likely touch — from the body and a quick look at the repo. Recorded for
observability; scheduling is priority + FIFO.

Record the scale **on the `intake → triaged` transition**, then set the coarse
footprint:

```bash
waypoint manager ticket transition {{ticket_id}} --to triaged --scale {{scale}}
waypoint manager ticket update {{ticket_id}} --footprint "{{footprint}}"   # repeat per glob; add --kind if useful
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
  approval gate (`{{templates_dir}}/manager/monitor.md`) before `ready`.
  ```bash
  waypoint manager ticket transition {{ticket_id}} --to spec_pending
  ```
- **→ ready, pass-through** (input is already a usable spec) — record the input doc
  as the spec and go straight to `ready`: no writer, no `spec_review` gate.
  `{{templates_dir}}/manager/delegate.md` spawns the tech-lead with this `spec_ref`. Point
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
owner-scoped, titled for reconcile; read-only in your tree ({{repo_dir}}), writing
only its spec doc under `{{spec_dir}}/`. The `role` (the title suffix reconcile
matches) is the `spec_route`; its `--role` render key is the underscore form:

```bash
role=<prd-writer|rfc-writer>          # from spec_route; the reconcile title suffix
render_role=<prd_writer|rfc_writer>   # its manifest key (underscore) for --role
# Launch args per route (baked from the manifest at init): prd-writer uses the
# first, rfc-writer the second.
if [ "$render_role" = prd_writer ]; then
  sid=$(waypoint sessions start {{prd_writer_launch}} \
    --cwd {{repo_dir}} --title "subagent:ticket-{{ticket_id}}:$role" \
    --spawner-session-id {{manager_session_id}} | jq -r .session.id)
else
  sid=$(waypoint sessions start {{rfc_writer_launch}} \
    --cwd {{repo_dir}} --title "subagent:ticket-{{ticket_id}}:$role" \
    --spawner-session-id {{manager_session_id}} | jq -r .session.id)
fi
waypoint manager ticket update {{ticket_id}} --lead-session-id "$sid"
waypoint sessions send "$sid" "$(waypoint manager render --role $render_role --step write --ticket {{ticket_id}})"
```

To **resume** a writer that died mid-spec, re-run this same spawn after terminating
the dead session and self-looping `spec_pending → spec_pending` (`--reason
lead-died`, spends `lead_restarts`). Past `max_lead_restarts` the self-loop 409s: post a
keyless `kind=decision` retry/abandon entry, then escalate `--to blocked` (branch-less):

```bash
waypoint board post {{ticket_channel}} \
  "the spec writer keeps dying on this ticket (restart budget spent); fix the cause then retry, or abandon. Options: retry; abandon." \
  --meta kind=decision
waypoint manager ticket transition {{ticket_id}} --to blocked --reason "writer-restart budget exhausted"
```

The next drain reconstructs the retry/abandon gate from that entry
(`{{templates_dir}}/manager/monitor.md`); on **retry** the gate resets the writer budget
and re-specs, on **abandon** it ends.

The same spawn serves a **re-spec** routed here from
`{{templates_dir}}/manager/monitor.md` (a request-changes or a blocked re-spec):
re-derive `role`/`render_role` from the ticket cell's `spec_route`, and the writer
revises from the newest `kind=respec` note on the channel. A `spec_route` of
`direct`/`passthrough` carries no writer; choose a writer route, stamp it
(`board set-meta {{tickets_channel}} --key ticket:{{ticket_id}} --merge --meta
spec_route=<prd-writer|rfc-writer>`), then spawn the matching writer.

When the `rfc-writer` route is **converting an input PRD**, pass that PRD to the
writer as its primary input — it preserves the PRD's intent and reduces it to a
concrete technical design. The writer posts the
spec ref back and recommends an execution strategy, or posts `infeasible` when the
request cannot be specced; you then drive the matching edge (`spec_pending →
spec_review` for a spec, `→ blocked` for infeasible) per
`{{templates_dir}}/manager/monitor.md`, which covers both branches, reaps the ephemeral
writer, and drives the relay. Pass-through and trivial routes skip this
section; `{{templates_dir}}/manager/delegate.md` picks them up when the tree frees.
