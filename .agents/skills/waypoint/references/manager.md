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
waypoint manager deinit [--yes]                                    # clear all tickets, config, and the lease
waypoint manager state [--json]                                    # whole ticket set + slot usage + integration lease
waypoint manager next [--tried <id>]... [--json]                   # slots, each ticket's legal transitions, one recommended pull move
```

`init` persists only the machine-relevant manifest fields (retry budgets, priority
levels, trunk, timeouts); the board/roles/scale/escalation fields are consumed by
the skill, not the server. `--owner` (default `$WAYPOINT_SESSION_ID`)
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
`review_requested`, `revising`, `merging`, and the terminals `merged`, `deferred`,
`abandoned`. `update` edits metadata without a state change.

## Integration lease

```bash
waypoint manager lock acquire --owner <sid> [--ttl-seconds N]      # $WAYPOINT_SESSION_ID by default
waypoint manager lock release --owner <sid>
waypoint manager lock steal   --owner <sid> [--ttl-seconds N]      # only after the current lease's TTL expires
```

A single implicit `integration` lease serializes trunk advancement; `acquire` fails
`409` if a live owner holds it, and `steal` succeeds only past the TTL.

## Rendering prompt templates

```bash
waypoint manager render <template-file> [--manifest <path>] [--ticket <id>] [--set key=value]... [--allow-unresolved]
```

Reads a prompt template and substitutes its `{{placeholders}}`, printing the body to
stdout (pipe it into `sessions send`). Resolves lowest precedence first: env
(`repo_dir`, `manager_session_id`) < `--manifest` (project, trunk, channels) < the `--ticket`
record < the ticket's board cell (`ticket_body`, `input_type`, `spec_route`) <
`--set`. `--manifest` defaults to `$WAYPOINT_MANAGER_MANIFEST`. Fails on an unknown
placeholder unless `--allow-unresolved`. Substitution runs CLI-side over the manifest
file and existing endpoints; the server has no knowledge of templates or placeholders.

## Notes

- Prefer JSON output and read ids back from the response rather than assuming them.
- Run `waypoint help manager` for the exact installed flag surface rather than
  trusting the lists here.
