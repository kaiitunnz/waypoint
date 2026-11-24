# Session Lifecycle

## Fork or clone

```bash
waypoint sessions fork <session-id> ["<first message>"] [--title <t>] [--tag k=v]
waypoint sessions clone <session-id> ["<first message>"] [--title <t>] [--tag k=v]
```

- `fork` starts a new session that continues a copy of the source's
  conversation (the web `/fork`). It needs a backend with `supports_fork` in
  `waypoint backends`. Forking a running session copies the conversation up to
  its in-flight message, without the unfinished reply.
- `clone` starts a fresh session with the source's launch settings (backend,
  cwd, transport, model, effort, permission mode, account profile, args, launch
  env) and no conversation (the web `/new`).
- Both are owned by the calling session (`--spawner-session-id`, defaulting to
  `$WAYPOINT_SESSION_ID`), so `sessions list --mine` and `reap --mine` cover
  them. Ownership is all the spawner contributes: the new session keeps the
  source's permission mode.
- The optional message is sent to the new session as the caller. A message the
  server cannot deliver prints the new session with `"send": "failed"` and exits
  1; take the id from that output instead of forking again.

## Revive

```bash
waypoint sessions reattach <session-id>
waypoint sessions resume <session-id>
```

`reattach` brings an `exited` or `error` session back with its conversation,
without sending it a message; `sessions send` to an exited session reattaches it
too. `resume` reattaches an exited session and, on a live terminal pane (tmux /
claude_tty), presses Enter and marks it `running` until the pane's next turn
ends. Use it for a tmux pane waiting on Enter, and `reattach` for anything
exited.

## Find exited sessions

`sessions history` searches and pages exited, unpinned sessions, newest
activity first:

```bash
waypoint sessions history                          # first 20
waypoint sessions history 'repo:waypoint status:error' --limit 50
waypoint sessions history --cursor <cursor>        # next page
```

The query matches like the dashboard search: space-separated terms are ANDed,
`OR` separates groups, and a term is a word, `"quoted phrase"`, or `/regex/`,
optionally prefixed by a field (`title:`, `repo:`, `branch:`, `dir:`, `agent:`,
`status:`). The output is `{sessions, has_more, cursor, total}`.
