# The org chart (role template)

Start from this organization instead of designing one per product, then make it
your own. It is a shallow hierarchy: one persistent lead and a small **standing
crew** of role sessions reused across the lifecycle. The roles, counts, and
backends below are a launch point, not a rule — collapse them toward the lead for
small work, expand them where a product warrants it.

## Roles

- **Lead / engineering manager** — you, and the one role active every phase. It
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

## A standing crew

**Reuse role sessions across phases; don't churn them.** The **code-touching
roles** — tech lead, engineers, QA — are the standing crew: they accumulate the
codebase and architecture context that makes them effective, so spawning them once
and keeping them beats tearing down and re-onboarding each phase. A parked role
(idle **and alive** — neither terminated nor deleted) picks up its next task via a
`sessions send`, preserving that context; reaping it and later reimporting the
thread spins a *new* session, replays history, and loses live state. The **PM and
reviewer stay on-demand or collapse into the lead** — their durable output is a
board cell (`prd`, a review verdict), so persistence buys them little beyond an
idle session.

Manage the cost by **size, not churn**:

- **Bound the crew** — roughly one tech lead, one backend, one frontend, one QA;
  scale by the work, not by the phase. This is the primary lever, because idle is
  not free: a `claude_tty` role holds a live tmux pane, a structured role holds a
  headless server process.
- **Context hygiene without teardown** — the board cells are the memory of record
  (`architecture`, `prd`, `contract:*`, `phase`); a role's session context is a
  cache over them. When a role nears its window it compacts — `codex`/`opencode`
  take an agent-invoked `/compact`; `claude_code`/`claude_tty` auto-compact in
  place (mid-turn, not on command) — then **re-grounds by re-reading the board**,
  so a lossy compaction doesn't sever continuity.
- **Reap at wind-down**, or when a role is genuinely never needed again. There is
  no idle-session GC, so the backstop for an abandoned crew is that the next actor
  to touch it (a successor lead, the user, or a maintenance sweep) reaps it if it
  is stale. Find stale sessions with `sessions list --idle-for <dur>` (e.g.
  `--idle-for 2h`), and reconstruct a crew you did not spawn with
  `sessions tree <lead-sid>` or `sessions list --spawned-by <lead-sid> --recursive`
  (which walks the whole spawn subtree, not just direct children). This is a
  deliberate exception to `waypoint-subagents`' reap-when-done posture — crew roles
  are long-lived like the lead — and a successor lead adopting a crew it did not
  spawn is sanctioned (as in workqueue's resume-after-lead-death).
- **Ephemeral overflow workers are the exception to all of the above.** A burst
  beyond standing headcount can spawn transient workers for one batch. Tag them at
  launch (`sessions start ... --tag overflow`) and reap the batch with
  `sessions reap --tag overflow` — this spares the standing crew without tracking
  sids. Equivalently, tag standing roles (`--tag role=backend`) and spare them from
  a `--spawned-by <lead>` sweep with `reap --spawned-by <lead> --exclude <sid> ...`.
  Never use the blanket `reap --spawned-by <lead>` sweep for overflow, which would
  also reap the standing crew. The blanket sweep is reserved for wind-down.
  Tags survive for the life of a session but not across a relaunch/fork — re-tag a
  respawned role with `sessions tag <sid> --set role=...`.
- **Under a hierarchy, wind-down is tier-ordered.** Members are `--spawned-by`
  their *team lead*, not the main lead, so a main-lead `reap --spawned-by <lead>`
  sweep would orphan the members while reaping the team leads. Wind down
  bottom-up: each team lead drains its members, the main lead migrates or retires
  each team's cells, then the team leads are reaped. A team lead being replaced is
  **adopted, never deleted, until its cells are read or migrated** — deleting it
  prunes them (the resume rules are in `references/coordination.md`).
  Exclusion-tracking is now two-level: the main lead tracks team-lead sids, each
  team lead its own members'.

Which roles exist at all is a judgement call:

- **Small product / thin slice** — the lead *is* the PM, tech lead, and reviewer;
  keep only an engineer or two and a QA session. Do not manufacture an org chart
  the work does not need.
- **Full product from zero** — stand up each role as its phase first needs it (PM
  and tech lead for discovery/architecture, engineers and QA for the build, a
  reviewer for ship), but once present a role **persists and is reused**; the
  *active* role shifts by phase, the sessions are not torn down.
- **Existing codebase (brownfield)** — the tech lead earns its seat up front for
  the codebase survey (give it, or a fan-out of readers, a strong model to map an
  unfamiliar repo) and **stays** for later architecture questions — its retained
  map of the repo is exactly the continuity win. The PM role is lighter — the
  change is often already agreed — so it usually collapses into the lead.

## Reporting lines

Flat under the lead — every role reports to the lead, not to each other. Roles
coordinate through the board and through the lead's sequencing, not by messaging
peers directly; a frontend session does not negotiate a contract with a backend
session on its own, it consumes the `contract:` cell the lead published. This
keeps all durable decisions on the one session that survives, and keeps coupled
work from racing (see `references/coordination.md`).

For a product too large for one lead to hold, this generalizes to **flat within
each tier** — see "Scaling to sub-teams" below.

## Scaling to sub-teams (hierarchical org)

The flat org above is the default and fits almost every product. Reach for a
hierarchy **only when a single lead can no longer hold the whole org** — too many
simultaneous `job:` channels to sequence, the lead compacting constantly, an org
chart too wide for one session to track. It buys headroom at the cost of an extra
coordination tier and a new failure mode (a team lead can die), so it is not a
starting point.

The shape is the same lead↔role pattern **one level deep**: a **team lead** is to
its team what the main lead is to the org. Cap it there — deeper nesting
multiplies the resume story without buying much.

- **Team lead** — an org-structural position, distinct from the **tech lead /
  architect** *role* above (a skillset). A team lead owns a team and its channel,
  and is usually a senior engineer session; the tech-lead role may itself be one.
- **Reporting stays flat within each tier.** Members report to their team lead,
  team leads to the main lead. The no-peer-messaging rule holds across teams: a
  member of one team never negotiates with another team's member — cross-team
  coupling goes through the main lead's org-level `contract:` cells. Within a team,
  the team lead is the cell-owning "lead" its members consume from.

**Cut teams along loosely-coupled seams, never through a tightly-coupled slice.**
A team boundary is a channel boundary, so it inherits the rule that coupled tasks
share one channel (`references/coordination.md`): teams are valid only where the
seam between them is a **stable, published cross-team `contract:`** — a
bounded-context / service boundary (a payments-service team, a search-service
team), each owning its subsystem behind an agreed API. A frontend/backend pair
co-building **one** tightly-coupled feature stays **inside one team channel**;
splitting it into a "frontend team" and a "backend team" reintroduces the
cross-channel racing the flat model forbids. So "backend lead / frontend lead" is
a valid split only when the two sides meet at a stable contract, not when they are
jointly building one coupled slice.

**Ownership is scoped per tier** — the durable-state, integration, contract, and
resume mechanics live in `references/coordination.md`:

- the **main lead** owns org-level durable state (`prd`, `architecture`, `phase`,
  `backlog`) and **all cross-team `contract:` cells**, and integrates the teams'
  results;
- a **team lead** owns its team channel's cells (`plan`, `task:<n>`, `status:<n>`,
  intra-team `contract:`) and integrates its own members' work into a single
  result it reports up.

Give a team lead a strong model tier, like the main lead and tech lead — it runs a
sub-org's sequencing and integration, the same wide-blast-radius work.

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

A session is **pinned to its launch `cwd` for life**, so a standing role keeps
**one worktree across all its tasks** and rotates the git branch inside it
(`git switch -c wq/<job>-t<n> <integration-tip>`) — the reuse mechanic
`waypoint-workqueue`'s `references/playbook.md` spells out under "Reusing a worker
across tasks". Crew-specific: each role gets its **own** worktree (never two roles
in one tree), and is pinned to its **repo** too — a task in another repository
needs a different session.
