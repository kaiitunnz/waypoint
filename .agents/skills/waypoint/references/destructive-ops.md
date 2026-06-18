# Destructive Operations

Destructive or irreversible operations require confirmation:

- `waypoint sessions terminate <session-id>`
- `waypoint sessions delete <session-id>` (and `reap`), especially with
  `--prune-branches`
- `waypoint reset --yes`
- changing another session's permission mode
  (`waypoint sessions set-permission-mode` / `mode`)
- deleting files, overwriting user work, or discarding transcripts

`waypoint reset` wipes runtime data such as sessions, events, tokens,
schedules, and logs while leaving config untouched. Run without `--yes` first
for a dry-run list of what would be removed.

The personal assistant is protected from generic session termination/deletion.
Use the assistant page lifecycle controls for assistant-specific reset,
terminate, and reattach behavior.

## Acting on sessions and branches you do not own

Two of these are easy to reach for and high-blast-radius, so get explicit user
authorization first:

- **Destroying a session (or its branches) you did not create.** Deleting,
  terminating, or reaping a *peer* session ends a workload that is not yours,
  and `--prune-branches` additionally force-deletes that session's git branches
  — work the user never asked you to discard. Only do this for sessions you
  spawned yourself (`waypoint sessions list --mine`), or with the user's
  explicit say-so for any other session.
- **Widening a live session's permission posture.** Switching a running session
  to an auto-approving or unrestricted mode (e.g. `bypassPermissions` /
  `dontAsk` on claude_code, `full_access` on codex, `allow` on opencode) removes
  approval gates the user was relying on. Do not widen a session — your own or a
  peer's — beyond the posture the user authorized; ask first.

These hold regardless of the permission mode you happen to be running in and
regardless of which coding agent backs the session. Your harness's own safety
layer may also refuse these actions and report a denial; treat that as a prompt
to surface the decision to the user, not a syntax problem to work around.
