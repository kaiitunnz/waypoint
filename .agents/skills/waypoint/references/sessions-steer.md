# Steering Sessions

Send input:

```bash
waypoint sessions send <session-id> <text>
```

A message sent from another session is held, reported as `"send": "held"` with
a `held_message_id`, while the target is in Focus or waiting on a dialog (it is
delivered once the human answers).

## Held messages

```bash
waypoint sessions held list <session-id>
waypoint sessions held cancel <session-id> --id <held-message-id>
waypoint sessions held release <session-id> --id <held-message-id>
```

Cancel your own held message when it has gone stale. Release delivers at once,
overriding Focus, so release only when the human asks. `--all` in place of
`--id` acts on every held message, including other senders' and scheduled ones.

Attach files to a message with `--attach` (upload-and-send in one step):

```bash
waypoint sessions send <session-id> "here are the files" --attach path/to/a.png --attach path/to/b.txt
```

Pre-upload files and reuse the returned IDs with `--attachment-id` (useful when
the same attachment will be sent to multiple sessions, or when upload and send
happen in separate steps):

```bash
# Upload first — prints {"attachments": [{id, filename, mime, size, kind}, ...]}
waypoint sessions upload <session-id> path/to/a.png path/to/b.txt

# Send later, referencing the IDs
waypoint sessions send <session-id> "here are the files" --attachment-id <id1> --attachment-id <id2>
```

When both `--attach` and `--attachment-id` are present on `sessions send`,
uploaded files come first in the attachment list, followed by the explicit IDs
in the order they were given.

Interrupt only when the user asks, or when a session is clearly stuck and the
user confirms:

```bash
waypoint sessions interrupt <session-id>
```

Terminate only with explicit confirmation:

```bash
waypoint sessions terminate <session-id>
```

After sending input or control signals, re-check state with `waypoint sessions
show <session-id>` or `waypoint sessions events <session-id> --messages N --compact`.
