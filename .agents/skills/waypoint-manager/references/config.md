# Configuration

Per-project config is a version-controlled `waypoint-manager.yaml` at the repo
root (the shipped example sits beside this skill). It has two audiences:

- **Machine-relevant fields** — `trunk`, `concurrency`, `retry`, `priority`,
  `integration`, `timeouts` — are persisted server-side by `waypoint manager init
  --manifest <path>`, so `manager next`/`transition` enforce those numbers no
  matter what the manager's context believes.
- **Skill-consumed fields** — `board`, `scale`, `escalation`, and `roles` — are
  read by the manager directly for spawn config and policy; the backend neither
  reads nor needs them.

Run `waypoint manager init --manifest waypoint-manager.yaml` once at setup (and
again after editing a machine-relevant field). It is idempotent.

## Fields

- **`project`** — the project name; used in summaries and channel labels.
- **`trunk`** — the integration branch every ticket worktree is cut from and the
  sole integrator advances (`{{trunk}}` in templates).
- **`board`** — `tickets_channel` (intake + `ticket:<id>` registry cells),
  `org_channel` (human-visible summaries + the `lock:integration` cell), and
  `ticket_channel_prefix` (per-ticket channel is `<prefix><id>`, e.g. `ticket-42`).
- **`concurrency.execution_slots`** — max tickets in `{delegated, building,
  revising}`; the server rejects a transition that would exceed it. Bounds
  concurrent **compute**, not liveness — parked leads in awaiting-human states do
  not count.
- **`concurrency.max_parked_leads`** — optional cap on live-but-idle leads under
  host pressure (`null` = unbounded). Skill-enforced.
- **`retry.max_delegate_attempts`** — initial-spawn retry budget before a ticket
  goes `blocked`-awaiting-human. Server-enforced on `ready → delegated`.
- **`retry.max_lead_restarts`** — fresh-lead resumes after a lead death before
  `blocked`. Server-enforced on the lead-died self-loop. **Independent** of
  `attempts` (`references/state-machine.md`).
- **`retry.backoff_seconds`** — base backoff between re-delegations; skill-applied.
- **`priority.levels`** — ordered high-to-low (`p0` highest); a ticket's
  `--priority` must be one of these or `ticket add`/`update` rejects it.
- **`priority.tiebreak`** — `fifo`: ties break oldest-first by `created_at`.
- **`scale.substantial_when`** — the natural-language rule triage applies to label
  a ticket `substantial` (→ spec) vs `trivial` (→ direct). Skill-consumed.
- **`integration.mode`** — `pr` (GitHub PR + CI) or `local` (rebase-ff); the
  manager is the sole integrator either way. `integration.require_ci_green` gates
  the merge on green CI.
- **`timeouts.human_latency_hours`** — how long an awaiting-human ticket
  (`spec_review`/`blocked`/`review_requested`) waits before the manager escalates
  and then abandons. Measured from `awaiting_since`, which the server stamps on
  entry and clears on exit, so only genuine human waits count. Skill-enforced.
- **`timeouts.lock_ttl_seconds`** — integration-lease TTL; the default for
  `manager lock acquire`/`steal` when `--ttl-seconds` is omitted, and the window
  after which a dead owner's lease can be stolen.
- **`escalation.self_decide` / `always_escalate`** — the policy the manager applies
  to decide whether a blocker is something it settles itself or routes to the
  human inbox. Skill-consumed.

## Roles: preset OR inline launch

Each role under `roles` is configured **one of two ways**, and the choice is the
user's:

- **`preset: <name>`** — reference an existing DB-backed session preset (backend,
  transport, model, effort, permission_mode, account_profile, launch_env, args,
  tags). At setup the manager **verifies** it exists (`waypoint presets show
  <name>`) and **halts and flags** the user if it is missing — it never runs
  `presets create`. Inspect the preset's model and permission posture rather than
  trusting the name.
- **`launch: { backend, model, permission_mode, … }`** — an inline launch block.
  The manager passes these as explicit `sessions start` flags. A role configured
  inline is a **deliberate config choice**, not a fallback for a missing preset.

`--cwd` and `--title` are always per-launch (a preset deliberately excludes them),
so the manager supplies `--cwd <repo-root>`, `--worktree`/`--worktree-base`, the
`subagent:ticket-<id>:<role>` title, and `--spawner-session-id` on top of either
config path; an explicit flag overrides a preset value.

## The manager's auto-approving posture (Security)

The `manager` role **runs unattended**, so it must use an **auto-approving
permission posture** — otherwise its own `sessions start` and `gh pr merge` tool
calls block forever on an absent approver. The per-backend auto-approve mode:

| Backend | Auto-approve mode |
|---|---|
| `claude_code` | `dontAsk` |
| `codex` | `full_access` |
| `opencode` | `allow` |

This blast radius is bounded by: the human-owned merge gate on every PR (nothing
reaches trunk without a human decision), per-role postures for the agents the
manager spawns (each explicit in its `preset`/`launch` block — set them
auto-approving too, since leads also run unattended), worktree isolation, and the
ownership rule that a session may act only on what it spawned. Set each role's
model id **verbatim** from `waypoint models <backend>` — a wrong id spawns fine and
dies on turn 1 — and confirm the permission mode from `waypoint backends`. When a
posture or model is ambiguous for unattended work, ask the user rather than
guessing.

## The manager runs on claude_tty (durability)

Launch the `manager` role on `claude_tty` — claude_code's default transport — not
a structured one. Its agent process lives under a persistent pty, so the manager's
in-flight turn survives a Waypoint backend restart (the process keeps running and
the backend reattaches on boot) rather than being interrupted. The loop treats a
`waypoint` CLI connection error during that window as transient, not a work failure
(`references/loop.md`). The example manifest's `backend: claude_code` with no
explicit `transport` already resolves to `claude_tty`.

## Templates

Each role's `templates:` path points at a directory of per-step Markdown prompts
(`templates/<role>/<step>.md`) with `{{placeholders}}` the manager substitutes
before `sessions send`. The shipped examples are self-contained per the
portability principle: PRD/RFC authoring, PR creation, rebasing, and
review-addressing are **inlined as prose**, not personal-skill calls. See
`templates/` and the SKILL overview.

## Manifest-derived placeholders

A template **never hardcodes** a preset, model, or channel name — every
manifest-owned value is a `{{placeholder}}` the manager expands from the loaded
manifest, so changing a preset or a channel prefix flows through without editing a
template. Besides the ticket-scoped placeholders (`{{ticket_id}}`,
`{{ticket_title}}`, `{{ticket_body}}`, `{{priority}}`, `{{scale}}`, `{{footprint}}`,
`{{input_type}}`, `{{spec_route}}`, `{{spec_ref}}`, `{{branch}}`,
`{{worktree_path}}`, `{{pr_url}}`) the manager fills per ticket, these come
straight from the manifest:

- `{{trunk}}` — `trunk`.
- `{{tickets_channel}}` / `{{org_channel}}` — `board.tickets_channel` /
  `board.org_channel`.
- `{{ticket_channel}}` — the current ticket's channel: `board.ticket_channel_prefix`
  + the ticket id (e.g. `ticket-42`). `{{ticket_channel_prefix}}` is the bare prefix,
  for referring to *other* in-flight tickets' channels (`{{ticket_channel_prefix}}<id>`).
- `{{tech_lead_launch}}` / `{{writer_launch}}` — the launch args for the role being
  spawned, expanded from that role's manifest entry: `--preset <name>` for a
  `preset:` role, or `--backend … --model … --permission-mode …` for an inline
  `launch:` role. `{{writer_launch}}` resolves to the matching writer
  (`roles.prd_writer` or `roles.rfc_writer`) for the ticket's spec route. The
  per-launch flags (`--cwd` / `--title` / `--worktree` / `--spawner-session-id`) are
  always added on top, and an explicit flag overrides a preset value.
