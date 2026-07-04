# The product lifecycle (greenfield or brownfield)

Seven phases carry a product from an intent to a shipped, iterating thing. Each
phase names the role that owns it, the board artifact it produces (all cells are
lead-written — see `references/coordination.md`), the exit criterion that lets
the next phase start, and whether it carries a **human checkpoint**. The lead
runs the phases in order but loops back freely — QA findings and new backlog
items re-enter earlier phases.

A **human checkpoint** is the one place the lead pauses for the *user* (the
board and direct sends only reach other sessions). Surface it through the inbox:
post the artifact as an approval item, wait until it resolves, and read the
decision back into the phase's `approved=` cell — the CLI mechanics are in the
`waypoint` skill's `references/inbox.md`. Between checkpoints the crew runs
autonomously.

Small products compress this: several phases can be a single lead turn. Do not
run a heavyweight seven-phase process on a thin slice.

## Greenfield vs. brownfield entry

The same seven phases run whether the product starts from an empty repo
(**greenfield**) or the crew is evolving an **existing codebase** (**brownfield**
— adding a feature, a redesign, a migration-plus-features effort). Phases 1 and 2
change shape the most; phases 3, 4, 6, and 7 are identical, and phase 5 adds a
brownfield regression guard.

- **Greenfield** — phase 1 writes a product PRD and phase 2 chooses a stack and
  scaffolds a skeleton, as written below.
- **Brownfield** — phase 1 is **codebase onboarding plus change scoping** against
  what already exists, and phase 2 **adopts the existing architecture** rather
  than choosing one and **skips scaffolding**. Each phase below carries a
  *Brownfield* note where it differs. On an existing workspace the crew inherits
  the repo's stack, conventions, and verify command — it does not re-decide them —
  and the integration branch is cut from the repo's current default branch, not
  an empty tree. A resumed or mid-flight product is itself brownfield: this is
  also the shape of every phase-7 iteration.

Set the `phase` cell's `current=` to wherever the work actually enters — a
brownfield effort with a clear, already-agreed change can open directly at
scoping or even backlog decomposition rather than replaying discovery from zero.
What is skippable is discovery/scoping, **not** phase 2's branch-and-baseline
step: however early you enter, still cut the integration branch from the default
branch and confirm the green baseline before any build batch, since phase 5's
regression guard compares against it.

## How the engineering work actually gets done

This skill does **not** name the skills that plan, implement, verify, review, or
ship code. It assumes the user has installed and configured those lifecycle
skills — **in the role sessions too, not only the lead** — and frames each
phase's task in the natural language that fires them. Where a phase says *"frame
it as plan this / build and ship this / verify it / review this diff"*, that
wording is deliberate: it is the trigger surface.

Two things must hold, or a phase quietly produces nothing:

- **The skills must be present where the work runs.** A standing engineer without
  the build skill installed will not build. Confirm the role sessions have them,
  or expect the fallback.
- **Fallback:** if no configured skill fires, the owning role does the phase's
  work inline — a phase is defined by its artifact, not by a skill firing.

## Phase 1 — Discovery / PRD

- **Owner:** product manager (or the lead).
- **Do:** turn the user's intent into a PRD — problem, users, scope, explicit
  non-goals — plus acceptance criteria and an initial backlog. Frame it as *plan
  this product / write the PRD*.
- **Brownfield:** first **onboard to the existing codebase** — the tech lead
  surveys the repo's structure, stack, conventions, and test/verify command (fan
  this out to readers for a large codebase) and captures it in the `architecture`
  cell up front. The PRD then scopes the *change* against what exists — what to
  reuse, what to touch, what to leave alone — and its non-goals call out parts of
  the system that must not regress. Frame it as *understand this codebase / plan
  this change*.
- **Artifact:** `prd` and `backlog` cells on `org:<product>` (brownfield also
  seeds the `architecture` cell with the survey).
- **Checkpoint (human):** post the PRD (and, brownfield, the codebase survey) and
  gate on the user's approval before building. This is where scope is agreed; the
  lead must not silently expand it.
- **Exit:** user has signed off the PRD (recorded in `approved=`).

## Phase 2 — Architecture & scaffold

- **Owner:** tech lead / architect.
- **Do:** choose the stack, define the repo layout, and scaffold a working
  skeleton with an integration branch, a verify command, and CI green on an empty
  app. Frame scaffolding as *start a new <language> project / set up the
  toolchain*. Define the API `contract:` shapes for any coupled features.
- **Brownfield:** **skip scaffolding — the project already exists.** Instead, cut
  the integration branch from the repo's current default branch, confirm the
  existing verify command runs green *before* any change (the baseline), and
  design the change to **fit the existing architecture and conventions** rather
  than imposing a new one. Deviations from the established patterns are a
  checkpoint item, not a silent choice. Fill the `architecture` cell with how the
  change slots into the current design (extending the survey from phase 1), and
  define `contract:` shapes for any new or changed interfaces — flag a changed
  contract as touching existing callers.
- **Artifact:** `architecture` cell; the integration branch (greenfield: a
  scaffolded skeleton; brownfield: a branch off the existing tree with a green
  baseline); `contract:<name>` cells for coupled interfaces.
- **Checkpoint (human):** post the architecture (greenfield: + stack choice;
  brownfield: + how the change fits and any deviation from existing patterns) and
  gate on approval — a wrong foundation is expensive to unwind.
- **Exit:** the verify command is green on the integration branch (greenfield: a
  building skeleton; brownfield: the untouched baseline); architecture approved
  (recorded in `approved=`).

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

- **Owner:** the standing engineers, sequenced by the lead.
- **Do:** for each parallelizable batch, spin a `job:<phase-slug>` channel with the
  work-queue task/status/deps/contract cells — but **fill its worker slots with the
  standing engineers** (assign each a `task:<n>`, flip `status:<n>` to `doing
  assignee=<sid>`), reused across tasks and batches (each keeps its pinned
  worktree, rotating the branch inside it — `references/org-chart.md`). Assign a
  task only when its `deps=` are all `done`. Coupled pairs build against the agreed
  `contract:`; if a contract must change, renegotiate it
  (`references/coordination.md`) rather than letting the sides diverge. Ephemeral
  **overflow workers** cover a burst beyond standing headcount, reaped per batch by
  tag or tracked id (`references/org-chart.md`). Frame each task as *build and ship this
  feature / implement this*, and verify each with its own check before integrating.
  For a build too large for one lead to sequence, split it into per-team `job:`
  channels each run by a **team lead**, with the main lead sequencing across teams
  by cross-team `contract:` cells and integrating their results (the hierarchical
  org and its merge-up are in `references/org-chart.md` and
  `references/coordination.md`).
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
- **Brownfield:** also guard against **regression** — the change must not break
  existing behavior. Compare against the phase-2 green baseline, run the
  pre-existing suite (not just tests added for the change), and exercise adjacent
  untouched flows the change could have disturbed.
- **Hierarchical:** QA is two-level — each team lead runs intra-team QA on its own
  result, and the main lead still runs this org-level QA on the cross-team-integrated
  app after merge-up. The team checks do not replace exercising the whole product.
- **Artifact:** bug reports on the log; the lead triages them into new backlog
  tasks.
- **Checkpoint:** none — findings loop back to phase 3/4.
- **Exit:** no open blocking defects; the running product meets the PRD's
  acceptance criteria (brownfield: and shows no regression against the baseline).

## Phase 6 — Ship / release

- **Owner:** reviewer / release engineer (or the lead).
- **Do:** review the integrated diff, then run the release — open the PR / cut the
  release. Frame it as *review this / ship it*.
- **Checkpoint (human):** pre-ship — post the release summary and gate on the
  user's go-ahead before anything leaves the repo.
- **Exit:** the change is shipped (PR opened/merged or release cut) with the
  user's approval.

## Phase 7 — Iterate / maintain

- **Owner:** lead, with the standing crew.
- **Do:** fold new requests, QA findings, and follow-ups into the backlog and
  loop back to phase 3 (or phase 1 for a new product area). **Keep the standing
  crew between iterations** — its retained context is what makes the next iteration
  cheap; each iteration is itself brownfield work on the product it just built.
- **Checkpoint:** re-enters phase 1's checkpoint for any material new scope.
- **Exit:** open-ended — the product keeps evolving, or the user winds it down and
  the crew is reaped (with the staleness backstop, `references/org-chart.md`, for a
  product abandoned without an explicit wind-down).

## Resuming a lifecycle

The `phase` cell records the current phase, the approvals already granted, and
the live build channels, so a lead restarting after losing context picks up
where it left off without re-running discovery or re-asking for approvals — see
"Lifecycle-state resume" in `references/coordination.md`.
