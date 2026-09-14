"""Guards for the manager template contract. These checked-in templates drive the
manager's human gates and the ephemeral-writer lifecycle; a drift here silently
reverts behavior (the inbox attachment gate, or the retain-writer-until-approval
fix), so pin the load-bearing wording and commands.

Two source families ship the manager role: the distributable baseline under
``.agents/skills/waypoint-manager/templates/manager`` and this project's
customized source under ``.waypoint/templates/manager``. ``waypoint manager init``
compiles the configured source into the runtime templates dir; the compilation
test exercises that path directly.
"""

from pathlib import Path

import pytest

from waypoint.cli import (
    _compile_manager_templates,
    _load_manifest,
    _manager_static_bindings,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED = _REPO_ROOT / ".agents/skills/waypoint-manager/templates/manager"
_LOCAL = _REPO_ROOT / ".waypoint/templates/manager"
_MANIFEST = _REPO_ROOT / ".waypoint/waypoint-manager.yaml"

# Both source families carry the same lifecycle sections; check each.
_MONITOR_SOURCES = (_SHIPPED / "monitor.md", _LOCAL / "monitor.md")
_TRIAGE_SOURCES = (_SHIPPED / "triage.md", _LOCAL / "triage.md")


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"template not present: {path}")
    return path.read_text(encoding="utf-8")


def test_spec_gate_attaches_the_spec_and_keeps_adoption_guard() -> None:
    monitor = _read(_SHIPPED / "monitor.md")
    # The shipped spec-review gate uploads and attaches the RFC/PRD file.
    assert 'inbox post --json - --attach "{{spec_ref}}"' in monitor
    # It fails closed rather than posting a path-only gate.
    assert '[ -f "{{spec_ref}}" ]' in monitor
    # And it still adopts an open gate a crash left behind before posting a new one.
    assert 'endswith("— spec review")' in monitor


def test_local_monitor_keeps_its_no_attach_customization() -> None:
    # The project-local source intentionally lacks the shipped attachment gate;
    # the lifecycle fix must not accidentally sync it in.
    monitor = _read(_LOCAL / "monitor.md")
    assert "--attach" not in monitor


def test_pr_gate_renders_an_explicit_pull_request_link() -> None:
    integrate = _read(_SHIPPED / "integrate.md")
    assert "[Open pull request]({{pr_url}})" in integrate


def test_monitor_does_not_reap_the_writer_at_the_spec_review_gate() -> None:
    # The bug: a delivery-time reap section deleted the writer on arrival at
    # spec_review / branch-less blocked, before the human answered.
    for path in _MONITOR_SOURCES:
        monitor = _read(path)
        assert "## Reap the delivered writer" not in monitor, path
        # The old spec_review-predicated delete guard must be gone.
        assert (
            '[ "$state" = spec_review ] || { [ "$state" = blocked ]' not in monitor
        ), path


def test_monitor_reaps_the_writer_at_the_terminal_disposition() -> None:
    # Cleanup moves to the answer branches that end writer ownership, before the
    # ready/terminal transition, and is idempotent.
    for path in _MONITOR_SOURCES:
        monitor = _read(path)
        assert (
            "# reap the parked writer before a handoff/terminal transition (idempotent)"
            in monitor
        ), path
        assert 'waypoint sessions delete "$writer" --force || true' in monitor, path
        # Both terminal spec-gate answers reap; request-changes keeps the writer.
        assert "`approve` → reap the writer (above)" in monitor, path
        assert "`reject` → reap the writer (above)" in monitor, path


def test_monitor_respec_reuses_the_live_writer() -> None:
    for path in _MONITOR_SOURCES:
        monitor = _read(path)
        # Re-spec must not re-spawn as the normal path; it retains and reuses.
        assert "re-spawn the writer per" not in monitor, path
        assert "retained live writer" in monitor, path
        assert "re-sends the" in monitor, path


def test_triage_reuses_the_live_writer_before_spawning() -> None:
    for path in _TRIAGE_SOURCES:
        triage = _read(path)
        assert "**two-path** operation" in triage, path
        # The liveness gate: a recorded exited/errored/missing writer is dropped
        # and re-spawned; a live one is reused without a fresh spawn.
        assert 'case "$st" in ""|exited|error) sid="";;' in triage, path
        assert "Do **not** start a session" in triage, path


def test_skill_states_the_two_writer_lifecycles() -> None:
    skill = _read(_REPO_ROOT / ".agents/skills/waypoint-manager/SKILL.md")
    assert "retain a read-only PRD/RFC **writer** through" in skill
    assert "reap it only when the spec is finally approved for handoff" in skill
    assert "after merge or a terminal build disposition" in skill
    # The pre-approval gate is explicitly not a reap point.
    assert (
        "Never reap either\n  role merely for reaching a pre-approval human gate"
        in skill
    )


def test_manager_init_compiles_the_retained_writer_behavior(tmp_path: Path) -> None:
    # Exercise the same compilation path `waypoint manager init` runs, and assert the
    # compiled runtime monitor.md carries the retain-writer fix after substitution.
    if not _MANIFEST.is_file():
        pytest.skip(f"manifest not present: {_MANIFEST}")
    raw = _load_manifest(_MANIFEST)
    static = _manager_static_bindings(raw, str(_REPO_ROOT), "test-owner", str(tmp_path))
    _compile_manager_templates(raw, static, tmp_path)

    compiled = (tmp_path / "manager" / "monitor.md").read_text(encoding="utf-8")
    assert "## Reap the delivered writer" not in compiled
    assert '[ "$state" = spec_review ] || { [ "$state" = blocked ]' not in compiled
    assert (
        "# reap the parked writer before a handoff/terminal transition (idempotent)"
        in compiled
    )
    assert "retained live writer" in compiled
    # The compiled body has its static conditionals resolved (per-ticket
    # placeholders like {{spec_ref}} remain for `manager render` to fill).
    assert "{{#if" not in compiled

    compiled_triage = (tmp_path / "manager" / "triage.md").read_text(encoding="utf-8")
    assert "**two-path** operation" in compiled_triage
