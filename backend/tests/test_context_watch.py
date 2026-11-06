"""The context watcher behind the waypoint-context skill.

The script ships inside the skill rather than the backend package, so it is loaded
by path. `poll` and the compaction helper run against a fake session table; the last
tests cross the real process boundary with a fake `waypoint` executable.
"""

import importlib.util
import json
import os
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / ".agents/skills/waypoint-context/scripts/ctx_watch.py"
)
T, L = "target-1", "lead-1"


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat().replace("+00:00", "Z")


class Fake:
    """A session table and a clock the script reads instead of the real ones."""

    def __init__(self, cw: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        self.cw = cw
        self.t = 1_000_000.0
        self.sent: list[tuple[str, str]] = []
        self.send_results: list[str | None] = []
        self.list_error: OSError | None = None
        self.on_sleep: list[Any] = []
        self.sessions: dict[str, dict[str, Any]] = {
            T: self.session(T),
            L: self.session(L),
        }
        monkeypatch.setattr(cw, "now", lambda: self.t)
        monkeypatch.setattr(cw, "list_sessions", self.list_sessions)
        monkeypatch.setattr(cw, "send", self.send)
        monkeypatch.setattr(cw.time, "sleep", self.sleep)

    def session(self, sid: str, **fields: Any) -> dict[str, Any]:
        return {
            "id": sid,
            "title": sid,
            "status": "idle",
            "focus": False,
            "transport": "claude_tty",
            "last_event_at": None,
            "context_usage": None,
            **fields,
        }

    def usage(self, pct: float, at: float | None = None) -> None:
        self.sessions[T]["context_usage"] = {
            "used_tokens": int(pct * 1000) * 1000,
            "context_window_tokens": 1_000_000,
            "updated_at": _iso(self.t if at is None else at),
        }

    def list_sessions(self) -> dict[str, dict[str, Any]]:
        if self.list_error:
            raise self.list_error
        return self.sessions

    def send(self, sid: str, text: str) -> str | None:
        self.sent.append((sid, text))
        return self.send_results.pop(0) if self.send_results else "delivered"

    def sleep(self, seconds: float) -> None:
        self.t += seconds
        if self.on_sleep:
            self.on_sleep.pop(0)()

    def levels(self) -> list[str]:
        return [
            text.split("level=")[1].split("]")[0].split(" ")[0]
            for _, text in self.sent
            if "level=" in text
        ]


@pytest.fixture
def cw(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location("ctx_watch_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path / "state")
    monkeypatch.delenv("WAYPOINT_SESSION_ID", raising=False)
    monkeypatch.delenv("CTX_WATCH_WAYPOINT", raising=False)
    return module


@pytest.fixture
def fake(cw: ModuleType, monkeypatch: pytest.MonkeyPatch) -> Fake:
    return Fake(cw, monkeypatch)


def _watch(cw: ModuleType, sid: str = T, lead: str = L, **cfg: Any) -> dict[str, Any]:
    d = cw.tdir(sid)
    d.mkdir(parents=True)
    cw.write_json(
        d / "config.json",
        {
            "target": sid,
            "lead": lead,
            "first": 0.6,
            "second": 0.7,
            "interval": 60,
            "handoff": None,
            **cfg,
        },
    )
    st = {"cycle": "armed", "cycle_id": 1}
    cw.write_json(d / "state.json", st)
    return st


def _sync(cw: ModuleType, st: dict[str, Any], sid: str = T) -> None:
    cw.write_json(cw.tdir(sid) / "state.json", st)


# ---- poll: thresholds and cycles -----------------------------------------------


def test_first_then_second_alert_once_each(cw: ModuleType, fake: Fake) -> None:
    st = _watch(cw)
    fake.usage(0.65)
    assert cw.poll(T, st)
    fake.t += 60
    assert cw.poll(T, st)
    fake.usage(0.75)
    fake.t += 60
    assert cw.poll(T, st)
    fake.t += 60
    assert cw.poll(T, st)
    assert fake.levels() == ["first", "second"]
    assert all(sid == L for sid, _ in fake.sent)
    assert st["cycle"] == "t2_sent"


def test_a_reminder_follows_after_the_reminder_interval(
    cw: ModuleType, fake: Fake
) -> None:
    st = _watch(cw)
    fake.usage(0.65)
    cw.poll(T, st)
    fake.t += cw.REMINDER_SEC - 1
    cw.poll(T, st)
    assert fake.levels() == ["first"]
    fake.t += 2
    cw.poll(T, st)
    assert fake.levels() == ["first", "reminder-first"]


def test_falling_below_first_rearms_and_lapses_the_phase(
    cw: ModuleType, fake: Fake
) -> None:
    st = _watch(cw)
    fake.usage(0.65)
    cw.poll(T, st)
    _sync(cw, st)
    cw.write_phase(T, "deferred")
    fake.usage(0.2)
    cw.poll(T, st)
    _sync(cw, st)
    assert (st["cycle"], st["cycle_id"]) == ("armed", 2)
    assert cw.current_phase(T) == "normal"


def test_user_held_outlives_a_rearm_and_silences_alerts(
    cw: ModuleType, fake: Fake
) -> None:
    st = _watch(cw)
    fake.usage(0.65)
    cw.poll(T, st)
    _sync(cw, st)
    cw.write_phase(T, "user-held")
    fake.usage(0.2)
    cw.poll(T, st)
    _sync(cw, st)
    assert cw.current_phase(T) == "user-held"
    fake.usage(0.9)
    fake.t += cw.REMINDER_SEC
    cw.poll(T, st)
    assert fake.levels() == ["first"]


def test_an_alert_that_fails_to_send_is_retried_and_not_recorded(
    cw: ModuleType, fake: Fake
) -> None:
    st = _watch(cw)
    fake.usage(0.65)
    fake.send_results = [None]
    cw.poll(T, st)
    assert st["cycle"] == "armed"
    cw.poll(T, st)
    assert st["cycle"] == "t1_sent"
    assert fake.levels() == ["first", "first"]


def test_a_gone_target_ends_the_watcher_after_the_grace(
    cw: ModuleType, fake: Fake
) -> None:
    st = _watch(cw)
    fake.sessions[T]["status"] = "exited"
    assert cw.poll(T, st)
    fake.t += cw.GONE_EXIT_SEC
    assert not cw.poll(T, st)


def test_a_gone_lead_holds_alerts(cw: ModuleType, fake: Fake) -> None:
    st = _watch(cw)
    fake.usage(0.9)
    fake.sessions[L]["status"] = "exited"
    assert cw.poll(T, st)
    assert fake.sent == []


# ---- poll: during and after a pair -----------------------------------------------


def _in_t1(cw: ModuleType, fake: Fake) -> dict[str, Any]:
    st = _watch(cw)
    fake.usage(0.65)
    cw.poll(T, st)
    _sync(cw, st)
    fake.sent.clear()
    return st


def test_a_fresh_high_reading_while_parking_is_no_refill_and_no_stall(
    cw: ModuleType, fake: Fake
) -> None:
    st = _in_t1(cw, fake)
    cw.write_phase(T, "parking", "proceed")
    fake.t += cw.STALL_SEC + 1
    fake.usage(0.66)
    cw.poll(T, st)
    assert fake.sent == []


def test_a_low_reading_while_parking_in_an_armed_cycle_leaves_the_phase(
    cw: ModuleType, fake: Fake
) -> None:
    st = _watch(cw)
    cw.write_phase(T, "parking", "proceed")
    fake.t += 5
    fake.usage(0.2)
    cw.poll(T, st)
    _sync(cw, st)
    assert (st["cycle"], st["cycle_id"]) == ("armed", 1)
    assert cw.current_phase(T) == "parking"


def test_a_fresh_low_reading_after_the_stamp_rearms_an_armed_cycle(
    cw: ModuleType, fake: Fake
) -> None:
    st = _watch(cw)
    cw.write_phase(T, "compacting", "proceed")
    fake.usage(0.2, at=fake.t - 1)
    cw.poll(T, st)
    _sync(cw, st)
    assert st["cycle_id"] == 1
    fake.t += 5
    fake.usage(0.2)
    cw.poll(T, st)
    _sync(cw, st)
    assert st["cycle_id"] == 2
    assert cw.current_phase(T) == "normal"


def test_a_reading_from_before_the_stamp_is_stale(cw: ModuleType, fake: Fake) -> None:
    st = _in_t1(cw, fake)
    fake.usage(0.66, at=fake.t - 1)
    cw.write_phase(T, "compacting", "proceed")
    fake.t += 60
    cw.poll(T, st)
    assert fake.sent == []


def test_a_fresh_high_reading_after_the_stamp_is_a_refill(
    cw: ModuleType, fake: Fake
) -> None:
    st = _in_t1(cw, fake)
    cw.write_phase(T, "compacting", "proceed")
    fake.t += 60
    fake.usage(0.66)
    cw.poll(T, st)
    assert fake.levels() == ["first"]
    assert "refill-fast" in fake.sent[0][1]
    fake.t += 60
    cw.poll(T, st)
    assert fake.levels() == ["first"]


def test_a_rise_soon_after_a_compaction_is_tagged_refill_fast(
    cw: ModuleType, fake: Fake
) -> None:
    st = _in_t1(cw, fake)
    fake.usage(0.2)
    cw.poll(T, st)
    fake.t += cw.REFILL_SEC - 10
    fake.usage(0.65)
    cw.poll(T, st)
    assert fake.levels() == ["first"]
    assert "refill-fast" in fake.sent[0][1]


def test_a_stalled_pair_alerts_once_per_stamp(cw: ModuleType, fake: Fake) -> None:
    st = _in_t1(cw, fake)
    fake.usage(0.66, at=fake.t - 1)
    cw.write_phase(T, "compacting", "proceed")
    fake.t += cw.STALL_SEC
    cw.poll(T, st)
    fake.t += 60
    cw.poll(T, st)
    assert fake.levels() == ["pair-stalled"]
    cw.write_phase(T, "compacting", "proceed")
    fake.t += cw.STALL_SEC
    cw.poll(T, st)
    assert fake.levels() == ["pair-stalled", "pair-stalled"]


def test_a_stall_on_the_lead_itself_is_prefixed_with_the_grounding(
    cw: ModuleType, fake: Fake
) -> None:
    st = _watch(cw, lead=T)
    fake.usage(0.65)
    cw.poll(T, st)
    _sync(cw, st)
    fake.sent.clear()
    fake.usage(0.65, at=fake.t - 1)
    cw.write_phase(T, "compacting", "proceed")
    fake.t += cw.STALL_SEC
    cw.poll(T, st)
    assert fake.sent[0][1].startswith(
        "Ground yourself comprehensively and proceed, then"
    )


# ---- text ------------------------------------------------------------------------


def test_grounding_text_names_the_notes_when_set(cw: ModuleType) -> None:
    assert (
        cw.grounding_text({"handoff": None}, "park")
        == "Ground yourself comprehensively and park"
    )
    assert (
        cw.grounding_text({"handoff": "docs/notes.md"}, "proceed")
        == "Re-read docs/notes.md, then ground yourself comprehensively and proceed"
    )


def test_an_alert_names_the_skill_and_the_notes(cw: ModuleType, fake: Fake) -> None:
    st = _watch(cw, handoff="docs/notes.md")
    fake.usage(0.65)
    cw.poll(T, st)
    text = fake.sent[0][1]
    assert text.startswith(f'[ctx-watch target={T} "{T}" cycle=1 level=first]')
    assert "notes: docs/notes.md." in text
    assert "waypoint-context skill's references/alerts.md" in text


def test_the_skill_carries_no_project_specific_text() -> None:
    skill = _SCRIPT.parents[1]
    for path in skill.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            text = path.read_text().lower()
            for word in ("flowmesh", "fm2-", "architect-handoff", "impl-plan"):
                assert word not in text, f"{word} in {path}"


# ---- the compaction helper ---------------------------------------------------------


def _pair(
    cw: ModuleType, grounding: str = "proceed", grounding_only: bool = False
) -> None:
    cw.cmd_pair(
        cw.argparse.Namespace(
            target=T, grounding=grounding, grounding_only=grounding_only
        )
    )


def _parked(cw: ModuleType, fake: Fake, **cfg: Any) -> dict[str, Any]:
    st = _watch(cw, **cfg)
    fake.usage(0.65, at=fake.t - 1000)
    fake.sessions[T]["last_event_at"] = _iso(fake.t - 1000)
    cw.write_phase(T, "parking", "proceed")
    (cw.tdir(T) / "pair.pid").write_text("1")
    return st


def test_a_pair_sends_compact_then_stamps_then_grounds(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _parked(cw, fake)
    phases_at_send: list[str | None] = []
    real_send = fake.send

    def spying_send(sid: str, text: str) -> str | None:
        phases_at_send.append(cw.current_phase(T))
        return real_send(sid, text)

    monkeypatch.setattr(cw, "send", spying_send)
    _pair(cw)
    assert [text for _, text in fake.sent] == [
        "/compact",
        "Ground yourself comprehensively and proceed",
    ]
    assert all(sid == T for sid, _ in fake.sent)
    assert phases_at_send == ["parking", "compacting"]
    assert not (cw.tdir(T) / "pair.pid").exists()


def test_a_pair_waits_for_quiet_and_for_the_compaction_to_end(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _parked(cw, fake)
    fake.sessions[T]["last_event_at"] = _iso(fake.t)
    fake.sessions[T]["status"] = "running"

    def end_turn() -> None:
        fake.sessions[T]["status"] = "idle"
        fake.sessions[T]["last_event_at"] = _iso(fake.t)

    fake.on_sleep = [end_turn]
    sent_at: list[float] = []
    real_send = fake.send

    def timed_send(sid: str, text: str) -> str | None:
        sent_at.append(fake.t)
        if text == "/compact":
            fake.sessions[T]["status"] = "running"
            fake.on_sleep = [
                lambda: None,
                lambda: fake.sessions[T].update(status="idle"),
            ]
        return real_send(sid, text)

    monkeypatch.setattr(cw, "send", timed_send)
    start = fake.t
    _pair(cw)
    assert sent_at[0] - start >= cw.PAIR_QUIET_SEC
    assert sent_at[1] - sent_at[0] >= 2 * cw.PAIR_POLL_SEC
    assert [text for _, text in fake.sent][0] == "/compact"


def test_the_grounding_waits_out_an_idle_flicker_during_the_compaction(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _parked(cw, fake)
    real_send = fake.send
    sent_at: dict[str, float] = {}

    def timed_send(sid: str, text: str) -> str | None:
        sent_at[text] = fake.t
        if text == "/compact":
            fake.sessions[T]["last_event_at"] = _iso(fake.t)
        return real_send(sid, text)

    monkeypatch.setattr(cw, "send", timed_send)
    _pair(cw)
    grounding = "Ground yourself comprehensively and proceed"
    assert sent_at[grounding] - sent_at["/compact"] >= cw.PAIR_POLL_SEC


@pytest.mark.parametrize(
    ("fields", "named"),
    [
        ({"status": "waiting_input"}, "waiting_input"),
        ({"focus": True}, "in Focus"),
        ({"status": "running"}, "running"),
    ],
)
def test_a_target_that_never_gets_ready_fails_the_pair_with_nothing_sent(
    cw: ModuleType, fake: Fake, fields: dict[str, Any], named: str
) -> None:
    _parked(cw, fake)
    fake.sessions[T].update(fields)
    _pair(cw)
    assert [sid for sid, _ in fake.sent] == [L]
    assert fake.levels() == ["pair-failed"]
    assert named in fake.sent[0][1]
    assert cw.current_phase(T) == "normal"


@pytest.mark.parametrize("status", ["error", "interrupted"])
def test_a_stopped_target_fails_the_pair_at_once(
    cw: ModuleType, fake: Fake, status: str
) -> None:
    _parked(cw, fake)
    fake.sessions[T]["status"] = status
    start = fake.t
    _pair(cw)
    assert fake.t == start
    assert fake.levels() == ["pair-failed"]


def test_a_failed_pair_lets_reminders_resume(cw: ModuleType, fake: Fake) -> None:
    st = _in_t1(cw, fake)
    fake.usage(0.65, at=fake.t - 1000)
    fake.sessions[T]["last_event_at"] = _iso(fake.t - 1000)
    fake.sessions[T]["status"] = "running"
    cw.write_phase(T, "parking", "proceed")
    _pair(cw)
    fake.sent.clear()
    fake.t += cw.REMINDER_SEC
    cw.poll(T, st)
    assert fake.levels() == ["reminder-first"]


def test_a_gone_target_abandons_the_pair_with_no_alert(
    cw: ModuleType, fake: Fake
) -> None:
    _parked(cw, fake)
    fake.sessions[T]["status"] = "exited"
    _pair(cw)
    assert fake.sent == []
    assert not (cw.tdir(T) / "pair.pid").exists()


def test_a_failed_list_is_not_ready_rather_than_fatal(
    cw: ModuleType, fake: Fake
) -> None:
    _parked(cw, fake)
    fake.list_error = OSError("backend restarting")

    def recover() -> None:
        fake.list_error = None

    fake.on_sleep = [lambda: None, recover]
    _pair(cw)
    assert [text for _, text in fake.sent][0] == "/compact"


def test_grounding_only_skips_compact_and_still_checks_the_phase(
    cw: ModuleType, fake: Fake
) -> None:
    _parked(cw, fake)
    _pair(cw, grounding_only=True)
    assert [text for _, text in fake.sent] == [
        "Ground yourself comprehensively and proceed"
    ]
    assert cw.current_phase(T) == "compacting"


def test_a_refused_send_retries_while_the_target_is_ready(
    cw: ModuleType, fake: Fake
) -> None:
    _parked(cw, fake)
    fake.send_results = [None, "delivered", None, "delivered"]
    _pair(cw)
    assert [text for _, text in fake.sent] == [
        "/compact",
        "/compact",
        "Ground yourself comprehensively and proceed",
        "Ground yourself comprehensively and proceed",
    ]


def test_a_refused_send_is_not_repeated_while_the_target_is_busy(
    cw: ModuleType, fake: Fake
) -> None:
    _parked(cw, fake)
    fake.send_results = [None]
    fake.on_sleep = [lambda: fake.sessions[T].update(status="running")]
    _pair(cw)
    assert [text for _, text in fake.sent][:1] == ["/compact"]
    assert sum(text == "/compact" for _, text in fake.sent) == 1
    assert fake.levels() == ["pair-failed"]
    assert cw.current_phase(T) == "normal"


def test_a_refused_grounding_to_the_deadline_leaves_compacting(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = _parked(cw, fake)

    def refusing_send(sid: str, text: str) -> str | None:
        fake.sent.append((sid, text))
        return "delivered" if text == "/compact" or sid == L else None

    monkeypatch.setattr(cw, "send", refusing_send)
    _pair(cw)
    assert fake.levels() == ["pair-failed"]
    assert cw.current_phase(T) == "compacting"
    assert cw.effective_phase(T, st["cycle_id"])["grounding"] == "proceed"


def test_a_compaction_that_never_ends_fails_the_pair_and_leaves_compacting(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _parked(cw, fake)
    real_send = fake.send

    def starting_send(sid: str, text: str) -> str | None:
        if text == "/compact":
            fake.sessions[T]["status"] = "running"
        return real_send(sid, text)

    monkeypatch.setattr(cw, "send", starting_send)
    _pair(cw)
    assert [text for sid, text in fake.sent if sid == T] == ["/compact"]
    assert fake.levels() == ["pair-failed"]
    assert cw.current_phase(T) == "compacting"


def test_an_unknown_compact_is_not_resent(cw: ModuleType, fake: Fake) -> None:
    _parked(cw, fake)
    fake.send_results = ["unknown"]
    _pair(cw)
    assert [text for _, text in fake.sent] == [
        "/compact",
        "Ground yourself comprehensively and proceed",
    ]


def test_a_held_compact_counts_as_sent_and_a_stall_can_follow(
    cw: ModuleType, fake: Fake
) -> None:
    st = _parked(cw, fake)
    st.update(cycle="t1_sent")
    _sync(cw, st)
    fake.send_results = ["held"]
    _pair(cw)
    assert fake.sent[0] == (T, "/compact")
    assert fake.levels() == ["pair-held"]
    assert len(fake.sent) == 2
    assert cw.current_phase(T) == "compacting"
    fake.t += cw.STALL_SEC
    fake.sent.clear()
    cw.poll(T, st)
    assert fake.levels() == ["pair-stalled"]


def test_a_held_grounding_alerts_and_stays_compacting(
    cw: ModuleType, fake: Fake
) -> None:
    _parked(cw, fake)
    fake.send_results = ["delivered", "held"]
    _pair(cw)
    assert fake.levels() == ["pair-held"]
    assert cw.current_phase(T) == "compacting"


def test_a_phase_changed_before_compact_sends_nothing(
    cw: ModuleType, fake: Fake
) -> None:
    _parked(cw, fake)
    cw.write_phase(T, "user-held")
    _pair(cw)
    assert [sid for sid, _ in fake.sent] == [L]
    assert fake.levels() == ["pair-failed"]
    assert cw.current_phase(T) == "user-held"


def test_a_rearm_after_compact_still_sends_the_grounding_without_a_stamp(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = _parked(cw, fake)
    real_send = fake.send

    def rearming_send(sid: str, text: str) -> str | None:
        if text == "/compact":
            st["cycle_id"] += 1
            _sync(cw, st)
        return real_send(sid, text)

    monkeypatch.setattr(cw, "send", rearming_send)
    _pair(cw)
    assert fake.sent[-1] == (T, "Ground yourself comprehensively and proceed")
    assert cw.current_phase(T) == "normal"


def test_the_self_prefix_comes_only_once_compact_has_gone_out(
    cw: ModuleType, fake: Fake
) -> None:
    _parked(cw, fake, lead=T)
    fake.sessions[T]["status"] = "running"
    _pair(cw)
    assert fake.sent[0][1].startswith("[ctx-watch")

    fake.sent.clear()
    fake.sessions[T]["status"] = "idle"
    fake.t += 100
    cw.write_phase(T, "parking", "proceed")
    fake.send_results = ["held"]
    _pair(cw)
    assert fake.sent[1][1].startswith(
        "Ground yourself comprehensively and proceed, then"
    )


def test_no_alert_goes_to_a_gone_lead(cw: ModuleType, fake: Fake) -> None:
    _parked(cw, fake)
    fake.sessions[T]["status"] = "running"
    fake.sessions[L]["status"] = "exited"
    _pair(cw)
    assert fake.sent == []


def test_a_helper_error_is_reported_and_the_pid_file_removed(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _parked(cw, fake)

    def boom(*_: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(cw, "deliver", boom)
    _pair(cw)
    assert fake.levels() == ["pair-failed"]
    assert "boom" in fake.sent[0][1]
    assert not (cw.tdir(T) / "pair.pid").exists()


# ---- commands --------------------------------------------------------------------


def _capture_spawn(cw: ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    spawned: list[list[str]] = []

    def spawn(sid: str, argv: list[str], pid_name: str) -> int:
        spawned.append(argv)
        (cw.tdir(sid) / pid_name).write_text("999999")
        return 999999

    monkeypatch.setattr(cw, "spawn_detached", spawn)
    return spawned


def test_compact_sets_parking_and_spawns_the_helper(
    cw: ModuleType,
    fake: Fake,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _watch(cw)
    spawned = _capture_spawn(cw, monkeypatch)
    monkeypatch.setenv("WAYPOINT_SESSION_ID", L)
    cw.cmd_compact(
        cw.argparse.Namespace(target=T, grounding="park", grounding_only=True)
    )
    assert spawned == [
        ["pair", "--target", T, "--grounding", "park", "--grounding-only"]
    ]
    assert cw.current_phase(T) == "parking"
    assert "follows once it is idle" in capsys.readouterr().out


def test_compact_refuses_this_session_and_names_self_compact(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _watch(cw)
    monkeypatch.setenv("WAYPOINT_SESSION_ID", T)
    with pytest.raises(SystemExit, match="self-compact"):
        cw.cmd_compact(
            cw.argparse.Namespace(target=T, grounding="proceed", grounding_only=False)
        )


def test_self_compact_needs_a_session_and_a_watcher(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = cw.argparse.Namespace(grounding="proceed")
    with pytest.raises(SystemExit, match="WAYPOINT_SESSION_ID"):
        cw.cmd_self_compact(args)
    monkeypatch.setenv("WAYPOINT_SESSION_ID", T)
    with pytest.raises(SystemExit, match="no watcher"):
        cw.cmd_self_compact(args)


def test_self_compact_hands_this_session_to_the_helper(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _watch(cw)
    spawned = _capture_spawn(cw, monkeypatch)
    monkeypatch.setenv("WAYPOINT_SESSION_ID", T)
    cw.cmd_self_compact(cw.argparse.Namespace(grounding="proceed"))
    assert spawned == [["pair", "--target", T, "--grounding", "proceed"]]


def test_compact_refuses_a_live_helper_tmux_and_a_gone_target(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _watch(cw)
    with pytest.raises(SystemExit, match="tmux"):
        fake.sessions[T]["transport"] = "tmux"
        cw.begin_pair(T, "proceed")
    fake.sessions[T]["transport"] = "claude_tty"
    with pytest.raises(SystemExit, match="gone"):
        fake.sessions[T]["status"] = "exited"
        cw.begin_pair(T, "proceed")
    fake.sessions[T]["status"] = "idle"
    monkeypatch.setattr(cw, "pair_pid", lambda sid: 4242)
    with pytest.raises(SystemExit, match="already running"):
        cw.begin_pair(T, "proceed")
    assert cw.current_phase(T) != "parking"


def test_scoping_of_status_and_stop_all(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _watch(cw, "a", lead="me")
    _watch(cw, "b", lead="other")
    monkeypatch.setenv("WAYPOINT_SESSION_ID", "me")
    assert cw.owned_targets(False) == ["a"]
    assert cw.owned_targets(True) == ["a", "b"]
    monkeypatch.delenv("WAYPOINT_SESSION_ID")
    assert cw.owned_targets(False) == ["a", "b"]

    monkeypatch.setenv("WAYPOINT_SESSION_ID", "me")
    cw.cmd_stop(cw.argparse.Namespace(all=True, all_leads=False, target=None))
    assert not cw.tdir("a").exists()
    assert cw.tdir("b").exists()


def test_status_reports_usage_or_its_absence(
    cw: ModuleType,
    fake: Fake,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _watch(cw)
    cw.cmd_status(cw.argparse.Namespace(target=T, all_leads=False))
    assert json.loads(capsys.readouterr().out)["usage"] is None
    fake.usage(0.65)
    cw.cmd_status(cw.argparse.Namespace(target=T, all_leads=False))
    out = json.loads(capsys.readouterr().out)
    assert out["usage"]["pct"] == 0.65
    assert out["pair_running"] is False


def test_status_shows_the_self_compact_command_only_for_this_session(
    cw: ModuleType,
    fake: Fake,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _watch(cw)
    monkeypatch.setattr(cw.sys, "orig_argv", ["python3", "/skills/ctx_watch.py"])
    monkeypatch.setattr(cw.sys, "argv", ["/skills/ctx_watch.py"])
    monkeypatch.setenv("WAYPOINT_SESSION_ID", L)
    cw.cmd_status(cw.argparse.Namespace(target=T, all_leads=False))
    assert "allow_rule" not in json.loads(capsys.readouterr().out)
    monkeypatch.setenv("WAYPOINT_SESSION_ID", T)
    cw.cmd_status(cw.argparse.Namespace(target=T, all_leads=False))
    out = json.loads(capsys.readouterr().out)
    assert (
        out["self_compact_command"]
        == "python3 /skills/ctx_watch.py self-compact --grounding proceed"
    )
    assert out["allow_rule"] == "Bash(python3 /skills/ctx_watch.py self-compact:*)"


def test_set_updates_the_handoff(
    cw: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    _watch(cw)
    cw.cmd_set(
        cw.argparse.Namespace(
            target=T, first=None, second=None, lead=None, handoff="notes.md"
        )
    )
    assert cw.read_json(cw.tdir(T) / "config.json")["handoff"] == "notes.md"


def test_a_gone_target_removes_the_watchers_directory(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _watch(cw)
    monkeypatch.setattr(cw.signal, "signal", lambda *_: None)
    monkeypatch.setattr(cw, "poll", lambda sid, st: False)
    cw.cmd_run(cw.argparse.Namespace(target=T))
    assert not cw.tdir(T).exists()


# ---- process and CLI helpers -------------------------------------------------------


def test_cli_resolution_order(
    cw: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cw.shutil, "which", lambda name: None)
    monkeypatch.delenv("WAYPOINT_HOME", raising=False)
    assert cw.waypoint_cli() == "waypoint"
    venv_cli = tmp_path / "backend/.venv/bin/waypoint"
    venv_cli.parent.mkdir(parents=True)
    venv_cli.touch()
    monkeypatch.setenv("WAYPOINT_HOME", str(tmp_path))
    assert cw.waypoint_cli() == str(venv_cli)
    monkeypatch.setattr(cw.shutil, "which", lambda name: "/on/path/waypoint")
    assert cw.waypoint_cli() == "/on/path/waypoint"
    monkeypatch.setenv("CTX_WATCH_WAYPOINT", "/override")
    assert cw.waypoint_cli() == "/override"


def test_send_returns_none_on_a_missing_cli_or_a_refusal(
    cw: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CTX_WATCH_WAYPOINT", str(tmp_path / "missing"))
    assert cw.send(T, "hello") is None
    with pytest.raises(OSError):
        cw.list_sessions()


def test_live_process_matches_only_this_targets_role(
    cw: ModuleType, tmp_path: Path
) -> None:
    pid_file = tmp_path / "x.pid"
    assert cw.live_process(pid_file, "run", T) is None
    pid_file.write_text("999999999")
    assert cw.live_process(pid_file, "run", T) is None
    pid_file.write_text("not a pid")
    assert cw.live_process(pid_file, "run", T) is None

    code = "import time; time.sleep(30)"
    proc = subprocess.Popen(
        [sys.executable, "-c", code, "ctx_watch", "run", "--target", T, "--x"]
    )
    try:
        pid_file.write_text(str(proc.pid))
        assert cw.live_process(pid_file, "run", T) == proc.pid
        assert cw.live_process(pid_file, "pair", T) is None
        assert cw.live_process(pid_file, "run", "target-12") is None
    finally:
        proc.kill()
        proc.wait()


def test_a_live_process_without_the_script_name_is_not_a_watcher(
    cw: ModuleType, tmp_path: Path
) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        pid_file = tmp_path / "x.pid"
        pid_file.write_text(str(proc.pid))
        assert cw.live_process(pid_file, "run", T) is None
    finally:
        proc.kill()
        proc.wait()


# ---- real processes, with a fake waypoint executable ---------------------------------

_FAKE_WAYPOINT = """#!{python}
import json, os, sys
from pathlib import Path
root = Path(os.environ["FAKE_WAYPOINT_DIR"])
if sys.argv[1:3] == ["sessions", "list"]:
    print((root / "sessions.json").read_text())
elif sys.argv[1:3] == ["sessions", "send"]:
    with open(root / "sends.log", "a") as f:
        f.write(json.dumps(sys.argv[3:5]) + "\\n")
    print(json.dumps({{"session": {{"id": sys.argv[3]}}}}))
else:
    sys.exit(2)
"""


class Stack:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state = root / "state"
        cli = root / "waypoint"
        cli.write_text(_FAKE_WAYPOINT.format(python=sys.executable))
        cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
        self.env = {
            **os.environ,
            "CTX_WATCH_WAYPOINT": str(cli),
            "CTX_WATCH_DIR": str(self.state),
            "FAKE_WAYPOINT_DIR": str(root),
            "WAYPOINT_SESSION_ID": L,
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def sessions(self, pct: float | None, **target: Any) -> None:
        usage = (
            None
            if pct is None
            else {
                "used_tokens": int(pct * 1000) * 1000,
                "context_window_tokens": 1_000_000,
                "updated_at": _iso(time.time()),
            }
        )
        rows = [
            {
                "id": T,
                "title": "t",
                "status": "idle",
                "focus": False,
                "transport": "claude_cli",
                "last_event_at": None,
                "context_usage": usage,
                **target,
            },
            {"id": L, "title": "l", "status": "idle", "focus": False},
        ]
        (self.root / "sessions.json").write_text(json.dumps({"sessions": rows}))

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(_SCRIPT), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def sends(self) -> list[list[str]]:
        log = self.root / "sends.log"
        return (
            [json.loads(line) for line in log.read_text().splitlines()]
            if log.exists()
            else []
        )

    def await_sends(self, count: int, within: float = 15) -> list[list[str]]:
        deadline = time.time() + within
        while time.time() < deadline and len(self.sends()) < count:
            time.sleep(0.1)
        return self.sends()


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Stack]:
    s = Stack(tmp_path)
    yield s
    s.run("stop", "--all", "--all-leads")


def test_start_alerts_for_real_and_stop_removes_everything(stack: Stack) -> None:
    stack.sessions(0.65)
    out = stack.run("start", "--target", T, "--interval", "1")
    assert out.returncode == 0, out.stderr
    started = json.loads(out.stdout)
    assert "warning" not in started
    sends = stack.await_sends(1)
    assert sends and sends[0][0] == L and "level=first" in sends[0][1]
    assert (stack.state / "README.md").exists()

    again = stack.run("start", "--target", T)
    assert again.returncode != 0
    assert "already running" in again.stderr

    status = stack.run("status")
    assert '"running": true' in status.stdout

    assert stack.run("stop", "--target", T).returncode == 0
    assert not (stack.state / T).exists()


def test_start_warns_when_the_target_reports_no_usage(stack: Stack) -> None:
    stack.sessions(None)
    started = json.loads(stack.run("start", "--target", T).stdout)
    assert "no context usage" in started["warning"]
    assert "allow_rule" not in started


def test_start_clears_a_helper_pid_left_by_a_reboot(stack: Stack) -> None:
    stack.sessions(0.2)
    (stack.state / T).mkdir(parents=True)
    (stack.state / T / "pair.pid").write_text("999999999")
    assert stack.run("start", "--target", T).returncode == 0
    assert not (stack.state / T / "pair.pid").exists()


def test_start_fails_in_one_line_without_a_cli(stack: Stack) -> None:
    stack.env["CTX_WATCH_WAYPOINT"] = str(stack.root / "missing")
    out = stack.run("start", "--target", T)
    assert out.returncode != 0
    assert "cannot list sessions" in out.stderr
    assert "Traceback" not in out.stderr


def test_an_unwritable_state_root_fails_in_one_line(stack: Stack) -> None:
    blocker = stack.root / "blocker"
    blocker.write_text("")
    stack.env["CTX_WATCH_DIR"] = str(blocker / "state")
    out = stack.run("status")
    assert out.returncode != 0
    assert "CTX_WATCH_DIR" in out.stderr
    assert "Traceback" not in out.stderr


def test_compact_spawns_a_helper_that_delivers_the_pair(stack: Stack) -> None:
    stack.sessions(0.2)
    assert stack.run("start", "--target", T, "--interval", "60").returncode == 0
    out = stack.run("compact", "--target", T, "--grounding", "proceed")
    assert out.returncode == 0, out.stderr
    sends = stack.await_sends(2)
    assert [text for _, text in sends] == [
        "/compact",
        "Ground yourself comprehensively and proceed",
    ]
    deadline = time.time() + 5
    while time.time() < deadline and (stack.state / T / "pair.pid").exists():
        time.sleep(0.1)
    assert not (stack.state / T / "pair.pid").exists()
    status = stack.run("status", "--target", T)
    assert '"phase": "compacting"' in status.stdout
    assert '"pair_running": false' in status.stdout


def test_stop_ends_a_helper_still_waiting_for_its_target(stack: Stack) -> None:
    stack.sessions(0.2, status="running")
    assert stack.run("start", "--target", T, "--interval", "60").returncode == 0
    assert stack.run("compact", "--target", T, "--grounding", "proceed").returncode == 0
    pid = int((stack.state / T / "pair.pid").read_text())
    status = stack.run("status", "--target", T)
    assert '"pair_running": true' in status.stdout
    refused = stack.run("compact", "--target", T, "--grounding", "proceed")
    assert "already running" in refused.stderr
    assert stack.run("stop", "--target", T).returncode == 0
    assert not (stack.state / T).exists()
    with pytest.raises(ProcessLookupError):
        for _ in range(50):
            os.kill(pid, 0)
            time.sleep(0.1)
    assert stack.sends() == []
