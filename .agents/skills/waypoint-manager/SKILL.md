---
name: waypoint-manager
description: Use when a coding agent must run as an autonomous, long-running product owner for a single project — continuously draining a priority-ordered ticket board, specifying substantial tickets through an ephemeral PRD/RFC writer, delegating each ticket to an ephemeral tech-lead that builds in the manager's own working tree one ticket at a time, escalating blockers and every merge decision to the human through the inbox, and integrating each merge the human makes. The manager is driven by board/inbox wake events and a durable `waypoint manager` state machine, so it survives its own context exhaustion and backend restarts without duplicating a spawn, a relay, or a merge. Not for a one-shot batch of independent tasks (use waypoint-workqueue), a single coupled change (use waypoint-subagents), or a fixed-scope product build a human lead is actively driving (use waypoint-crew).
---

# Waypoint Manager

Run one durable Waypoint session as the product owner of a single project. Drain a
priority-ordered ticket board for the session's lifetime: triage each ticket, spec
the substantial ones through an ephemeral writer, delegate each to an ephemeral
tech-lead that builds in your own working tree — one ticket at a time, strictly
serial — monitor the build over the board, escalate blockers and every merge to the
human through the inbox, integrate each merge the human makes, then loop.

## Setup

Run Setup once to stand the manager up — a human triggers it by messaging the session
`/waypoint-manager init`.

You run in the project's working tree — your own cwd, `{{repo_dir}}`. Every
tech-lead builds **here**, on its ticket branch, so the tree must be clean on
`{{trunk}}` before you start and returns to `{{trunk}}` between tickets. Confirm the
CLI is reachable (`waypoint manager state --json` returns JSON) and everything below
is present before entering the loop. A missing prerequisite is a halt-and-flag,
never a `create`/`install`.

1. **Load config.** `waypoint manager init --manifest <path-to>/waypoint-manager.yaml`
   (idempotent) persists the machine-relevant config and compiles your step templates
   and every child prompt to the templates dir (default `.waypoint/manager/templates`),
   baking in the manifest's `board`, `roles`, `scale`, and `escalation`. That dir is
   your runtime source of truth; `manager state` reports its path.
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
intent before the side effect, act idempotently, confirm. Re-read your compiled
`manager/loop-cycle.md` (under the templates dir `manager state` reports) every wake
for the step-by-step procedure. No recommendation and no outstanding external signal
→ go idle.

A `409` means the picture is stale: re-anchor and reconcile, never blind-retry.
Trust `manager next` and the board over memory; a `waypoint` CLI connection error
during a backend restart is transient — retry with backoff, never charge it to a
ticket's budget.

## Teardown

`/waypoint-manager deinit` retires the manager or resets the backlog. `manager deinit`
drops the state records only, so reap what you own first:

1. **Reap your subtree** — delete every session you spawned (worker sub-worktrees
   prune with them):
   ```bash
   for s in $(waypoint sessions list --spawned-by "$WAYPOINT_SESSION_ID" --recursive | jq -r '.sessions[].id'); do
     waypoint sessions delete "$s" --force --prune-branches
   done
   ```
2. **Reset the tree** to `{{trunk}}` and drop any leftover `ticket/<id>` branches
   (`git -C {{repo_dir}} checkout {{trunk}}`, then `git -C {{repo_dir}} branch -D` each).
3. **Clear the board channels** you own — the intake, org, and per-ticket channels —
   with `board clear`, when retiring the backlog rather than pausing it.
4. **Deinit** — `waypoint manager deinit --yes` drops the tickets and config.
   Deleting this manager session instead cascades the same record cleanup.

## Templates

`manager init` compiles every template to the templates dir, baking the static
values (channels, launch commands, policy, paths) into each body. You operate from
that compiled dir (path from `manager state`): read your OWN compiled step templates
directly — they carry the live per-ticket `{{placeholders}}` you fill each wake. A
child's prompt you **render and send** as fully-substituted prose; a child never opens
a template or calls render. Render with `waypoint manager render --role <role> --step
<step> --ticket {{ticket_id}}`: it reads the compiled child template and fills its
per-ticket `{{placeholders}}` from the ticket record and its board cell, printing the
body you pipe into `sessions send`. It fails on an unknown placeholder; pass `--set
key=value` for a runtime binding the ticket does not carry.

- **manager** — `loop-cycle` (loop entry), `triage`, `delegate`, `monitor`,
  `integrate`. You read these directly.
- **prd_writer** / **rfc_writer** — `write`; render and send it to the writer.
- **tech_lead** — `brief` (the whole autonomous run, sent at delegate/resume) and
  `address-review` (sent each review round); render and send them to the lead.

## Placeholders

**Static** values are baked into the compiled bodies at `manager init`, so a compiled
template never carries them as `{{…}}`: `{{project}}`, `{{trunk}}`, `{{spec_dir}}`,
`{{tickets_channel}}`, `{{org_channel}}`, `{{ticket_channel_prefix}}`,
`{{manager_session_id}}`, `{{repo_dir}}` (your own working tree, where every lead
builds); the launch args `{{tech_lead_launch}}`, `{{prd_writer_launch}}`,
`{{rfc_writer_launch}}` (a role's `--preset <name>`, or its inline `launch:` as
`--backend/--model/--permission-mode`); the policy `{{substantial_when}}`,
`{{self_decide}}`, `{{always_escalate}}`; and `{{templates_dir}}` (the compiled root,
for a template naming its siblings).

**Per-ticket** values remain in the compiled bodies and are filled at use — by you as
you read your own steps, by `manager render` for a child: `{{ticket_id}}`,
`{{ticket_title}}`, `{{ticket_body}}`, `{{priority}}`, `{{scale}}`, `{{footprint}}`,
`{{input_type}}`, `{{spec_route}}`, `{{spec_ref}}`, `{{branch}}` (`ticket/<id>` by
convention), `{{pr_url}}`, and `{{ticket_channel}}` (`{{ticket_channel_prefix}}` + the
ticket id, e.g. `ticket-42`).

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
- **One tree, strictly serial; the human owns the merge.** Every ticket builds on
  its own branch in your one shared tree, one at a time — a ticket holds the tree from
  `delegated` through a terminal state (parked `blocked`/`review_requested` included).
  Trunk advances only when the human merges the PR (or, opt-in, one they ask you to
  merge); the single tree serializes builds. Read-only PRD/RFC writers are the
  one thing that runs in parallel.
