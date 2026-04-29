from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

HOOK_SCRIPT_NAME = "claude_pretool_hook.py"
HOOK_SETTINGS_NAME = "claude_settings.json"
HOOK_SECRET_NAME = "claude_hook_secret"
GATED_TOOLS_REGEX = (
    "^(?:Bash|Edit|Write|MultiEdit|NotebookEdit|Task|WebFetch|WebSearch|ExitPlanMode)$"
)


@dataclass
class ClaudeHookBundle:
    hook_script_path: Path
    settings_path: Path
    secret: str


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts"


def ensure_claude_hook_bundle(data_dir: Path) -> ClaudeHookBundle:
    """Materialize the hook artifacts inside `data_dir`.

    - Resolve the in-tree hook script and ensure it's executable.
    - Generate (or reuse) a per-installation secret.
    - Write a settings.json that points Claude's PreToolUse at the hook.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    hook_script = _scripts_dir() / HOOK_SCRIPT_NAME
    if not hook_script.exists():
        raise FileNotFoundError(f"claude hook script missing: {hook_script}")
    try:
        os.chmod(hook_script, 0o755)
    except PermissionError:
        pass
    secret_path = data_dir / HOOK_SECRET_NAME
    if secret_path.exists():
        secret = secret_path.read_text(encoding="utf-8").strip()
    else:
        secret = secrets.token_urlsafe(32)
        secret_path.write_text(secret, encoding="utf-8")
        try:
            os.chmod(secret_path, 0o600)
        except PermissionError:
            pass
    settings_path = data_dir / HOOK_SETTINGS_NAME
    settings_payload = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": GATED_TOOLS_REGEX,
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(hook_script),
                        }
                    ],
                }
            ]
        }
    }
    settings_path.write_text(json.dumps(settings_payload, indent=2), encoding="utf-8")
    return ClaudeHookBundle(
        hook_script_path=hook_script,
        settings_path=settings_path,
        secret=secret,
    )
