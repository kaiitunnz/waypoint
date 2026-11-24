# Session Lifecycle

## Fork or clone

```bash
waypoint sessions fork <session-id> ["<first message>"] [--title <t>] [--tag k=v]
waypoint sessions clone <session-id> ["<first message>"] [--title <t>] [--tag k=v]
```

- `fork` starts a new session that continues a copy of the source's
  conversation (the web `/fork`); it needs `supports_fork` in
  `waypoint backends`. Forking a running session copies the conversation up to
  its in-flight message, without the unfinished reply.
- `clone` starts a fresh session with the source's launch settings and no
  conversation (the web `/new`).
- Both are owned by the calling session, so `sessions list --mine` and
  `reap --mine` cover them. The spawner sets ownership only; the permission mode
  comes from the source.
- If the optional first message can't be delivered, the output still carries the
  new session; take its id rather than forking again.

## Revive

```bash
waypoint sessions reattach <session-id>
waypoint sessions resume <session-id>
```

`reattach` revives an `exited` or `error` session with its conversation, without
sending a message (`sessions send` to an exited session also reattaches it); on
a live session it restarts the agent and ends any running turn. `resume` presses
Enter on a live tmux / claude_tty pane. When the Enter submits a message typed
into a Claude composer, the session goes `running` and `sessions wait` tracks
the turn; otherwise the status stays as it was. A generic tmux pane of another
agent keeps its pane-output status. While a dialog is open, `resume` fails
(HTTP 409, exit 1). Answer an approval with `sessions approve` or a plan with
`approve-plan`. A claude_tty session accepts its folder-trust prompt by itself
within a few seconds, so retry then; on a generic tmux pane the human answers
it in the terminal. Codex and OpenCode panes can't be read for dialogs, so
resume presses Enter on them regardless.

## Find exited sessions

`sessions history` searches and pages exited, unpinned sessions, newest
activity first; `QUERY` uses the dashboard search syntax
(`waypoint sessions history --help`):

```bash
waypoint sessions history 'repo:waypoint status:error' --limit 50
waypoint sessions history --cursor <cursor>
```
