# Send and Reply

The `reply-to` field is the contract that decides how the answer comes back.
Set it deliberately and there is exactly one reply path — no double delivery.

- **`reply-to` present** → the receiver **pushes** its answer to that id. Use
  this for async peers, when you do not want to sit and poll.
- **`reply-to` omitted** → the receiver does **not** push; you **pull** the
  answer off its stream when you are ready. Use this when you are blocked
  waiting on the result anyway (e.g. a parent waiting on a child).

Pick one. Do not both set `reply-to` and pull — that is the double-reply that
makes exchanges confusing.

## Frame the message

A send lands in the target's input and is read as a turn, indistinguishable from
a human instruction unless you mark it. Prefix every message with a header line:

```
[wp-msg from=<your-sid> reply-to=<your-sid>] <body>
```

- `from` — your session id, for attribution.
- `reply-to` — where to push the answer. Include it to get a push reply; omit it
  (drop the token entirely) to be pulled instead.

Send it:

```bash
# Push reply wanted — include reply-to:
waypoint sessions send <target-id> "[wp-msg from=$ME reply-to=$ME] Run the backend test suite in /home/me/proj and reply with pass/fail counts."

# Pull instead — omit reply-to:
waypoint sessions send <target-id> "[wp-msg from=$ME] Run the backend test suite in /home/me/proj and report pass/fail counts."
```

## Pull: read the target's stream (you omitted `reply-to`)

Poll the target to a settled status, then read its `agent_output`:

```bash
waypoint sessions show <target-id>     # poll until idle / waiting_input / exited
waypoint sessions events <target-id> --messages 20
```

Filter events for `kind == "agent_output"` to get the reply; skip
`system_note`, `tool_call`, and `tool_result`. (For sessions on the `tmux`
(Terminal) transport, kinds are heuristic — also check `raw_terminal_chunk`.)
This path does not disturb your
own turn: you choose when to look.

## Push: the answer arrives in your input (you set `reply-to`)

The receiver sends a framed reply to your `reply-to` id, which lands as input on
your next turn. You do not poll the other session — just read the framed message
when it arrives. Note this **interrupts** whatever you were doing, so only set
`reply-to` when you are ready to be interrupted by the answer.

## Receiving a `wp-msg`

When a turn's input starts with `[wp-msg from=… …]`, treat it as an inter-agent
message, not a human instruction:

1. Do the requested work.
2. **Honor `reply-to`**: if the header carried a `reply-to` id, push your answer
   there as a framed `[wp-msg from=<you> reply-to=<you-if-you-need-a-response>]`
   send. If there was **no** `reply-to`, do **not** push — just answer in your
   own turn; the sender will pull it from your stream.
3. Only include a `reply-to` in your reply if you genuinely need a response back;
   otherwise omit it so the exchange terminates.

## Bound the exchange

Decide up front how many round-trips the task needs and stop when you have the
answer. Do not bounce framed messages back and forth indefinitely — two agents
that each reply with a `reply-to` set will ping-pong forever.
