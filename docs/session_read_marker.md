# Session read marker

Every session record carries `read_at`: activity at or before that instant has
been seen. A session is **unread** while `last_event_at > read_at`. Clients use
the marker to show activity the human has not looked at yet (the Done state on
the v2 sessions home).

```
unread = read_at != null && last_event_at > read_at
```

A missing or `null` `read_at` reads as read.

## Who moves the marker

The marker is driven by [session presence](telegram-notifications.md#active-session-presence):
the `POST /api/sessions/{id}/presence` lease a visible session page renews, and
the matching `DELETE`. Clients make no extra call, so opening a session in any
frontend marks it read for every device.

| Moment | Effect |
| --- | --- |
| A viewer registers or renews its lease | The marker moves to the session's latest event. |
| An event is recorded while any viewer holds a lease | The marker moves to that event, so it follows the live transcript. |
| A viewer releases its lease | The marker moves to the current time. Other viewers keep it following. |
| An event is recorded with no viewer | The marker stays put and the session becomes unread. |

The marker only moves forward. Session state frames on `/ws/sessions` carry it,
and a frame is sent when the marker flips a session from unread to read. Leases
are held in memory; after a backend restart the next renewal (every 15 seconds)
restores presence and marks the session read.

## Start values

New and existing sessions start read (`read_at = last_event_at`) and become
unread when they produce activity nobody watched.

The notes a lifecycle step records are not activity. Starting a session (launch,
import, fork, side-question fork, tmux attach) marks it read once the step
completes. Restoring or restarting one (backend startup, reattach, a launch-settings
switch, a mode, model or effort change) leaves it as it was, so a backend
restart does not turn restored sessions into Done.

## Done

The v2 sessions home shows **Done** for a session that is idle, is not waiting
on a question or approval, and is unread. Any event recorded outside a
lifecycle step moves `last_event_at`, system notes included, so Done means the
session has activity the human has not seen. The rule is one comparison with no
per-agent knowledge of what counts as a turn.
