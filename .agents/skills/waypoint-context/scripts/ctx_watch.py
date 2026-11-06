"""Watch one Waypoint session's context usage and alert its lead session.

The watcher runs detached from every agent process, so it survives the lead's
and the target's compactions and restarts. It exits on `stop`, or once its
target has been gone for GONE_EXIT_SEC. Claude Code queues /compact until its turn
ends, so its compaction pair goes out at once. A separate detached helper sends
whatever has to wait for a turn to end.
"""

import argparse
import contextlib
import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

if sys.version_info < (3, 11):
    sys.exit("ctx_watch.py needs Python 3.11 or newer")

ROOT = Path(
    os.environ.get("CTX_WATCH_DIR", Path.home() / ".waypoint" / "context-watch")
)
SESSION_ID = re.compile(r"\w[\w.-]*")
GONE = {"exited"}
IDLE = {"idle", "waiting_input"}
PHASES = ["normal", "deferred", "parking", "compacting", "user-held"]
GROUNDINGS = ["proceed", "park"]
DEFAULTS = {"first": 0.60, "second": 0.70, "interval": 60, "handoff": None}
QUEUES_COMPACT = {"claude_cli", "claude_tty"}
TURN_ENDED = ("idle", "interrupted", "exited", "error")
GONE_EXIT_SEC = 600
REMINDER_SEC = 900
STALL_SEC = 600
REFILL_SEC = 1800
MAX_BACKOFF_SEC = 300
PAIR_QUIET_SEC = 30
PAIR_SETTLE_SEC = 5
# Measured: Codex never leaves idle without input and rejects an early /compact
# cleanly; opencode reads idle between the steps of a turn for under a second.
QUIET_SEC = {"codex_app_server": 2, "opencode_http": 5}
SETTLE_SEC = {"codex_app_server": 2}
PAIR_POLL_SEC = 10
PAIR_GIVE_UP_SEC = 600
PAIR_TURN_GIVE_UP_SEC = 6 * 3600
PAIR_RECENT_SEC = 60
STOP_WAIT_SEC = 2
FOLLOW = "Follow the waypoint-context skill's references/alerts.md."
NO_USAGE = (
    "this session has reported no context usage yet. A first turn normally "
    "reports it; tmux sessions rarely do, and without it this watcher never "
    "alerts. Check `status` after its first turn."
)

README = """# Context-management watchers

One detached `ctx_watch.py` process per watched Waypoint session. Each alerts
its lead when the target's context usage crosses the first or second threshold.
A watcher exits on `stop`, or once its target has been gone for 10 minutes. A
host reboot ends every watcher; start them again with `start`.

    W={script}
    python3 $W start --target <sid> [--lead <sid>] [--first 0.60] [--second 0.70]
    python3 $W status [--target <sid>] [--all-leads]
    python3 $W set --target <sid> --first 0.60 --second 0.70
    python3 $W stop --target <sid>
    python3 $W stop --all [--all-leads]

Remove everything: stop every watcher, then delete this directory.

Per target, `<sid>/` holds `config.json`, `state.json` (watcher-written),
`phase.json` (lead- and helper-written), `watcher.pid`, `pair.lock`, `pair.pid`
while a compaction helper runs, and `watcher.log`. `stop` removes it.
"""


class PairFailed(Exception):
    """A compaction pair stopped early, for the reason given."""


class TargetGone(Exception):
    """A compaction pair's target exited."""


def now() -> float:
    return time.time()


def tdir(sid: str) -> Path:
    # `stop` deletes what this returns, so `--target ..` must never reach it.
    if not SESSION_ID.fullmatch(sid):
        sys.exit(f"not a session id: {sid!r}")
    return ROOT / sid


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def log(sid: str, msg: str) -> None:
    with open(tdir(sid) / "watcher.log", "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def waypoint_cli() -> str:
    """The `waypoint` CLI: the override, then PATH, then the backend's venv."""
    if cli := os.environ.get("CTX_WATCH_WAYPOINT") or shutil.which("waypoint"):
        return cli
    if home := os.environ.get("WAYPOINT_HOME"):
        venv_cli = Path(home, "backend/.venv/bin/waypoint")
        if venv_cli.exists():
            return str(venv_cli)
    return "waypoint"


def live_process(pid_file: Path, role: str, sid: str) -> int | None:
    """The pid in `pid_file`, while that process is still `role` for this target."""
    try:
        pid = int(pid_file.read_text())
        os.kill(pid, 0)
        out = subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    command = f"{out.stdout.strip()} "
    return (
        pid
        if "ctx_watch" in command and f" {role} --target {sid} " in command
        else None
    )


def live_pid(sid: str) -> int | None:
    return live_process(tdir(sid) / "watcher.pid", "run", sid)


def pair_pid(sid: str) -> int | None:
    return live_process(tdir(sid) / "pair.pid", "pair", sid)


def list_sessions() -> dict[str, dict[str, Any]]:
    try:
        out = subprocess.run(
            [waypoint_cli(), "sessions", "list"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if out.returncode != 0:
            raise OSError(out.stderr.strip()[:200])
        return {s["id"]: s for s in json.loads(out.stdout)["sessions"]}
    except (subprocess.SubprocessError, ValueError, KeyError) as e:
        raise OSError(repr(e)) from e


def send(sid: str, text: str) -> str | None:
    """Send `text` to session `sid`; return its delivery status, or None on error."""
    try:
        out = subprocess.run(
            [waypoint_cli(), "sessions", "send", sid, text],
            capture_output=True,
            text=True,
            timeout=120,
        )
        data = json.loads(out.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    session = data.get("session") if isinstance(data, dict) else None
    if not isinstance(session, dict):
        return None
    return str(session.get("send") or "delivered")


def apply_flags(cfg: dict[str, Any], args: argparse.Namespace) -> None:
    """Overlay the flags the caller passed on `cfg`."""
    for key in ("first", "second", "lead", "interval", "handoff"):
        if (value := getattr(args, key, None)) is not None:
            cfg[key] = value
    if not 0 < cfg["first"] < cfg["second"] < 1:
        sys.exit("thresholds must satisfy 0 < first < second < 1")


def effective_phase(sid: str, cycle_id: int) -> dict[str, Any]:
    ph = read_json(tdir(sid) / "phase.json", {}) or {}
    if ph.get("phase") == "user-held" or ph.get("cycle_id") == cycle_id:
        return ph
    return {"phase": "normal"}


def phase_record(sid: str) -> dict[str, Any]:
    st = read_json(tdir(sid) / "state.json", {}) or {}
    return effective_phase(sid, st.get("cycle_id", 0))


def current_phase(sid: str) -> str | None:
    return phase_record(sid).get("phase")


def write_phase(
    sid: str,
    phase: str,
    grounding: str | None = None,
    pending: bool = False,
    sent: bool = False,
) -> dict[str, Any]:
    d = tdir(sid)
    st = read_json(d / "state.json") or sys.exit(f"no watcher state for {sid}")
    ph = {"phase": phase, "phase_at": now(), "cycle_id": st["cycle_id"]}
    if pending:
        ph["grounding_pending"] = True
    if sent:
        ph["sent_at"] = ph["phase_at"]
    if grounding:
        ph["grounding"] = grounding
    elif (old := read_json(d / "phase.json", {}) or {}).get("cycle_id") == st[
        "cycle_id"
    ]:
        ph["grounding"] = old.get("grounding")
    write_json(d / "phase.json", ph)
    return ph


def epoch(iso: str | None) -> float:
    return datetime.fromisoformat(iso).timestamp() if iso else 0.0


def context_fraction(target: dict[str, Any]) -> float | None:
    cu = target.get("context_usage") or {}
    if not cu.get("used_tokens") or not cu.get("context_window_tokens"):
        return None
    return cu["used_tokens"] / cu["context_window_tokens"]


def grounding_text(cfg: dict[str, Any], action: str) -> str:
    if handoff := cfg.get("handoff"):
        return f"Re-read {handoff}, then ground yourself comprehensively and {action}"
    return f"Ground yourself comprehensively and {action}"


def self_compact_hint() -> dict[str, str]:
    """The exact command a session compacts itself with, and the rule that allows it."""
    prefix = shlex.join(
        [sys.orig_argv[0], str(Path(sys.argv[0]).absolute()), "self-compact"]
    )
    return {
        "self_compact_command": f"{prefix} --grounding proceed",
        "allow_rule": f"Bash({prefix}:*)",
    }


def alert_text(
    target: dict[str, Any],
    cfg: dict[str, Any],
    st: dict[str, Any],
    level: str,
    pct: float,
) -> str:
    cu = target["context_usage"]
    tags = f"cycle={st['cycle_id']} level={level}"
    if st.get("refill_fast"):
        tags += " refill-fast"
    notes = f" notes: {cfg['handoff']}." if cfg.get("handoff") else ""
    text = (
        f'[ctx-watch target={target["id"]} "{target.get("title") or ""}" {tags}] '
        f"context at {pct:.0%} ({cu['used_tokens'] // 1000}k of "
        f"{cu['context_window_tokens'] // 1000}k); thresholds "
        f"{cfg['first']:.0%}/{cfg['second']:.0%}.{notes} {FOLLOW}"
    )
    if level == "pair-stalled" and target["id"] == cfg["lead"]:
        text = f"{grounding_text(cfg, 'proceed')}, then handle this: {text}"
    return text


def poll(sid: str, st: dict[str, Any]) -> bool:
    """Run one poll; return False once the target is gone for good."""
    cfg = read_json(tdir(sid) / "config.json")
    sessions = list_sessions()
    target = sessions.get(sid)
    t = now()
    if target is None or target.get("status") in GONE:
        st.setdefault("gone_since", t)
        return t - st["gone_since"] < GONE_EXIT_SEC
    st.pop("gone_since", None)
    lead = sessions.get(cfg["lead"])
    if lead is None or lead.get("status") in GONE:
        if not st.get("lead_gone"):
            log(
                sid, f"lead {cfg['lead']} gone; holding alerts (set --lead to re-point)"
            )
        st["lead_gone"] = True
        return True
    st.pop("lead_gone", None)
    pct = context_fraction(target)
    if pct is None:
        return True
    cu = target["context_usage"]
    prev_pct, st["last_pct"] = st.get("last_pct"), round(pct, 4)

    ph = effective_phase(sid, st["cycle_id"])
    compacting = ph.get("phase") == "compacting"
    # Usage updates only at the target's next turn, so a reading from before the
    # pair would look like a refill, as would the last turn's before its grounding.
    fresh = not compacting or (
        not ph.get("grounding_pending")
        and epoch(cu.get("updated_at")) > ph.get("phase_at", 0)
    )

    if pct < cfg["first"] and (st["cycle"] != "armed" or (compacting and fresh)):
        st.update(cycle="armed", cycle_id=st["cycle_id"] + 1)
        st.pop("refill_fast", None)
        if prev_pct is None or pct < prev_pct:
            st["last_compaction_at"] = t
        log(
            sid,
            f"usage {pct:.0%} below first threshold; re-armed cycle {st['cycle_id']}",
        )
        return True

    if ph.get("phase") == "user-held":
        return True

    level, cycle = None, st["cycle"]
    if fresh and pct >= cfg["second"] and cycle in ("armed", "t1_sent"):
        level, cycle = "second", "t2_sent"
    elif fresh and pct >= cfg["first"] and cycle == "armed":
        level, cycle = "first", "t1_sent"
    elif (
        compacting
        and fresh
        and pct >= cfg["first"]
        and st.get("refill_key") != [st["cycle_id"], ph.get("phase_at")]
    ):
        level = "second" if pct >= cfg["second"] else "first"
    if level:
        compacted_at = st.get("last_compaction_at")
        st["refill_fast"] = compacting or (
            compacted_at is not None and t - compacted_at < REFILL_SEC
        )
    elif (
        cycle != "armed"
        and ph.get("phase") == "normal"
        and t - st.get("last_alert_at", 0) >= REMINDER_SEC
    ):
        level = "reminder-second" if cycle == "t2_sent" else "reminder-first"
    elif (
        compacting
        and target.get("status") in IDLE
        and t - ph.get("phase_at", t) >= STALL_SEC
        and st.get("stalled_key") != [st["cycle_id"], ph.get("phase_at")]
    ):
        level = "pair-stalled"
    if not level:
        return True

    status = send(cfg["lead"], alert_text(target, cfg, st, level, pct))
    if status is None:
        log(sid, f"alert {level} failed to send; retrying next poll")
        return True
    log(sid, f"alert {level} at {pct:.0%} (send: {status})")
    st.update(cycle=cycle, last_alert_at=t)
    if level == "pair-stalled":
        st["stalled_key"] = [st["cycle_id"], ph.get("phase_at")]
    elif compacting:
        st["refill_key"] = [st["cycle_id"], ph.get("phase_at")]
    return True


def spawn_detached(sid: str, argv: list[str], pid_name: str) -> int:
    d = tdir(sid)
    with open(d / "watcher.log", "a") as logf:
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), *argv],
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=logf,
            start_new_session=True,
        )
    (d / pid_name).write_text(str(proc.pid))
    return proc.pid


def remove_target(sid: str, pids: list[int | None]) -> None:
    """End the given processes of a target and delete its directory."""
    running = [pid for pid in pids if pid]
    for pid in running:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    deadline = now() + STOP_WAIT_SEC
    while (
        running
        and now() < deadline
        and (live_pid(sid) in running or pair_pid(sid) in running)
    ):
        time.sleep(0.1)
    shutil.rmtree(tdir(sid), ignore_errors=True)


def owned_targets(all_leads: bool) -> list[str]:
    """The targets this session leads; all of them with `all_leads` or outside one."""
    sids = sorted(p.name for p in ROOT.glob("*/") if p.is_dir())
    me = os.environ.get("WAYPOINT_SESSION_ID")
    if all_leads or not me:
        return sids
    return [
        s for s in sids if (read_json(tdir(s) / "config.json") or {}).get("lead") == me
    ]


def cmd_run(args: argparse.Namespace) -> None:
    sid = args.target
    path = tdir(sid) / "state.json"
    st = read_json(path) or {"cycle": "armed", "cycle_id": 1}
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    backoff = 0
    log(sid, f"watcher started (pid {os.getpid()})")
    while True:
        try:
            if not poll(sid, st):
                remove_target(sid, [pair_pid(sid)])
                return
            write_json(path, st)
            backoff = 0
        except Exception as e:  # the detached loop outlives any single bad poll
            backoff = min(max(backoff * 2, 30), MAX_BACKOFF_SEC)
            log(sid, f"poll failed ({e!r}); retrying in {backoff}s")
            time.sleep(backoff)
            continue
        time.sleep(read_json(tdir(sid) / "config.json", {}).get("interval", 60))


def cmd_start(args: argparse.Namespace) -> None:
    sid = args.target
    me = os.environ.get("WAYPOINT_SESSION_ID")
    d = tdir(sid)
    # A restart keeps the watcher's settings unless a flag changes them.
    cfg = {
        "target": sid,
        "lead": me,
        **DEFAULTS,
        **(read_json(d / "config.json") or {}),
    }
    apply_flags(cfg, args)
    lead = cfg["lead"]
    if not lead:
        sys.exit("no --lead and no WAYPOINT_SESSION_ID")
    if pid := live_pid(sid):
        sys.exit(f"a watcher for {sid} is already running (pid {pid})")
    try:
        sessions = list_sessions()
    except OSError as e:
        sys.exit(f"cannot list sessions through {waypoint_cli()}: {e}")
    for who in (sid, lead):
        if who not in sessions:
            sys.exit(f"session {who} not found")
    d.mkdir(parents=True, exist_ok=True)
    (ROOT / "README.md").write_text(README.format(script=Path(sys.argv[0]).absolute()))
    write_json(d / "config.json", cfg)
    if not (d / "state.json").exists():
        write_json(d / "state.json", {"cycle": "armed", "cycle_id": 1})
    out = {
        "pid": spawn_detached(sid, ["run", "--target", sid], "watcher.pid"),
        **cfg,
        "dir": str(d),
    }
    if context_fraction(sessions[sid]) is None:
        out["warning"] = NO_USAGE
    if sid == me:
        out.update(self_compact_hint())
    print(json.dumps(out, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    sids = [args.target] if args.target else owned_targets(args.all_leads)
    try:
        sessions = list_sessions()
    except OSError:
        sessions = None
    for sid in sids:
        st = read_json(tdir(sid) / "state.json", {}) or {}
        out: dict[str, Any] = {
            "target": sid,
            "running": live_pid(sid) is not None,
            "pair_running": pair_pid(sid) is not None,
            "config": read_json(tdir(sid) / "config.json"),
            "state": st,
            "phase": effective_phase(sid, st.get("cycle_id", 0)),
        }
        if sessions is not None:
            target = sessions.get(sid) or {}
            pct = context_fraction(target)
            out["status"] = target.get("status")
            out["usage"] = (
                None
                if pct is None
                else {**target["context_usage"], "pct": round(pct, 4)}
            )
        if sid == os.environ.get("WAYPOINT_SESSION_ID"):
            out.update(self_compact_hint())
        print(json.dumps(out, indent=2))
        logf = tdir(sid) / "watcher.log"
        if logf.exists():
            print("".join(logf.read_text().splitlines(keepends=True)[-5:]))


def cmd_set(args: argparse.Namespace) -> None:
    path = tdir(args.target) / "config.json"
    cfg = read_json(path) or sys.exit(f"no watcher config for {args.target}")
    apply_flags(cfg, args)
    write_json(path, cfg)
    print(json.dumps(cfg, indent=2))


def cmd_phase(args: argparse.Namespace) -> None:
    print(json.dumps(write_phase(args.target, args.phase, args.grounding)))


def spawn_helper(sid: str, grounding: str, *flags: str) -> None:
    argv = ["pair", "--target", sid, "--grounding", grounding, *flags]
    spawn_detached(sid, argv, "pair.pid")


def send_queued_pair(
    sid: str, grounding: str, grounding_only: bool, target_idle: bool
) -> None:
    """Send a pair to a backend that queues /compact until its turn ends.

    A running turn consumes plain text, so its grounding waits for the turn's end.
    """
    held = False
    if not grounding_only:
        status = send(sid, "/compact")
        if status in (None, "unknown"):
            sys.exit(
                f"/compact not confirmed (send: {status}); grounding not scheduled"
            )
        print(f"'/compact': {status}")
        held = status == "held"
    if held or target_idle:
        write_phase(sid, "compacting", grounding, sent=True)
        cfg = read_json(tdir(sid) / "config.json") or {}
        status = send(sid, grounding_text(cfg, grounding))
        if status is not None:
            print(f"grounding: {status}")
            if held or status == "held":
                # The grounding is queued too, so the lead needs no second one.
                pair_alert(sid, "pair-held", "the pair is held for the human", False)
            return
    write_phase(sid, "compacting", grounding, pending=True, sent=True)
    spawn_helper(sid, grounding, "--after-compact")
    print("the grounding follows once the turn has ended")


def begin_pair(
    sid: str, grounding: str, grounding_only: bool = False, own_turn: bool = False
) -> None:
    """Send a session's compaction pair, or hand it to a detached helper."""
    if not (tdir(sid) / "state.json").exists():
        sys.exit(f"no watcher for {sid}; run start --target {sid}")
    # Two callers at once must not both find nothing in progress.
    with open(tdir(sid) / "pair.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if pid := pair_pid(sid):
            sys.exit(f"a compaction of {sid} is already running (pid {pid})")
        sent_at = phase_record(sid).get("sent_at", 0)
        if (left := PAIR_RECENT_SEC - (now() - sent_at)) > 0:
            sys.exit(
                f"a compaction pair went to {sid} just now; "
                f"wait {left:.0f}s and re-run only if it is still needed"
            )
        try:
            target = list_sessions().get(sid)
        except OSError as e:
            sys.exit(f"cannot list sessions through {waypoint_cli()}: {e}")
        if target is None or target.get("status") in GONE:
            sys.exit(f"session {sid} is gone")
        if target.get("transport") == "tmux":
            sys.exit(
                "a tmux session has no reliable idle status; "
                "send it /compact and the grounding message yourself"
            )
        status = target.get("status")
        # A Bash dialog answered in the terminal leaves the caller at waiting_input
        # until the tool returns, and a real dialog comes back held.
        if status in ("error", "interrupted") or (
            status == "waiting_input" and not own_turn
        ):
            sys.exit(f"session {sid} is {status} and needs a person first")
        if target.get("transport") in QUEUES_COMPACT:
            target_idle = status == "idle" and not own_turn
            send_queued_pair(sid, grounding, grounding_only, target_idle)
            return
        write_phase(sid, "parking", grounding)
        spawn_helper(sid, grounding, *["--grounding-only"] * grounding_only)
        print(
            "compaction follows once this turn ends"
            if own_turn
            else f"compaction of {sid} follows once it is idle"
        )


def cmd_compact(args: argparse.Namespace) -> None:
    if args.target == os.environ.get("WAYPOINT_SESSION_ID"):
        sys.exit("that is this session; use self-compact")
    begin_pair(args.target, args.grounding, args.grounding_only)


def cmd_self_compact(args: argparse.Namespace) -> None:
    sid = os.environ.get("WAYPOINT_SESSION_ID") or sys.exit("no WAYPOINT_SESSION_ID")
    begin_pair(sid, args.grounding, own_turn=True)


def quiet_sec(target: dict[str, Any]) -> float:
    return QUIET_SEC.get(target.get("transport", ""), PAIR_QUIET_SEC)


def settle_sec(target: dict[str, Any]) -> float:
    return SETTLE_SEC.get(target.get("transport", ""), PAIR_SETTLE_SEC)


def await_turn_end(sid: str, timeout: float) -> None:
    """Block until the target's turn ends; each CLI call costs too much to poll with."""
    try:
        out = subprocess.run(
            [waypoint_cli(), "sessions", "wait", sid]
            + ["--until", ",".join(TURN_ENDED), "--timeout", str(int(timeout) + 1)],
            capture_output=True,
            text=True,
            timeout=timeout + 60,
        )
        status = json.loads(out.stdout)["session"]["status"]
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        status = None
    # A server that is down exits like a timeout and prints no status.
    if status not in TURN_ENDED:
        time.sleep(PAIR_POLL_SEC)


def deliver(
    sid: str,
    text: str,
    quiet_for: Callable[[dict[str, Any]], float],
    before_send: Callable[[], None],
    give_up: float = PAIR_GIVE_UP_SEC,
    send_in_focus: bool = False,
) -> str:
    """Send `text` once the target is idle and quiet; return the delivery status.

    Readiness is checked before every attempt, so a refused send is retried only
    while the target is ready for it. `send_in_focus` sends during Focus, where the
    message waits for the human behind whatever is already held.
    """
    deadline = now() + give_up
    seen = "unreadable"
    while now() < deadline:
        try:
            target = list_sessions().get(sid)
        except OSError:
            time.sleep(PAIR_POLL_SEC)
            continue
        if target is None or target.get("status") in GONE:
            raise TargetGone
        seen = str(target.get("status"))
        if seen in ("error", "interrupted"):
            raise PairFailed(f"the target is {seen}")
        focus = target.get("focus")
        if focus and not send_in_focus:
            seen = "in Focus"
            time.sleep(PAIR_POLL_SEC)
            continue
        if not focus:
            if seen != "idle":
                await_turn_end(sid, deadline - now())
                continue
            quiet_left = quiet_for(target) - (
                now() - epoch(target.get("last_event_at"))
            )
            if quiet_left > 0:
                time.sleep(quiet_left)
                continue
        before_send()
        status = send(sid, text)
        if status is not None:
            return status
        log(sid, f"{text!r} refused; retrying")
        deadline = min(deadline, now() + PAIR_GIVE_UP_SEC)
        time.sleep(PAIR_POLL_SEC)
    raise PairFailed(f"gave up with the target {seen}")


def pair_alert(sid: str, level: str, reason: str, compact_sent: bool) -> None:
    """Tell a live lead how a pair ended; a send to an exited session relaunches it."""
    cfg = read_json(tdir(sid) / "config.json") or {}
    st = read_json(tdir(sid) / "state.json", {}) or {}
    log(sid, f"{level}: {reason}")
    try:
        sessions = list_sessions()
    except OSError:
        return
    lead = sessions.get(cfg.get("lead", ""))
    if lead is None or lead.get("status") in GONE:
        return
    title = (sessions.get(sid) or {}).get("title") or ""
    text = (
        f'[ctx-watch target={sid} "{title}" cycle={st.get("cycle_id")} level={level}] '
        f"{reason}. {FOLLOW}"
    )
    if compact_sent and sid == cfg["lead"]:
        text = f"{grounding_text(cfg, 'proceed')}, then handle this: {text}"
    log(sid, f"alert {level} (send: {send(cfg['lead'], text)})")


def cmd_pair(args: argparse.Namespace) -> None:
    """Send what has to wait for the target's turn: /compact, then the grounding."""
    sid, grounding = args.target, args.grounding
    cfg = read_json(tdir(sid) / "config.json") or {}
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    compact_sent = args.after_compact

    def require_parking() -> None:
        if (phase := current_phase(sid)) != "parking":
            raise PairFailed(f"the phase changed to {phase}")

    def stamp_compacting() -> None:
        # A reading from the compaction or the turn before it is older than this
        # stamp, so it cannot look like a refill. A re-arm already lapsed the phase
        # when usage fell, and the grounding still goes out.
        ph = phase_record(sid)
        if ph.get("phase") == "parking" or ph.get("grounding_pending"):
            write_phase(sid, "compacting", grounding)

    def before_grounding() -> None:
        # A refused send comes back here with the phase already stamped.
        if args.grounding_only and current_phase(sid) != "compacting":
            require_parking()
        stamp_compacting()

    try:
        if not (args.grounding_only or args.after_compact):
            status = deliver(sid, "/compact", quiet_sec, require_parking)
            compact_sent = True
            log(sid, f"/compact sent (send: {status})")
            if status == "held":
                stamp_compacting()
                pair_alert(sid, "pair-held", "/compact is held for the human", True)
                return
        # Some backends read idle for a moment between the steps of a compaction.
        quiet_for = quiet_sec if args.grounding_only else settle_sec
        # A queued /compact can wait out a long turn, and blocking on it is cheap.
        give_up = PAIR_TURN_GIVE_UP_SEC if args.after_compact else PAIR_GIVE_UP_SEC
        text = grounding_text(cfg, grounding)
        status = deliver(
            sid, text, quiet_for, before_grounding, give_up, args.after_compact
        )
        log(sid, f"grounding sent (send: {status})")
        if status == "held":
            # The grounding is queued, so the lead needs no second one.
            pair_alert(sid, "pair-held", "the grounding is held for the human", False)
    except TargetGone:
        log(sid, "target gone; pair abandoned")
        if phase_record(sid).get("grounding_pending"):
            stamp_compacting()
    except Exception as e:  # a detached helper's only report is its alert
        reason = str(e) if isinstance(e, PairFailed) else f"helper error: {e!r}"
        if compact_sent:
            stamp_compacting()
        elif current_phase(sid) == "parking":
            write_phase(sid, "normal")
        went_out = "/compact went out" if compact_sent else "nothing went out"
        pair_alert(sid, "pair-failed", f"{reason}; {went_out}", compact_sent)
    finally:
        (tdir(sid) / "pair.pid").unlink(missing_ok=True)


def cmd_stop(args: argparse.Namespace) -> None:
    for sid in owned_targets(args.all_leads) if args.all else [args.target]:
        remove_target(sid, [live_pid(sid), pair_pid(sid)])
        print(f"stopped {sid}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--target", required=True)
    s.add_argument("--lead")
    s.add_argument("--first", type=float)
    s.add_argument("--second", type=float)
    s.add_argument("--interval", type=int)
    s.add_argument("--handoff", help="where the target keeps its notes")
    s.set_defaults(fn=cmd_start)
    s = sub.add_parser("run")
    s.add_argument("--target", required=True)
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("status")
    s.add_argument("--target")
    s.add_argument("--all-leads", action="store_true")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("set")
    s.add_argument("--target", required=True)
    s.add_argument("--first", type=float)
    s.add_argument("--second", type=float)
    s.add_argument("--lead")
    s.add_argument("--handoff")
    s.set_defaults(fn=cmd_set)
    s = sub.add_parser("phase")
    s.add_argument("--target", required=True)
    s.add_argument("phase", choices=PHASES)
    s.add_argument("--grounding", choices=GROUNDINGS)
    s.set_defaults(fn=cmd_phase)
    s = sub.add_parser("compact")
    s.add_argument("--target", required=True)
    s.add_argument("--grounding", choices=GROUNDINGS, required=True)
    s.add_argument("--grounding-only", action="store_true")
    s.set_defaults(fn=cmd_compact)
    s = sub.add_parser("self-compact")
    s.add_argument("--grounding", choices=GROUNDINGS, required=True)
    s.set_defaults(fn=cmd_self_compact)
    s = sub.add_parser("pair")
    s.add_argument("--target", required=True)
    s.add_argument("--grounding", choices=GROUNDINGS, required=True)
    s.add_argument("--grounding-only", action="store_true")
    s.add_argument("--after-compact", action="store_true")
    s.set_defaults(fn=cmd_pair)
    s = sub.add_parser("stop")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--target")
    g.add_argument("--all", action="store_true")
    s.add_argument("--all-leads", action="store_true")
    s.set_defaults(fn=cmd_stop)
    args = p.parse_args()
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        args.fn(args)
    except OSError as e:
        sys.exit(f"{e} (state directory {ROOT}; set CTX_WATCH_DIR to change it)")


if __name__ == "__main__":
    main()
