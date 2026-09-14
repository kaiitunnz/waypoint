# Changing session settings

`waypoint sessions settings <session-id>` changes a non-assistant session's
settings in one non-interactive command: title, permission mode, model/effort,
transport, account profile, launch args/config/env, and usage-limit source (see
`--help` for the flags). An omitted option is unchanged; a repeated `--arg` /
`--config-override` replaces the whole list, `--clear-*` empties it, and
`--transport` cannot be combined with `--permission-mode` / `--model` /
`--effort`.

## The restart-consent rule

Some changes apply live; others restart the agent process and resume it (an
account-profile switch, a launch-settings edit, or any tuning on a Claude TTY
session, which has no in-process knob). A restart interrupts a running turn. The
command never asks interactively:

- `--dry-run` prints the plan (`restart_count`, `will_interrupt_turn`,
  `warnings`) and exits `0` without mutating.
- Without it, an inline-only plan applies immediately.
- A restart-required plan without `--restart` prints `confirmation_required`
  with a `rerun with --restart` remediation and exits `4` — zero mutations.
  Route that decision through your approval flow, then re-run with `--restart`.

```bash
waypoint sessions settings sess-123 --model gpt-5.4 --dry-run   # inspect
waypoint sessions settings sess-123 --model gpt-5.4             # exit 4 if restart-required
waypoint sessions settings sess-123 --model gpt-5.4 --restart   # apply once approved
```

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Applied, or a successful `--dry-run`. |
| `2` | Usage / preflight error (bad flag combo, unsupported setting, attached tmux). No mutation. |
| `4` | Restart required but `--restart` was not given. No mutation. |
| `1` | An HTTP/runtime failure; earlier steps in a multi-step plan stay applied (no rollback), reported in `applied`. |

Success emits `plan`, the final `session`, and the ordered `applied` names.
Environment values are never echoed (keys only); a `--env VALUE` is still visible
in shell history and process listings.

## Focused commands

`sessions mode` / `set-permission-mode` and `sessions set-account` share the same
plan and consent guard. `sessions mode <id> <mode>` applies inline for structured
pairs and restarts a Claude TTY session (refused without `--restart`).
`sessions set-account <id> <profile> --restart` performs the switch; without
`--restart` it exits `4`, and `--no-restart` is a deprecated synonym for that
safe default. The personal assistant is edited through its own controls.
