---
name: waypoint-context
description: Use when a Waypoint session — your own, one you spawned, or one the user named — is long-running and should compact its context at a checkpoint, before the harness's auto-compaction fires. A detached watcher alerts a lead at usage thresholds; the lead parks the session and compacts it.
---

# Waypoint Context

A detached watcher reads one session's context usage from `waypoint sessions list` and messages its **lead** when usage crosses the first threshold, then the second (defaults 60% / 70% of the window). The lead parks the **target** at a checkpoint with its notes written, then compacts it.

**Notes** are whatever durable record the target needs to resume: its project's handoff or plan file, or the file named with `--handoff`. A target with no notes still has its compaction summary.

One watcher per target; a replacement session needs its own. A watcher survives compactions and restarts of either session, and exits on `stop` or once its target has been gone for 10 minutes. A host reboot ends every watcher: `status` shows them `running: false`, and `start` brings them back with their settings.

## Ownership

Compacting interrupts a session's work. Watch only your own session, a session you spawned, or one the user named; to manage anyone else's context, ask the user first. Stop a watcher early only when the user asks.

## Commands

`$W` stands for `python3 <this skill's directory>/scripts/ctx_watch.py`. It needs Python 3.11 and the `waypoint` CLI, and keeps its state in `~/.waypoint/context-watch` (`CTX_WATCH_DIR` overrides).

```
$W start --target <sid> [--lead <sid>] [--first 0.6 --second 0.7] [--handoff <notes>]
$W status [--target <sid>] [--all-leads]
$W set --target <sid> [--first X --second Y] [--lead <sid>] [--handoff <notes>]
$W phase --target <sid> <normal|deferred|parking|compacting|user-held> [--grounding proceed|park]
$W compact --target <sid> --grounding proceed|park [--grounding-only]
$W stop --target <sid>          # or --all, which covers the watchers you lead
```

`--lead` defaults to your own session. `status` and `stop --all` cover the watchers you lead unless you pass `--all-leads`.

Record every decision with `phase`: it is what a compacted lead resumes from. A phase lapses on its own once the watcher re-arms after a compaction; `user-held` lasts until you clear it.

## Backends

Claude Code, Codex and opencode sessions can all be targets. tmux sessions rarely report usage, and `compact` refuses them. Lead a sandboxed Codex session from another session: its shell can't reach the `waypoint` API or the state directory.

## Next

- An alert tagged `[ctx-watch ...]` arrived, or you are about to send follow-up work to a watched target: follow [`references/alerts.md`](references/alerts.md).
- You are compacting a session, compacting yourself, or a pair did not finish: follow [`references/compaction.md`](references/compaction.md).
