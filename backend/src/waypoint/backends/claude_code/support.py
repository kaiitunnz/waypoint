from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

THREAD_ENUMERATOR_NAME = "claude_thread_enumerator.sh"


@dataclass
class ClaudeSupportBundle:
    thread_enumerator_path: Path


def _scripts_dir() -> Path:
    # __file__ lives at backend/src/waypoint/backends/claude_code/support.py;
    # the helper scripts ship at backend/scripts/.
    return Path(__file__).resolve().parents[4] / "scripts"


def ensure_claude_support_bundle(data_dir: Path) -> ClaudeSupportBundle:
    """Resolve the host-side scripts the Claude backend depends on.

    Tool approval now rides the ``can_use_tool`` control protocol, so the only
    bundled artifact left is the remote thread enumerator.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    thread_enumerator = _scripts_dir() / THREAD_ENUMERATOR_NAME
    if not thread_enumerator.exists():
        raise FileNotFoundError(
            f"claude thread enumerator missing: {thread_enumerator}"
        )
    try:
        os.chmod(thread_enumerator, 0o755)
    except PermissionError:
        pass
    return ClaudeSupportBundle(thread_enumerator_path=thread_enumerator)
