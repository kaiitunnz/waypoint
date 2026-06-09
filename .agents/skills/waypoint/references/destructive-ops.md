# Destructive Operations

Destructive or irreversible operations require confirmation:

- `waypoint sessions terminate <session-id>`
- `waypoint reset --yes`
- deleting files, overwriting user work, or discarding transcripts

`waypoint reset` wipes runtime data such as sessions, events, tokens,
schedules, and logs while leaving config untouched. Run without `--yes` first
for a dry-run list of what would be removed.

The personal assistant is protected from generic session termination/deletion.
Use the assistant page lifecycle controls for assistant-specific reset,
terminate, and reattach behavior.
