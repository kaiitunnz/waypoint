"""The context watcher behind the waypoint-context skill.

The script ships inside the skill, so it is loaded by path. `poll` and the compaction helper run against a fake session table; the last
tests cross the real process boundary with a fake `waypoint` executable.
"""

import importlib.util
import json
import os
import signal
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
        self.waits: list[str] = []
        self.sessions: dict[str, dict[str, Any]] = {
            T: self.session(T),
            L: self.session(L),
        }
        monkeypatch.setattr(cw, "now", lambda: self.t)
        monkeypatch.setattr(cw, "list_sessions", self.list_sessions)
        monkeypatch.setattr(cw, "send", self.send)
        monkeypatch.setattr(cw.time, "sleep", self.sleep)
        monkeypatch.setattr(cw, "await_turn_end", self.await_turn_end)

    def session(self, sid: str, **fields: Any) -> dict[str, Any]:
        return {
            "id": sid,
            "title": sid,
            "status": "idle",
            "focus": False,
            "transport": "codex_app_server",
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

    def await_turn_end(self, sid: str, timeout: float) -> None:
        self.waits.append(sid)
        self.sleep(self.cw.PAIR_POLL_SEC)

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


def test_a_target_that_returns_restarts_the_gone_clock(
    cw: ModuleType, fake: Fake
) -> None:
    st = _watch(cw)
    fake.sessions[T]["status"] = "exited"
    assert cw.poll(T, st)
    fake.t += cw.GONE_EXIT_SEC - 1
    fake.sessions[T]["status"] = "idle"
    assert cw.poll(T, st)
    fake.sessions[T]["status"] = "exited"
    fake.t += 2
    assert cw.poll(T, st)


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


# ---- the compaction helper ---------------------------------------------------------


G = "Ground yourself comprehensively and proceed"


def _pair(
    cw: ModuleType,
    grounding: str = "proceed",
    grounding_only: bool = False,
    after_compact: bool = False,
) -> None:
    cw.cmd_pair(
        cw.argparse.Namespace(
            target=T,
            grounding=grounding,
            grounding_only=grounding_only,
            after_compact=after_compact,
        )
    )


def _parked(cw: ModuleType, fake: Fake, **cfg: Any) -> dict[str, Any]:
    st = _watch(cw, **cfg)
    fake.usage(0.65, at=fake.t - 1000)
    fake.sessions[T]["last_event_at"] = _iso(fake.t - 1000)
    cw.write_phase(T, "parking", "proceed")
    (cw.tdir(T) / "pair.pid").write_text("1")
    return st


def _time_sends(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> dict[str, float]:
    real_send = fake.send
    sent_at: dict[str, float] = {}

    def timed_send(sid: str, text: str) -> str | None:
        sent_at[text] = fake.t
        if text == "/compact":
            fake.sessions[T]["last_event_at"] = _iso(fake.t)
        return real_send(sid, text)

    monkeypatch.setattr(cw, "send", timed_send)
    return sent_at


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
    assert [text for _, text in fake.sent] == ["/compact", G]
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
    assert sent_at[0] - start >= cw.QUIET_SEC["codex_app_server"]
    assert sent_at[1] - sent_at[0] >= 2 * cw.PAIR_POLL_SEC
    assert fake.waits, "a busy target is waited on, not polled"
    assert [text for _, text in fake.sent][0] == "/compact"


def test_the_grounding_waits_out_an_idle_flicker_during_the_compaction(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _parked(cw, fake)
    sent_at = _time_sends(cw, fake, monkeypatch)
    _pair(cw)
    assert sent_at[G] - sent_at["/compact"] >= cw.SETTLE_SEC["codex_app_server"]


@pytest.mark.parametrize(
    ("transport", "quiet", "settle"),
    [
        ("codex_app_server", 2, 2),
        ("opencode_http", 5, 5),
        ("a_new_backend", 30, 5),
    ],
)
def test_each_backend_waits_its_own_quiet_and_settle(
    cw: ModuleType,
    fake: Fake,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    quiet: float,
    settle: float,
) -> None:
    _parked(cw, fake)
    fake.sessions[T].update(transport=transport, last_event_at=_iso(fake.t))
    sent_at = _time_sends(cw, fake, monkeypatch)
    start = fake.t
    _pair(cw)
    assert sent_at["/compact"] - start == quiet
    assert sent_at[G] - sent_at["/compact"] == settle


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
    assert "nothing went out" in fake.sent[0][1]
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
    assert [text for _, text in fake.sent] == [G]
    assert cw.current_phase(T) == "compacting"

    fake.sent.clear()
    cw.write_phase(T, "deferred")
    _pair(cw, grounding_only=True)
    assert [sid for sid, _ in fake.sent] == [L]
    assert fake.levels() == ["pair-failed"]
    assert "the phase changed to deferred; nothing went out" in fake.sent[0][1]
    assert cw.current_phase(T) == "deferred"


def test_a_refused_send_retries_while_the_target_is_ready(
    cw: ModuleType, fake: Fake
) -> None:
    _parked(cw, fake)
    fake.send_results = [None, "delivered", None, "delivered"]
    _pair(cw)
    assert [text for _, text in fake.sent] == ["/compact", "/compact", G, G]


def test_a_refused_grounding_only_send_retries_after_its_stamp(
    cw: ModuleType, fake: Fake
) -> None:
    _parked(cw, fake)
    fake.send_results = [None, "delivered"]
    _pair(cw, grounding_only=True)
    assert [text for _, text in fake.sent] == [G, G]
    assert cw.current_phase(T) == "compacting"


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
    assert "/compact went out" in fake.sent[-1][1]
    assert cw.current_phase(T) == "compacting"


def test_an_unknown_compact_is_not_resent(cw: ModuleType, fake: Fake) -> None:
    _parked(cw, fake)
    fake.send_results = ["unknown"]
    _pair(cw)
    assert [text for _, text in fake.sent] == ["/compact", G]


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


def test_a_held_grounding_alerts_with_no_second_grounding(
    cw: ModuleType, fake: Fake
) -> None:
    _parked(cw, fake, lead=T)
    fake.send_results = ["delivered", "held"]
    _pair(cw)
    assert fake.levels() == ["pair-held"]
    assert fake.sent[-1][1].startswith("[ctx-watch")
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
    assert fake.sent[-1] == (T, G)
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
    fake.sessions[T]["transport"] = "codex_app_server"
    with pytest.raises(SystemExit, match="gone"):
        fake.sessions[T]["status"] = "exited"
        cw.begin_pair(T, "proceed")
    fake.sessions[T]["status"] = "idle"
    monkeypatch.setattr(cw, "pair_pid", lambda sid: 4242)
    with pytest.raises(SystemExit, match="already running"):
        cw.begin_pair(T, "proceed")
    assert cw.current_phase(T) != "parking"


@pytest.mark.parametrize("transport", ["claude_tty", "codex_app_server"])
@pytest.mark.parametrize("status", ["error", "interrupted", "waiting_input"])
def test_a_target_that_needs_a_person_is_refused_with_nothing_sent(
    cw: ModuleType,
    fake: Fake,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    status: str,
) -> None:
    _watch(cw)
    spawned = _capture_spawn(cw, monkeypatch)
    fake.sessions[T].update(status=status, transport=transport)
    with pytest.raises(SystemExit, match="needs a person"):
        cw.begin_pair(T, "proceed")
    assert fake.sent == [] and spawned == []
    assert cw.current_phase(T) == "normal"


# ---- Claude Code: the pair at once ----------------------------------------------------


def _claude(
    cw: ModuleType,
    fake: Fake,
    monkeypatch: pytest.MonkeyPatch,
    status: str = "idle",
    transport: str = "claude_tty",
    lead: str = L,
    **cfg: Any,
) -> list[list[str]]:
    _watch(cw, lead=lead, **cfg)
    fake.sessions[T].update(status=status, transport=transport)
    return _capture_spawn(cw, monkeypatch)


AFTER_COMPACT = ["pair", "--target", T, "--grounding", "proceed", "--after-compact"]


@pytest.mark.parametrize("transport", ["claude_cli", "claude_tty"])
def test_an_idle_claude_target_gets_both_at_once_with_no_helper(
    cw: ModuleType,
    fake: Fake,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
) -> None:
    spawned = _claude(cw, fake, monkeypatch, transport=transport)
    fake.sessions[T]["last_event_at"] = _iso(fake.t)
    start = fake.t
    cw.begin_pair(T, "proceed")
    assert fake.t == start, "nothing waits for quiet"
    assert fake.sent == [(T, "/compact"), (T, G)]
    assert spawned == []
    ph = cw.phase_record(T)
    assert ph["phase"] == "compacting" and "grounding_pending" not in ph


def test_a_running_claude_target_gets_compact_now_and_its_grounding_after_the_turn(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned = _claude(cw, fake, monkeypatch, status="running")
    cw.begin_pair(T, "proceed")
    assert fake.sent == [(T, "/compact")]
    assert spawned == [AFTER_COMPACT]
    assert cw.phase_record(T)["grounding_pending"] is True


NOTES_PARK = "Re-read docs/notes.md, then ground yourself comprehensively and park"


def test_the_at_once_pair_sends_the_recorded_grounding_with_the_notes(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claude(cw, fake, monkeypatch, handoff="docs/notes.md")
    cw.begin_pair(T, "park")
    assert fake.sent == [(T, "/compact"), (T, NOTES_PARK)]
    assert cw.phase_record(T)["grounding"] == "park"


def test_the_helper_gets_the_recorded_grounding_and_sends_it_with_the_notes(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned = _claude(cw, fake, monkeypatch, status="running", handoff="docs/notes.md")
    cw.begin_pair(T, "park")
    assert spawned == [
        ["pair", "--target", T, "--grounding", "park", "--after-compact"]
    ]
    fake.sent.clear()
    fake.sessions[T]["status"] = "idle"
    _pair(cw, grounding="park", after_compact=True)
    assert fake.sent == [(T, NOTES_PARK)]
    assert cw.phase_record(T)["grounding"] == "park"


@pytest.mark.parametrize("status", ["error", "interrupted"])
def test_self_compact_is_refused_when_the_session_needs_a_person(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    spawned = _claude(cw, fake, monkeypatch, status=status)
    monkeypatch.setenv("WAYPOINT_SESSION_ID", T)
    with pytest.raises(SystemExit, match="needs a person"):
        cw.cmd_self_compact(cw.argparse.Namespace(grounding="proceed"))
    assert fake.sent == [] and spawned == []
    assert cw.current_phase(T) == "normal"


@pytest.mark.parametrize("status", ["error", "interrupted"])
def test_a_failed_helper_sends_nothing_to_a_stopped_session_leading_itself(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    _claude(cw, fake, monkeypatch, status=status, lead=T)
    cw.write_phase(T, "compacting", "proceed", pending=True)
    _pair(cw, after_compact=True)
    assert fake.sent == []
    assert "grounding_pending" not in cw.phase_record(T)


@pytest.mark.parametrize("status", ["idle", "running", "waiting_input"])
def test_claude_self_compact_sends_compact_in_its_own_turn(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    spawned = _claude(cw, fake, monkeypatch, status=status)
    monkeypatch.setenv("WAYPOINT_SESSION_ID", T)
    cw.cmd_self_compact(cw.argparse.Namespace(grounding="proceed"))
    assert fake.sent == [(T, "/compact")]
    assert spawned == [AFTER_COMPACT]
    assert cw.phase_record(T)["grounding_pending"] is True


@pytest.mark.parametrize("result", [None, "unknown"])
def test_an_unconfirmed_claude_compact_fails_the_command(
    cw: ModuleType,
    fake: Fake,
    monkeypatch: pytest.MonkeyPatch,
    result: str | None,
) -> None:
    spawned = _claude(cw, fake, monkeypatch)
    fake.send_results = [result]
    with pytest.raises(SystemExit, match="not confirmed"):
        cw.begin_pair(T, "proceed")
    assert fake.sent == [(T, "/compact")] and spawned == []
    assert cw.current_phase(T) == "normal"
    fake.send_results = []
    cw.begin_pair(T, "proceed")
    assert fake.sent[-1] == (T, G)


@pytest.mark.parametrize(
    ("status", "results"),
    [
        ("idle", ["held", "held"]),
        ("running", ["held", "held"]),
        ("idle", ["held", "delivered"]),
        ("running", ["held", "delivered"]),
        ("idle", ["delivered", "held"]),
    ],
)
def test_a_held_claude_pair_queues_both_and_alerts(
    cw: ModuleType,
    fake: Fake,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    results: list[str],
) -> None:
    spawned = _claude(cw, fake, monkeypatch, status=status, lead=T)
    fake.send_results = [*results, "held"]
    cw.begin_pair(T, "proceed")
    assert fake.sent[:2] == [(T, "/compact"), (T, G)]
    assert fake.levels() == ["pair-held"]
    assert fake.sent[2][1].startswith("[ctx-watch"), "no second grounding"
    assert spawned == []
    assert cw.current_phase(T) == "compacting"


def test_a_refused_immediate_grounding_falls_back_to_the_helper(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned = _claude(cw, fake, monkeypatch)
    fake.send_results = ["delivered", None]
    cw.begin_pair(T, "proceed")
    assert spawned == [AFTER_COMPACT]
    assert cw.phase_record(T)["grounding_pending"] is True


def test_grounding_only_on_claude_skips_compact(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned = _claude(cw, fake, monkeypatch)
    cw.begin_pair(T, "proceed", grounding_only=True)
    assert fake.sent == [(T, G)] and spawned == []


def test_a_pair_just_sent_refuses_another_until_the_window_passes(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claude(cw, fake, monkeypatch)
    cw.begin_pair(T, "proceed")
    fake.sent.clear()
    with pytest.raises(SystemExit, match="just now; wait 60s"):
        cw.begin_pair(T, "proceed")
    assert fake.sent == []
    fake.t += cw.PAIR_RECENT_SEC
    cw.begin_pair(T, "proceed")
    assert fake.sent == [(T, "/compact"), (T, G)]


def test_the_window_outlives_the_rearm_a_finished_pair_brings(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claude(cw, fake, monkeypatch)
    cw.begin_pair(T, "proceed")
    fake.t += 5
    fake.usage(0.2)
    st = cw.read_json(cw.tdir(T) / "state.json")
    cw.poll(T, st)
    _sync(cw, st)
    assert st["cycle_id"] == 2 and cw.current_phase(T) == "normal"
    fake.sent.clear()
    with pytest.raises(SystemExit, match="just now"):
        cw.begin_pair(T, "proceed")
    assert fake.sent == []


def test_the_window_outlives_a_helper_that_died(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claude(cw, fake, monkeypatch, status="running")
    cw.begin_pair(T, "proceed")
    with pytest.raises(SystemExit, match="just now"):
        cw.begin_pair(T, "proceed")
    assert fake.sent == [(T, "/compact")]


@pytest.mark.parametrize("after_compact", [True, False])
def test_a_pair_the_helper_finished_refuses_another_until_the_window_passes(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch, after_compact: bool
) -> None:
    if after_compact:
        _claude(cw, fake, monkeypatch)
        cw.write_phase(T, "compacting", "proceed", pending=True)
    else:
        _parked(cw, fake)
    spawned = _capture_spawn(cw, monkeypatch)
    _pair(cw, after_compact=after_compact)
    fake.sent.clear()
    with pytest.raises(SystemExit, match="just now"):
        cw.begin_pair(T, "proceed")
    assert fake.sent == [] and spawned == []
    fake.t += cw.PAIR_RECENT_SEC
    cw.begin_pair(T, "proceed")
    assert fake.sent or spawned


def test_the_window_does_not_refuse_recovery_right_after_a_pair_failed(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claude(cw, fake, monkeypatch, status="running")
    cw.begin_pair(T, "proceed")
    fake.sessions[T]["status"] = "error"
    _pair(cw, after_compact=True)
    assert fake.levels() == ["pair-failed"]
    (cw.tdir(T) / "pair.pid").unlink(missing_ok=True)
    fake.sent.clear()
    fake.sessions[T]["status"] = "idle"
    cw.begin_pair(T, "proceed", grounding_only=True)
    assert fake.sent == [(T, G)]


def _pending(
    cw: ModuleType, fake: Fake, cycle: str = "t1_sent", pct: float = 0.75
) -> dict[str, Any]:
    st = _watch(cw)
    st["cycle"] = cycle
    _sync(cw, st)
    cw.write_phase(T, "compacting", "proceed", pending=True, sent=True)
    fake.t += 5
    fake.usage(pct)
    return st


@pytest.mark.parametrize("cycle", ["armed", "t1_sent"])
def test_the_last_turns_reading_is_stale_until_the_grounding_goes_out(
    cw: ModuleType, fake: Fake, cycle: str
) -> None:
    st = _pending(cw, fake, cycle=cycle)
    cw.poll(T, st)
    assert fake.sent == []
    assert cw.current_phase(T) == "compacting"


def test_a_low_reading_in_an_armed_cycle_waits_for_the_grounding_to_re_arm(
    cw: ModuleType, fake: Fake
) -> None:
    st = _pending(cw, fake, cycle="armed", pct=0.2)
    cw.poll(T, st)
    assert st["cycle_id"] == 1
    cw.write_phase(T, "compacting", "proceed")
    fake.t += 5
    fake.usage(0.2)
    cw.poll(T, st)
    assert st["cycle_id"] == 2


def test_after_the_grounding_a_high_reading_is_a_real_refill(
    cw: ModuleType, fake: Fake
) -> None:
    st = _pending(cw, fake)
    _sync(cw, st)
    cw.write_phase(T, "compacting", "proceed")
    fake.t += 5
    fake.usage(0.75)
    cw.poll(T, st)
    assert "refill-fast" in fake.sent[0][1]


def test_a_stall_still_fires_while_the_grounding_is_pending(
    cw: ModuleType, fake: Fake
) -> None:
    st = _pending(cw, fake)
    fake.t += cw.STALL_SEC
    cw.poll(T, st)
    assert fake.levels() == ["pair-stalled"]


def test_after_compact_sends_only_the_grounding_once_the_turn_ends(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claude(cw, fake, monkeypatch, status="running")
    cw.write_phase(T, "compacting", "proceed", pending=True, sent=True)

    def end_turn() -> None:
        fake.sessions[T].update(status="idle", last_event_at=_iso(fake.t))

    fake.on_sleep = [end_turn]
    writes: list[str] = []
    real_write = cw.write_phase

    def counting_write(*args: Any, **kwargs: Any) -> dict[str, Any]:
        writes.append(args[1])
        return real_write(*args, **kwargs)

    monkeypatch.setattr(cw, "write_phase", counting_write)
    fake.send_results = [None, "delivered"]
    _pair(cw, after_compact=True)
    assert fake.sent == [(T, G), (T, G)]
    assert fake.waits == [T]
    assert writes == ["compacting"], "stamped once, not on every attempt"
    ph = cw.phase_record(T)
    assert ph["phase"] == "compacting"
    assert "grounding_pending" not in ph and "sent_at" in ph


def test_after_compact_sends_during_focus_and_alerts(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claude(cw, fake, monkeypatch, status="running", lead=T)
    fake.sessions[T]["focus"] = True
    cw.write_phase(T, "compacting", "proceed", pending=True)
    fake.send_results = ["held", "held"]
    _pair(cw, after_compact=True)
    assert fake.sent[0] == (T, G)
    assert fake.levels() == ["pair-held"]
    assert fake.sent[1][1].startswith("[ctx-watch"), "no second grounding"


def test_after_compact_outlasts_a_long_turn(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claude(cw, fake, monkeypatch, status="running")
    cw.write_phase(T, "compacting", "proceed", pending=True)
    start = fake.t

    def long_turn(sid: str, timeout: float) -> None:
        fake.t += 60
        if fake.t - start > 2 * cw.PAIR_GIVE_UP_SEC:
            fake.sessions[T]["status"] = "idle"

    monkeypatch.setattr(cw, "await_turn_end", long_turn)
    _pair(cw, after_compact=True)
    assert fake.sent == [(T, G)]


def test_after_compact_gives_up_after_its_cap_and_clears_the_flag(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claude(cw, fake, monkeypatch, status="running")
    cw.write_phase(T, "compacting", "proceed", pending=True)
    start = fake.t
    _pair(cw, after_compact=True)
    assert fake.t - start >= cw.PAIR_TURN_GIVE_UP_SEC
    assert fake.levels() == ["pair-failed"]
    assert "/compact went out" in fake.sent[0][1]
    assert "grounding_pending" not in cw.phase_record(T)


def test_after_compact_uses_the_short_cap_once_the_target_is_ready(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claude(cw, fake, monkeypatch)
    cw.write_phase(T, "compacting", "proceed", pending=True)
    real_send = fake.send

    def refusing_send(sid: str, text: str) -> str | None:
        real_send(sid, text)
        return "delivered" if sid == L else None

    monkeypatch.setattr(cw, "send", refusing_send)
    start = fake.t
    _pair(cw, after_compact=True)
    assert fake.t - start <= cw.PAIR_GIVE_UP_SEC + 2 * cw.PAIR_POLL_SEC
    assert fake.levels() == ["pair-failed"]


@pytest.mark.parametrize("phase", ["user-held", "lapsed"])
def test_the_stamp_leaves_a_later_decision_alone(
    cw: ModuleType, fake: Fake, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    st = _pending(cw, fake)
    if phase == "user-held":
        cw.write_phase(T, "user-held")
    else:
        st["cycle_id"] += 1
        _sync(cw, st)
    fake.sent.clear()
    _pair(cw, after_compact=True)
    assert fake.sent == [(T, G)]
    assert cw.current_phase(T) == ("user-held" if phase == "user-held" else "normal")


def test_a_gone_target_clears_the_flag(cw: ModuleType, fake: Fake) -> None:
    _pending(cw, fake)
    fake.sessions[T]["status"] = "exited"
    _pair(cw, after_compact=True)
    assert fake.sent == []
    assert "grounding_pending" not in cw.phase_record(T)


def test_scoping_of_status_and_stop_all(
    cw: ModuleType,
    fake: Fake,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def status(target: str | None, all_leads: bool) -> tuple[bool, bool]:
        cw.cmd_status(cw.argparse.Namespace(target=target, all_leads=all_leads))
        out = capsys.readouterr().out
        return '"target": "a"' in out, '"target": "b"' in out

    _watch(cw, "a", lead="me")
    _watch(cw, "b", lead="other")
    monkeypatch.setenv("WAYPOINT_SESSION_ID", "me")
    assert status(None, False) == (True, False)
    assert status(None, True) == (True, True)
    assert status("b", False) == (False, True)
    monkeypatch.delenv("WAYPOINT_SESSION_ID")
    assert status(None, False) == (True, True)

    monkeypatch.setenv("WAYPOINT_SESSION_ID", "me")
    cw.cmd_stop(cw.argparse.Namespace(all=True, all_leads=False, target=None))
    assert not cw.tdir("a").exists()
    assert cw.tdir("b").exists()


def test_status_reports_usage_or_its_absence(
    cw: ModuleType, fake: Fake, capsys: pytest.CaptureFixture[str]
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


def test_status_prints_one_document_with_the_log_tail(
    cw: ModuleType, fake: Fake, capsys: pytest.CaptureFixture[str]
) -> None:
    _watch(cw)
    cw.log(T, "first")
    cw.log(T, "second")
    cw.log(T, "third")
    cw.log(T, "fourth")
    cw.log(T, "fifth")
    cw.log(T, "sixth")
    cw.cmd_status(cw.argparse.Namespace(target=T, all_leads=False))
    out = json.loads(capsys.readouterr().out)
    assert [line.split(" ", 2)[2] for line in out["recent_log"]] == [
        "second",
        "third",
        "fourth",
        "fifth",
        "sixth",
    ]


def test_status_prints_an_array_and_parses_with_many_watchers(
    cw: ModuleType, fake: Fake, capsys: pytest.CaptureFixture[str]
) -> None:
    _watch(cw, "a", lead=L)
    _watch(cw, "b", lead=L)
    cw.log("a", "only")
    cw.cmd_status(cw.argparse.Namespace(target=None, all_leads=False))
    out = json.loads(capsys.readouterr().out)
    assert [record["target"] for record in out] == ["a", "b"]
    assert [len(record["recent_log"]) for record in out] == [1, 0]


def test_status_reads_a_log_with_stray_bytes(
    cw: ModuleType, fake: Fake, capsys: pytest.CaptureFixture[str]
) -> None:
    _watch(cw)
    (cw.tdir(T) / "watcher.log").write_bytes(b"\xff\xfe bad bytes\nlast\n")
    cw.cmd_status(cw.argparse.Namespace(target=T, all_leads=False))
    out = json.loads(capsys.readouterr().out)
    assert out["recent_log"][-1] == "last"


def test_status_prints_an_empty_array_with_no_watchers(
    cw: ModuleType, fake: Fake, capsys: pytest.CaptureFixture[str]
) -> None:
    cw.cmd_status(cw.argparse.Namespace(target=None, all_leads=False))
    assert json.loads(capsys.readouterr().out) == []


def test_set_updates_the_handoff(cw: ModuleType) -> None:
    _watch(cw)
    cw.cmd_set(
        cw.argparse.Namespace(
            target=T, first=None, second=None, lead=None, handoff="notes.md"
        )
    )
    assert cw.read_json(cw.tdir(T) / "config.json")["handoff"] == "notes.md"
    with pytest.raises(SystemExit, match="thresholds"):
        cw.cmd_set(
            cw.argparse.Namespace(
                target=T, first=0.9, second=None, lead=None, handoff=None
            )
        )
    assert cw.read_json(cw.tdir(T) / "config.json")["first"] == 0.6


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


@pytest.mark.parametrize(
    ("stdout", "code", "expected"),
    [
        ('{"session": {"id": "x"}}', 0, "delivered"),
        ('{"session": {"id": "x", "send": "held"}}', 0, "held"),
        ('{"session": {"id": "x", "send": "unknown"}}', 1, "unknown"),
        ("", 1, None),
    ],
)
def test_send_reads_the_clis_result(
    cw: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stdout: str,
    code: int,
    expected: str | None,
) -> None:
    cli = tmp_path / "waypoint"
    cli.write_text(f"#!/bin/sh\nprintf '%s' '{stdout}'\nexit {code}\n")
    cli.chmod(0o755)
    monkeypatch.setenv("CTX_WATCH_WAYPOINT", str(cli))
    assert cw.send(T, "hello") == expected


def test_send_returns_none_on_a_missing_cli_or_a_timeout(
    cw: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CTX_WATCH_WAYPOINT", str(tmp_path / "missing"))
    assert cw.send(T, "hello") is None
    with pytest.raises(OSError):
        cw.list_sessions()

    def timed_out(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired("waypoint", 1)

    monkeypatch.setattr(cw.subprocess, "run", timed_out)
    assert cw.send(T, "hello") is None


@pytest.mark.parametrize(
    ("stdout", "code", "sleeps"),
    [
        ('{"session": {"id": "x", "status": "idle"}}', 0, False),
        ('{"session": {"id": "x", "status": "interrupted"}}', 0, False),
        ('{"session": {"id": "x", "status": "exited"}}', 0, False),
        ('{"session": {"id": "x", "status": "error"}}', 1, False),
        ('{"session": {"id": "x", "status": "running"}}', 124, True),
        ('{"session": {"id": "x"}}', 124, True),
        ("", 1, True),
    ],
)
def test_await_turn_end_reads_the_status_rather_than_the_exit_code(
    cw: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stdout: str,
    code: int,
    sleeps: bool,
) -> None:
    args_file = tmp_path / "args"
    cli = tmp_path / "waypoint"
    cli.write_text(
        f"#!/bin/sh\necho \"$@\" > {args_file}\nprintf '%s' '{stdout}'\nexit {code}\n"
    )
    cli.chmod(0o755)
    monkeypatch.setenv("CTX_WATCH_WAYPOINT", str(cli))
    slept: list[float] = []
    monkeypatch.setattr(cw.time, "sleep", slept.append)
    cw.await_turn_end(T, 30.5)
    assert slept == ([cw.PAIR_POLL_SEC] if sleeps else [])
    assert (
        args_file.read_text().strip()
        == f"sessions wait {T} --until idle,interrupted,exited,error --timeout 31"
    )


def test_await_turn_end_survives_a_missing_cli_or_a_hung_one(
    cw: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(cw.time, "sleep", slept.append)
    monkeypatch.setenv("CTX_WATCH_WAYPOINT", str(tmp_path / "missing"))
    cw.await_turn_end(T, 5)

    def timed_out(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired("waypoint", 1)

    monkeypatch.setattr(cw.subprocess, "run", timed_out)
    cw.await_turn_end(T, 5)
    assert slept == [cw.PAIR_POLL_SEC, cw.PAIR_POLL_SEC]


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
    longer = f"{T}2"
    proc = subprocess.Popen(
        [sys.executable, "-c", code, "ctx_watch", "run", "--target", longer, "--x"]
    )
    try:
        pid_file.write_text(str(proc.pid))
        assert cw.live_process(pid_file, "run", longer) == proc.pid
        assert cw.live_process(pid_file, "pair", longer) is None
        assert cw.live_process(pid_file, "run", T) is None
    finally:
        proc.kill()
        proc.wait()


def test_a_live_process_without_the_script_name_is_not_a_watcher(
    cw: ModuleType, tmp_path: Path
) -> None:
    code = "import time; time.sleep(30)"
    proc = subprocess.Popen([sys.executable, "-c", code, "run", "--target", T, "--x"])
    try:
        pid_file = tmp_path / "x.pid"
        pid_file.write_text(str(proc.pid))
        assert cw.live_process(pid_file, "run", T) is None
    finally:
        proc.kill()
        proc.wait()


# ---- real processes, with a fake waypoint executable ---------------------------------

_FAKE_WAYPOINT = """#!{python}
import json, os, sys, time
from pathlib import Path
root = Path(os.environ["FAKE_WAYPOINT_DIR"])
if sys.argv[1:3] == ["sessions", "list"]:
    time.sleep(float(os.environ.get("FAKE_LIST_DELAY", 0)))
    print((root / "sessions.json").read_text())
elif sys.argv[1:3] == ["sessions", "wait"]:
    with open(root / "waits.log", "a") as f:
        f.write(str(os.getpid()) + "\\n")
    sid = sys.argv[3]
    until = sys.argv[sys.argv.index("--until") + 1].split(",")
    deadline = time.time() + float(sys.argv[sys.argv.index("--timeout") + 1])
    while True:
        rows = json.loads((root / "sessions.json").read_text())["sessions"]
        row = next(r for r in rows if r["id"] == sid)
        if row["status"] in until or time.time() >= deadline:
            print(json.dumps({{"session": row}}))
            sys.exit(0 if row["status"] in until else 124)
        time.sleep(0.05)
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
        tmp = self.root / "sessions.json.tmp"
        tmp.write_text(json.dumps({"sessions": rows}))
        os.replace(tmp, self.root / "sessions.json")

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(_SCRIPT), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def kill_watcher(self) -> None:
        os.kill(int((self.state / T / "watcher.pid").read_text()), signal.SIGKILL)
        deadline = time.time() + 5
        while time.time() < deadline:
            if '"running": false' in self.run("status", "--target", T).stdout:
                return
            time.sleep(0.1)
        raise AssertionError("the watcher outlived SIGKILL")

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

    def await_path(self, path: Path, exists: bool = True, within: float = 15) -> None:
        deadline = time.time() + within
        while time.time() < deadline and path.exists() != exists:
            time.sleep(0.1)

    def two_compacts(self) -> str:
        """Run `compact` twice at once; assert one succeeded and return the errors."""
        self.env["FAKE_LIST_DELAY"] = "0.5"
        procs = [
            subprocess.Popen(
                [sys.executable, str(_SCRIPT), "compact", "--target", T]
                + ["--grounding", "proceed"],
                env=self.env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        errors = [proc.communicate(timeout=30)[1] for proc in procs]
        assert sorted(proc.returncode for proc in procs) == [0, 1]
        return "".join(errors)


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


def test_a_restart_keeps_the_watchers_settings(stack: Stack) -> None:
    stack.sessions(0.2)
    flags = [
        "--first",
        "0.8",
        "--second",
        "0.9",
        "--handoff",
        "n.md",
        "--interval",
        "30",
    ]
    assert stack.run("start", "--target", T, *flags).returncode == 0
    stack.kill_watcher()
    out = json.loads(stack.run("start", "--target", T).stdout)
    assert (out["first"], out["second"]) == (0.8, 0.9)
    assert (out["handoff"], out["interval"], out["lead"]) == ("n.md", 30, L)
    stack.kill_watcher()
    out = json.loads(stack.run("start", "--target", T, "--first", "0.7").stdout)
    assert (out["first"], out["second"]) == (0.7, 0.9)


def test_a_restart_leaves_a_running_helper_in_place(stack: Stack) -> None:
    stack.sessions(0.2, status="running")
    assert stack.run("start", "--target", T, "--interval", "60").returncode == 0
    assert stack.run("compact", "--target", T, "--grounding", "proceed").returncode == 0
    stack.kill_watcher()
    assert stack.run("start", "--target", T, "--interval", "60").returncode == 0
    assert '"pair_running": true' in stack.run("status", "--target", T).stdout
    refused = stack.run("compact", "--target", T, "--grounding", "proceed")
    assert "already running" in refused.stderr


def test_start_refuses_an_unknown_lead(stack: Stack) -> None:
    stack.sessions(0.2)
    out = stack.run("start", "--target", T, "--lead", "nobody")
    assert out.returncode != 0
    assert "session nobody not found" in out.stderr


@pytest.mark.parametrize("sid", ["", ".", "..", "a/b"])
def test_stop_refuses_anything_but_a_session_id(stack: Stack, sid: str) -> None:
    stack.sessions(0.2)
    assert stack.run("start", "--target", T, "--interval", "60").returncode == 0
    out = stack.run("stop", "--target", sid)
    assert out.returncode != 0
    assert "not a session id" in out.stderr
    assert (stack.state / T / "config.json").exists()
    assert (stack.root / "sessions.json").exists()


def test_two_compacts_at_once_start_one_helper(stack: Stack) -> None:
    stack.sessions(0.2, status="running")
    assert stack.run("start", "--target", T, "--interval", "60").returncode == 0
    assert "already running" in stack.two_compacts()


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


def test_compact_on_claude_sends_both_itself(stack: Stack) -> None:
    stack.sessions(0.2)
    assert stack.run("start", "--target", T, "--interval", "60").returncode == 0
    out = stack.run("compact", "--target", T, "--grounding", "proceed")
    assert out.returncode == 0, out.stderr
    assert [text for _, text in stack.sends()] == ["/compact", G]
    assert not (stack.state / T / "pair.pid").exists()
    assert '"phase": "compacting"' in stack.run("status", "--target", T).stdout


def test_a_running_claude_target_gets_its_grounding_once_status_flips(
    stack: Stack,
) -> None:
    stack.sessions(0.2, status="running")
    assert stack.run("start", "--target", T, "--interval", "60").returncode == 0
    out = stack.run("compact", "--target", T, "--grounding", "proceed")
    assert out.returncode == 0, out.stderr
    assert [text for _, text in stack.sends()] == ["/compact"]
    stack.await_path(stack.root / "waits.log")
    assert (stack.root / "waits.log").exists(), "the helper blocks on the turn"
    flipped_at = time.time()
    stack.sessions(0.2, status="idle", last_event_at=_iso(flipped_at))
    sends = stack.await_sends(2, within=20)
    assert [text for _, text in sends] == ["/compact", G]
    assert time.time() - flipped_at >= 5, "the settle was waited out"


def test_two_compacts_at_once_on_an_idle_claude_target_send_one_pair(
    stack: Stack,
) -> None:
    stack.sessions(0.2)
    assert stack.run("start", "--target", T, "--interval", "60").returncode == 0
    assert "just now" in stack.two_compacts()
    assert [text for _, text in stack.sends()] == ["/compact", G]


def test_compact_elsewhere_spawns_a_helper_that_delivers_the_pair(stack: Stack) -> None:
    stack.sessions(0.2, transport="codex_app_server")
    assert stack.run("start", "--target", T, "--interval", "60").returncode == 0
    out = stack.run("compact", "--target", T, "--grounding", "proceed")
    assert out.returncode == 0, out.stderr
    sends = stack.await_sends(2)
    assert [text for _, text in sends] == ["/compact", G]
    stack.await_path(stack.state / T / "pair.pid", exists=False, within=5)
    assert not (stack.state / T / "pair.pid").exists()
    status = stack.run("status", "--target", T)
    assert '"phase": "compacting"' in status.stdout
    assert '"pair_running": false' in status.stdout


def test_stop_ends_a_helper_and_its_wait_for_the_target(stack: Stack) -> None:
    stack.sessions(0.2, status="running", transport="codex_app_server")
    assert stack.run("start", "--target", T, "--interval", "60").returncode == 0
    assert stack.run("compact", "--target", T, "--grounding", "proceed").returncode == 0
    pid = int((stack.state / T / "pair.pid").read_text())
    stack.await_path(stack.root / "waits.log")
    wait_pid = int((stack.root / "waits.log").read_text().split()[0])
    status = stack.run("status", "--target", T)
    assert '"pair_running": true' in status.stdout
    refused = stack.run("compact", "--target", T, "--grounding", "proceed")
    assert "already running" in refused.stderr
    assert stack.run("stop", "--target", T).returncode == 0
    assert not (stack.state / T).exists()
    for gone in (pid, wait_pid):
        with pytest.raises(ProcessLookupError):
            for _ in range(50):
                os.kill(gone, 0)
                time.sleep(0.1)
    assert stack.sends() == []
