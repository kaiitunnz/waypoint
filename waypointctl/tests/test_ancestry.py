import os
import subprocess
import sys
import time
from typing import Any

import pytest

from waypointctl import ancestry


def test_parent_pid_self() -> None:
    parent = ancestry.parent_pid(os.getpid())
    assert parent == os.getppid()


def test_is_descendant_of_self_parent() -> None:
    assert ancestry.is_descendant_of(os.getpid(), os.getppid()) is True


def test_is_descendant_of_self_is_false() -> None:
    # Walk starts from the parent; a pid is never its own descendant.
    assert ancestry.is_descendant_of(os.getpid(), os.getpid()) is False


def test_is_descendant_of_unrelated_pid_is_false() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        start_new_session=True,
    )
    try:
        time.sleep(0.1)
        assert ancestry.is_descendant_of(os.getpid(), proc.pid) is False
    finally:
        proc.terminate()
        proc.wait(timeout=2)


def test_parent_pid_returns_none_when_ps_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError

    monkeypatch.setattr(ancestry.subprocess, "run", fake_run)
    assert ancestry.parent_pid(os.getpid()) is None
