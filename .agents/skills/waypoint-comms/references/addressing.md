# Addressing

Messages are addressed to a target session by its **session id**.

## Discover sessions

```bash
waypoint sessions list
```

Returns JSON for every live session, including `id`, `title`, `backend`,
`status`, `cwd`, and `source`. Pick the target by:

- **Known id** — you already hold it (e.g. a child you spawned). Use it
  directly.
- **Title convention** — sessions you coordinate with are recognizable by their
  title. Children spawned via the subagents skill carry `subagent:<purpose>`;
  filter `list` output by that prefix.
- **cwd / backend** — narrow by repository path or backend when several sessions
  share a title.

Confirm the target before sending:

```bash
waypoint sessions show <session-id>
```

## Who you may address

- A session **you spawned**: address it freely.
- Any **other** session: only with explicit user confirmation — a message
  injects a turn and disrupts whatever that agent is doing.
- The **personal assistant** (`source: assistant`): do not message it for
  coordination.

## Identify yourself

Your own session id is in the environment as `WAYPOINT_SESSION_ID`:

```bash
echo "$WAYPOINT_SESSION_ID"
```

Use it as `from` / `reply-to` when you send (see `send-and-reply.md`). It is set
for claude_code and tmux sessions; if it is empty (e.g. a codex/opencode session,
where it is not yet injected), fall back to the id the user or spawning parent
gave you and carry it in your working state.
