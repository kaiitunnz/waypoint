---
name: waypoint-manager
description: Use when a coding agent must run as an autonomous, long-running product owner for a single project — continuously draining a priority-ordered ticket board, specifying substantial tickets through an ephemeral PRD/RFC writer, delegating each ticket to an ephemeral tech-lead in its own git worktree, escalating blockers and every merge decision to the human through the inbox, and integrating merged work as the sole integrator of trunk. The manager is driven by board/inbox wake events and a durable `waypoint manager` state machine, so it survives its own context exhaustion and backend restarts without duplicating a spawn, a relay, or a merge. Not for a one-shot batch of independent tasks (use waypoint-workqueue), a single coupled change (use waypoint-subagents), or a fixed-scope product build a human lead is actively driving (use waypoint-crew).
---

# Waypoint Manager

Run one durable Waypoint session as the **product owner** of a single project. It
drains a priority-ordered ticket board for its lifetime: for each ticket it
triages a scale and footprint, writes a PRD/RFC through an ephemeral writer when
the work is substantial, delegates the ticket to an ephemeral **tech-lead** in
its own git worktree, monitors the build over a typed board protocol, escalates
blockers and **every merge** to the human through the inbox, and integrates the
merged work as the **sole integrator** of trunk. Then it loops.

The manager is not a crew lead at the wheel of one product build — it is an
*unattended, continuously running* backlog owner that pulls the human in only for
judgment: the substantial-spec approval gate and the per-PR review-until-merge
loop (the human is the sole merge authority for every PR).

## When to use this vs. the other orchestration skills

Pick by the shape of the work, not its size:

- **Manager** (this skill) — an unattended orchestrator that owns a project's
  **backlog over its lifetime**, intakes tickets continuously, and runs each
  through a specify → delegate → review → merge lifecycle. One manager per
  project. It *delegates* a crew-scale ticket to a tech-lead rather than being
  that crew.
- **Crew** (`waypoint-crew`) — a role-specialized org driving **one product build**
  with a human lead sequencing coupled phases and checkpointing at boundaries. A
  manager delegates to a lead that may itself run a crew.
- **Work queue** (`waypoint-workqueue`) — a flat batch of **independent** tasks a
  lead merges. A tech-lead picks this for a wide, uncoupled ticket.
- **Delegate-and-review** (`waypoint-subagents`) — *one* child takes a coherent
  coupled chunk and you review its diff. A tech-lead picks this for a single
  coupled ticket.

If the deliverable is not a *continuously-owned backlog* — if it is one build, one
batch, or one change — reach for crew, work queue, or subagents instead.

## The two native primitives it stands on

The manager is a **skill over the `waypoint` CLI** plus two runtime additions that
already ship; everything else (board, inbox, presets, sessions/subagents,
worktrees) is used as-is.

- **`waypoint sessions wake-on-board`** — an event-driven wake. The manager
  registers a subscription and the runtime *starts a turn* on it whenever a
  watched board channel or the inbox changes, so it is driven by events, not by
  polling or a fragile self-timer. Content-free: a wake says only "something
  changed — re-read." Self-mutations never wake the author. See `references/wake.md`.
- **`waypoint manager`** — a durable, DB-backed **per-ticket state machine**. It
  holds one record per ticket, validates every transition and scheduler invariant
  server-side, and `waypoint manager next` returns the derived slot state, each
  ticket's legal transitions, and the single highest-priority recommended action.
  This externalizes the manager's procedure state so a growing context window
  cannot make it forget a step or enact an illegal one. See
  `references/state-machine.md`.

## The crash-safe drain loop (the core idea)

Every wake **drains all currently-actionable work to a fixpoint**, then idles — it
does not take one action and stop. Draining is what lets a slot-freeing action (a
merge, an abandon) immediately enable the next action *in the same turn* without
relying on a self-wake. Each iteration:

1. **Re-anchor** — `waypoint manager next` for the legal transitions + the one
   recommended action; re-read `templates/manager/loop-cycle.md`.
2. **Reconcile** — adopt reality *before* acting: re-read the board, list spawned
   sessions, check lead liveness in every live-lead state, check `gh pr view` for
   already-merged PRs.
3. **Choose** the recommended action for the highest-priority actionable ticket.
4. **Record intent** with a dedup key *before* any side effect (the state
   transition carrying `intended_lead_title` / `pr-url`; a relay's `relay_version`).
5. **Act idempotently** — spawn only if no live same-title session exists; relay
   as a durable versioned board post + a content-free nudge; `gh pr merge` only if
   not already merged.
6. **Confirm**, then loop; a failed delegate is added to this drain's `tried` set
   and not re-selected.

Because state is rebuilt from the machine + the board each iteration and every
side effect is intent-guarded, reconcile-adopted, or idempotently consumed, a
growing context or a mid-turn crash **resumes** the procedure rather than
repeating it. The full protocol — the durable versioned relay log and its
idempotent-by-version consumer — is in `references/loop.md`.

## Non-mutating setup (verify, never create)

On instantiation the manager sets up its own state but **changes nothing about the
user's environment**:

- **Create** its board channels, register its wake subscription (§ `wake.md`), and
  `waypoint manager init --manifest waypoint-manager.yaml`.
- **Verify** each role's `preset:` exists with `waypoint presets show <name>`; if
  one is missing, **halt and flag the user** — never run `presets create`. A role
  configured with an inline `launch:` block instead of `preset:` is a deliberate
  config choice, not a missing-preset fallback.
- **Preflight** the Waypoint orchestration skills each role's backend needs
  (`waypoint-subagents`, `waypoint-workqueue`, `waypoint-crew`, `waypoint-comms`,
  `waypoint-worktree`); if a required one is absent for that backend, **halt and
  flag** — never install a skill.

Setup runs no `presets create` and no skill install. This is the portability
contract: the only hard dependency is the `waypoint` CLI; the shipped skills are a
*checked* prerequisite the human provisions.

## Portability principle

The manager and every role must work as a Claude Code, Codex, or OpenCode session
— nothing may depend on one harness's features. The Waypoint-shipped orchestration
skills above are a checked prerequisite. Behaviors a user's **personal** skills
might provide — PRD/RFC authoring, PR creation, rebasing, review-addressing,
loop-revise — are **inlined as prose** in the shipped example templates, never
invoked as personal-skill slash commands (which may be absent). The `/waypoint-*`
orchestration skills *are* Waypoint-shipped, so the tech-lead templates reference
them by name; nothing else is assumed installed.

## Routing

- The bounded drain-to-fixpoint cycle, write-ahead intent + reconcile-adopt, and
  the durable versioned relay log: `references/loop.md`.
- The state machine — transition table, what each state means, the server-enforced
  invariants: `references/state-machine.md`.
- Registering and reasoning about the event wake: `references/wake.md`.
- Per-ticket worktree isolation, terminate-not-delete resume, the serialized
  integration lease, PR-based integration: `references/git-integration.md`.
- The `waypoint-manager.yaml` manifest fields and per-role launch config:
  `references/config.md`.
- Per-step prompt templates the manager renders and sends: `templates/`.

## Guardrails

- **Preflight first, then degrade by halting.** Confirm the CLI is reachable
  (`waypoint manager state` returns JSON) and every preset/skill prerequisite is
  present before entering the loop. A missing prerequisite is a **halt-and-flag**,
  never a silent fallback and never a `create`/`install`.
- **Trust `manager next`, not memory.** The server enumerates the legal
  transitions and the recommended action; never hand-guess a `--to` target or
  skip a slot/invariant gate. A drifting context is exactly what this defends.
- **Reconcile before every side effect.** Adopt a live orphan by title rather than
  re-spawning; check `gh pr view` before a merge; check lead liveness in every
  live-lead state. A spawn or merge is never duplicated.
- **Never self-wake into a livelock.** The manager writes the very channels and
  files the inbox items it subscribes to; the wake excludes self-mutations, but
  keep every board/inbox write authored as the manager (`--author-session-id` /
  `--actor-session-id` default from `$WAYPOINT_SESSION_ID`) so the exclusion holds.
- **The human owns every merge.** Autonomy runs up to each PR; the substantial-spec
  gate and the per-PR review-until-merge loop always route through the inbox.
- **Own and reap only your subtree.** Every role carries `--spawner-session-id`
  and a `subagent:ticket-<id>:<role>` title; reap a ticket's whole subtree only
  after integration, and only what this manager spawned.
- **Isolate every ticket; integrate serially.** Each ticket builds in its own
  worktree + branch; trunk advances only through the manager behind the
  `integration` lease. This is the zero-tree-conflict guarantee, not an
  optimization.
