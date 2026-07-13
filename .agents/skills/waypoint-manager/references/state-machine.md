# State machine

The `waypoint manager` command group is the manager's externalized procedure
state — one durable record per ticket, with every transition and scheduler
invariant validated **server-side**. A drifting context cannot enact an illegal
step: the server rejects it with a `409`. Read this to know what each state means,
which transitions are legal, and which invariants hold no matter what the manager
believes.

## Commands

```bash
waypoint manager init --manifest waypoint-manager.yaml   # persist machine-relevant config
waypoint manager state [--json]                          # whole ticket set + slots + lease
waypoint manager next [--tried <id>]... [--json]         # re-anchor (below)
waypoint manager ticket add <title> [--id …] [--priority p2] [--kind …] [--scale …] [--footprint <glob>]… [--dep <id>]…
waypoint manager ticket show <id>
waypoint manager ticket update <id> [--priority] [--kind] [--scale] [--footprint] [--dep] [--spec-ref] [--intended-lead-title] [--lead-session-id] [--branch] [--worktree-path] [--pr-url]
waypoint manager ticket transition <id> --to <state> [--reason …] [--scale] [--spec-ref] [--intended-lead-title] [--lead-session-id] [--branch] [--worktree-path] [--pr-url] [--is-partial | --not-partial]
waypoint manager lock acquire --owner <sid> [--ttl-seconds N]
waypoint manager lock steal   --owner <sid> [--ttl-seconds N]
waypoint manager lock release --owner <sid>
```

`transition` is keyed by **target state** (`--to <state>`), and the server checks
the edge is legal from the ticket's current state. Metadata rides the same call
(`--intended-lead-title`, `--branch`, `--worktree-path`, `--pr-url`, `--spec-ref`,
`--scale`, `--is-partial`) so intent and its dedup key land atomically with the
state change. Use `ticket update` for `footprint`/`kind`/`deps` refinement (no
state change). Illegal edges, exhausted budgets, and violated invariants all
return `409` — treat a `409` as "re-anchor and reconcile," never as a reason to
retry blindly.

## The 14 states

Three groups by how they occupy resources:

- **Compute states** — `delegated`, `building`, `revising`. Each holds one
  **execution slot** (a running tech-lead compute session). Entry is gated on a
  free slot.
- **Awaiting-human states** — `spec_review`, `blocked`, `review_requested`. They
  **release the slot** (the parked lead stays alive but idle) and stamp
  `awaiting_since` on entry, cleared on exit, so the latency timeout counts only
  genuine human waits.
- **Non-occupying** — `intake`, `triaged`, `spec_pending`, `ready`; and the
  terminals `merged`, `deferred`, `abandoned`.

| State | Meaning |
|---|---|
| `intake` | A user posted a ticket; untriaged. |
| `triaged` | Scale + coarse footprint assigned. |
| `spec_pending` | A PRD/RFC writer is authoring the spec (**≤ 1 ticket here at a time** — serial analysis). |
| `spec_review` | Spec posted; awaiting the human approval gate. |
| `ready` | Approved (or trivial); awaiting a free slot to delegate. |
| `delegated` | Intent recorded, lead spawned; awaiting lead-accepted + strategy chosen. |
| `building` | Lead is executing the ticket in its worktree. |
| `blocked` | A genuine blocker (error / decision / attention / infeasible / budget-exhausted) escalated to the human. |
| `review_requested` | Lead reported `done`/`partial`, PR open; the per-PR review-until-merge loop is with the human. |
| `revising` | Lead is addressing requested review changes. |
| `merging` | The manager holds the integration lease and is landing the PR (**≤ 1 ticket here**). |
| `merged` | Terminal — fully integrated. |
| `deferred` | Terminal — a partial subset merged; unmet goals spawned as new tickets. |
| `abandoned` | Terminal — rejected, aborted, or a latency timeout. |

`done` and `partial` are **feedback signals** the lead posts on the board, not
states; the manager reads them and drives `building → review_requested`. `done` is
distinct from the terminal `merged`.

## Transition table (target-state adjacency, as the server enforces it)

| From | Legal `--to` targets | Notes |
|---|---|---|
| `intake` | `triaged` | |
| `triaged` | `spec_pending`, `ready`, `abandoned` | substantial → spec; trivial → ready; reject/duplicate → abandoned |
| `spec_pending` | `spec_review`, `spec_pending`, `blocked` | spec posted; self = writer-died resume; writer infeasible/budget → blocked |
| `spec_review` | `ready`, `spec_pending`, `abandoned` | approve; request-changes; reject/latency-timeout |
| `ready` | `delegated` | consumes an `attempts` budget unit; slot-gated by invariant |
| `delegated` | `building`, `ready`, `blocked`, `delegated` | lead-accepted; spawn-fail retry (attempts < N); spawn-fail exhausted/infeasible; self = lead-died resume |
| `building` | `review_requested`, `blocked`, `building` | done/partial; error/decision/attention; self = lead-died resume |
| `blocked` | `building`, `abandoned`, `blocked` | human answer relayed; abort/latency-timeout; self = parked-lead-died resume |
| `review_requested` | `revising`, `merging`, `abandoned`, `blocked`, `review_requested` | request-changes; human merge; abort/latency; blocker found; self = parked-lead-died resume |
| `revising` | `review_requested`, `blocked`, `revising` | re-pushed (kind=done); needs a decision; self = lead-died resume |
| `merging` | `merged`, `deferred`, `revising`, `blocked` | full green; partial green → deferred; semantic conflict; CI red / needs human |
| `merged` / `deferred` / `abandoned` | *(none — terminal)* | |

### The lead-died resume is a self-loop

The RFC's "live-lead state S → S (resume)" is encoded as a **self-transition**:
`ticket transition <id> --to <same-state>` on any of the six resumable states —
`spec_pending`, `delegated`, `building`, `revising`, `blocked`, `review_requested`
— consuming one `lead_restarts` budget unit and rebinding a fresh lead to the
**preserved worktree**. Once `lead_restarts` hits `max_lead_restarts` the self-loop
is rejected (`409`); the manager then escalates with an explicit `--to blocked`.
`merging` has **no** self-loop: a lost integrator is recovered by reconciling
against `gh pr view`, not by resuming a session.

## `manager next` — the re-anchor

```bash
waypoint manager next --json          # add --tried <id> per ticket that failed earlier in THIS drain
```

Returns three things over the live ticket set:

- **`slots`** — `{total, used, free}`, `used = count(delegated|building|revising)`.
- **`tickets[]`** — each live ticket's current state and its `legal_transitions`
  (the target states above).
- **`recommended`** — at most **one** action: the highest-priority actionable
  ticket (priority order, then FIFO by `created_at`, then id), slot/invariant/
  budget-gated, excluding every id passed via `--tried`.

**`next` recommends only the manager-initiated *pull* moves** — `triage`
(`intake → triaged`), `substantial` (`triaged → spec_pending`, gated on no other
ticket in `spec_pending`), `trivial` (`triaged → ready`), and `delegate`
(`ready → delegated`, gated on a free slot and the `attempts` budget). Every
human- or lead-driven edge — spec posted, human approve, lead-accepted, done/
partial, human merge, request-changes, and the lead-died self-loops — is returned
as a **legal transition** but never as the recommendation: the manager enacts
those the moment it *observes* the external signal during reconcile (a board
status cell, an inbox answer, a dead lead, a merged PR), not because `next` told
it to. So the drain is: take `recommended` if present, *and* separately apply
every observed external edge, until `next` reports nothing and no external signal
is outstanding.

## Server-enforced invariants

`check_invariants` runs on every `transition`/`update` over the whole set and
rejects the write with `409` if any fails:

- **Slot cap** — at most `execution_slots` tickets in `{delegated, building,
  revising}`.
- **≤ 1 merging** — the serialized integration gate.
- **≤ 1 spec_pending** — serial analysis (one active writer).
- **Unique `intended_lead_title`** across all live (non-terminal) tickets — the
  spawn dedup key, so a re-fired delegate cannot create a second lead.
- **Budget ceilings** — `attempts ≤ max_delegate_attempts` and `lead_restarts ≤
  max_lead_restarts`. The two counters are **independent**: an initial spawn
  failure spends `attempts`, a post-work lead death spends `lead_restarts`, so a
  lead that dies after working is never mistaken for a failing delegate. Each
  routes to `blocked`-awaiting-human at its own ceiling.

These are enforced server-side precisely so context growth is irrelevant: the
manager may propose anything; only legal, in-budget, invariant-preserving moves
commit.
