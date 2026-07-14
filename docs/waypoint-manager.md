# Waypoint Manager

The Waypoint Manager is a single long-running Waypoint session that owns one
project's backlog. It drains a priority-ordered ticket board for its lifetime: for
each ticket it assigns a scale and footprint, writes a PRD or RFC through an
ephemeral writer when the work is substantial, delegates the ticket to an ephemeral
tech-lead that builds in the manager's own working tree, monitors the build over a
typed board protocol, escalates blockers and every merge to the human through the
inbox, and integrates the merged work as the sole integrator of trunk.

The tech-lead runs in the manager's tree rather than a throwaway sibling worktree
because that tree carries the project's real environment — its virtualenv, secrets,
and deployment config — that a fresh worktree lacks, so it is where a build can
actually compile, test, and deploy. The tree is therefore a single serial resource:
one ticket builds at a time.

The agent-facing procedure lives in the `waypoint-manager` skill
(`.agents/skills/waypoint-manager/`): `SKILL.md` and the per-step prompt templates
under `templates/`. This document is the design reference behind that procedure —
the model the skill executes, the state machine and invariants the backend
enforces, and the full configuration surface.

## Scope

One manager owns one project's backlog over its lifetime, intaking tickets
continuously and running each through a specify → delegate → review → merge
lifecycle. It pulls the human in for two decisions only: the substantial-spec
approval gate, and the per-PR review-until-merge loop. The human is the sole merge
authority for every PR.

A crew-scale ticket is delegated to a tech-lead that may itself run a crew; the
manager is not that crew. The related orchestration surfaces divide by the shape of
the work:

- **Manager** — an unattended backlog owner over a project's lifetime.
- **Crew** (`waypoint-crew`) — one product build with a human lead sequencing
  coupled phases.
- **Work queue** (`waypoint-workqueue`) — a flat batch of independent tasks a lead
  merges.
- **Delegate-and-review** (`waypoint-subagents`) — one coupled chunk done by one
  child and reviewed.

## The human workflow

The Waypoint web app is the human's primary surface: the **board** page for filing
tickets and reading progress, and the **inbox** for the manager's approval requests
and the blockers it escalates. Manager lifecycle actions are done by messaging the
manager session through the `/waypoint-manager` (or `/waypoint`) skill. Each step also
has a direct `waypoint` CLI equivalent. End to end, the human:

1. **Sets up** — messages the manager session `/waypoint-manager init`; it loads the
   manifest, registers its wake, verifies its roles, and records the owner session
   (also `/waypoint manager init`, or directly `waypoint manager init --manifest
   <path>`; manifest fields are detailed under Configuration).
2. **Files a ticket** — posts the request to the intake channel from the board page
   (or `waypoint board post <tickets_channel> "<request>"`); the manager registers and
   triages it on its next wake.
3. **Approves the spec**, for substantial tickets only — answers the manager's spec
   approval item in the inbox: approve, request changes, or reject. Approval releases
   the ticket to build; trivial tickets skip this gate.
4. **Reviews and merges the PR** — answers the PR approval item in the inbox (it
   carries a summary and CI status). The human is the sole merge authority: merge,
   request changes (which loops the lead back), or abort. In `local` integration mode
   the same review gate applies without a PR.
5. **Follows progress** — reads the manager's drain and outcome summaries on the org
   channel from the board page.
6. **Retires or resets** — messages the manager session `/waypoint-manager deinit`,
   which reaps the spawned subtree and clears the backlog, config, and lease (also
   `/waypoint manager deinit`, or directly `waypoint manager deinit`). Deleting the
   manager's own session cascades the state cleanup.

## Architecture

The manager is a skill over the `waypoint` CLI plus two runtime primitives. Board,
inbox, presets, and sessions/subagents are used as they ship; the manager itself
opens no worktree, though a tech-lead that fans work out may give its own workers
sub-worktrees.

### The event wake

`waypoint sessions wake-on-board` registers a subscription; the runtime starts a
turn on the subscribed session whenever a matching board channel or the inbox
changes. This is the same input path the scheduler uses, so it is backend-agnostic:
the manager and its leads behave identically as Claude Code, Codex, or OpenCode
sessions.

A wake carries no payload — it means "a channel or the inbox you watch changed;
re-read and reconcile." A burst of posts may coalesce into one wake, and two posts
may double-fire; both are absorbed because the payload is always re-read and the
drain is idempotent.

The runtime excludes the mutating author from its own wake on both the board and
inbox axes, so the manager's own writes to channels it subscribes to do not wake it.
This holds as long as every board and inbox write is authored as the manager: board
posts default `--author-session-id` from `$WAYPOINT_SESSION_ID`, and inbox answers
default `--actor-session-id` from it. A human answer carries no session id, so it
does wake the manager — that is the intended signal — and reading answers via
`inbox get` is a non-mutating GET that triggers no broadcast.

Delivery is state-aware. A wake into `idle` or finished-turn `waiting_input` starts
the turn immediately. A wake arriving during `running`, `starting`, `interrupted`,
or approval-pending `waiting_input` is marked pending and fires on the next
transition into a deliverable state, so it never interrupts an active turn or
injects while an approval is pending. A wake into `exited` or `error` is dropped — a
stopped session is not resurrected by a board post; only a liveness reconcile or an
explicit resume recovers it.

`waypoint board wait` blocks until a watched channel changes and is an interactive
convenience for a human or a one-off script. It is not the manager's loop driver;
the manager is driven by its registered subscription and its own drain.

### The state machine

The `waypoint manager` command group holds one durable record per ticket, with
every transition and scheduler invariant validated server-side. Because procedure
state lives outside the context window, a drifting or compacted context cannot enact
an illegal step — the backend rejects it with a `409`.

```
waypoint manager init --manifest <path> [--owner <sid>]  # persist machine-relevant config; record the owner session
waypoint manager deinit [--yes]                          # clear all tickets, config, and the lease
waypoint manager render <template> [--manifest <path>] [--ticket <id>] [--set k=v]... [--allow-unresolved]
waypoint manager state [--json]                          # whole ticket set + slots + lease
waypoint manager next [--tried <id>]... [--json]         # re-anchor
waypoint manager ticket add <title> [--id] [--priority p2] [--kind] [--scale] [--footprint <glob>]... [--dep <id>]...
waypoint manager ticket show <id>
waypoint manager ticket delete <id>                      # remove one ticket's state record
waypoint manager ticket update <id> [--priority] [--kind] [--scale] [--footprint] [--dep] [--spec-ref] [--intended-lead-title] [--lead-session-id] [--branch] [--pr-url]
waypoint manager ticket transition <id> --to <state> [--reason] [--scale] [--spec-ref] [--intended-lead-title] [--lead-session-id] [--branch] [--pr-url] [--is-partial | --not-partial]
waypoint manager lock acquire --owner <sid> [--ttl-seconds N]
waypoint manager lock steal   --owner <sid> [--ttl-seconds N]
waypoint manager lock release --owner <sid>
```

`transition` is keyed by target state; the backend checks the edge is legal from
the ticket's current state, and metadata (`--intended-lead-title`, `--branch`,
`--pr-url`, `--spec-ref`, `--scale`, `--is-partial`) rides the
same call so intent and its dedup key land atomically with the state change.
`ticket update` refines `footprint`/`kind`/`deps` without a state change. Illegal
edges, exhausted budgets, and violated invariants all return `409`.

#### The 14 states

Groups by how they occupy resources:

- **On-tree states** — `delegated`, `building`, `revising`, `blocked`,
  `review_requested`, `merging`. A ticket holds the shared working tree across this
  whole span: from the moment its branch is checked out at `delegated` until it
  reaches a terminal state. There is one shared tree, so at most one ticket occupies
  it at a time — execution is strictly serial, intrinsic to the model rather than a
  tunable. Entry to `delegated` is gated on the tree being free. A parked lead in `blocked`/
  `review_requested` still holds the tree (its branch stays checked out and its
  committed work lives there); it does not free the tree for another ticket.
- **Awaiting-human states** — `spec_review`, `blocked`, `review_requested`. These
  stamp `awaiting_since` on entry, cleared on exit, so the latency timeout counts
  only genuine human waits. `blocked` and `review_requested` are on-tree as well;
  `spec_review` is not (a writer produced its spec off-tree).
- **Off-tree states** — `intake`, `triaged`, `ready`, and `spec_pending`. A ticket
  in `spec_pending` has a read-only PRD/RFC writer running in the manager's tree that
  touches no tracked file, so spec authoring runs in parallel with a build and does
  not occupy the tree. The terminals `merged`, `deferred`, `abandoned` occupy
  nothing.

`merging` holds both the shared tree (to rebase the branch) and the `integration`
lease; at most one ticket occupies it, which the single-tree cap already guarantees.

| State | Meaning |
|---|---|
| `intake` | A user posted a ticket; untriaged. |
| `triaged` | Scale + coarse footprint assigned. |
| `spec_pending` | A PRD/RFC writer is authoring the spec (≤ 1 ticket here at a time). |
| `spec_review` | Spec posted; awaiting the human approval gate. |
| `ready` | Approved (or trivial); awaiting a free slot to delegate. |
| `delegated` | Intent recorded, lead spawned; awaiting lead-accepted + strategy chosen. |
| `building` | Lead is executing the ticket on its branch in the shared tree. |
| `blocked` | A blocker (error / decision / attention / infeasible / budget-exhausted) escalated to the human. |
| `review_requested` | Lead reported `done`/`partial`, PR open; the per-PR review-until-merge loop is with the human. |
| `revising` | Lead is addressing requested review changes. |
| `merging` | The manager holds the integration lease and is landing the PR (≤ 1 ticket here). |
| `merged` | Terminal — fully integrated. |
| `deferred` | Terminal — a partial subset merged; unmet goals spawned as new tickets. |
| `abandoned` | Terminal — rejected, aborted, or a latency timeout. |

`done` and `partial` are feedback signals a lead posts on the board, not states; the
manager reads them and drives `building → review_requested`. `done` is distinct from
the terminal `merged`.

#### Transition table

Target-state adjacency, as the backend enforces it:

| From | Legal `--to` targets | Notes |
|---|---|---|
| `intake` | `triaged` | |
| `triaged` | `spec_pending`, `ready`, `abandoned` | substantial → spec; trivial → ready; reject/duplicate → abandoned |
| `spec_pending` | `spec_review`, `spec_pending`, `blocked` | spec posted; self = writer-died resume; writer infeasible/budget → blocked |
| `spec_review` | `ready`, `spec_pending`, `abandoned` | approve; request-changes; reject/latency-timeout |
| `ready` | `delegated` | consumes an `attempts` budget unit; slot-gated |
| `delegated` | `building`, `ready`, `blocked`, `delegated` | lead-accepted; spawn-fail retry; spawn-fail exhausted/infeasible; self = lead-died resume |
| `building` | `review_requested`, `blocked`, `building` | done/partial; error/decision/attention; self = lead-died resume |
| `blocked` | `building`, `abandoned`, `blocked` | human answer relayed; abort/latency-timeout; self = parked-lead-died resume |
| `review_requested` | `revising`, `merging`, `abandoned`, `blocked`, `review_requested` | request-changes; human merge; abort/latency; blocker found; self = parked-lead-died resume |
| `revising` | `review_requested`, `blocked`, `revising` | re-pushed (kind=done); needs a decision; self = lead-died resume |
| `merging` | `merged`, `deferred`, `revising`, `blocked` | full green; partial green → deferred; semantic conflict; CI red / needs human |
| `merged` / `deferred` / `abandoned` | *(terminal)* | |

The lead-died resume is a self-transition: `ticket transition <id> --to <same-state>`
on any of the six resumable states (`spec_pending`, `delegated`, `building`,
`revising`, `blocked`, `review_requested`) consumes one `lead_restarts` budget unit
and rebinds a fresh lead to the ticket branch preserved in the tree. At `max_lead_restarts` the
self-loop is rejected and the manager escalates with `--to blocked`. `merging` has
no self-loop: a lost integrator is recovered by reconciling against `gh pr view`,
not by resuming a session.

#### `manager next`

`manager next` returns three things over the live ticket set:

- **`slots`** — `{total, used, free}`; the slot is the shared tree, so
  `used = count(on-tree tickets)` (`delegated` through `merging`).
- **`tickets[]`** — each live ticket's current state and its `legal_transitions`.
- **`recommended`** — at most one action: the highest-priority actionable ticket
  (priority order, then FIFO by `created_at`, then id), slot/invariant/budget-gated,
  excluding every id passed via `--tried`.

`recommended` is only ever a manager-initiated pull move: `triage` (`intake →
triaged`), `substantial` (`triaged → spec_pending`, gated on no other ticket in
`spec_pending`), `trivial` (`triaged → ready`), and `delegate` (`ready →
delegated`, gated on a free slot and the `attempts` budget). Every human- or
lead-driven edge — spec posted, human approve, lead-accepted, done/partial, human
merge, request-changes, the lead-died self-loops — is returned as a legal transition
but never as the recommendation. The manager enacts those when it observes the
external signal during reconcile.

#### Server-enforced invariants

`check_invariants` runs on every `transition`/`update` over the whole set and
rejects the write with `409` if any fails:

- **Tree cap** — at most one ticket occupies the shared tree (`delegated` through
  `merging`, parked `blocked`/`review_requested` included), so delegation of a
  second ticket is refused until the current one terminates. The cap counts
  `blocked` uniformly, so a ticket blocked
  from `spec_pending` (a writer deemed the work infeasible) or from a turn-1 spawn
  death holds the slot even though its work never reached the tree; that is the
  conservative choice, and the human decision or the latency timeout that clears the
  block also frees the slot.
- **≤ 1 merging** — the serialized integration gate (implied by a tree cap of 1).
- **≤ 1 spec_pending** — one active writer.
- **Unique `intended_lead_title`** across all live tickets — the spawn dedup key, so
  a re-fired delegate cannot create a second lead.
- **Budget ceilings** — `attempts ≤ max_delegate_attempts` and `lead_restarts ≤
  max_lead_restarts`. The two counters are independent: an initial spawn failure
  spends `attempts`, a post-work lead death spends `lead_restarts`, and each routes
  to `blocked`-awaiting-human at its own ceiling.

## The drain loop

Procedure state lives outside the context window (the `waypoint manager` machine
plus the board), every wake rebuilds position from that state, and every side effect
is guarded so a mid-turn crash or a growing context resumes the procedure rather
than repeating it.

Nothing polls. The manager's registered wake drives its turns. A slow liveness timer
is the only self-scheduled wake — a minutes-scale backstop for duties with no event
source (dead/parked-lead detection, expired-lease steal, latency-timeout
abandonment, `gh`/CI advancement). The drain is correct even if the timer never
fires.

Each wake drains all currently-actionable work to a fixpoint, then idles — draining
is what lets a slot-freeing action (a merge, an abandon) enable the next action in
the same turn without a self-wake. The manager keeps a per-drain `tried` set (ticket
ids that failed an action this drain) and repeats:

1. **Re-anchor.** `waypoint manager next --json` (passing `--tried <id>` for each id
   already tried this drain) → slots, per-ticket legal transitions, the one
   recommended action. If there is no recommendation and no outstanding external
   signal, the drain is done — go idle.
2. **Reconcile — adopt reality before acting** (below).
3. **Choose** the single action: the recommended pull move if present, or an
   observed external edge (spec posted, human answer, done/partial, human merge,
   dead lead, merged PR), highest-priority first.
4. **Record intent** with a dedup key before the side effect.
5. **Act idempotently.**
6. **Confirm** — write resulting ids back onto the ticket; on a failed delegate, add
   the ticket to `tried` and continue. Loop to step 1.

Each iteration strictly advances a well-founded measure: an action either moves a
ticket toward a terminal or awaiting-human state, occupies a bounded slot, or is
added to `tried` and not re-selected this drain. Auto-retry is bounded by the
`attempts` budget with backoff; past the budget a ticket goes to
`blocked`-awaiting-human. So the drain always reaches a fixpoint.

### Reconcile

Recorded state is a hypothesis; observed reality wins. Before choosing an action,
each iteration:

- **Re-reads the board.** The intake channel and every in-flight ticket's per-ticket
  `status` cell by key — not via `--since`, because a keyed cell overwrite keeps its
  row id and a `--since` poller misses it. Relay logs (append posts) are read with
  `--since`.
- **Matches spawned sessions to tickets.** `waypoint sessions list --spawned-by
  $WAYPOINT_SESSION_ID --recursive`, matched by `subagent:ticket-<id>:<role>` title.
  A live child whose ticket is not recorded as spawned is adopted (not re-spawned). A
  dead child is recovered: a death in `building`/`revising`/`blocked`/
  `review_requested`/`spec_pending` (work exists on the branch) is a lead-died
  self-loop that spends `lead_restarts` and resumes the ticket branch in the tree; a death
  in `delegated` with no committed work (a turn-1 spawn/startup failure) is a spawn
  failure → `delegated → ready` that spends `attempts`.
- **Checks PR/CI reality.** For `review_requested`/`merging`, `gh pr view <pr-url>
  --json state,mergeStateStatus,statusCheckRollup`: an already-`MERGED` PR means the
  merge happened (record it, do not re-merge); CI state gates the merge action.
- **Checks lead liveness in every live-lead state**, including parked leads in
  `blocked`/`review_requested` — a backend restart can mark a reattach-failed lead
  `error` while parked, and only reconcile catches it.

Reconcile is why a crash never duplicates: a re-fired spawn finds the live orphan
and adopts it; a re-attempted merge finds the PR already merged and stops.

### Write-ahead intent and idempotent action

Intent is recorded before the observable side effect, so a crash between "intend"
and "do" is recoverable by reconcile:

- **Delegate** — `ticket transition <id> --to delegated --intended-lead-title …
  --branch …` first (bumps `attempts`, reserves the unique title), then spawn. A turn
  that dies after the transition but before the spawn leaves a `delegated` ticket
  with no live lead; the next drain re-spawns into the reserved title exactly once.
- **Merge** — acquire the lease and transition to `merging` before touching `gh pr
  merge`; reconcile against `gh pr view` on the next drain if the turn died mid-merge.
- **Relay** — append the versioned relay post before the nudge (below).

Actions are idempotent: spawn only if no live session with the intended title exists;
relay via the durable versioned log, never a bare `sessions send` carrying the
payload; merge only if `gh pr view` does not already report `MERGED`.

### The durable versioned relay log

A `waypoint sessions send` is a fire-and-forget, non-observable sink — it starts a
turn but leaves no durable record, so a lead that dies mid-processing loses the
message. Every manager→lead relay (a human answer to a blocker, review feedback, a
re-delegate briefing) is a `kind=relay` append-log post to the ticket channel,
followed by a content-free nudge:

```
# Durable payload on the log:
waypoint board post <ticket-channel> "<the human answer / review feedback>" --meta kind=relay
# Content-free nudge — carries no payload, just wakes the lead:
waypoint sessions send <lead-sid> "[wp-msg from=<manager-sid>] Relay posted; read owed relays and act."
```

The lead is wake-subscribed to its own ticket channel, so the post wakes it; the
`sessions send` is a fallback. The lead consumes `kind=relay` posts in board-entry
`id` order — the id is a monotonic per-channel cursor — acting on those past the
highest id it has processed. This makes relays complete (re-derivable from the log
on every lead re-entry, so a fresh lead after a death still sees every owed relay),
idempotent (each applied once, keyed by id), and death-surviving (the payload is on
the durable board). A human answer is delivered at-least-once and applied
at-most-once; the ticket-channel log is authoritative.

### Backend restarts

On `claude_tty` (claude_code's default transport) the agent process lives under a
persistent pty, so an in-flight turn keeps executing while the Waypoint backend
restarts; on any other transport the turn is interrupted and resumed by the durable
state machine on the next wake. Either way boot-restore reattaches the session and
re-reads the wake subscriptions from the database. Two consequences hold on every
transport:

- A `waypoint` CLI call that fails with a connection error is transient — the backend
  is down mid-restart. It is retried with backoff and never counted against a
  ticket's `attempts`/`lead_restarts` budget or used to move a ticket to `blocked`.
  Only a real operation failure (the backend answered and the operation failed)
  consumes a budget.
- Events that arrive while the backend is down do not wake the manager (the wake
  driver is the backend and the pending-wake set is in-memory). They sit durably on
  the board; the slow liveness timer is the catch-up that guarantees an idle manager
  picks them up.

### Teardown

`waypoint manager deinit` drops every ticket, the persisted config, and the
integration lease in one call — the way to retire a manager or start a project's
backlog fresh. It clears **state records only**: the sessions the manager spawned,
their branches, and the board channels are reaped separately (`sessions delete`,
`board clear`), the same way the manager reaps a merged ticket's subtree. The
`/waypoint-manager deinit` skill workflow runs both halves — the reap, then
`manager deinit`.

Teardown is also wired to the manager's own session. `manager init` records the
initiating session (its `$WAYPOINT_SESSION_ID`, or an explicit `--owner`) as the
config's `owner_session_id`; deleting that session cascades a `deinit`, so a manager
never leaves an orphaned backlog behind a session that no longer exists. The
manager's session id is its stable identity (the session's primary key, unchanged by
a restart-and-reattach), so the cascade fires only on a deliberate `sessions delete`
of the manager, not on a restart. Re-running `init` after a manifest edit preserves
the recorded owner when no new `--owner` is passed. `waypoint manager ticket delete
<id>` removes a single ticket's record for one-off cleanup.

## Git and integration

Two guarantees are always on: strictly serial execution on the manager's single
shared tree (one ticket builds at a time, on its own branch) and a serialized
single-integrator gate (trunk advances only through the manager, behind a lease).
Conflicts surface only at that gate, never as a corrupted tree. Scheduling itself is
priority + FIFO; a ticket's recorded `footprint` and `deps` are not yet read by the
scheduler (footprint-based conflict-aware scheduling is a future addition), so a
ready ticket simply waits for the tree rather than being pre-ordered against the one
in flight.

### Serial execution on the shared tree

The manager delegates by cutting the ticket branch in its own tree and spawning the
lead there — with `--cwd` only, never `--worktree`:

```
git -C <repo-dir> checkout <trunk>
git -C <repo-dir> checkout -b ticket/<id> <trunk>
sid=$(waypoint sessions start <role-launch> \
  --cwd <repo-dir> \
  --title "subagent:ticket-<id>:tech-lead" \
  --spawner-session-id "$WAYPOINT_SESSION_ID" | jq -r .session.id)
```

`<repo-dir>` is the manager's own working tree. A `--cwd`-only session has no
worktree of its own (`session.worktree_path` stays unset), so deleting or reaping the
lead never removes the tree — the safety property behind sharing it. The branch
stays checked out for the ticket's whole life; the manager does tree operations
(checkout, rebase, merge) only at the boundaries of that one ticket, never while the
lead is mid-edit, and the tree cap guarantees no second lead is building meanwhile.
`--spawner-session-id` makes the lead owner-scoped, so the manager lists and reaps
only its own subtree; the `subagent:ticket-<id>:tech-lead` title is the spawn dedup
key reconcile matches on.

A tech-lead that fans its ticket out (via `waypoint-workqueue` or `waypoint-crew`)
gives its workers sub-worktrees off the ticket branch and integrates them into one
commit ref before reporting up. The manager only ever sees the single ticket branch.
A read-only PRD/RFC writer for another ticket may run in the tree in parallel; it
writes only under the configured `spec_dir` (default `.waypoint/specs`, gitignored)
and touches no tracked file or branch, so it never collides with the building lead's
commits.

### Spawn dedup and branch collisions

Reconcile picks between three spawn paths: a live same-title session is adopted, not
spawned; an initial delegate that collides with a stale `ticket/<id>` branch from an
incomplete reap returns the tree to trunk and deletes the branch (no committed work)
before re-creating it; a lead-died resume keeps the branch (it holds committed work),
checks it out, and re-spawns onto it.

### Terminate-not-delete resume

A dead lead in a live-lead state is recovered without losing its branch:

- `waypoint sessions terminate <sid>` stops the process but keeps the record; the
  branch and its commits survive in the tree.
- `waypoint sessions delete <sid>` removes the record — used only after integration,
  when the work has landed on trunk.

A lead-died resume terminates the dead session, checks the ticket branch out in the
tree, and spawns a fresh lead there (again `--cwd <repo-dir>`, no `--worktree`),
re-registers that lead's wake on the ticket channel, and sends it the kickoff. The
fresh lead re-reads the durable ticket-channel log — the `status` cell and every owed
relay — so committed work and a human answer given while the old lead was alive are
both preserved. The reap of a merged ticket's subtree happens after integration,
which is also when the tree returns to trunk and the merged branch is dropped.

### The serialized integration lease

Trunk is advanced by the manager alone, in both `pr` and `local` modes, behind the
`integration` lease:

```
waypoint manager lock acquire --owner "$WAYPOINT_SESSION_ID"   # --ttl-seconds defaults to the manifest
# … rebase/update onto trunk → verify / CI → merge …
waypoint manager lock release --owner "$WAYPOINT_SESSION_ID"
```

There is a single implicit `integration` lease; `acquire` fails `409` if another live
owner holds it. The lease is released on every exit from `merging` — `merged`,
`deferred`, `revising` (conflict), or `blocked` (CI red) — so a stuck merge never
strands the gate. A manager that dies holding the lease is recovered by `manager lock
steal`, which succeeds only after the TTL expires; on restart a `merging` ticket is
reconciled against `gh pr view` before any re-attempt, so a mid-merge crash never
double-merges.

### PR-based integration and human review

With `integration.mode: pr`, the manager opens a PR for the ticket branch with `gh`
and the human is the sole merge authority — autonomy runs up to the PR, never through
it. The manager posts the PR to the human as an inbox approval item and moves the
ticket to `review_requested`; the lead parks alive and idle, but the ticket keeps
holding the tree (strict serial — a parked ticket does not free the tree). On the
human's answer, relayed via the durable log: request-changes → `revising` (relay the
feedback to the lead); merge → acquire the lease, move to `merging`, land the PR;
abort/latency-timeout → `abandoned`. Each new PR head re-posts `done` while
`revising`, looping review-until-merge until the human merges or aborts.

Before landing, the branch is rebased onto the advanced trunk with `git rebase` in
the shared tree; only trivial lockfile/generated conflicts are resolved
in-place, and a semantic conflict bounces the ticket to `revising`. A partial
completion spawns follow-up tickets for the unmet goals only at the `merging →
deferred` edge, once the delivered subset has merged, with a deterministic dedup key.

With `integration.mode: local`, the manager fast-forwards trunk locally behind the
same lease instead of opening a PR; the human review gate still applies.

## Configuration

Per-project config is `waypoint-manager.yaml`. `waypoint manager init --manifest
<path>` persists the machine-relevant fields server-side (idempotent; re-run after
editing one) so `manager next`/`transition` enforce them regardless of context. The
skill-consumed fields are read directly by the manager for spawn config and policy;
the backend neither reads nor needs them.

### Manifest fields

| Field | Consumed by | Meaning |
|---|---|---|
| `project` | skill | Project name, used in summaries and channel labels. |
| `trunk` | backend | The integration branch every ticket branch is cut from and the sole integrator advances. |
| `spec_dir` | skill | Directory the PRD/RFC writers write specs into (default `.waypoint/specs`; keep it gitignored). |
| `board.tickets_channel` | skill | Intake channel; also holds `ticket:<id>` registry cells. |
| `board.org_channel` | skill | Human-visible drain and outcome summaries. |
| `board.ticket_channel_prefix` | skill | Per-ticket channel is `<prefix><id>` (e.g. `ticket-42`). |
| `retry.max_delegate_attempts` | backend | Initial-spawn retry budget before `blocked`-awaiting-human. Enforced on `ready → delegated`. |
| `retry.max_lead_restarts` | backend | Fresh-lead resumes after a lead death before `blocked`. Enforced on the lead-died self-loop. Independent of `attempts`. |
| `priority.levels` | backend | Ordered high-to-low (`p0` highest); a ticket's `--priority` must be one of these. Ties break oldest-first (FIFO by `created_at`). |
| `scale.substantial_when` | skill | Natural-language rule triage applies to label a ticket `substantial` (→ spec) vs `trivial` (→ direct). |
| `integration.mode` | skill | `pr` (GitHub PR) or `local` (rebase-ff). Sole integrator either way. |
| `integration.require_ci_green` | skill | Gate the merge on green CI. |
| `timeouts.human_latency_hours` | skill | How long an awaiting-human ticket waits before the manager escalates then abandons. Measured from `awaiting_since`. |
| `timeouts.lock_ttl_seconds` | backend | Integration-lease TTL; the default for `lock acquire`/`steal`, and the window after which a dead owner's lease can be stolen. |
| `escalation.self_decide` | skill | Blocker classes the manager settles itself. |
| `escalation.always_escalate` | skill | Blocker classes routed to the human inbox. |

### Roles

Each role under `roles` is configured one of two ways, the choice being the user's:

- **`preset: <name>`** — an existing DB-backed session preset (backend, transport,
  model, effort, permission_mode, account_profile, launch_env, args, tags). At setup
  the manager verifies it exists with `waypoint presets show <name>` and halts,
  flagging the user, if it is missing — it never runs `presets create`. Inspect the
  preset's model and permission posture rather than trusting the name.
- **`launch: { backend, model, permission_mode, … }`** — an inline launch block,
  passed as the matching `sessions start` flags (`--backend`, `--model`,
  `--permission-mode`, …).

`--cwd` and `--title` are always per-launch; the manager supplies `--cwd`
(its own tree, `{{repo_dir}}`), the `subagent:ticket-<id>:<role>` title, and
`--spawner-session-id` on top of either config path, and an explicit flag overrides a
preset value. No role is ever spawned with `--worktree`.

The `manager` and `tech_lead` roles run unattended, so their permission posture must
auto-approve or their own `sessions start` / `gh` / `waypointctl` tool calls block on
an absent approver. The per-backend auto-approve mode is `auto` for `claude_code`,
`auto_review` for `codex`, `allow` for `opencode`. The blast radius is bounded by the
human-owned merge gate on every PR, the one-ticket-at-a-time serial tree, and the
ownership rule that a session acts only on what it spawned. Set each role's model id verbatim from
`waypoint models <backend>` and confirm the permission mode from `waypoint backends`.

The `manager` role launches on `claude_tty` — claude_code's default transport —
recommended because a pty-backed turn survives a Waypoint backend restart mid-turn,
though not required: the durable state machine recovers the manager on any transport. A
`launch:` block with `backend: claude_code` and no explicit `transport` resolves to
`claude_tty`.

Each role's `templates:` path points at a directory of per-step Markdown prompts
(`templates/<role>/<step>.md`).

### Template placeholders

A template never hardcodes a preset, model, or channel name; every manifest-owned
value is a `{{placeholder}}` the manager substitutes from the loaded manifest, so
changing a preset or a channel prefix flows through without editing a template.
Alongside the ticket-scoped placeholders the manager fills per ticket (`{{ticket_id}}`,
`{{ticket_title}}`, `{{ticket_body}}`, `{{priority}}`, `{{scale}}`, `{{footprint}}`,
`{{input_type}}`, `{{spec_route}}`, `{{spec_ref}}`, `{{branch}}`, `{{pr_url}}`,
`{{manager_session_id}}`, `{{repo_dir}}`), these come from the manifest. `{{branch}}`
is the ticket's branch, `ticket/<id>` by convention, and `{{repo_dir}}` is the
manager's own working tree, where every lead builds.

| Placeholder | Source |
|---|---|
| `{{project}}` | `project` |
| `{{trunk}}` | `trunk` |
| `{{spec_dir}}` | `spec_dir` (default `.waypoint/specs`) |
| `{{tickets_channel}}` | `board.tickets_channel` |
| `{{org_channel}}` | `board.org_channel` |
| `{{ticket_channel}}` | `board.ticket_channel_prefix` + the current ticket id (e.g. `ticket-42`) |
| `{{ticket_channel_prefix}}` | `board.ticket_channel_prefix` (bare, for other tickets' channels and the wake glob) |
| `{{tech_lead_launch}}` | `roles.tech_lead` launch args (`--preset <name>` or the inline `launch:` flags) |
| `{{writer_launch}}` | the matching writer role (`roles.prd_writer` / `roles.rfc_writer`) launch args |
| `{{repo_dir}}` | the manager's own working tree (its cwd) |
| `{{manager_session_id}}` | `$WAYPOINT_SESSION_ID` |

`waypoint manager render <template> --ticket <id>` performs the substitution and
prints the body, which the templates pipe into `sessions send`. It resolves each
placeholder lowest precedence first — env (`{{repo_dir}}`, `{{manager_session_id}}`)
< manifest (`{{project}}`, `{{trunk}}`, `{{spec_dir}}`, and the channels) < the ticket record < the ticket's board
cell (`{{ticket_body}}`, `{{input_type}}`, `{{spec_route}}`) < a `--set key=value`
override — and fails on an unknown placeholder unless `--allow-unresolved`, so a
literal `{{…}}` never reaches a subagent. It runs entirely CLI-side over the manifest
file and existing endpoints; the server has no knowledge of templates or placeholders.

## Portability

The manager and every role work as a Claude Code, Codex, or OpenCode session; nothing
depends on one harness's features. The only hard dependency is the `waypoint` CLI. The
Waypoint-shipped orchestration skills (`waypoint-subagents`, `waypoint-workqueue`,
`waypoint-crew`, `waypoint-comms`, `waypoint-worktree`) are a checked prerequisite the
setup confirms and, if one is missing for a role's backend, halts and flags rather than
installing. PRD/RFC authoring, PR creation, rebasing, and review-addressing are carried
out directly with the `waypoint` CLI, `git`, and `gh` in the prompt templates.
