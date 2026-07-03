# The org chart (role template)

Start from this organization instead of designing one per product, then make it
your own. It is a shallow hierarchy: one persistent lead and a small **standing
crew** of role sessions reused across the lifecycle. The roles, counts, and
backends below are a launch point, not a rule — collapse them toward the lead for
small work, expand them where a product warrants it.

## Roles

- **Lead / engineering manager** — you, and the one role active every phase (the
  standing crew persists too, but its members shift focus phase to phase; the lead
  never does). It
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
- **Transport is a trade** (not a clear win either way): a structured transport
  (`claude_cli` / `codex` / `opencode`) avoids the interactive pane but is
  `supports_resume=False`, so a Waypoint/backend restart forces a reimport;
  `claude_tty` / `tmux` cost a pane but are `supports_resume=True` and re-attach.
  Pick by which failure matters for a long-lived crew.
- **Context hygiene without teardown** — the board cells are the memory of record
  (`architecture`, `prd`, `contract:*`, `phase`); a role's session context is a
  cache over them. When a role nears its window it compacts — `codex`/`opencode`
  take an agent-invoked `/compact`; `claude_code`/`claude_tty` auto-compact in
  place (Claude Code does this mid-turn, not on command) — then **re-grounds by
  re-reading the board**. Because the load-bearing decisions live on durable cells,
  a lossy compaction doesn't sever continuity.
- **Reap at wind-down**, or when a role is genuinely never needed again. There is
  no idle-session GC, so the backstop for an abandoned crew is that the next actor
  to touch it (a successor lead, the user, or a maintenance sweep) reaps it if it
  is stale. This is a deliberate exception to `waypoint-subagents`' reap-when-done
  posture — crew roles are long-lived like the lead — and a successor lead
  adopting a crew it did not spawn is sanctioned (as in workqueue's
  resume-after-lead-death).
- **Ephemeral overflow workers are the exception to all of the above.** A burst
  beyond standing headcount can spawn transient workers for one batch; reap those
  **by their tracked session ids** (or a distinct `subagent:overflow-*` title) as
  the batch finishes — never with the blanket `reap --spawned-by <lead>` sweep,
  which would also reap the standing crew. Track the standing sids so they are
  excluded. The blanket sweep is reserved for wind-down.

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

A session is **pinned to its launch `cwd` for life** — there is no command to
repoint a running session at a different directory. So a standing role keeps **one
worktree, its `cwd`, across all its tasks**; you rotate the **git branch inside
that worktree**, never the worktree. Per task the role (in its own `cwd`) runs
`git switch -c wq/<job>-t<n> <integration-tip>`, does the work, and commits; the
lead ff-merges that branch into the integration branch from its own checkout, then
the role switches to the next fresh task branch off the updated tip — same
worktree throughout. Because worktrees share the repo's object store, the
integration ref is always visible for branching; because each role has its own
worktree and works its own task branches (never the integration branch directly,
which lives in the lead's tree), no two checkouts collide. Workqueue's fixed
"work in your `cwd`" therefore **still holds** — the role never leaves its `cwd`.
(A role is also pinned to its **repo**: a task in a different repository needs a
different session, not a branch switch.)
