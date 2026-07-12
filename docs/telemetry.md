# Usage telemetry dashboard

Waypoint turns the session, event, token, context, and rate-limit signals it
already records into a queryable history of AI coding-agent usage, surfaced on a
first-party **Telemetry** page (`/telemetry`). It is local-first and
privacy-preserving: collection is aggregate-only by default, nothing leaves the
host, and no prompts, model output, tool arguments, or paths are ever stored.

The page answers questions the raw session list cannot — which backend and model
consumed the most tokens this week, whether a provider limit is about to
interrupt work, how many sessions/turns/tool calls a range produced, and which
sessions are under context pressure — and lets a user drill from any aggregate
into the source facts.

## What the page shows

- **Filters:** a date range (Today / 7 days / 30 days / custom, in the host
  timezone, inclusive start / exclusive end) plus backend, resolved model,
  repository, tag, source, transport, and top-level-vs-child scope. Every
  attributable number and chart responds to the active filters.
- **Overview:** total tokens (new-work only — see [Token accounting](#token-accounting)),
  session lifecycle counts and a point-in-time active count, user/agent turn and
  tool-call counts, alerts, and a meter-coverage indicator.
- **Charts:** token usage over time / by group, a session-activity trend and a
  weekday × hour heatmap, and context/rate-limit health. All are hand-built inline
  SVG with a text-summary equivalent and non-color status indicators.
- **Drill-down:** any aggregate links to a time-filtered, paginated list of the
  underlying facts (session/turn/tool ids and timestamps — never raw text).
- **Insights:** deterministic, evidence-linked cards (see [Insights](#insights)),
  plus an optional AI summarizer.
- **Settings:** retention, coverage, the privacy statement, and a control to
  delete retained telemetry.

## Architecture

The subsystem lives in `backend/src/waypoint/telemetry/` and is backend-neutral:
generic runtime, storage, API, and frontend code never branch on backend id.

- **Fact contract** (`facts.py`) — a versioned, typed fact envelope
  (`session_lifecycle`, `turn`, `tool_call`, `context_snapshot`, `limit_snapshot`).
  Every fact carries a stable, source-owned `fact_id`, a `revision` (a newer
  revision replaces an earlier one), and denormalized session dimensions stamped
  at ingest, so filtered queries need no join. A fact with no stable id is marked
  `partial` and excluded from dedup-requiring totals.
- **Store** (`store.py`) — a single indexed `telemetry_facts` table, a normalized
  `telemetry_fact_tag` side table, and recompute-on-write daily rollups. Ingestion
  is idempotent and revision-aware; the token ledger
  (`session_token_usage_records`) is reused rather than duplicated.
- **Ingestion** (`ingest.py`) — a generic `TelemetryIngester` derives facts from
  the already-normalized event stream and session-field updates at the runtime's
  existing publish seams. Derivation only enqueues (bounded, error-swallowing —
  telemetry never blocks a turn); a runtime-owned background task drains the queue
  in yielding batches. A one-time, guarded backfill can seed facts from existing
  history so the dashboard is populated immediately — it is off by default and
  runs only when `telemetry_backfill` is enabled alongside the master switch.
- **Aggregation + API** (`aggregate.py`, endpoints in `api.py`) — range/rollup
  queries shape the `/api/telemetry/*` responses (`overview`, `tokens`,
  `activity`, `health`, `drilldown`, `insights`, `settings`, and `DELETE`). A
  `telemetry_update` WebSocket frame keeps the page fresh without polling, and
  `waypoint telemetry` exposes the overview on the CLI.

### Retention and deletion

Fact-level telemetry is retained for **90 days**, daily aggregates for **13
months** (both configurable). Pruning runs on a slow runtime-owned task.
Deleting a session immediately drops its facts and recomputes affected rollups;
deleting all telemetry from Settings removes facts, rollups, dismissals, and the
stored NL digest. Neither touches the session's separately managed transcript.
The all-telemetry delete deliberately keeps the one-time `backfill_done` marker,
so a later restart with `telemetry_backfill` on cannot recreate the erased
pre-enable history.

## Token accounting

Each backend reports token usage in its own vocabulary. Telemetry normalizes them
at each agent's parse boundary into five disjoint, non-overlapping buckets —
**fresh input**, **cache read**, **cache write**, **output**, **reasoning** — so
totals compose safely across backends without double counting (`telemetry/tokens.py`).

Cache reads are the same prior context re-sent on every turn: accumulative and
uninformative to sum, so they are **excluded from the token total** and reported
as a standalone "cached re-reads" figure. The headline total is new work only
(fresh input + cache write + output + reasoning).

Model comparison uses the **actual model at each turn**, not the session's latest
setting. The per-turn token ledger row carries the resolved model and effort in
effect for that turn; a mid-session model change is attributed to the turn that
used it.

## Provider limits

Rate-limit snapshots are grouped by **account**, not session — a 5-hour or weekly
window is shared by every session on that account (two profiles can resolve to
the same account). The grouping key is a **pseudonymous digest**; the account is
labeled by its user-chosen profile name ("Default", or e.g. "nus"), never a raw
OAuth email or org. The provider-derived account label is shown only when
`telemetry_local_labels` is enabled (an explicit opt-in). Limit data is
account-wide, so the limit card is hidden whenever a session-scoping filter is
active.

## Insights

Deterministic insight cards fire only when their evidence gates are met, and each
links to the aggregate that produced it; an inference is never presented as a
measured outcome. The MVP rules are near-limit / context-pressure warnings and
token-volume change versus the preceding equal-length range.

### Natural-language summarizer (opt-in)

An optional summarizer turns the on-screen aggregates into a short prose digest
with labeled evidence and a confidence level. It is **off by default**. When
enabled it runs a configured coding agent as a throwaway one-shot session
(via the generic `runtime.run_oneshot`, defaulting to `claude_tty`) and receives
only the numeric aggregates plus a bounded set of **redacted** drill-down rows
(session id, normalized tool name, timestamp, outcome, model) — never raw
prompts, tool arguments, or paths. A weekly digest is generated on the runtime's
maintenance task; a digest can also be regenerated on demand from the card.

## Privacy

- Aggregate-only by default. No raw user/agent text, tool inputs/results,
  filenames, paths, or secret-like values are stored.
- Account identity is pseudonymous unless the user opts into local labels.
- Nothing leaves the host: there is no external export or content capture in this
  release.
- Retention and deletion are user-controlled from Settings.

## Configuration

Telemetry is **opt-in and off by default**. With no `telemetry_enabled` setting,
a fresh install collects nothing, hides the dashboard entry point, and returns
`404` from every telemetry read/insight endpoint. To turn it on, set
`telemetry_enabled: true` in `backend/waypoint.yaml` (or
`WAYPOINT_TELEMETRY_ENABLED=true`) and **restart the backend** — settings load at
startup. Live collection begins at that point; it does not import earlier
history unless you also opt in to `telemetry_backfill` (see below).

Backend settings (defaults shown; override in `backend/waypoint.yaml` or via env):

| Setting | Env var | Default | Purpose |
|---|---|---|---|
| `telemetry_enabled` | `WAYPOINT_TELEMETRY_ENABLED` | `false` | Master switch: enables live collection and dashboard/API availability. |
| `telemetry_backfill` | `WAYPOINT_TELEMETRY_BACKFILL` | `false` | One-time import of history from sessions/ledger rows that predate activation. Only runs when the master switch is also on. |
| `telemetry_retention_days` | `WAYPOINT_TELEMETRY_RETENTION_DAYS` | `90` | Fact-level retention. |
| `telemetry_rollup_retention_months` | `WAYPOINT_TELEMETRY_ROLLUP_RETENTION_MONTHS` | `13` | Daily-rollup retention. |
| `telemetry_context_thresholds` | `WAYPOINT_TELEMETRY_CONTEXT_THRESHOLDS` | `70,90,100` | Context/limit warning thresholds. |
| `telemetry_local_labels` | `WAYPOINT_TELEMETRY_LOCAL_LABELS` | `false` | Show provider-derived account labels instead of the pseudonym. |

Environment variables override YAML. `telemetry_backfill` is a one-time
migration flag — the import is guarded by a persistent `backfill_done` marker, so
leaving it `true` is harmless (subsequent restarts are no-ops), but it is
clearest to remove it after the first enabled boot.

Two upgrade notes for deployments that ran telemetry before it became opt-in:
existing collectors must add `telemetry_enabled: true` before restarting or
collection and the dashboard silently go away (existing facts stay on disk but
become inaccessible until re-enabled). And a config that sets
`telemetry_nl.enabled: true` without `telemetry_enabled: true` now fails to boot
— the NL summarizer requires the master switch.

Disabling telemetry later stops new collection but never deletes existing facts;
use the explicit delete control (or `DELETE /api/telemetry`, which stays
available while disabled) to erase them. Deletion preserves the `backfill_done`
marker, so a later re-enable does not re-derive erased pre-enable history.

The opt-in summarizer is configured under a `telemetry_nl` block (or the matching
`WAYPOINT_TELEMETRY_NL_*` env vars): `enabled` (default `false`), `backend`,
`transport`, `model`, `account_profile`, `mode` (`managed` / `headless`),
`interval_hours` (default weekly), and an optional `preset` (a session preset id
or name that supplies backend/transport/model/permission/profile, overriding the
individual fields). See `backend/waypoint.example.yaml`.

## Extending it

A new agent plugin gets telemetry for free: the runtime derives facts generically
from the normalized event stream. The only backend-specific work is at the
plugin's parse boundary — populate the resolved model/effort on the token-ledger
record (so model-at-turn is accurate) and implement `rate_limit_account` (so
provider limits attribute to an account). See
[`coding_agent_plugins.md`](coding_agent_plugins.md).

## File-pointer reference

- [`backend/src/waypoint/telemetry/facts.py`](../backend/src/waypoint/telemetry/facts.py) — the fact contract.
- [`backend/src/waypoint/telemetry/store.py`](../backend/src/waypoint/telemetry/store.py) — tables, rollups, retention, deletion.
- [`backend/src/waypoint/telemetry/ingest.py`](../backend/src/waypoint/telemetry/ingest.py) — generic derivation + backfill.
- [`backend/src/waypoint/telemetry/tokens.py`](../backend/src/waypoint/telemetry/tokens.py) — the unified token buckets.
- [`backend/src/waypoint/telemetry/aggregate.py`](../backend/src/waypoint/telemetry/aggregate.py) — the read/query layer behind `/api/telemetry/*`.
- [`backend/src/waypoint/telemetry/insights.py`](../backend/src/waypoint/telemetry/insights.py) — deterministic insight rules.
- [`backend/src/waypoint/telemetry/summarizer.py`](../backend/src/waypoint/telemetry/summarizer.py) — the opt-in NL summarizer.
- [`frontend/src/app/telemetry/page.tsx`](../frontend/src/app/telemetry/page.tsx) — the dashboard page.
