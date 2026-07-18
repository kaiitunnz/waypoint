# Telegram notifications

Waypoint is a web app served over HTTP, so a browser that is not open cannot
surface a newly posted inbox item or a session blocked on your decision. The
notification center closes that gap: when a new inbox item is created or a
session needs human action, it delivers a bounded preview and a deep link to
your Telegram chat.

It is **opt-in and off by default**. While disabled, no delivery rows are
written, no HTTP calls are made, and nothing leaves the host.

## What triggers a notification

- **A new inbox item** — subject, sender, a preview of its first blocks, and a
  link to `/inbox/<id>`.
- **A session tool approval** — the tool and its allowed decisions.
- **An `AskUserQuestion`** — each question and its option labels/descriptions.
- **A plan approval** (Claude Code `ExitPlanMode`) — the plan body and its
  decisions.

Each links to `/session/<id>` or `/inbox/<id>`. Opening a link uses Waypoint's
normal login — the link itself carries no token. Existing pending items are not
retro-notified when you enable the feature; only new transitions are.

## Privacy

Preview text (inbox subjects, plan text, question prompts, approval commands) is
sent to **Telegram, a third party**. Attachments are never uploaded — an
attachment appears only as `Attachment: <filename>`. Enable this only for chats
you control, and prefer a private chat or a private group over anything public.

## Setup

### 1. Create a bot

1. In Telegram, message [@BotFather](https://t.me/BotFather) and send
   `/newbot`.
2. Follow the prompts to name the bot. BotFather replies with an **HTTP API
   token** like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
3. Keep the token secret.

### 2. Provide the token by environment variable

Waypoint never reads the token from `waypoint.yaml`. Export it (or add it to the
repo-root `.env` / your service manager's secret store) under the name you will
reference as `bot_token_env`:

```bash
export WAYPOINT_TELEGRAM_BOT_TOKEN='123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
```

### 3. Start the bot and find your chat ID

A bot cannot message a chat that has not started it first.

1. Open your bot (the `t.me/<botname>` link BotFather gave you) and press
   **Start** (or send any message).
2. Discover the chat ID. The simplest way:
   ```bash
   curl "https://api.telegram.org/bot$WAYPOINT_TELEGRAM_BOT_TOKEN/getUpdates"
   ```
   Read `result[].message.chat.id` from the JSON. A personal chat id is a
   positive integer; a group/channel id is negative (e.g. `-100123456789`).
   Add the bot to a group and post a message there to get the group's id.
3. Configure every id as a **string** so negative and large ids keep their exact
   form.

### 4. Enable it in `waypoint.yaml`

```yaml
notifications:
  enabled: true
  public_base_url: https://waypoint.example.ts.net
  channels:
    - id: personal-telegram
      type: telegram
      enabled: true
      bot_token_env: WAYPOINT_TELEGRAM_BOT_TOKEN
      chat_ids:
        - "123456789"
```

`public_base_url` is the operator-controlled origin the deep links are built
from. It is **required** when notifications are enabled, must be an absolute
`http`/`https` URL with no query, fragment, or userinfo, and should be reachable
from your phone (your Tailscale MagicDNS name or a public HTTPS host — not
`localhost`).

Multiple `chat_ids` on one channel fan the same message out to each — use this
for a private group or several devices.

### 5. Restart and verify

```bash
waypointctl restart backend
waypointctl status
```

Create an inbox item (or let a session ask for approval) and confirm the message
arrives with a working **Open** button. Check delivery health at
`GET /api/notifications/status` (authenticated):

```json
{
  "enabled": true,
  "channels": [{ "channel_id": "personal-telegram", "available": true }],
  "counts": { "sent": 3, "queued": 0, "failed": 0 }
}
```

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
| `retention_days` | `30` | Sent/failed delivery rows are purged after this. |
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

- **`available: false, "token environment variable … is unset"`** — the env var
  named by `bot_token_env` is empty in the backend's environment. Export it and
  restart.
- **Nothing arrives, `counts.failed` climbs** — the target chat has not started
  the bot, or a chat id is wrong. Telegram returns a terminal `400`/`403` for
  these; press Start in the chat and re-check the id via `getUpdates`.
- **`counts.queued` stays high** — Telegram is unreachable or rate-limiting.
  Rows retry automatically; check backend logs (structured, non-content fields
  only) for the HTTP status.
- **The Open button 404s or asks for login** — expected. Links carry no token;
  log in to Waypoint as usual. A 404 means `public_base_url` is wrong or the
  item/session no longer exists.

## Limitations (v1)

Delivery is **one-way**. You cannot approve, answer, or reply from Telegram —
the button opens Waypoint, where you act as normal. The architecture defines an
inbound contract for a future two-way channel (free-text replies and inline
callback buttons) and for additional channels such as WhatsApp, but v1 ships
neither an inbound handler nor those channels.
