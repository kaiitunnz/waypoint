# Usage providers

Usage providers surface account rate-limit usage that has no live coding session
behind it. A provider fetches its own account's usage directly and publishes it
to the home-page usage panel and `GET /api/usage` alongside the session-derived
Claude Code and Codex cards, so the card persists even when no session is
running.

The subsystem is **opt-in and off by default**. With no enabled
`usage_providers` block, nothing is fetched, no background requests are made, and
no provider cards appear.

**Lumid** is the only provider in v1.

## Lumid

The Lumid provider identifies each configured personal-access token (PAT) via
`GET https://lum.id/api/v1/user`, then publishes only that account's matching row
from `GET https://lum.id/api/v1/admin/claude-user-usage` (matched on email,
case-insensitively). Other users returned by the admin endpoint are never shown.
It maps the row to a 5-hour and a 7-day window with used/limit tokens,
percentages, and the 7-day request count.

### Setup

1. **Provide the token(s) by environment variable.** Waypoint never reads a PAT
   from `waypoint.yaml`. Set `LUMID_TOKENS` in the repo-root `.env` (or your
   service manager's secret store). Comma-separate multiple PATs:
   ```bash
   LUMID_TOKENS=lm_pat_live_first,lm_pat_live_second
   ```

2. **Enable it in `backend/waypoint.yaml`:**
   ```yaml
   usage_providers:
     - id: lumid
       type: lumid
       enabled: true
       token_env: LUMID_TOKENS
       label: Lumid
       refresh_interval_seconds: 300   # 60–3600
   ```

3. **Restart the backend.** Configuration is read at startup:
   ```bash
   waypointctl restart backend
   ```

4. **Verify.** `waypoint usage --refresh` returns the Lumid account card and
   provider health; the home panel shows it tagged `Lumid`.

Several PATs may resolve to the same Lumid account; the provider publishes one
card per account and keeps the freshest snapshot. Removing a PAT (and
restarting) drops any account that no configured credential still observes.

## Refresh

Enabled providers refresh once at startup and then every
`refresh_interval_seconds`. `POST /api/usage/refresh` (the panel's refresh
button and `waypoint usage --refresh`) refreshes providers and session sources
together. The latest successful snapshot is durable across restart; a failed
refresh leaves the last-good snapshot in place, marked stale.

## Health states

A provider reports a coarse, safe state per refresh, visible in the usage panel
and `GET /api/usage` even when no account resolves:

| State | Meaning |
|---|---|
| `missing_token` | `token_env` is unset or empty. |
| `identity_failed` | `/user` did not authenticate the PAT. |
| `permission_denied` | The PAT lacks access to Claude user usage; the last-good card stays stale. |
| `no_matching_usage` | No usage row matched the account email. |
| `usage_unavailable` | The usage endpoint returned an unexpected or malformed response. |
| `network` | The request timed out or could not connect. |

## Per-session usage limit source

A session's rate-limit readout defaults to its coding-agent plugin resolver
(`usage_limit_source: plugin`). A session may instead read one enabled provider
account (`usage_limit_source: usage_provider` with `usage_provider_id` and an
opaque `usage_provider_account_key`). The selection is durable across restart,
schedule firing, and reload.

Choose it under **Advanced → Usage limit source** in the launch sheet (New and
Schedule) and in the session settings modal: a provider with one account is a
single option (`Lumid — user@example.com`), one with several reveals an account
picker. The CLI takes `--usage-limit-source`, `--usage-provider`, and
`--usage-provider-account` on `sessions start` and `schedule create`; the API
takes the same fields on create/schedule plus a dedicated non-restart
`PATCH /api/sessions/{id}/usage-limit-source`, and `GET /api/usage-provider-options`
(and launch bootstrap) list the current choices. Only opaque account keys and
server-derived labels cross the wire.

Changing the source in settings never restarts or interrupts the agent.
Switching to a provider projects that account's cached snapshot immediately;
switching back runs the plugin refresh. A provider-selected session refreshes
only through its provider, coalesced onto one upstream request.

A removed or unrefreshable provider/account retains its last-good projection
marked stale/unavailable rather than falling back to plugin data, and a
scheduled run with a now-unavailable selection fails instead of launching under
an unintended source. The projection reuses the provider's own dashboard card
and telemetry facts, so it is never counted twice.

## Security

PATs live only in process memory, read from the environment at refresh. They are
never stored, logged, returned by the API, or written to telemetry; request
headers are redacted in diagnostics. The durable store holds normalized
snapshots and non-reversible HMAC digests only. The provider requests identity
and usage endpoints over a fixed HTTPS origin and does not mutate Lumid state.

## Telemetry

When `telemetry_enabled` is true, each provider snapshot also becomes an
account-scoped `limit_snapshot` fact, keyed by a pseudonymous account digest and
gated by `telemetry_local_labels` like every other account label. See
[`telemetry.md`](telemetry.md#provider-limits).
