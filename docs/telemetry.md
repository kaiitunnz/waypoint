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
- **Instance health & capacity:** the local deployment's managed-storage
  footprint, maintenance signals, and evidence-linked maintenance
  recommendations (see [Instance health and capacity](#instance-health-and-capacity)).
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

#### Regeneration lifecycle

A regeneration is **observable across reloads and clients**, not just in the tab
that clicked. The runtime owns a single generation status — `idle`, `generating`,
or `failed` — persisted in a `telemetry_meta` marker distinct from the digest
slot so status transitions never risk the digest's integrity. `GET
/api/telemetry/nl-insight` returns this status in a `generation` field alongside
the digest, so a fresh page load or a second tab renders "Regenerating…" (or a
failed state) without having initiated the run.

Generation runs on a **detached, single-flight** runtime task rather than inline
in the request. `POST /api/telemetry/nl-insight` records the `generating` marker,
starts the task, and returns `202 Accepted` immediately with the generation id —
it no longer holds the connection open for the summarizer's full duration, and a
failure is no longer a `409` (input-validation errors are still a synchronous
`4xx`). An in-process flag is the single-flight authority (an await-free
check-and-set on the one event loop; the persisted marker is derived state): a
concurrent trigger whose range/filters **match** the active run coalesces onto
it, and one over a **different** range/filters is rejected-with-reason
(`requested_range_differs`) rather than silently writing the wrong range's digest.
The cadence maintenance tick shares the same guard.

Start and settle transitions are pushed to every client on a dedicated,
un-debounced `nl_insight_status` WebSocket frame (the generic `telemetry_update`
frame only fires on completion and is debounced), so the card updates live: on
success it shows the new digest, on failure a coarse error with a retry
affordance while the prior digest is left intact. The settle is generation-id
guarded, so a run superseded by a delete (which cancels the task from the event
loop before clearing the digest + marker) cannot re-write a stale status. On
startup any `generating` marker left by a dead process is reconciled to
`failed("interrupted by restart")` before the app serves requests, so no client
sees a permanent spinner.

The digest also carries **instance health & capacity** bullets. Those are
generated by a separate, prose-free call: the model receives only the path-free
instance aggregate and a fixed menu of claim templates + allowlisted evidence
ids, and returns only `{template_id, evidence_ids}` selections. The server
validates each selection and renders the bullet text and numbers itself, so the
model never supplies an instance number, route, or free-form claim. A
recommendation template is only offered when its matching deterministic insight
fired, so the summarizer can explain a maintenance condition but never invent
one.

## Instance health and capacity

A companion section on `/telemetry` reports the local Waypoint deployment's
managed-data footprint and safe-to-inspect maintenance signals. It observes and
guides; it never deletes, vacuums, or trims anything — `waypoint maintenance`
remains the execution surface.

**Canonical accounting.** The top-level footprint total is the sum of six
mutually-exclusive categories, in this fixed order (also the hard-link dedup
precedence):

| Category | Includes |
|---|---|
| Database | The main SQLite database file |
| SQLite companions | The `-wal` and `-shm` sidecars |
| Live sessions | Session directories with a matching stored session id |
| Orphan sessions | Session directories with no matching stored session id |
| Attachments | The managed attachment/artifact directory tree |
| Unclassified | Non-symlink direct entries under the data root not claimed above |

Structured-log (`events.jsonl`) sizes and redundant-log candidate sizes are
**overlays** on the tree that owns them — reported to explain a portion of the
total, never added again as a separate category. Bytes are logical regular-file
`st_size`; a hard-linked inode is counted once in the earliest category above;
symlinks are never traversed. Each measured value carries a data-quality state
(`complete` / `partial` / `unavailable`), and a category that exceeds its budget
or fails to read degrades to partial without hiding the others.

**Safety.** Database facts (table/event counts, and `page_size` / `page_count`
/ `freelist_count` reclaimability) come from a dedicated `mode=ro`, no-migration
connection with a 250 ms busy timeout and a 250 ms per-query execution budget.
Each filesystem tree has a 50,000-entry / 2-second budget. Collection runs off
the request path: a `GET` serves a cached snapshot (revalidated in the
background past a five-minute freshness window, shown unavailable past 24 hours),
and only the explicit **Refresh** control recomputes synchronously.

**Maintenance insights** fire only on measured evidence and each carries a
safety note pointing at the relevant `waypoint maintenance` command:

- **Orphan data** — at least one orphaned session directory is measured; review
  `waypoint maintenance prune-orphans` (dry-run by default).
- **Redundant logs** — inactive `events.jsonl` logs of non-`RUNNING` sessions
  can be cleared with `waypoint maintenance clear-structured-logs` (running
  sessions excluded); bytes already inside an orphan directory defer to the
  orphan-data card.
- **Database vacuum** — free pages establish both ≥ 100 MiB and ≥ 20% free; a
  `VACUUM` is an operator decision, not a guaranteed filesystem saving. WAL size
  is displayed but never triggers a recommendation.

**History.** After telemetry is enabled the runtime keeps one aggregate point
per host-local calendar day (keyed on the date plus the numeric UTC offset,
first complete point wins, a partial point may be replaced), retained for
`telemetry_rollup_retention_months`. Enabling telemetry writes the first point
immediately; later points land at the first maintenance tick on or after 00:05
local. Deleting telemetry removes the daily points and the cached snapshot, but
never touches operational data. The dashboard measures come from this shared
read-only snapshot definition; `waypoint maintenance stats` currently reports a
narrower set (database/WAL sizes, table/event counts, orphan-directory count)
and may adopt the shared definition later.

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
| `telemetry_instance_fs_signals` | `WAYPOINT_TELEMETRY_INSTANCE_FS_SIGNALS` | `false` | Report host free space for the volume holding the data directory (free/total only; never host-wide). |

Environment variables override YAML. `telemetry_backfill` is a one-time
migration flag — the import is guarded by a persistent `backfill_done` marker, so
leaving it `true` is harmless (subsequent restarts are no-ops), but it is
clearest to remove it after the first enabled boot.

To re-run the import on demand — after enabling telemetry without
`telemetry_backfill`, or to recover history you deleted and later decided you
want — use `waypoint maintenance rebuild-telemetry`. It re-derives facts from
the existing `sessions`/`events`/token-ledger rows and rebuilds the rollups via
the same code path as the boot backfill. Run it **with the backend stopped**
(`waypointctl stop`); the command refuses when it detects a live backend on the
configured host/port or a concurrent writer holding the database. Because the
`backfill_done` marker is one-shot and preserved across deletion, a database
that already backfilled requires `--force` plus a confirmation that names the
effect — re-deriving history includes any previously deleted or pre-enablement
activity. Add `--yes` to skip the prompt for scripting. Re-running is safe to
repeat: facts upsert on their primary key and rollups are fully rebuilt, so
totals never double-count. One recovery-depth caveat: activity is recovered as
far back as the `events` table reaches, but token totals only as far back as the
token ledger, so token charts may start later than activity charts.
`waypoint maintenance stats` reports the current `telemetry_backfill` state
(`done`, `through`) so you can see whether a re-run is warranted and confirm the
result afterward.

Two upgrade notes for deployments that ran telemetry before it became opt-in:
existing collectors must add `telemetry_enabled: true` before restarting or
collection and the dashboard silently go away (existing facts stay on disk but
become inaccessible until re-enabled). And a config that sets
`telemetry_nl.enabled: true` without `telemetry_enabled: true` now fails to boot
— the NL summarizer requires the master switch. Because of that fail-fast, a
`WAYPOINT_TELEMETRY_ENABLED=false` env kill-switch over a YAML config that leaves
`telemetry_nl.enabled: true` must also disable the summarizer
(`WAYPOINT_TELEMETRY_NL_ENABLED=false`) so the resolved config stays valid.

Disabling telemetry later stops new collection but never deletes existing facts;
use the explicit delete control (or `DELETE /api/telemetry`, which stays
available while disabled) to erase them. Deletion preserves the `backfill_done`
marker, so a later re-enable does not re-derive erased pre-enable history; the
only way back is the deliberate, confirmed `waypoint maintenance
rebuild-telemetry --force` (see above).

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
- [`backend/src/waypoint/telemetry/instance/`](../backend/src/waypoint/telemetry/instance/) — the shared read-only instance-snapshot definition, collector, insights, history service, and NL claim templates.
- [`frontend/src/app/telemetry/page.tsx`](../frontend/src/app/telemetry/page.tsx) — the dashboard page.
- [`frontend/src/components/telemetry/InstanceHealthPanel.tsx`](../frontend/src/components/telemetry/InstanceHealthPanel.tsx) — the instance health & capacity section.
