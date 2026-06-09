# Steering Sessions

Send input:

```bash
waypoint sessions send <session-id> <text>
```

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
show <session-id>` or `waypoint sessions events <session-id> --messages N`.
