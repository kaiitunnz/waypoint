"""Guards for the shipped manager template contract (RFC: manager inbox
attachments). These checked-in templates drive the manager's human gates; a
drift here silently reverts the attachment behavior, so pin the command pattern.
"""

from pathlib import Path

import pytest

_TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / ".agents/skills/waypoint-manager/templates/manager"
)


def _read(name: str) -> str:
    path = _TEMPLATES / name
    if not path.is_file():
        pytest.skip(f"shipped template not present: {path}")
    return path.read_text(encoding="utf-8")


def test_spec_gate_attaches_the_spec_and_keeps_adoption_guard() -> None:
    monitor = _read("monitor.md")
    # The spec-review gate uploads and attaches the RFC/PRD file.
    assert 'inbox post --json - --attach "{{spec_ref}}"' in monitor
    # It fails closed rather than posting a path-only gate.
    assert '[ -f "{{spec_ref}}" ]' in monitor
    # And it still adopts an open gate a crash left behind before posting a new one.
    assert 'endswith("— spec review")' in monitor


def test_pr_gate_renders_an_explicit_pull_request_link() -> None:
    integrate = _read("integrate.md")
    assert "[Open pull request]({{pr_url}})" in integrate
