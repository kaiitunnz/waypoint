# Account / config-profile switching

A single machine often has more than one account for the same coding agent — a
personal Claude login and a work one, two Codex accounts on different plans.
Waypoint models each as a named **account profile**: a label bound to a
config-dir environment variable (`CLAUDE_CONFIG_DIR` for Claude, `CODEX_HOME`
for Codex) plus a policy for how a running session's transcript follows it.

You can pick a profile when launching, scheduling, or presetting a session, and
switch a *running* session between profiles without restarting the Waypoint
service — the session terminates and resumes the same thread under the new
config dir (restart-and-resume).

Only the agent backends that own a config-dir env var host profiles today:
**`claude_code` and `codex`**. `opencode` and the transport wrappers do not, and
the config parser rejects an `account_profiles` block on any other backend.

## Configuring profiles

Profiles live under `plugin_configs.<agent>.account_profiles` in `waypoint.yaml`,
keyed by a profile id:

```yaml
plugin_configs:
  claude_code:
    account_profiles:
      personal:
        label: Personal
        config_dir: ~/.claude
        transcript_policy: require_existing
      work:
        label: Work
        config_dir: ~/.claude-work
        transcript_policy: copy_thread_on_switch
        expected_account_key: claude_code:acme-corp
  codex:
    account_profiles:
      personal:
        label: Personal
        config_dir: ~/.codex
      work:
        label: Work
        config_dir: ~/.codex-work
        expected_account_key: codex:work@example.com
```

Each profile accepts:

| Field | Required | Meaning |
| --- | --- | --- |
| `label` | yes | Human-facing name shown in the CLI, picker, and badges. |
| `config_dir` | yes | Value for the agent's config-dir env var. `~` is kept verbatim and expanded only when used. |
| `transcript_policy` | no (default `require_existing`) | How the target profile gets to see the session's native thread on a switch. See below. |
| `shared_transcript_dir` | only for `symlink_shared` | The shared transcript directory the target's store is symlinked to. |
| `expected_account_key` | no | The account the profile is asserted to authenticate as (e.g. `claude_code:<org>`, `codex:<email>`). When set, a switch is rejected unless the target actually authenticates as this key; when unset, a switch that would resolve to the *current* account is rejected as a no-op. |

### Transcript policies

When a running session switches onto a profile, its native thread must be
visible under the target config dir or the resumed agent can't continue it. The
policy decides how:

- **`require_existing`** (default) — verify the target config dir already has the
  thread; never touch files. Use when profiles share a config dir, or you manage
  the transcript trees yourself.
- **`symlink_shared`** — point the target's native transcript store at a shared
  directory (requires `shared_transcript_dir`), so every profile sees the same
  threads.
- **`copy_thread_on_switch`** — copy just the current thread's artifacts into the
  target config dir on the switch. Use for fully independent config dirs.

## Using profiles from the CLI

List the configured profiles (redacted — ids, labels, and the config-dir env key
only), optionally scoped to a backend or launch target:

```bash
waypoint accounts list
waypoint accounts list --backend codex
waypoint accounts list --launch-target-id my-ssh-host
```

Launch, schedule, or preset a session under a profile with `--account-profile`:

```bash
waypoint sessions start --backend claude_code --cwd ~/proj --account-profile work
waypoint schedule create --backend codex --cwd ~/proj --delay-seconds 300 \
  --account-profile work
waypoint presets create --name work-claude --backend claude_code --account-profile work
waypoint sessions import codex --thread-id <uuid> --account-profile work
```

Inspect a session's restart-applied launch settings (env is redacted to keys):

```bash
waypoint sessions launch-settings <session-id>
```

Switch a running session onto a different profile. This restarts the session
and resumes its thread under the new config dir, so it is confirmed by default:

```bash
waypoint sessions set-account <session-id> work
waypoint sessions set-account <session-id> work --no-restart   # rejected in phase 1
```

## Using profiles in the web UI

- The launch and schedule forms show an **Account** selector for backends that
  host profiles; the choice is carried into the session and into any preset you
  save from the form.
- A session's profile appears as a badge on its header, its list card, and any
  scheduled-session row.
- The running-session settings ("tune") popover has an **Account** selector that
  performs the in-place switch — the GUI mirror of `sessions set-account`.

## How the switch works, and its limits

The switch is **restart-and-resume**: Waypoint verifies the target account,
flushes any running turn, makes the thread available under the target config dir
per the transcript policy, terminates the session, persists the new profile, and
restores it — resuming the *same* thread under the new `CLAUDE_CONFIG_DIR` /
`CODEX_HOME`.

- It applies only to **structured transports** whose agent owns a config-dir env
  var: Claude's native (`claude_cli`) and emulated (`claude_tty`) transports and
  Codex's app-server transport. The generic `tmux` wrapper has no config-dir env
  var and is refused.
- A switch that wouldn't change the account is rejected, so set
  `expected_account_key` when two profiles intentionally point at the same
  underlying account (e.g. a copied config dir).
- Remote (SSH) `copy_thread_on_switch` and tmux-wrapped switching are not yet
  supported.

See issue [#230](https://github.com/kaiitunnz/waypoint/issues/230) for the
original design.
