---
name: waypoint-comms
description: Use when an agent inside a Waypoint session needs to message another live Waypoint session — hand a task to a peer, ask a question, or relay a result — by injecting input into that session and reading its reply. Direct point-to-point messaging over the `waypoint sessions` CLI.
---

# Waypoint Comms

Send a message to another live Waypoint session and read its reply, using the
`waypoint sessions` CLI. A message you send lands in the **target's input** and
starts a turn for that agent, exactly as if a user had typed it; its reply comes
back on the target's event stream.

This is **point-to-point and interrupt-driven**. It is the right tool for a
direct hand-off or question to a specific session — a parent delegating to a
child it spawned, or one peer asking another for something. It is the wrong tool
for broadcasting, for posting findings many sessions might consume, or for
messaging a session that is busy mid-task; decoupled, poll-when-ready
coordination wants a shared store, not a direct send.

## Preflight

Confirm the CLI is reachable before relying on it:

```bash
waypoint sessions list   # returns JSON => CLI is on PATH and authenticated
```

If `waypoint` is not found, try the backend venv
(`"$WAYPOINT_HOME/backend/.venv/bin/waypoint"`). If it is unreachable or
unauthenticated, stop and report — do not guess.

If your own session prompts for approval on every `waypoint sessions …` call,
coordinating gets noisy. Allowlist `Bash(waypoint sessions *)` (and `Bash(waypoint
backends)`) in this session's permissions so the comms commands run without a
prompt each time.

## Routing

- Find and address the session you want to reach: `references/addressing.md`.
- Send a framed message and read the reply: `references/send-and-reply.md`.
- Delivery rules, interrupt semantics, and ownership: `references/etiquette.md`.

## Guardrails

- A send **interrupts** the target's turn. Check `waypoint sessions show
  <id>` first and prefer `idle` / `waiting_input` targets.
- Message freely the sessions you spawned; message a session you did not create
  only with explicit user confirmation, and never the personal assistant.
- Frame every message so the receiver can tell it apart from a human
  instruction (see `references/send-and-reply.md`).
- Avoid ping-pong loops: bound the number of round-trips and stop when the
  exchange has what it needs.
- Ground every claim about a reply in `waypoint sessions events` output.
