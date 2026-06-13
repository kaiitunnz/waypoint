---
name: waypoint
description: Use when managing Waypoint coding sessions through the `waypoint` CLI, including listing sessions, reading transcripts, launching agents, discovering available models, sending messages, approvals, answering questions, interrupts, termination, doctor output, auth, or runtime reset decisions.
---

# Waypoint Sessions

Use this skill for the `waypoint` backend CLI. Prefer `waypoint sessions ...`
for a running Waypoint server; avoid the singular `waypoint session ...` flow
unless the user explicitly wants an in-process one-shot runtime.

Start by running `waypoint sessions --help` or the specific subcommand's
`--help` when uncertain; the CLI is the source of truth for the installed
version.

## Common Routing

- Read session state or transcript: see `references/sessions-read.md`; list importable threads with `waypoint backends threads <backend>`, import one with `waypoint sessions import <backend> --json ...`, or pipe just the assistant text with `waypoint sessions output <session-id> --text`.
- Launch a new coding agent: see `references/sessions-launch.md`.
- Discover the backend ids and the models/efforts each offers: `waypoint
  backends` and `waypoint models [backend]` (see `references/sessions-launch.md`).
- Send messages, interrupt running work, or terminate sessions: see
  `references/sessions-steer.md`.
- Respond to approval requests or answer a session's question: see
  `references/sessions-approvals.md`.
- Handle destructive operations such as reset or termination: see
  `references/destructive-ops.md`.
- Diagnose CLI auth/config issues: see `references/auth-config.md`.

## Guardrails

- Prefer JSON output from the CLI over inferring session state from memory.
- Confirm before terminating sessions, resetting runtime data, or taking action
  that can discard work.
- Never try to delete or terminate the personal assistant through generic
  session endpoints; it is a protected singleton.
- When reporting status, include concrete session ids and current statuses.
