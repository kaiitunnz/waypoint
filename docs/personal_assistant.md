# Personal assistant

The personal assistant is a single, long-lived conversation thread, separate
from the coding sessions you launch for tasks. It runs an ordinary coding
backend but is created and kept alive by the runtime, reachable from a
dedicated page in the app. Use it to ask about the host machine, to ground
questions in your running Waypoint sessions, and to spin up or steer those
sessions on your behalf.

It is not a new backend — it reuses the existing plugin, runtime, storage, and
streaming machinery. Its only distinguishing traits are a dedicated session
`source`, deletion/termination protection, and the out-of-band tooling
described below.

## Enabling it

Add an `assistant` block to `waypoint.yaml` (see `backend/waypoint.example.yaml`):

```yaml
assistant:
  enabled: true                       # omit the whole block to disable
  backend: claude_code                # defaults to `default_backend`
  model: opus                         # must exist in the backend's catalogue
  effort: high                        # ignored by backends without an effort knob
  permission_mode: bypassPermissions  # see the security note below
```

`model`, `effort`, and `permission_mode` seed the thread; they can be changed
live from the assistant page, so treat them as defaults rather than a lockdown.
The block is enabled by default when present — set `enabled: false` to keep the
config but turn the assistant off.

The assistant always runs in a managed working directory (`<data_dir>/assistant`),
so it has no `cwd` setting. Its charter is written there as `AGENTS.md` /
`CLAUDE.md` and loaded silently by the backend as project context — there is no
visible bootstrap message in the transcript. The working directory is only a
scratch cwd; shell access reaches the whole host, so host inspection is
unaffected.

## Lifecycle

- On startup the runtime reuses any still-alive assistant thread that lives in
  the managed workspace, **regardless of its backend**. The live thread is the
  source of truth, so a backend chosen from the UI survives a redeploy. The
  `assistant` block in `waypoint.yaml` only seeds the *first* creation; editing
  `assistant.backend` later has no effect while a thread exists — clear the
  context to re-seed.
- If no live thread exists (first boot, or the previous one exited), a fresh
  thread is created from the `waypoint.yaml` defaults.
- The assistant cannot be **deleted**, and the generic session terminate/delete
  endpoints reject it (it is a protected singleton).

### Controls (assistant page)

The settings popover next to the composer exposes the assistant's lifecycle:

- **Switch backend** — rebuild the assistant on a different coding agent. The
  conversation cannot migrate between backends, so this starts a fresh thread
  at the new backend's default model/effort/permission mode.
- **Clear context** — start a fresh thread on the same backend.
- **Terminate / Reattach** — stop the thread (keeping it the pinned singleton)
  and later revive the same conversation. Reattach is offered only when the
  backend can resume after exit.
- **Model / effort / permission mode** — applied live to the running thread; no
  context is lost.

Switching backend and clearing context discard the conversation: the previous
thread is **demoted to an ordinary stopped session** (its transcript is
preserved and it becomes deletable), never destroyed.

A terminated assistant survives reattach only within the running deployment; a
redeploy cannot reattach an exited thread, so it demotes that thread to a normal
stopped session and creates a fresh assistant.

Both the Waypoint session id and the backend-native thread id (e.g. the value
for `claude --resume`) are surfaced on the assistant page and via `/api/me`, so
the thread can be recovered outside the app if needed.

## Managing sessions: the `waypoint sessions` CLI

The assistant manages your coding sessions through the `waypoint` CLI, which
talks to the running server over HTTP (distinct from the in-process `waypoint
session` commands):

```
waypoint sessions list
waypoint sessions show <id>
waypoint sessions events <id> [--messages N] [--before-sequence S]
waypoint sessions start --backend <id> --cwd <path> [--model M] [--effort E]
waypoint sessions send <id> <text>
waypoint sessions interrupt|terminate <id>
waypoint sessions approve <id> <decision> [--text T] [--approval-id A]
```

Output is JSON. The same `WaypointClient` (in `waypoint.client`) backs the CLI
and can be imported directly.

### Authentication

`waypoint serve` writes a token to `<data_dir>/cli-token` (mode `0600`) on
startup. The CLI resolves auth in order: the `WAYPOINT_TOKEN` env var, that
token file, then a password login (`WAYPOINT_PASSWORD` or the configured
password), caching the result back to the file. On the same host the assistant
needs no secret — it reads the token file.

## Security

The assistant is high-privilege: it has shell access to the host (to answer
environment questions) and can create, message, and terminate other agent
sessions. Run it in an autonomous `permission_mode` so it isn't blocked on
per-action approvals — but understand the blast radius before exposing the
service beyond localhost. The `cli-token` file grants full API access,
equivalent to the password; treat it as a secret.
