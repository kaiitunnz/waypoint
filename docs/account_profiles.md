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

### A profile's `config_dir` must be a set-up config home

`config_dir` becomes the agent's config-dir env var, and each agent keeps *all*
of its per-config state under that dir. For Claude this includes the config file
itself: with `CLAUDE_CONFIG_DIR` set, the CLI reads `<config_dir>/.claude.json`
— **not** the unset-default home location `~/.claude.json`. So pointing a profile
at `~/.claude` does *not* reuse your normal account: your onboarding, MCP servers,
and permissions live in `~/.claude.json` (home), while `~/.claude/.claude.json`
is a separate, usually un-onboarded file. Each profile dir must be a
self-contained config home that has completed first-run setup.

Set one up once, before selecting the profile:

- **Claude** — run `CLAUDE_CONFIG_DIR=<config_dir> claude` interactively and finish
  the first-run wizard (theme, login), or copy an already-onboarded
  `~/.claude.json` into `<config_dir>/.claude.json`. Waypoint refuses to launch or
  switch an **interactive** session (the `claude_tty` / tmux transports) onto a
  profile whose `<config_dir>/.claude.json` hasn't completed onboarding — the TUI
  would relaunch into the wizard and a headless-driven turn can't dismiss it, so it
  would hang. The rejection is a 400 (`account profile '<id>' is not set up: …`).
  The headless `claude --print` transport doesn't onboard and isn't blocked.
- **Codex** — run `CODEX_HOME=<config_dir> codex login` to write `auth.json`. Codex
  has no onboarding wizard: its default app-server transport fails fast on an
  unauthenticated home (surfaced error, no hang), so profiles aren't guarded the
  way Claude's are.

### Transcript policies

When a running session switches onto a profile, its native thread must be
visible under the target config dir or the resumed agent can't continue it. The
policy decides how:

- **`require_existing`** (default) — verify the target config dir already has the
  thread; never touch files. Use when profiles share a config dir, or you manage
  the transcript trees yourself.
- **`symlink_shared`** — make the target profile's native transcript store a
  symlink to a shared directory (requires `shared_transcript_dir`), so every
  profile pointed at that directory reads and writes the same threads. See below.
- **`copy_thread_on_switch`** — copy just the current thread's artifacts into the
  target config dir on the switch. Use for fully independent config dirs.

#### How `symlink_shared` resolves transcripts

Each agent keeps its transcripts under a fixed subdirectory of its config dir —
its *native store*: `projects` for Claude (`$CLAUDE_CONFIG_DIR/projects/…`),
`sessions` for Codex (`$CODEX_HOME/sessions/…`). On the first switch onto a
`symlink_shared` profile whose target can't already see the thread, Waypoint
makes `<config_dir>/<native-store>` a symlink to `shared_transcript_dir`. Point
every such profile's `shared_transcript_dir` at the **same** directory and they
all resolve to one physical transcript tree, so a thread written under one
profile is immediately visible under another — no copy.

The conversion is idempotent and **non-destructive**: a missing store directory
is created as the symlink, the correct symlink already in place is left alone,
and an *empty* store directory is replaced — but a store directory that already
holds real transcripts is refused rather than moved (migrate it into the shared
directory first). So point profiles at a shared directory on fresh or empty
config dirs. If the thread still isn't visible after linking, the switch is
rejected before the session is touched.

When Waypoint launches a **new** session under a `symlink_shared` profile, it
performs that guarded missing-or-empty setup before the agent starts, so the new
thread is written to the shared tree from its first event. This does not affect a
Codex or Claude process launched outside Waypoint: its config-dir environment
causes the agent itself to create a normal native-store directory. Run
`accounts setup-transcripts` before using such a populated profile for a
restart-and-resume switch.

The tradeoff versus `copy_thread_on_switch`: one shared tree means the accounts
have **no transcript isolation** — every profile sees every thread — whereas
copying keeps independent trees and duplicates only the switched thread.

## Using profiles from the CLI

List the configured profiles (redacted — ids, labels, and the config-dir env key
only), optionally scoped to a backend or launch target:

```bash
waypoint accounts list
waypoint accounts list --backend codex
waypoint accounts list --launch-target-id my-ssh-host
```

### Verifying, diagnosing, and setting up a profile

Three companion commands verify a profile from the CLI instead of discovering
breakage at launch/switch time.

**`probe`** resolves a profile, authenticates as its config dir, and prints the
verified account. The label shows by default; the private-class `account_key`
(what you put in `expected_account_key`) is hidden unless you ask for it:

```bash
waypoint accounts probe claude_code work
waypoint accounts probe claude_code work --show-key    # reveal account_key
```

**`doctor`** runs a per-profile checklist — config dir exists, readiness
(claude onboarding / codex `auth.json`), transcript-policy setup, expected-account
match, and backend support — and **exits non-zero** if any check fails, so it is
scriptable in CI. It renders a table by default and structured JSON with `--json`;
config-dir paths stay hidden unless `--show-paths`:

```bash
waypoint accounts doctor                       # all profile-hosting backends
waypoint accounts doctor --backend codex
waypoint accounts doctor --json                # machine-readable report
```

`waypoint doctor` (the top-level diagnostic) also appends a short per-profile
summary from the same checklist, minus the live account probe — run
`accounts doctor` for the account-match verification.

**`setup-transcripts`** performs the guarded conversion a `symlink_shared`
profile needs: it makes `<config_dir>/<native-store>` a symlink to the shared
dir, and when a populated real directory is already there it migrates the
contents in before replacing it with the symlink. It is idempotent on a correct
symlink and never runs implicitly during a switch:

```bash
waypoint accounts setup-transcripts claude_code work
waypoint accounts setup-transcripts codex work --shared-dir ~/.waypoint/codex-sessions
```

The migration is a **recursive, conflict-aware merge**, so two stores may share
directory ancestors — the usual Codex `sessions/YYYY/MM/DD/…` and Claude
`projects/<project>/…` layouts — as long as their leaf transcript files do not
truly collide:

- A source file whose destination path is absent is copied in.
- A byte-identical file at the same relative path is **deduplicated** (the shared
  copy is retained), reported in the action summary.
- A differing file, a file/directory type mismatch, or a diverging symlink target
  is a **conflict**. Conflicts abort the whole migration before anything is
  copied, renamed, or backed up. The error lists a bounded, deterministic set of
  relative conflict paths (never transcript contents), for example:

  ```text
  cannot migrate ~/.codex-nus/sessions into ~/.codex/sessions: 2 conflicting paths:
  2026/07/10/rollout-abc.jsonl (different regular files)
  2026/07/11 (source file, shared directory)
  ```

The success summary reports the copied-file count, deduplicated-file count,
directories created, the timestamped backup location, and the symlink
destination. The original store is renamed to `<native-store>.bak-<timestamp>`
(a complete snapshot) and is **never deleted automatically**.

**Stop active sessions first.** A migration copies files while agents may still
be writing to the source store; the command verifies each staged copy against its
source but cannot take a filesystem snapshot, so a session writing mid-migration
can still lose that in-flight write. Migrate only stores that are idle.

**Recovery.** New files are staged in a temporary `.wp-migrate-<timestamp>`
sibling of the shared dir and verified before any merge begins. On failure before
the source rename the original store is left untouched. If the process dies
mid-merge, the original store still exists intact, the staging directory is retained
for inspection, and a re-run reports only the already-moved destinations as
conflicts — finish the move by hand from the retained source/staging state.

Each command has an HTTP endpoint behind it
(`GET .../accounts/{profile}/probe`, `GET .../accounts/doctor`,
`POST .../accounts/{profile}/setup-transcripts`) so the web UI can surface the
same diagnostics. Remote (SSH launch target) `setup-transcripts` is not yet
supported; `doctor` reports its filesystem checks as skipped there.

Launch, schedule, or preset a session under a profile with `--account-profile`:

```bash
waypoint sessions start --backend claude_code --cwd ~/proj --account-profile work
waypoint schedule create --backend codex --cwd ~/proj --delay-seconds 300 \
  --account-profile work
waypoint presets create --name work-claude --backend claude_code --account-profile work
waypoint sessions import codex --thread-id <uuid> --account-profile work
```

Discovery is profile-scoped too, so listing matches the account a session will
launch under. Pass `--account-profile` to the two discovery commands (and it
flows through `import`/thread-delete), otherwise they resolve the process-default
store:

```bash
waypoint models claude_code --account-profile work            # models the profile's account offers
waypoint backends threads claude_code --account-profile work  # resumable threads in the profile's store
```

In the web UI the launch/schedule panel re-scopes its model catalogue and
resumable-thread list to the selected profile, and importing or resuming a listed
thread carries that profile. Remote (SSH launch target) profile-scoped discovery
is not yet wired — a remote list resolves the target's default store.

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
`CODEX_HOME`. A fresh Codex thread has an ID before it has a native rollout
file. With `symlink_shared` or `copy_thread_on_switch`, Waypoint may safely
start a replacement thread under the selected profile only when it has no
persisted native artifact and Waypoint has no user or agent conversation events.
It preserves the Waypoint session and assistant identity, but the native thread
starts fresh. A missing artifact after conversation events, or under
`require_existing`, remains a pre-restart error.

- Eligibility is derived from the session's **composed `(agent, transport)`
  pair**, not from whichever plugin happens to own the transport: the agent
  contributes the config-dir env var / native thread store, the transport
  contributes the restart-with-resume story. Claude's native (`claude_cli`) and
  emulated (`claude_tty`) transports, Codex's app-server transport, and the
  generic `tmux` wrapper around Claude or Codex all support it — a
  tmux-wrapped session inherits its agent's config-dir env var and resumes the
  wrapped CLI (`claude --resume` / `codex resume`) under the switched profile,
  same as the structured transports. `opencode` (no config-dir env var) and a
  pure attached-tmux pane (`tmux` with no wrapped agent) stay refused
  regardless of transport, as does macOS Claude (the Keychain is a single
  global account).
- A tmux-wrapped switch flushes a running turn before terminating the pane the
  same way the structured transports do, but on a best-effort basis: a scraped
  pane has no structured turn-end signal, so the flush polls the wrapped
  agent's transcript (or the pane itself) until it settles, and proceeds
  rather than aborting if it never does. The transcript-availability check
  right after it is still the hard fail-before-destroy guard.
- A switch that wouldn't change the account is rejected, so set
  `expected_account_key` when two profiles intentionally point at the same
  underlying account (e.g. a copied config dir).
- tmux-wrapped switching works on both local and remote (SSH launch target)
  sessions — the tmux pane itself is always local (for a remote target it
  runs `ssh ... <agent CLI>` in that local pane), and the transcript step
  already routes to the remote filesystem implementation by launch target.

See issue [#230](https://github.com/kaiitunnz/waypoint/issues/230) for the
original design.
