# Telegram notifications

Waypoint is a web app served over HTTP, so a browser that is not open cannot
surface a newly posted inbox item or a session blocked on your decision. The
notification center closes that gap: when a new inbox item is created or a
session needs human action, it delivers a bounded preview and a deep link to
your Telegram chat.

It is **opt-in and off by default**. While disabled, no delivery rows are
written, no HTTP calls are made, and nothing leaves the host.

> Preview text (inbox subjects, plan text, question prompts, approval commands)
> is sent to **Telegram, a third party**. Enable this only for chats you
> control. Attachments are never uploaded — an attachment shows only as
> `Attachment: <filename>`.

## Quickstart

1. **Create a bot.** In Telegram, message [@BotFather](https://t.me/BotFather),
   send `/newbot`, and follow the prompts. It replies with an HTTP API **token**
   like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.

2. **Provide the token by environment variable.** Waypoint never reads it from
   `waypoint.yaml`. Set `WAYPOINT_TELEGRAM_BOT_TOKEN` in the repo-root `.env`
   (or your service manager's secret store):
   ```bash
   WAYPOINT_TELEGRAM_BOT_TOKEN=123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

3. **Start the bot and get your chat ID.** A bot cannot message a chat that has
   not started it first. Open your bot (the `t.me/<botname>` link) and press
   **Start**, then read the chat id from:
   ```bash
   curl "https://api.telegram.org/bot$WAYPOINT_TELEGRAM_BOT_TOKEN/getUpdates"
   ```
   Take `result[].message.chat.id`. A personal chat id is a positive integer; a
   group id is negative (e.g. `-100123456789`).

4. **Enable it in `backend/waypoint.yaml`:**
   ```yaml
   notifications:
     enabled: true
     public_base_url: https://waypoint.example.ts.net   # any http(s) origin reachable
                                                        # from your phone (http://host:port
                                                        # is fine); see public_base_url below
     channels:
       - id: personal-telegram
         type: telegram
         enabled: true
         bot_token_env: WAYPOINT_TELEGRAM_BOT_TOKEN
         chat_ids:
           - "123456789"
   ```

5. **Restart and verify:**
   ```bash
   waypointctl restart backend
   ```
   Create an inbox item (or let a session ask for approval) and confirm the
   message arrives with a working **Open** button. Check health with:
   ```bash
   waypoint notifications status
   ```
   `channels[].available` should be `true`.

That is the whole setup. The rest of this page is reference.

## What gets notified

- **A new inbox item** — subject, sender, a preview of its first blocks, linked
  to `/inbox/<id>`.
- **A tool approval** — the tool and its allowed decisions.
- **An `AskUserQuestion`** — each question and its option labels/descriptions.
- **A plan approval** (Claude Code `ExitPlanMode`) — the plan body and its
  decisions.

Session notifications link to `/session/<id>`. Opening any link uses Waypoint's
normal login — the link itself carries no token. Enabling the feature does not
retro-notify already-pending items; only new transitions notify.

## Choosing which signals notify

`signals` turns each notification kind on or off independently. All four default
to `true`, so leaving the block out notifies on everything.

```yaml
notifications:
  enabled: true
  signals:
    inbox: true         # new inbox items
    plan: true          # plan approvals
    permission: false   # permission approvals
    question: true      # user questions
```

Turning a signal off stops only its notifications; the inbox item or session
request still appears in Waypoint as usual. Unknown keys are rejected.

## Active-session presence

While you have a session's page open and visible, Waypoint skips that session's
plan, permission, and question notifications — you are already looking at it.
Inbox notifications always arrive, since an inbox item can come from another
session.

Closing the tab, switching away, or locking the screen makes the session
notifiable again within about 45 seconds. Opening the same session on two
devices keeps it silent until you leave the last one. To always be notified,
keep the session page closed.

## `public_base_url`

The operator-controlled origin the deep links are built from. It is **required**
when notifications are enabled and must be an absolute `http` or `https` URL,
with an optional port, and no query, fragment, or userinfo. Any such origin
works for the deep link as long as it is reachable **from your phone** — a
Tailscale MagicDNS name over plain HTTP is fine (e.g.
`http://host.ts.net:3010`), a public HTTPS host is fine; `localhost` and a bare
internal alias like `http://h0:8787` are not (they don't resolve on mobile).

A valid **https** origin with a real hostname (e.g.
`https://waypoint.example.ts.net`) additionally lets Telegram render the deep
link as a tappable **Open** button. For any other accepted origin the link is
included in the message text instead — delivery still succeeds and the link
still opens on a device that can reach that host.

## Multiple chats and channels

List several `chat_ids` on one channel to fan the same message out to a private
group or several devices. Every id is a **string**, so negative group ids and
large ids keep their exact form.

## Configuration reference

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Master switch. |
| `public_base_url` | — | Required when enabled; origin for deep links. |
| `preview_chars` | `900` | Max preview body before truncation. |
| `title_chars` | `160` | Max title length. |
| `worker_concurrency` | `4` | Concurrent sends per worker tick. |
| `max_attempts` | `8` | Retries before a delivery is marked failed. |
| `http_timeout_seconds` | `10` | Per-request Telegram timeout. |
| `retention_days` | `30` | Sent/failed/suppressed delivery rows are purged after this. |
| `signals.inbox` | `true` | Notify on new inbox items. |
| `signals.plan` | `true` | Notify on plan approvals. |
| `signals.permission` | `true` | Notify on permission approvals. |
| `signals.question` | `true` | Notify on user questions. |
| `channels[].bot_token_env` | — | Env var holding the bot token. |
| `channels[].chat_ids` | `[]` | Target chat ids, as strings. |

## Delivery behavior

Notifications are queued in a durable outbox and delivered by a background
worker, so a slow or unavailable Telegram never blocks an API request or a
coding session. Failed sends are retried with exponential backoff (honoring
Telegram's `retry_after` on rate limits) up to `max_attempts`.

Delivery is **at-least-once**: if the process dies after Telegram accepts a
message but before the local row is marked sent, that one message may be
re-delivered on restart. Each message links to a stable Waypoint item/session,
so a duplicate is harmless.

## Troubleshooting

Run `waypoint notifications status` to see channel health and delivery counts.

- **`available: false`, "token environment variable … is unset"** — the env var
  named by `bot_token_env` is empty in the backend's environment. Set it and
  restart.
- **Nothing arrives, `counts.failed` climbs** — the target chat has not started
  the bot, or a chat id is wrong. Telegram returns a terminal `400`/`403`; press
  Start in the chat and re-check the id via `getUpdates`.
- **`counts.queued` stays high** — Telegram is unreachable or rate-limiting.
  Rows retry automatically; check backend logs (structured, non-content fields
  only) for the HTTP status.
- **`counts.suppressed` grows** — expected when a session page is open or a
  signal is off. A suppressed message is intentionally not sent.
- **The Open button asks for login or 404s** — expected: links carry no token,
  so log in as usual. A 404 means `public_base_url` is wrong or the
  item/session no longer exists.

## Limitations (v1)

Delivery is **one-way**. You cannot approve, answer, or reply from Telegram —
the button opens Waypoint, where you act as normal. The architecture defines an
inbound contract for a future two-way channel (free-text replies and inline
callback buttons) and for additional channels such as WhatsApp, but v1 ships
neither.
