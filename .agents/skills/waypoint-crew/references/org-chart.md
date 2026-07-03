# The org chart (role template)

Start from this organization instead of designing one per product, then make it
your own. It is a shallow hierarchy: one persistent lead, a set of role sessions
spawned to fit the current phase. The roles, counts, and backends below are a
launch point, not a rule — collapse them toward the lead for small work, expand
them where a product warrants it.

## Roles

- **Lead / engineering manager** — you, and the only role that is always on. It
  splits the product into phases and tasks, sequences coupled work, owns every
  keyed board cell, integrates each result, and runs the phase checkpoints with
  the user. Exactly one lead. Everything durable lives with it, because it is the
  one session guaranteed to outlive the others.
- **Product manager** — turns the user's product intent into a written PRD,
  acceptance criteria, and a backlog. Owns the *content* of the `prd` and
  `backlog` cells (the lead still writes them to the board). For a small product
  this role collapses into the lead.
- **Tech lead / architect** — turns the PRD into a stack choice, a repo layout, a
  decomposition into features with their dependency edges, and the API
  `contract:` shapes for coupled work. Owns the *content* of `architecture`. The
  highest-leverage role to give a strong model.
- **Frontend engineer(s)** — implement UI slices against the agreed contracts.
- **Backend engineer(s)** — implement services/APIs; usually produce the
  contract a frontend slice depends on, so they lead coupled pairs.
- **QA / test engineer** — writes and runs tests, and **exercises the integrated
  running app**, filing bugs to the board's log for the lead to triage back into
  the backlog. A distinct role because "green unit tests" and "the product works"
  are different claims.
- **Reviewer / release engineer** *(optional)* — an independent second opinion
  before merge and the owner of the ship/release step. Add it for high-blast
  changes; otherwise the lead reviews in-process.

## Persistent vs. per-phase

Only the lead is persistent. Spawn the rest to fit the phase and reap them when
it ends — a standing multi-role crew burns host resources idle (see the
`waypoint-subagents` reap guidance). Across a long-lived product, keep at most a
small standing crew (say one backend, one frontend, one QA) between iterations
rather than the whole org.

Which roles exist at all is a judgement call:

- **Small product / thin slice** — the lead *is* the PM, tech lead, and reviewer;
  spawn only an engineer or two and a QA session. Do not manufacture an org chart
  the work does not need.
- **Full product from zero** — stand up the PM and tech-lead roles for the
  discovery and architecture phases, then swap to engineers + QA for the build,
  then a reviewer for ship. Roles come and go by phase; the lead is the through
  line.
- **Existing codebase (brownfield)** — the tech lead earns its seat up front for
  the codebase survey; give it (or a fan-out of readers) a strong model to map an
  unfamiliar repo before scoping. The PM role is lighter — the change is often
  already agreed — so it usually collapses into the lead. Otherwise the build/QA
  staffing matches the greenfield case.

## Reporting lines

Flat under the lead — every role reports to the lead, not to each other. Roles
coordinate through the board and through the lead's sequencing, not by messaging
peers directly; a frontend session does not negotiate a contract with a backend
session on its own, it consumes the `contract:` cell the lead published. This
keeps all durable decisions on the one session that survives, and keeps coupled
work from racing (see `references/coordination.md`).

## Choosing each role's backend and model

Per-role backend/model choice is the same decision the work queue makes
per-worker — **defer the mechanics and the model-tier heuristic to
`waypoint-workqueue`'s `references/backends.md`** (discover with `waypoint
doctor` / `waypoint backends` / `waypoint models`; pass ids verbatim; prefer
large-context variants for real implementation work). The role-specific slant:

- **Tech lead / architect and reviewer** — the ambiguous, wide-blast-radius
  seats. Give them a frontier / deep-reasoning tier; a wrong call here is
  expensive downstream.
- **Frontend / backend engineers** — the balanced daily-driver tier for
  well-specified feature tasks; drop to a cheaper tier for mechanical slices.
- **QA** — balanced tier; it must reason about what the running product actually
  does, not just run a command.
- **An independent reviewer or a heterogeneous engineer pair** is the main reason
  to use durable sessions over in-process subagents — a different model or
  backend gives a genuine second opinion. If you need none of that for a role,
  the lead can do it in-process instead of spawning.

Place each engineer where its task's code lives (`--cwd`). Coupled roles branch
their own worktrees off a common integration base so their work converges cleanly
(per the coordination rules) — but each role still gets its **own** worktree;
never let two roles edit the same tree.
