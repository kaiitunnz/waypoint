# Session read marker

Every session record carries `read_at`: activity at or before that instant has
been seen. A session is **unread** while `last_event_at > read_at`. Clients use
the marker to show a finished turn the human has not looked at yet (the Done
state on the v2 sessions home).

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

The marker only moves forward. Moving it does not change `updated_at`. The
session state frames on `/ws/sessions` carry it like any other field, and it is
broadcast when it flips a session from unread to read.

## Start values

Sessions that existed before the marker was introduced start read
(`read_at = last_event_at`). A new session starts read and becomes unread when
it produces activity nobody watched.

The notes a lifecycle step records are not activity. Starting a session
(launch, import, fork, side-question fork, tmux attach) marks it read once the
step completes, and restoring one (backend startup, reattach, a launch-settings
switch) leaves it as it was: a read session stays read and one with unseen
activity stays unread. A backend restart therefore does not turn restored
sessions into Done.

## Done

The v2 sessions home shows **Done** for a session that is idle, is not waiting
on a question or approval, and is unread. Any other recorded event
moves `last_event_at`, including system notes, so an idle session can show Done
after activity that is not a finished turn.

Leases are held in memory, so a restart drops them; the next renewal (every 15
seconds) restores presence and marks the session read.
