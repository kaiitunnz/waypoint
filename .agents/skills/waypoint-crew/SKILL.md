---
name: waypoint-crew
description: Use when a coding agent must stand up a multi-role software-engineering organization — product manager, tech lead, frontend, backend, QA, release — across durable Waypoint sessions to carry a product through its lifecycle, whether starting a new product from zero or evolving an existing codebase: discovery, architecture, iterative build, QA, ship, and iterate. A lead runs the org chart, sequences coupled work by dependency and contract, and checkpoints with the user at phase boundaries. Not for a batch of independent tasks (use waypoint-workqueue) or a single coupled change (use delegate-and-review).
---

# Waypoint Crew

Stand up a **software-engineering organization** — a lead plus role sessions
(product, tech lead, frontend, backend, QA, release) — and drive a product
through its **full lifecycle**: discovery → architecture → build → QA → ship →
iterate. It works both **greenfield** (a new product from zero) and
**brownfield** (evolving an existing codebase) — the same phases run either way,
some just compress or adapt on an existing workspace (see
`references/lifecycle.md`). The org lives on the blackboard, so it survives the
lead running out of context and can be resumed.

Builds on three skills — skim them first: `waypoint-subagents` (spawn and reap
role sessions), `waypoint-comms` (the board and direct sends), and
`waypoint-workqueue`, whose crew mechanics (spawn/worktree/reap, immutable task
contract + mutable status cells, linear integration) this skill **reuses
unchanged** for each parallelizable build batch. This skill does not re-teach
those mechanics; it layers an **org chart**, a **lifecycle**, and
**dependency-aware sequencing of coupled work** on top of them.

## Crew vs. work queue vs. delegate-and-review

Three shapes sit on the same `subagents` + `comms` foundation; pick by the shape
of the work, not its size:

- **Work queue** (`waypoint-workqueue`) — a flat batch of **independent** tasks
  merged by a lead. Migrations, codemods, per-file sweeps. No roles, no
  lifecycle, no cross-task dependencies.
- **Delegate-and-review** (`waypoint-subagents`) — *one* child takes a coherent,
  tightly-coupled chunk and you review its diff. For work too big to do inline
  but too coupled to parallelize.
- **Crew** (this skill) — a **hierarchical org** driving a whole product over
  time. Roles specialize, work is **coupled** (frontend depends on a backend
  API), and the job runs through **lifecycle phases** rather than a one-shot
  batch. This is the shape work queue explicitly disclaims.

Reach for a crew when the deliverable is a *product built or evolved over time* —
whether from an empty repo or an existing codebase — not a bounded batch or a
single change.

## The organization in brief

- **Lead / engineering manager** — you. The one persistent role: owns the board,
  the org chart, dependency sequencing, integration, and every phase checkpoint.
  Never delegated away.
- **Role sessions** — product manager, tech lead/architect, frontend, backend,
  QA, reviewer/release. Spawned to fit the current phase and reaped when idle;
  small products collapse several roles into the lead. Each is a durable Waypoint
  session on any backend/model. The full template is in `references/org-chart.md`.
- **Lifecycle** — seven phases from intake to a shipped, iterating product
  (greenfield or brownfield), each with an owner, a board artifact, an exit
  criterion, and (at PRD / architecture / pre-ship) a **human checkpoint**. See
  `references/lifecycle.md`.
- **Coordination** — a two-tier board: an `org:<product>` channel for durable
  lifecycle artifacts, and a `job:<phase-slug>` channel per parallelizable build
  batch that composes the work-queue crew natively. Dependency sequencing and
  contract-first coupling live here. See `references/coordination.md`.

## How it works

Read the three references in order — `references/org-chart.md` (who),
`references/lifecycle.md` (what, when), `references/coordination.md` (how they
talk and stay in sync) — then run the phases. For the spawn/model/permission,
worktree, and integration commands, defer to `waypoint-workqueue`'s
`references/playbook.md` and `references/backends.md`; this skill does not
duplicate them.

The lifecycle skills that do the actual per-phase engineering work (planning,
implementing, verifying, reviewing, shipping) are **not named** here: frame each
phase's task in the natural language that fires whatever skills the user has
configured, and if none fires, the role does that phase's work inline rather
than producing nothing (see `references/lifecycle.md`).

## Guardrails

- **Size the org to the phase, not the product.** A standing multi-role crew
  exhausts the host. Spawn the roles a phase needs, reap them when it ends, keep
  at most a small standing crew across iterations. Fan-out has no server-side
  limit.
- **Coupled work is sequenced, never raced.** Two roles touching a shared
  interface must agree a `contract:` cell first, and the lead assigns a task only
  when its `deps=` are all `done` (`references/coordination.md`). Racing coupled
  work is the failure mode the crew exists to avoid.
- **The lead owns all durable state.** Every keyed cell (`prd`, `architecture`,
  `contract:*`, `phase`, per-task contracts) is written only by the long-lived
  lead; roles append to the log. Worker-authored cells are pruned on reap.
- **Checkpoint, don't drift.** At PRD, architecture, and pre-ship, post the
  artifact and gate on the user's approval — the lead must not silently redefine
  the product's scope. Between checkpoints, run autonomously.
- **Be inquisitive about environmental choices.** Settle each role's model and an
  auto-approving permission mode before spawning — a guessed mode parks the
  session and a wrong model id dies on turn 1. Pass ids verbatim from `waypoint
  models` / `waypoint backends`, and ask the user when unsure.
- **Check the shipped product, not just green tests.** Before a phase or the
  product is called done, exercise the real running app, not only unit tests.
