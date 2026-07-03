# Coordination

How the org stays in sync on the blackboard and how the lead sequences coupled
work. This builds directly on `waypoint-comms` (the board and direct sends) and
reuses `waypoint-workqueue`'s cell shapes; read those first. Everything durable
is a **lead-written keyed cell** — roles only append to the log, because a
worker-authored cell is pruned when the worker is reaped.

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
  **unchanged and scoped to that channel**. This composes the work queue natively
  and confines the `<n>` counter to one batch, so a later build phase never
  overwrites an earlier one's cells.

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
  only when every id in its `task:<n>` `deps=` is `done`. That gate is what lets a
  flat crew execute a coupled dependency graph correctly.
- **Scope: `deps=` is intra-`job:`-channel.** Because `<n>` is channel-local, a
  `deps=` id can only reference tasks **in the same `job:` channel**. Two
  consequences, and they are the crux of coupled work:
  - **Coupled tasks share one channel.** A frontend slice and the backend API it
    depends on are one coupled batch and must live in the **same** `job:` channel
    so `deps=` (and the contract's dependent set) can be expressed. Never split
    tightly-coupled work across separate sub-phase channels.
  - **Cross-sub-phase ordering is the lifecycle's job.** Ordering *between*
    sub-phases (e.g. "scaffold before any build batch") is sequenced by the phase
    progression and the `phase` cell, not by `deps=`, which cannot reach across
    channels.

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

## Handoff

- **Durable artifact → board cell**, written by the lead. Anything a role must
  read later, or that must survive a reap, is a cell (or a keyless log post for
  history).
- **"Act now" → direct send.** To hand a specific idle role a task or to wake it
  after a contract change, `waypoint sessions send`. A send injects a turn; a
  board post interrupts no one — prefer the board when the message can wait.
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
- `jobs=` — every live `job:<phase-slug>` sub-phase channel. Without this a
  resumed lead has no pointer to the in-flight build batches (work-queue resume
  operates *within* a known channel and cannot announce that the channel exists),
  so it would have to guess from `board channels` and could silently drop a batch.
  The lead updates `jobs=` whenever it spins up or retires a sub-phase channel.

A lead restarting reads `phase`, reattaches to each channel in `jobs=`, and
resumes each with the work-queue resume procedure (done tasks skipped, `todo`
reassigned, orphaned `doing` handed back).

### Resume after the lead dies

The task-state plumbing is the work queue's — for each live `job:` channel,
follow `waypoint-workqueue`'s "Resume after the lead dies" (read the board, find
the old lead's workers by `--spawned-by`, hand orphaned `doing` tasks back to
`todo`, adopt still-running workers). What is new here is the **lifecycle** layer:
first read the `phase` cell to learn `current`, `approved`, and `jobs`, then run
that per-channel task recovery over every channel in `jobs=`.

## Human checkpoints under autonomy

Checkpoints at PRD / architecture / pre-ship must work even when the lead runs
unattended, so a blocking question is wrong — it would stall an unattended turn
forever. Instead:

- The owner **posts the artifact to the board** (`prd`, `architecture`, or a
  release summary) and gates on a **user board post / approval**, not a blocking
  prompt.
- **While waiting, park or reap the phase's roles** rather than holding a standing
  crew idle-spinning (per the cost-discipline guardrail).
- Record the granted approval in the `phase` cell's `approved=` meta so it
  survives resume and is never re-asked.
