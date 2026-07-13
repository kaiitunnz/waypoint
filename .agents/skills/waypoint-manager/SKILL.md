---
name: waypoint-manager
description: Use when a coding agent must run as an autonomous, long-running product owner for a single project — continuously draining a priority-ordered ticket board, specifying substantial tickets through an ephemeral PRD/RFC writer, delegating each ticket to an ephemeral tech-lead in its own git worktree, escalating blockers and every merge decision to the human through the inbox, and integrating merged work as the sole integrator of trunk. The manager is driven by board/inbox wake events and a durable `waypoint manager` state machine, so it survives its own context exhaustion and backend restarts without duplicating a spawn, a relay, or a merge. Not for a one-shot batch of independent tasks (use waypoint-workqueue), a single coupled change (use waypoint-subagents), or a fixed-scope product build a human lead is actively driving (use waypoint-crew).
---

# Waypoint Manager

Run one durable Waypoint session as the product owner of a single project. Drain a
priority-ordered ticket board for the session's lifetime: triage each ticket, spec
the substantial ones through an ephemeral writer, delegate each to an ephemeral
tech-lead in its own worktree, monitor the build over the board, escalate blockers
and every merge to the human through the inbox, integrate merged work as the sole
integrator of trunk, then loop.

This file plus the per-step templates are the procedure to run; the manifest
(`waypoint-manager.yaml`) documents every config field and placeholder inline.

## Setup

Confirm the CLI is reachable (`waypoint manager state --json` returns JSON) and
everything below is present before entering the loop. A missing prerequisite is a
halt-and-flag, never a `create`/`install`.

1. **Load config.** `waypoint manager init --manifest <path-to>/waypoint-manager.yaml`
   (idempotent). Read the manifest — its `board`, `roles`, `scale`, and `escalation`
   drive the steps below.
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
an action this drain. Each iteration:

1. **Re-anchor.** `waypoint manager next --json` (add `--tried <id>` per id already
   in `tried`) for `slots`, each ticket's `legal_transitions`, and the single
   `recommended` action. Re-read `templates/manager/loop-cycle.md` so the procedure
   is re-injected, not remembered. No recommendation and no outstanding external
   signal → the drain is done; go idle.
2. **Reconcile — adopt reality before acting.** Re-read the board (`{{tickets_channel}}`
   and each in-flight ticket's `status` cell by key; relay logs by `--since`); list
   spawned sessions (`--spawned-by "$WAYPOINT_SESSION_ID" --recursive`) and match
   `subagent:ticket-<id>:<role>` titles; check `gh pr view` for already-merged PRs;
   check lead liveness in every live-lead state.
3. **Choose one action** — the highest-priority of the `recommended` pull move or an
   external edge reconcile surfaced (spec posted, human answer, done/partial, human
   merge, dead lead, merged PR).
4. **Record intent before the side effect** — transition first, carrying the dedup
   key (`--intended-lead-title` / `--branch` / `--worktree-path` / `--pr-url`), then
   act.
5. **Act idempotently** — spawn only if no live same-title session exists; relay via
   a versioned board post + a content-free nudge; `gh pr merge` only if not already
   `MERGED`. Route to the per-step template (below).
6. **Confirm** — write resulting ids back onto the ticket. On a failed delegate, add
   the id to `tried` and continue. Loop to step 1.

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
`<step>` file in that role's `templates:` dir. `$(render <path>)` is shorthand —
substitute this ticket's `{{placeholders}}` into that file and send the text as the
message body; it is not a shell command. `{{manager_session_id}}` is your own session
id (`$WAYPOINT_SESSION_ID`).

- **manager** (`roles.manager.templates`) — `loop-cycle` (loop entry), `triage`
  (route by input type), `delegate` (spawn a lead for a `ready` ticket), `monitor`
  (build / blocker / spec-gate / relay), `integrate` (review-until-merge, land PR).
- **prd_writer** / **rfc_writer** (`roles.<writer>.templates`) — `write`.
- **tech_lead** (`roles.tech_lead.templates`) — `kickoff`, `strategy-gate`,
  `execute`, `report`, `address-review`.

## Placeholders

Substitute these before sending a template; never hardcode a preset or channel.

- **From the manifest:** `{{trunk}}`; `{{tickets_channel}}`, `{{org_channel}}`;
  `{{ticket_channel}}` (`board.ticket_channel_prefix` + the ticket id, e.g.
  `ticket-42`) and bare `{{ticket_channel_prefix}}`; `{{tech_lead_launch}}` /
  `{{writer_launch}}` (a role's `--preset <name>`, or its inline `launch:` as
  `--backend/--model/--permission-mode`).
- **Per ticket:** `{{ticket_id}}`, `{{ticket_title}}`, `{{ticket_body}}`,
  `{{priority}}`, `{{scale}}`, `{{footprint}}`, `{{input_type}}`, `{{spec_route}}`,
  `{{spec_ref}}`, `{{branch}}` (`ticket/<id>` by convention), `{{worktree_path}}`
  (runtime-derived), `{{pr_url}}`.
- **Constant:** `{{manager_session_id}}` = `$WAYPOINT_SESSION_ID`.

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
- **Isolate every ticket; integrate serially.** Each ticket builds in its own
  worktree + branch; trunk advances only through the manager behind the `integration`
  lease.
