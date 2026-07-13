---
name: waypoint
description: Use when managing Waypoint coding sessions through the `waypoint` CLI, including listing sessions, reading transcripts, launching agents, discovering available models, sending messages, scheduling session launches or messages, approvals, answering questions, interrupts, termination, doctor output, auth, or runtime reset decisions.
---

# Waypoint Sessions

Use this skill for the `waypoint` backend CLI. Prefer `waypoint sessions ...`
for a running Waypoint server; avoid the singular `waypoint session ...` flow
unless the user explicitly wants an in-process one-shot runtime.

Run `waypoint help` to dump the entire CLI surface — every nested command, its
arguments, and its options — in one call (`waypoint help --json` for structured
output). `--json` is **not** a universal flag: most commands already emit JSON by
default and take no `--json`, and only a few — `help`, `board read`/`board log`,
`sessions import` — accept an explicit one. `waypoint help` is generated directly
from the command definitions, so it is ground truth for the installed version and
never drifts. Prefer it over running
`--help` at each nested level, and defer to it for exact flags rather than
trusting any flag list reproduced in this skill.

## Common Routing

- Read session state or transcript: see `references/sessions-read.md`; list importable threads with `waypoint backends threads <backend>`, import one with `waypoint sessions import <backend> --thread-id <id>` (replays the thread's prior conversation into the new session by default; pass `--no-import-history` to start empty), or pipe just the assistant text with `waypoint sessions output <session-id> --text`.
- Launch a new coding agent: see `references/sessions-launch.md`.
- Discover the backend ids and the models/efforts each offers: `waypoint
  backends` and `waypoint models [backend]` (see `references/sessions-launch.md`).
- Send messages, interrupt running work, or terminate sessions: see
  `references/sessions-steer.md`.
- Schedule a session launch or a message for later (deferred, server-side): see
  `references/scheduling.md`.
- Respond to approval requests or answer a session's question: see
  `references/sessions-approvals.md`.
- Reach the **human** — post a message the user triages in the inbox UI (ask a
  question, request an approval, or surface an FYI) and optionally block until
  they answer: see `references/inbox.md`.
- Handle destructive operations such as reset or termination: see
  `references/destructive-ops.md`.
- Diagnose CLI auth/config issues: see `references/auth-config.md`.
- Surface a file to the user — make an artifact outside a session's working
  directory visible in the UI: see `references/artifacts.md`.
- Drive the per-project ticket state machine (`waypoint manager ...`) — config,
  re-anchor, ticket transitions, the integration lease, and prompt-template
  rendering: see `references/manager.md`.

## Guardrails

- Prefer JSON output from the CLI over inferring session state from memory.
- Confirm before terminating sessions, resetting runtime data, or taking action
  that can discard work.
- Never try to delete or terminate the personal assistant through generic
  session endpoints; it is a protected singleton.
- When reporting status, include concrete session ids and current statuses.
- A file outside a session's working directory is invisible in that session's
  file explorer. If the user needs to see it, either keep it in a session cwd (they
  can open that session) or `sessions upload` it — with `--pin` for anything
  durable, since an unpinned upload is swept after the orphan TTL
  (`references/artifacts.md`).
