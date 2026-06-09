# Approvals

Use approvals when a coding backend is waiting for a plan or tool decision.

```bash
waypoint sessions approve <session-id> <decision>
waypoint sessions approve <session-id> <decision> --approval-id <id>
waypoint sessions approve <session-id> <decision> --text <message>
```

Read the pending request from `waypoint sessions events <session-id>` before
approving. If multiple approvals are pending, pass `--approval-id` when the
transcript exposes one.

Do not approve destructive, privileged, or unclear requests without user
confirmation.
