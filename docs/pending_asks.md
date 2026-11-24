# Pending asks

The pending-asks feed lists every request that is waiting on the human across all
live sessions: `AskUserQuestion` questions, tool approvals, and plan approvals.
Each item carries what an inline answer needs; answers use the per-session
endpoints.

Plugin obligations are in [`coding_agent_plugins.md`](coding_agent_plugins.md).

## API

`GET /api/pending-asks` returns a snapshot:

```json
{
  "epoch": "9f3c01ab",
  "revision": 12,
  "items": [ { "id": "question:s1:toolu_01", "kind": "question", "...": "..." } ]
}
```

The global `/ws/sessions` stream sends the same snapshot as a
`{"type": "pending_asks", "payload": <snapshot>}` frame right after its initial
`session_list_update`, and again whenever the list changes. Frames are complete
snapshots. A client applies one when its `epoch` differs from the held one (the
backend restarted) or its `revision` is higher; `revision` increases whenever
the item list changes.

Items are ordered by `created_at`, oldest first, then by `session_id` and
`sequence`.

## Items

| Field | Meaning |
| --- | --- |
| `id` | `"<kind>:<session_id>:<request id>"`, stable for the life of the request |
| `kind` | `question`, `approval`, or `plan_approval` |
| `session_id`, `session_title`, `session_backend`, `spawner_session_id` | The asking session; `spawner_session_id` marks a subagent session |
| `created_at`, `sequence` | Timestamp and sequence of the request's event |
| `availability` | `actionable`, `starting` (asked, not yet able to take a reply), or `unavailable` (not reachable now, may recover) |
| `title`, `body` | Headline and body text for a list row; `body` is null when it would repeat `title` |

A `question` item carries a `question` payload, an `approval` item an `approval`
payload, and a `plan_approval` item a `plan` payload; the other two payload
fields are omitted.

**`question`** — `{tool_use_id, questions: [{question, header, multi_select, options: [{label, description, recommended}]}]}`.
`label` is the answer value, verbatim. `recommended` is true when the label ends
with "(Recommended)", ignoring case and surrounding whitespace. Answer with
`POST /api/sessions/{id}/answer-question` (`tool_use_id`, `answer`, `answers`) or
decline with `.../cancel-question`.

**`approval`** — `{approval_id, tool_name, tool_input, tool_input_truncated, decisions, supports_note}`.
`decisions` is the agent's approval vocabulary and `supports_note` says whether
a note may accompany the decision. Answer with
`POST /api/sessions/{id}/approve` (`decision`, `text`, `approval_id`).

**`plan`** (on `plan_approval` items) — `{plan_id, text, decisions, supports_note, resolve_via}`.
`resolve_via` names the endpoint: `approve` answers through
`POST /api/sessions/{id}/approve` with `approval_id` set to `plan_id`;
`approve_plan` answers through `POST /api/sessions/{id}/approve-plan` with
`plan_item_id` set to `plan_id`. An approval request whose plan text is empty is
listed as a plain `approval`.

## Bounded payloads

Free text is bounded. In an `approval`, each
string in `tool_input` longer than 2,000 characters is cut to 2,000 characters
plus `…` and `tool_input_truncated` is true. An item's `title` and `body` are cut
the same way. The full input stays in the session transcript. Plan text and
question prompts are never cut.

## What stays listed

An item is listed while its request is open and still answerable:

- A question stays until a paired result, an answer, a cancellation, or a
  closure note resolves it. A question the agent cannot reach now is
  `unavailable`; one the agent has ended is dropped.
- An approval stays until a resolution note or an invalidation resolves it, and
  only while the transport still holds that request. A request left open in the
  transcript after a terminate or a restart is not listed.
- A plan reported by the agent stays until a decision is recorded or a newer
  plan replaces it.
- Sessions that have exited and throwaway telemetry sessions contribute nothing.
  Approvals and plans also need a structured transport, and a session in an
  error state contributes its questions only.
