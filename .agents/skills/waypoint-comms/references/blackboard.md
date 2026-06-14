# Blackboard

A shared store other sessions read **when they are ready**. Where a direct send
injects a turn into one target, a board post writes to a **channel** that any
session can read later — nobody is interrupted. This is the tool for
broadcasting, for posting findings several sessions may consume, and for sharing
state with a busy peer.

Driven by the `waypoint board` sub-command. Confirm reachability the same way as
the rest of the CLI (`waypoint board channels` returns JSON), and allowlist
`Bash(waypoint board *)` in this session so posting and polling don't prompt.

## Two shapes per channel

A channel holds two kinds of entry, chosen by whether you pass `--key`:

- **Append-log** (no key) — each post is a new, ordered, immutable entry. Use it
  for a stream of findings, progress notes, or events many readers tail.
- **Keyed cell** (`--key K`) — the latest post for `(channel, key)` overwrites
  the previous one in place. Use it as a shared named variable: a status, a
  current decision, a hand-off pointer. Readers fetch the cell by key and get
  only the latest value.

Both live in the same channel; an append-log and several keyed cells coexist.

## Commands

```bash
# Append a finding to a channel (keyless):
waypoint board post topic:auth-refactor "Found the token bug in auth/jwt.py:88"

# Upsert a keyed cell (shared variable / latest-wins):
waypoint board post team:$PARENT "blocked on migration review" --key status

# Attach structured metadata (repeatable key=value):
waypoint board post topic:bench "throughput 1240 rps" --meta run=3 --meta host=gpu1

# Read a whole channel, append-log entries since an id, or one cell:
waypoint board read topic:auth-refactor
waypoint board read topic:auth-refactor --since 42
waypoint board read team:$PARENT --key status

# List channels with entry counts:
waypoint board channels

# Clear a channel's posts but keep the (now empty) channel:
waypoint board clear topic:auth-refactor

# Delete a channel outright, posts and all:
waypoint board delete topic:auth-refactor

# Delete a single post (log entry or cell) by its id:
waypoint board delete-entry topic:auth-refactor 42

# Edit a post's text (and replace its metadata) in place:
waypoint board edit-entry topic:auth-refactor 42 "Fixed: the bug was in jwt.py:91"
```

`clear` and `delete` act on a whole channel: `clear` empties a channel you want
to reuse (it stays listed with zero posts); `delete` removes the channel
entirely.

`delete-entry` and `edit-entry` act on one post, addressed by the `id` a
`board read` returns. `edit-entry` rewrites the post's text and replaces its
metadata (the cell key is immutable — to rename a cell, delete it and repost); it
stamps an `edited_at` the UI shows as "edited" while preserving `created_at`, so
the log keeps its order.

`post` stamps the author from `WAYPOINT_SESSION_ID` automatically (the same id as
`addressing.md`); you don't pass it. When a session is deleted, only its **keyed
cells** are pruned — keyless log posts are durable history and survive the session
row being removed. Read the log with `board log <channel>`. Durable shared state
(keyed cells) should live on a long-lived session; append-log posts are safe to
make from short subagents.

## Channel naming

Channels are free-form strings; two conventions keep them legible:

- `team:<spawner-sid>` — a coordination space for a parent and the subagents it
  spawned. The parent's session id is the natural key (it is the `spawner_session_id`
  every child carries).
- `topic:<name>` — a peer space organized by subject (`topic:auth-refactor`),
  open to any session working that subject.

Keep channel names to a single segment — **no slashes** (they break the API
path). Colons and dashes are fine.

## Read at turn boundaries

The board is pull-based: nothing tells you an entry arrived. An agent that never
looks never sees it. So build the habit explicitly —

- **Start of a turn** on shared work: use
  `board read <channel> --since <last-id>` to pick up new append-log entries.
  Track the highest id you've seen. Because keyed cells overwrite in place and
  keep their original id, also read the whole channel or the specific `--key`
  cells you depend on when cell updates matter.
- **End of a turn**: post what a peer or parent would need (a result, a status
  cell update) before you go idle.

## Board vs direct send

- **Board** when the message is not urgent, has no single recipient, should
  persist, or the target is busy — readers consume it on their own schedule.
- **Direct send** (`send-and-reply.md`) when one specific session must act now
  and you want its turn to start.
- **Both**, occasionally: post the detail to a channel, then fire a one-line
  `waypoint sessions send <id> "check board <channel>"` to wake an idle peer.
  Pull store plus a light push — use it sparingly, and mind that the send still
  injects input (see `etiquette.md`).
