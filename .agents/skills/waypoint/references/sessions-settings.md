# Changing session settings

`waypoint sessions settings <session-id>` changes a non-assistant session's
editable settings in one non-interactive command. It builds the same
capability-aware plan as the web settings editor and never blocks on a prompt.

```bash
waypoint sessions settings <session-id> \
  [--title T] \
  [--permission-mode MODE] \
  [--model M | --clear-model] [--effort E | --clear-effort] \
  [--transport ID] \
  [--account-profile P | --clear-account-profile] \
  [--arg A ... | --clear-args] \
  [--config-override C ... | --clear-config-overrides] \
  [--env KEY=VALUE ...] [--unset-env KEY ...] \
  [--usage-limit-source plugin|usage_provider [--usage-provider ID --usage-provider-account KEY]] \
  [--restart] [--dry-run]
```

An omitted option is left unchanged. A repeated `--arg` / `--config-override`
replaces the whole list; `--clear-*` sends an empty list. `--clear-model` /
`--clear-effort` / `--clear-account-profile` reset back to the launch default.
`--transport` cannot be combined with `--permission-mode` / `--model` /
`--effort` — switch the interface first, then tune in a second command.

## The restart-consent rule (why this is agent-safe)

Some changes apply live; others restart the underlying agent process and resume
it (an account-profile switch, a launch-settings edit, or any tuning on a
transport that has no in-process knob — a Claude TTY session restarts to apply
model / effort / permission mode). A restart interrupts a running turn.

The command never asks interactively. Instead:

- **`--dry-run`** prints the plan (`restart_count`, `will_interrupt_turn`,
  `warnings`) and exits `0` without mutating.
- Without `--dry-run`, an **inline-only** plan applies immediately.
- A **restart-required** plan without `--restart` prints
  `{"confirmation_required": true, "remediation": "rerun with --restart", ...}`
  and exits **`4`** — zero mutations. Route that decision through your normal
  approval flow (an inbox item, a board relay), then re-run with `--restart`.

```bash
# 1. Inspect
waypoint sessions settings sess-123 --model gpt-5.4 --dry-run
# 2. If restart-required, this exits 4 and does not mutate
waypoint sessions settings sess-123 --model gpt-5.4
# 3. Once approved, apply deliberately
waypoint sessions settings sess-123 --model gpt-5.4 --restart
# Restart-scoped edits share one restart:
waypoint sessions settings sess-123 --account-profile work --arg --foo --env FOO=bar --restart
```

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Applied (or a successful `--dry-run`). |
| `2` | Usage / preflight error (bad flag combo, unsupported setting, attached-tmux launch edit). No mutation. |
| `4` | Restart required but `--restart` was not given. No mutation. |
| `1` | An HTTP/runtime failure. On a multi-step plan, earlier successes are preserved and reported in `applied`; there is no rollback. |

## Output

Success emits one JSON object: `plan`, the final public `session`, and the
ordered `applied` setting names. Dry runs emit `plan` + `dry_run: true` and no
`session`. **Environment values are never echoed** — plans and output list env
keys only. A `--env VALUE` is still visible in your shell history and process
list, so treat command-line secrets accordingly.

## Focused commands

`sessions mode` / `sessions set-permission-mode` and `sessions set-account` use
the same plan and consent guard:

- `sessions mode <id> <mode>` applies inline for structured pairs. On a Claude
  TTY session the mode applies by restarting, so it is refused without
  `--restart` (exit `4`) — pass `--dry-run` to see the plan first.
- `sessions set-account <id> <profile> --restart` performs the profile switch.
  Without `--restart` it prints the `confirmation_required` plan and exits `4`.
  `--no-restart` is a deprecated synonym for that safe default.

The personal assistant is edited through its own controls, not
`sessions settings`.
