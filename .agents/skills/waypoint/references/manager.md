# Manager Ticket State Machine

`waypoint manager ...` drives a durable, per-project ticket board and scheduler —
the backlog behind an autonomous product-owner session. State lives server-side, so
transitions and scheduler invariants are validated regardless of any client's
context. Running a session that *acts* as the manager (draining the board, spawning
leads, integrating) is the `waypoint-manager` skill; the commands here are that
skill's surface and are also how a human or a one-off script inspects or nudges the
board.

## Config and re-anchor

```bash
waypoint manager init --manifest <path/to/waypoint-manager.yaml> [--owner <sid>]   # persist machine-relevant config (idempotent)
waypoint manager deinit [--yes]                                    # clear all tickets and config
waypoint manager state [--json]                                    # whole ticket set + tree state
waypoint manager next [--tried <id>]... [--json]                   # tree state, each ticket's legal transitions, one recommended pull move
waypoint manager reconcile [--json]                                # server-derived reconcile signals (intake, dead leads, latency)
```

`init` persists the machine-relevant manifest fields (retry budgets, priority
levels, trunk, timeouts) and compiles the prompt templates: it bakes the
board/roles/scale/escalation values into the templates under `templates_dir`
(default `.waypoint/manager/templates`) and persists the render context render
needs. `--owner` (default `$WAYPOINT_SESSION_ID`)
records the manager's own session; deleting that session cascades a `deinit`.
`next` recommends at most one manager-initiated move (triage / spec / delegate);
human- and lead-driven edges are returned as legal transitions, not recommendations.
`deinit` clears state records only — spawned sessions, branches, and board channels
are reaped separately.

## Tickets

```bash
waypoint manager ticket add <title> [--id <id>] [--priority p2] [--kind] [--scale] [--footprint <glob>]... [--dep <id>]...
waypoint manager ticket show <id>
waypoint manager ticket delete <id>                               # remove one ticket's state record
waypoint manager ticket update <id> [--priority] [--scale] [--footprint] [--dep] [--spec-ref] [--lead-session-id] [--branch] [--pr-url] ...
waypoint manager ticket transition <id> --to <state> [--reason] [--scale] [--spec-ref] [--intended-lead-title] [--branch] [--pr-url] [--is-partial | --not-partial]
```

`transition` is keyed by target state; the server rejects an illegal edge, an
exhausted budget, or a violated invariant with `409`. States: `intake`, `triaged`,
`spec_pending`, `spec_review`, `ready`, `delegated`, `building`, `blocked`,
`review_requested`, `revising`, and the terminals `merged`, `deferred`, `abandoned`.
`update` edits metadata without a state change.

## Rendering prompt templates

```bash
waypoint manager render --role <role> --step <step> [--ticket <id>] [--set key=value]... [--allow-unresolved]
```

Renders a child prompt (the manager's job — a child never opens a template).
`--role`/`--step` locate the compiled template under the `templates_dir` persisted at
`init` (its static placeholders already baked); this fills the per-ticket
placeholders and prints the body to stdout (pipe it into `sessions send`). Resolves
lowest precedence first: the `--ticket` record < the ticket's board cell
(`ticket_body`, `input_type`, `spec_route`) < `--set`. Fails on an unknown
placeholder unless `--allow-unresolved`.

## Notes

- Prefer JSON output and read ids back from the response rather than assuming them.
- Run `waypoint help manager` for the exact installed flag surface rather than
  trusting the lists here.
