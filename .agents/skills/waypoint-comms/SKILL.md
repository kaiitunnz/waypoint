---
name: waypoint-comms
description: Use when an agent inside a Waypoint session needs to coordinate with other live Waypoint sessions — hand a task to a peer, ask a question, relay a result, broadcast a finding, or share state. Two channels — direct point-to-point messaging and a shared blackboard — over the `waypoint` CLI.
---

# Waypoint Comms

Coordinate with other live Waypoint sessions over the `waypoint` CLI. There are
two mechanisms, and picking the right one matters:

- **Direct send** (`waypoint sessions send`) — a message lands in the **target's
  input** and starts a turn for that agent, exactly as if a user had typed it;
  its reply comes back on the target's event stream. **Point-to-point and
  input-injecting.** The right tool for a direct hand-off or question to a
  specific session — a parent delegating to a child it spawned, or one peer
  asking another for something.

- **Blackboard** (`waypoint board`) — post to a shared **channel** that any
  session reads when it is ready. **n:m and poll-when-ready; nobody is
  interrupted.** The right tool for broadcasting, for posting findings many
  sessions might consume, or for sharing state with a session that is busy
  mid-task.

Rule of thumb: one specific session must act now → direct send. Otherwise (no
single recipient, not urgent, should persist, or the target is busy) → board.

Both reach only **other sessions**. To ask the **human** something and
optionally block on their answer, use the inbox (the `waypoint` skill's
`references/inbox.md`), not a send or board post.

## Preflight

Confirm the CLI is reachable before relying on it:

```bash
waypoint sessions list   # returns JSON => CLI is on PATH and authenticated
```

If `waypoint` is not found, try the backend venv
(`"$WAYPOINT_HOME/backend/.venv/bin/waypoint"`). If it is unreachable or
unauthenticated, stop and report — do not guess.

If your own session prompts for approval on every `waypoint …` call, coordinating
gets noisy. Allowlist `Bash(waypoint sessions *)`, `Bash(waypoint board *)`, and
`Bash(waypoint backends)` in this session's permissions so the comms commands run
without a prompt each time.

## Routing

- Find and address the session you want to reach: `references/addressing.md`.
- **Direct send** — frame a message and read the reply: `references/send-and-reply.md`.
- **Blackboard** — post to and read shared channels: `references/blackboard.md`.
- Delivery rules, interrupt semantics, and ownership: `references/etiquette.md`.

## Guardrails

- A **send injects input** into the target and may disrupt or interleave with an
  active turn. Check `waypoint sessions show <id>` first and prefer `idle` /
  `waiting_input` targets. A **board post interrupts no one** — prefer it when
  the message can wait.
- Message freely the sessions you spawned; message a session you did not create
  only with explicit user confirmation, and never the personal assistant. Any
  session may read and post to any board channel.
- Frame every direct message so the receiver can tell it apart from a human
  instruction (see `references/send-and-reply.md`).
- Avoid ping-pong loops: bound the number of round-trips and stop when the
  exchange has what it needs.
- The board is pull-based — read your channels at turn boundaries, or an entry
  sits unseen (see `references/blackboard.md`).
- Ground every claim about a reply in `waypoint sessions events` output, and
  every claim about shared state in `waypoint board read` output.
