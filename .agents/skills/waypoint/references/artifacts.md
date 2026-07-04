# Surfacing files to the user

A session's **file explorer** browses only that session's **working directory**.
A file the user should see that lives **outside** every session's cwd — a report
or plan written to a scratchpad / `/tmp` path — is invisible in the UI until you
surface it. The user can, however, open **any** session (including a subagent's or
worker's) and browse *its* cwd, so a file already sitting in some session's
working directory is reachable already.

## Decide before uploading

1. **Already in a session's cwd → do nothing.** The current session's project
   dir, or a subagent/worker's own cwd, is browsable by opening that session. This
   is the durable default — the file persists on disk and can be committed. Just
   tell the user which session to open.
2. **Outside every session's cwd, and the user should see it → upload it.**

   ```bash
   waypoint sessions upload "$WAYPOINT_SESSION_ID" <file> [<file> ...]
   ```

   `$WAYPOINT_SESSION_ID` is your own session id, so this lands the file in your
   session's files panel, where the user opens it. Upload sends **no message**, so
   it doesn't disturb the agent in the target session.
3. **Must persist for the long term → pin it, or keep it in a cwd.** An eager
   upload that nothing keeps is an **orphan**: it shows in the panel but is swept
   once older than `WAYPOINT_ATTACHMENT_ORPHAN_TTL_SECONDS` (default **24h**). To
   keep an uploaded file *without* sending a message, pass **`--pin`**:

   ```bash
   waypoint sessions upload --pin "$WAYPOINT_SESSION_ID" <file>
   ```

   Pinned uploads are exempt from the sweep (a file carried by a sent message —
   `sessions send --attach` — is likewise kept). Keeping the file in a session cwd
   (option 1) is durable too. Either way, uploads and cwd files vanish if the
   session is **deleted/reaped** — surface or commit before wind-down.

## Managing what's surfaced

```bash
waypoint sessions attachments list <session-id>              # what's already surfaced
waypoint sessions attachments get <session-id> <att-id>      # download one back
waypoint sessions attachments pin <session-id> <att-id>      # keep a past upload; unpin re-exposes it
waypoint sessions attachments delete <session-id> <att-id>   # (delete-all clears the session)
```

List first to avoid re-uploading a file already surfaced.

## Notes

- Uploads are capped by `WAYPOINT_MAX_UPLOAD_BYTES` (default 25 MiB); the server
  rejects a larger file with `413`.
- `sessions send` can upload and attach in one step (`--attach <file>`) or
  reference an already-uploaded id (`--attachment-id <id>`); prefer these when the
  file is meant to accompany a message rather than just appear in the panel.
