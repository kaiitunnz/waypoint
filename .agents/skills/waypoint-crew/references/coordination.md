# Coordination

How the org stays in sync on the blackboard and how the lead sequences coupled
work. This builds directly on `waypoint-comms` (the board and direct sends) and
reuses `waypoint-workqueue`'s cell shapes; read those first. Everything durable
is a keyed cell written by its **owning lead** — roles only append to the log,
because a worker-authored cell is pruned when its author is reaped. In the flat
org the owning lead is always the one lead; under a hierarchy
(`references/org-chart.md`) ownership is **scoped per tier** — the main lead owns
org-level cells and cross-team `contract:` cells, a team lead owns its team
channel's cells — but the rule is identical within each tier.

## Channel model (two tiers)

A single flat channel would collide a `task:<n>` counter across build batches and
re-iterations, and would fight the work-queue template (which keys off its own
`job:` channel). So split into two tiers:

- **Org channel `org:<product>`** — durable, lead-owned lifecycle artifacts as
  keyed cells: `prd`, `architecture`, `phase` (resume state, below),
  `contract:<name>` (the agreed API/interface shapes), and `backlog`. Its
  append-log carries standup/status notes and QA bug reports.
- **Per-sub-phase channel `job:<phase-slug>`** — each parallelizable build batch
  spins its own work-queue-style channel (`job:build-auth`, `job:build-ui-v2`)
  and reuses `waypoint-workqueue`'s `plan` / `task:<n>` / `status:<n>` cells
  **unchanged and scoped to that channel**. This confines the `<n>` counter to one
  batch, so a later build phase never overwrites an earlier one's cells. The change
  from a plain work queue is only *who fills the worker slots*: the **standing crew
  sessions** are assigned to the task cells (ephemeral overflow workers only beyond
  standing headcount) — the channel and its cells are identical.

Under a **hierarchical org** (`references/org-chart.md`) a third owner tier
appears: each **team lead** owns a team channel — an ordinary `job:<team-slug>`
channel (`job:team-payments`) with the same `plan` / `task:<n>` / `status:<n>`
cells and channel-local `<n>`; the only difference is the owner. Teams sit behind
a stable cross-team `contract:` (a loosely-coupled seam — never split a
tightly-coupled slice across two team channels).

Keep every channel name and cell key **single-segment and slash-free** — the
blackboard API path breaks on slashes. Use `contract:orders-api`, never
`contract:/orders`.

## Dependency-aware sequencing (the coupled-work mechanism)

Work queue assumes independent tasks; a product's tasks are coupled. The lead
turns that coupling into an explicit, enforced order:

- **Where deps live.** A task's dependencies are static DAG edges, so they belong
  on the **immutable `task:<n>` cell**, set in its `--meta` at creation (not on
  `status:<n>`, the only cell the work queue mutates). Encode multiple deps as one
  comma-separated value — `--meta deps=1,3` — **not** repeated `--meta deps=1
  --meta deps=3` (repeated same-key metadata is not documented to accumulate;
  treat it as last-wins).
- **The gate.** There is no server-side enforcement — it is lead discipline. Each
  lead turn: read all `status:<n>` cells in the channel and assign a `todo` task
  only when every id in its `task:<n>` `deps=` is `done`. `waypoint board ready
  <channel>` computes this for you — a read-only view that returns the `todo` tasks
  whose deps are all `done` — but it enforces nothing; the assignment discipline is
  still the lead's. That gate is what lets a flat crew execute a coupled dependency
  graph correctly.
- **Scope: `deps=` is intra-`job:`-channel.** Because `<n>` is channel-local, a
  `deps=` id can only reference tasks **in the same `job:` channel**. Two
  consequences, and they are the crux of coupled work:
  - **Coupled tasks share one channel.** A frontend slice and the backend API it
    depends on are one coupled batch and must live in the **same** `job:` channel
    so `deps=` (and the contract's dependent set) can be expressed. Never split
    tightly-coupled work across separate sub-phase channels.
  - **Cross-sub-phase ordering is the lifecycle's job.** Ordering *between*
    sub-phases (e.g. "scaffold — or, brownfield, establish the green baseline —
    before any build batch") is sequenced by the phase progression and the
    `phase` cell, not by `deps=`, which cannot reach across channels.

## Contract-first coupling (and renegotiation)

For coupled sides — classically frontend against a backend API — the lead agrees
the interface **before** either side starts, so they build in parallel without
racing:

- **Establish.** The lead publishes the interface as a `contract:<name>` cell on
  `org:<product>` (with `--meta version=1`). Both sides read it and build against
  it.
- **Renegotiate — spell this out, it is where the illusion of decoupling
  breaks.** The `contract:` cell is upsert-replaces-text and the board is
  pull-only, so a silent re-post strands every side that already consumed the old
  value. When a contract must change mid-flight:
  1. bump `--meta version=` on the cell;
  2. treat the change as **invalidating every dependent `doing` task** — hand each
     back to `todo`;
  3. **direct-send-to-wake** the affected role sessions so they re-read.
- **Cross-channel note.** The `contract:` cell lives on `org:<product>` but its
  dependent tasks live in the coupled `job:` channel, so renegotiation is a
  **cross-channel action**: repost on the org channel, then scan the affected
  `job:` channel(s) to invalidate and re-notify. (One more reason coupled tasks
  share a single `job:` channel — the dependent set is then a single-channel
  scan.)
- **Hierarchical note — renegotiation is delegated.** When ownership is split
  across tiers, the main lead owns a cross-team `contract:` but **not** the team
  channels whose tasks depend on it, so it cannot invalidate those `doing` tasks
  itself. It bumps `version=`, then **direct-sends each affected team lead** to run
  steps 2–3 inside its own channel (invalidate its dependent `doing` tasks,
  re-notify its members). The main lead knows which teams consume the contract —
  that is why it is org-level. The dependent set now spans team channels (not the
  single-channel scan of intra-channel coupling); that is an accepted cost, bounded
  because a team seam is loosely-coupled by construction, so cross-team
  renegotiation is rare. Intra-team `contract:` cells live on the team channel and
  renegotiate single-channel exactly as above.

## Merge-up (two-level integration, hierarchical org)

Flat integration is centralized on the one lead. Under a hierarchy it splits in
two, mirroring the ownership tiers:

- **Intra-team.** A team lead integrates its own members' branches into a single
  team result using `waypoint-workqueue`'s linear-integration procedure (rebase in
  the worker's worktree, ff-merge from the team base), runs the team's dependency
  gate, and does intra-team QA.
- **Report up as a commit ref, not a tree.** When the team's batch is green the
  team lead hands the main lead **one reviewed commit ref** (plus a completion note
  on `org:<product>`'s log) — never its members' individual branches, and never a
  live working tree.
- **Cross-team.** The main lead integrates the team refs in cross-team dependency
  order, **in its own pinned integration worktree** — never inside a team's live
  tree, which sidesteps the "a live worker holds its branch checked out" hazard.
  The first team ff-merges; because the org tip then advances, each later team's
  ref is **rebased onto the new tip before merging** (the same rebase-then-integrate
  step the playbook teaches, one tier up). Quiesce a team — batch done, members
  idle — before integrating its ref, so it does not move underfoot.

## Handoff

- **Durable artifact → board cell**, written by the lead. Anything a role must
  read later, or that must survive a reap, is a cell (or a keyless log post for
  history).
- **File deliverable the user must open → keep it in a cwd or upload it.** Text
  artifacts stay board cells as above, but a *file* the user should open (a
  generated report, design doc, or diagram) that lives outside any session's
  working directory is invisible in the UI — write it into the repo/cwd, or
  `sessions upload --pin` it (the `waypoint` skill's `references/artifacts.md`;
  `--pin` keeps it past the orphan sweep).
- **"Act now" → direct send.** To hand a specific idle role a task or wake it
  after a contract change, `waypoint sessions send` (send-vs-board trade:
  `waypoint-comms`).
- **Ground truth is the repo and the running app**, not the board. The board is
  the narrative and the coordination state; confirm what actually landed from git
  and by exercising the app.

## Lifecycle-state resume

The org outlives a lead's context far more often than a one-shot job, and
work-queue resume recovers only *task* state within a known channel. Add a
lead-owned **`phase` cell** on `org:<product>` carrying:

```bash
waypoint board post org:<product> "lifecycle state" --key phase \
  --meta current=build \
  --meta approved=prd,architecture \
  --meta jobs=build-auth,build-ui-v2
```

- `current=` — the active phase, so a fresh lead does not re-run discovery.
- `approved=` — the checkpoints the user already signed off, so a fresh lead does
  not re-prompt for an approval already given.
- `jobs=` — every live **self-run** `job:<phase-slug>` sub-phase channel (those the
  lead drives itself). Without this a resumed lead has no pointer to the in-flight
  build batches (work-queue resume operates *within* a known channel and cannot
  announce that the channel exists), so it would have to guess from `board
  channels` and could silently drop a batch. The lead updates `jobs=` whenever it
  spins up or retires a self-run sub-phase channel.
- `teams=` *(hierarchical org only)* — the **delegated** team channels as
  `channel:team-lead-sid` pairs (`team-payments:sess-abc`). These are recovered
  *via their team lead*, not by the main lead running task recovery on them
  (below), so they are tracked apart from the self-run `jobs=`. The main lead
  rewrites `teams=` whenever a team lead is spawned, respawned (new sid), or a team
  is absorbed — move the entry to `jobs=` if the main lead keeps running the
  channel, or drop it on lift-to-org.

Update the cell with `board set-meta ... --key phase --merge` (it preserves the
text). `--merge` patches only the keys you pass and leaves the rest intact, so an
update that passes only `jobs=` keeps `current=`, `approved=`, and (under a
hierarchy) `teams=` untouched; drop a stale key with `--unset <key>`. Without
`--merge`, `--meta` replaces the cell's metadata wholesale — you must then
re-supply every meta or silently lose the omitted ones.

A lead restarting reads `phase`, then for each **self-run** channel in `jobs=`
runs the work-queue resume procedure directly (done tasks skipped, `todo`
reassigned, orphaned `doing` handed back). For each **delegated** channel in
`teams=` it does **not** run that recovery itself — it re-establishes the
channel's **team lead** (adopt if alive, respawn or absorb if dead — below) and
lets that owner recover its own channel; running task recovery on a delegated
channel would race its team lead on the same `status:<n>` cells.

### Resume after the lead dies

The task-state plumbing is the work queue's — for each live `job:` channel,
follow `waypoint-workqueue`'s "Resume after the lead dies" (read the board, find
the old lead's workers by `--spawned-by`, hand orphaned `doing` tasks back to
`todo`). Here the still-running workers are the **standing crew**: a successor lead
**adopts** them (this is the sanctioned exception to "don't touch sessions you
didn't create") and resumes steering them rather than reaping — reaping would throw
away exactly the context persistence is protecting. What is new beyond the work
queue is the **lifecycle** layer: first read the `phase` cell to learn `current`,
`approved`, `jobs`, and (hierarchical) `teams`, then run that per-channel task
recovery over every **self-run** channel in `jobs=`. Delegated `teams=` channels
are recovered through their team leads, not directly (below). If the product is
genuinely abandoned (no successor, crew idle past a staleness threshold), the crew
is reaped as the backstop — tier-ordered under a hierarchy
(`references/org-chart.md`).

### Resume after a team lead dies

A team lead **authors** its channel's cells, so cell durability is tied to that
session **not being deleted** — not merely to living on the board. Team state
therefore survives a *dead* team lead for the same reason it survives a dead main
lead: a dead-but-not-deleted session's cells are not pruned. The rule is
explicit — **a team lead that has died or must be replaced is adopted, never
deleted, until its cells are read or migrated.**

- **Recovery (team lead dead, main lead alive).** The main lead (or a successor
  main lead reading `teams=`) either **respawns a team lead that adopts** the team
  channel and its members, or **absorbs the team directly**. If it absorbs, the
  team's cells are **migrated to the new owner** (a successor team lead re-authors
  them, or the main lead lifts them to org level) **before** the old team lead is
  reaped — reaping first prunes them. Orphaned `doing` tasks are then handed back
  to `todo` by the standard recovery, run by whoever now owns the channel.
- **Any owner change rewrites the `phase` metas, symmetrically** — else a *later*
  successor is stranded on a stale entry (a `teams=` channel with no team lead
  behind it): respawn → rewrite `teams=` with the new sid; absorb-and-keep-running
  → move the entry `teams=` → `jobs=`; absorb-and-lift-to-org → drop it from
  `teams=`. Use `set-meta --merge` to patch just `teams=` (and `--unset teams` to
  drop it) without disturbing the other metas.
- **Double death.** If a team lead is *also* dead, its members were `--spawned-by`
  the dead team-lead sid (recorded in `teams=`), not by the old main lead, so the
  successor walks the spawn tree **tier by tier** — recover the team via the path
  above, then adopt its members through the respawned/absorbed owner.

## Human checkpoints under autonomy

Checkpoints at PRD / architecture / pre-ship must work even when the lead runs
unattended, so a blocking question is wrong — it would stall an unattended turn
forever. Instead:

- The owner **posts the artifact to the board** (`prd`, `architecture`, or a
  release summary) and gates on an explicit **approval signal** from the user,
  not a blocking prompt. The signal can be a board post the user (or the UI)
  makes on `org:<product>`, or a direct message to the lead session — either way
  the lead reads it at a turn boundary and records it into `approved=`.
- **While waiting, park the roles** — leave them idle **and alive** (a `sessions
  send` resumes them the moment approval lands); do not reap between checkpoints,
  which would only force a later reimport (see the standing-crew rule in
  `references/org-chart.md`).
- Record the granted approval in the `phase` cell's `approved=` meta so it
  survives resume and is never re-asked.
