# The product lifecycle (from zero)

Seven phases carry a product from an intent to a shipped, iterating thing. Each
phase names the role that owns it, the board artifact it produces (all cells are
lead-written — see `references/coordination.md`), the exit criterion that lets
the next phase start, and whether it carries a **human checkpoint**. The lead
runs the phases in order but loops back freely — QA findings and new backlog
items re-enter earlier phases.

Small products compress this: several phases can be a single lead turn. Do not
run a heavyweight seven-phase process on a thin slice.

## How the engineering work actually gets done

This skill does **not** name the skills that plan, implement, verify, review, or
ship code. It assumes the user has installed and configured those lifecycle
skills — **in the role sessions too, not only the lead** — and frames each
phase's task in the natural language that fires them. Where a phase says *"frame
it as plan this / build and ship this / verify it / review this diff"*, that
wording is deliberate: it is the trigger surface.

Two things must hold, or a phase quietly produces nothing:

- **The skills must be present where the work runs.** A spawned engineer without
  the build skill installed will not build. Confirm the role sessions have them,
  or expect the fallback.
- **Fallback: if no skill fires, the role does the phase's work inline.** A phase
  is defined by its artifact and exit criterion, not by a skill firing. When no
  configured skill picks up the task, the owning role performs the work directly
  rather than no-op'ing.

## Phase 1 — Discovery / PRD

- **Owner:** product manager (or the lead).
- **Do:** turn the user's intent into a PRD — problem, users, scope, explicit
  non-goals — plus acceptance criteria and an initial backlog. Frame it as *plan
  this product / write the PRD*.
- **Artifact:** `prd` and `backlog` cells on `org:<product>`.
- **Checkpoint (human):** post the PRD and gate on the user's approval before
  building. This is where scope is agreed; the lead must not silently expand it.
- **Exit:** user has signed off the PRD (recorded in `approved=`).

## Phase 2 — Architecture & scaffold

- **Owner:** tech lead / architect.
- **Do:** choose the stack, define the repo layout, and scaffold a working
  skeleton with an integration branch, a verify command, and CI green on an empty
  app. Frame scaffolding as *start a new <language> project / set up the
  toolchain*. Define the API `contract:` shapes for any coupled features.
- **Artifact:** `architecture` cell; the scaffolded repo (ground truth) on the
  integration branch; `contract:<name>` cells for coupled interfaces.
- **Checkpoint (human):** post the architecture + stack choice and gate on
  approval — a wrong foundation is expensive to unwind.
- **Exit:** skeleton builds and the verify command is green; architecture
  approved (recorded in `approved=`).

## Phase 3 — Backlog decomposition & sequencing

- **Owner:** lead (with the tech lead).
- **Do:** break the backlog into concrete tasks, assign each a role, and record
  its dependency edges. Coupled tasks (a frontend slice against a backend API)
  are grouped so they share one build channel; independent tasks are marked
  parallelizable.
- **Artifact:** the task set with `deps=` metadata, laid out across the sub-phase
  channels the build will use (`references/coordination.md`).
- **Checkpoint:** none — internal planning.
- **Exit:** every backlog item is a task with a role, a check, and its deps.

## Phase 4 — Iterative build

- **Owner:** engineers, sequenced by the lead.
- **Do:** for each parallelizable batch, spin a `job:<phase-slug>` channel and run
  it as a work-queue crew — this is where `waypoint-workqueue` is composed
  directly. Assign a task only when its `deps=` are all `done`. Coupled pairs
  build against the agreed `contract:`; if a contract must change, renegotiate it
  (`references/coordination.md`) rather than letting the sides diverge. Frame each
  task as *build and ship this feature / implement this*, and verify each with
  its own check before integrating.
- **Artifact:** merged commits on the integration branch; `status:<n>` cells
  flipping to `done`.
- **Checkpoint:** none per task; the lead integrates one task at a time and keeps
  the branch green.
- **Exit:** every planned task is `done` or explicitly deferred back to the
  backlog.

## Phase 5 — QA & integration

- **Owner:** QA / test engineer.
- **Do:** run the full suite on the integration branch, then **exercise the real
  running app** end-to-end — the paths mocked tests miss. File each defect to the
  board's log. Frame it as *verify this / does the app actually work*.
- **Artifact:** bug reports on the log; the lead triages them into new backlog
  tasks.
- **Checkpoint:** none — findings loop back to phase 3/4.
- **Exit:** no open blocking defects; the running product meets the PRD's
  acceptance criteria.

## Phase 6 — Ship / release

- **Owner:** reviewer / release engineer (or the lead).
- **Do:** review the integrated diff, then run the release — open the PR / cut the
  release. Frame it as *review this / ship it*.
- **Checkpoint (human):** pre-ship — post the release summary and gate on the
  user's go-ahead before anything leaves the repo.
- **Exit:** the change is shipped (PR opened/merged or release cut) with the
  user's approval.

## Phase 7 — Iterate / maintain

- **Owner:** lead, with a small standing crew.
- **Do:** fold new requests, QA findings, and follow-ups into the backlog and
  loop back to phase 3 (or phase 1 for a new product area). Reap the roles a
  finished iteration no longer needs; keep only a small standing crew between
  iterations.
- **Checkpoint:** re-enters phase 1's checkpoint for any material new scope.
- **Exit:** open-ended — the product keeps evolving, or the user winds the crew
  down (reap all roles).

## Resuming a lifecycle

The `phase` cell records the current phase, the approvals already granted, and
the live build channels, so a lead restarting after losing context picks up
where it left off without re-running discovery or re-asking for approvals — see
"Lifecycle-state resume" in `references/coordination.md`.
