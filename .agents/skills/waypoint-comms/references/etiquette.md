# Etiquette and Delivery Semantics

Direct messaging injects input into the target session, which **starts a turn**
for that agent. Treat it accordingly.

## A send interrupts

`waypoint sessions send` flips the target to `running` and hands the text to its
backend. There is no "deliver when free" queue you can rely on — sending to a
session that is mid-turn has backend-dependent behavior (the input may interleave
or be picked up only after the current turn). So:

- Check the target first: `waypoint sessions show <id>`.
- Prefer targets in `idle` or `waiting_input`.
- If the target is `running`, wait for it to settle unless the interruption is
  intentional and the user (or task) wants it.

## Ownership

- **Sessions you spawned**: message, poll, and read them freely.
- **Sessions you did not create**: message only with explicit user confirmation.
  A message disrupts that agent's work and may confuse a user who is steering it.
- **The personal assistant**: never message it for coordination.

## Keep exchanges bounded and legible

- Frame every message (`[wp-msg ...]`, see `send-and-reply.md`) so a human
  reading either transcript can see it was agent-to-agent traffic.
- Decide the number of round-trips before you start; stop when the exchange has
  what it needs. Two agents that each reply to the other will ping-pong forever.
- Keep each message self-contained — the receiver does not share your context.
  State the request, the inputs, and exactly what you want back.

## When direct messaging is the wrong tool

Reach for the **blackboard** (`references/blackboard.md`) instead when:

- **Broadcasting** to many sessions, or posting a finding others may or may not
  consume → post to a channel, not N direct sends.
- **The target is busy** and the message is informational, not urgent → post it
  where the target reads when ready, rather than interrupting its turn.
- **No specific recipient** ("whoever picks this up") → direct addressing does
  not apply; a `topic:` channel does.
