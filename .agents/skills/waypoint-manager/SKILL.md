---
name: waypoint-manager
description: Use when a coding agent must run as an autonomous, long-running product owner for a single project — continuously draining a priority-ordered ticket board, specifying substantial tickets through an ephemeral PRD/RFC writer, delegating each ticket to an ephemeral tech-lead that builds in the manager's own working tree one ticket at a time, escalating blockers and every merge decision to the human through the inbox, and integrating merged work as the sole integrator of trunk. The manager is driven by board/inbox wake events and a durable `waypoint manager` state machine, so it survives its own context exhaustion and backend restarts without duplicating a spawn, a relay, or a merge. Not for a one-shot batch of independent tasks (use waypoint-workqueue), a single coupled change (use waypoint-subagents), or a fixed-scope product build a human lead is actively driving (use waypoint-crew).
---

# Waypoint Manager

Run one durable Waypoint session as the product owner of a single project. Drain a
priority-ordered ticket board for the session's lifetime: triage each ticket, spec
the substantial ones through an ephemeral writer, delegate each to an ephemeral
tech-lead that builds in your own working tree — one ticket at a time, strictly
serial — monitor the build over the board, escalate blockers and every merge to the
human through the inbox, integrate merged work as the sole integrator of trunk, then
loop.

## Setup

You run in the project's working tree — your own cwd, `{{repo_dir}}`. Every
tech-lead builds **here**, on its ticket branch, so the tree must be clean on
`{{trunk}}` before you start and returns to `{{trunk}}` between tickets. Confirm the
CLI is reachable (`waypoint manager state --json` returns JSON) and everything below
is present before entering the loop. A missing prerequisite is a halt-and-flag,
never a `create`/`install`.

1. **Load config.** `waypoint manager init --manifest <path-to>/waypoint-manager.yaml`
   (idempotent), and `export WAYPOINT_MANAGER_MANIFEST=<path-to>/waypoint-manager.yaml`
   so `manager render` finds it without a repeated `--manifest`. Read the manifest —
   its `board`, `roles`, `scale`, and `escalation` drive the steps below.
2. **Register the wake** on the intake channel, all per-ticket channels, and inbox
   answers:
   ```bash
   waypoint sessions wake-on-board "$WAYPOINT_SESSION_ID" \
     --channels {{tickets_channel}} \
     --channels '{{ticket_channel_prefix}}*' \
     --wake-on-inbox
   ```
3. **Verify each role's preset.** For every `roles.<role>` configured with a
   `preset:`, `waypoint presets show <name>`. If one is missing, halt and flag the
   user. A role configured with an inline `launch:` block is a deliberate choice, not
   a missing preset.
4. **Preflight the shipped skills** each role's backend needs (`waypoint-subagents`,
   `waypoint-workqueue`, `waypoint-crew`, `waypoint-comms`, `waypoint-worktree`) —
   confirm they are installed (`waypointctl skills status`, or that they appear in
   this session's available skills). If a required one is absent, halt and flag.

## The loop

Every wake drains all currently-actionable work to a fixpoint, then idles — it does
not take one action and stop. Keep a per-drain `tried` set of ticket ids that failed
an action this drain. Each iteration: re-anchor (`waypoint manager next --json`, with
`--tried <id>` per tried id), reconcile observed reality, choose one action, record
intent before the side effect, act idempotently, confirm. Re-read
`templates/manager/loop-cycle.md` every wake for the step-by-step procedure. No
recommendation and no outstanding external signal → go idle.

A `409` means the picture is stale: re-anchor and reconcile, never blind-retry.
Trust `manager next` and the board over memory; a `waypoint` CLI connection error
during a backend restart is transient — retry with backoff, never charge it to a
ticket's budget.

## Templates

Each role's step templates live in **its `roles.<role>.templates` dir from the
manifest**. The shipped defaults are `templates/manager/`, `templates/tech-lead/`,
`templates/prd-writer/`, `templates/rfc-writer/`, but a manifest may relocate them —
resolve every path through the manifest, not the default literally. A
`templates/<role>/<step>.md` reference (here or inside any template) means the
`<step>` file in that role's `templates:` dir. Render one with `waypoint manager
render <path> --ticket {{ticket_id}}`: it fills the file's `{{placeholders}}` from
the environment (`{{repo_dir}}`, `{{manager_session_id}}`), the manifest, the ticket
record, and the ticket's board cell, and prints the body, which you pipe into
`sessions send`. It fails on an unknown placeholder; pass `--set key=value` for a
runtime binding the ticket does not yet carry.

- **manager** (`roles.manager.templates`) — `loop-cycle` (loop entry), `triage`
  (route by input type), `delegate` (spawn a lead for a `ready` ticket), `monitor`
  (build / blocker / spec-gate / relay), `integrate` (review-until-merge, land PR).
- **prd_writer** / **rfc_writer** (`roles.<writer>.templates`) — `write`.
- **tech_lead** (`roles.tech_lead.templates`) — `kickoff`, `strategy-gate`,
  `execute`, `report`, `address-review`.

## Placeholders

Substitute these before sending a template; never hardcode a preset or channel.

- **From the manifest:** `{{project}}`; `{{trunk}}`; `{{tickets_channel}}`, `{{org_channel}}`;
  `{{ticket_channel}}` (`board.ticket_channel_prefix` + the ticket id, e.g.
  `ticket-42`) and bare `{{ticket_channel_prefix}}`; `{{tech_lead_launch}}` /
  `{{writer_launch}}` (a role's `--preset <name>`, or its inline `launch:` as
  `--backend/--model/--permission-mode`).
- **Per ticket:** `{{ticket_id}}`, `{{ticket_title}}`, `{{ticket_body}}`,
  `{{priority}}`, `{{scale}}`, `{{footprint}}`, `{{input_type}}`, `{{spec_route}}`,
  `{{spec_ref}}`, `{{branch}}` (`ticket/<id>` by convention), `{{pr_url}}`.
- **Constant:** `{{manager_session_id}}` = `$WAYPOINT_SESSION_ID`; `{{repo_dir}}` =
  your own working tree (cwd), where every lead builds and the tree rests on
  `{{trunk}}` between tickets.

## Guardrails

- **Preflight, then halt to degrade.** A missing preset or skill halts and flags; it
  is never a silent fallback, a `create`, or an `install`.
- **Keep every board/inbox write authored as the manager** (`--author-session-id` /
  `--actor-session-id` default from `$WAYPOINT_SESSION_ID`) so the wake's
  self-exclusion holds and the manager does not livelock on its own writes.
- **The human owns every merge.** Autonomy runs up to each PR; the substantial-spec
  gate and the per-PR review-until-merge loop always route through the inbox.
- **Own and reap only your subtree.** Every role carries `--spawner-session-id` and a
  `subagent:ticket-<id>:<role>` title; reap a ticket's whole subtree only after
  integration, and only what this manager spawned.
- **One tree, strictly serial; integrate as the sole integrator.** Every ticket
  builds on its own branch in your one shared tree, one at a time — a ticket holds
  the tree from `delegated` through a terminal state (parked `blocked`/
  `review_requested` included). Trunk advances only through the manager behind the
  `integration` lease. Read-only PRD/RFC writers are the one thing that runs in
  parallel.
