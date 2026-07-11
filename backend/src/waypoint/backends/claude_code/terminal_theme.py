"""Classify a Claude Code session's effective light/dark terminal surface.

This module is **stdlib-only and imports nothing from ``waypoint``** on purpose:
the exact same file is fed to ``python3 -`` over SSH as the remote theme probe
(see ``terminal_theme_remote.py``), so there is one canonical classifier with no
hand-maintained remote copy to drift. All public functions return plain strings
(``"light"`` / ``"dark"`` / ``"unknown"``); the plugin wraps the result into
``waypoint.backends.base.TerminalAppearance``.

Resolution reads only the session's profile-scoped Claude configuration and never
issues ``/theme``, mutates a file, or returns config contents/paths/slugs to its
caller. It classifies the theme's declared light/dark *base* — it does not recolor
Claude's emitted ANSI/truecolor cells.

Precedence for the effective ``theme`` value (first present wins), mirroring
Claude's documented file-settings precedence excluding the command-line and
enterprise-managed sources Waypoint cannot inspect:

1. ``<cwd>/.claude/settings.local.json``   (local project)
2. ``<cwd>/.claude/settings.json``         (shared project)
3. ``<settings home>/settings.json``       (user)
4. ``<legacy home>/.claude.json`` ``theme`` (pre-2.1.119 compatibility fallback)

where ``<settings home>`` is ``CLAUDE_CONFIG_DIR`` when a profile is active else
``~/.claude``, and ``<legacy home>`` is ``CLAUDE_CONFIG_DIR`` else ``~`` (the CLI
keeps its legacy ``.claude.json`` in ``$HOME`` when the var is unset, but inside
the dir when it is set).
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

LIGHT = "light"
DARK = "dark"
UNKNOWN = "unknown"

# Built-in Claude theme preferences and the six custom-theme ``base`` values.
_LIGHT_THEMES = frozenset({"light", "light-ansi", "light-daltonized"})
_DARK_THEMES = frozenset({"dark", "dark-ansi", "dark-daltonized"})

_CUSTOM_PREFIX = "custom:"
# A user custom theme slug. Deliberately narrow: rejects path traversal and
# separators so a slug can never escape the theme directory.
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# A custom theme file we are willing to read; anything larger is treated as
# hostile/corrupt and resolves to unknown.
_MAX_CUSTOM_THEME_BYTES = 64 * 1024


def _read_json_object(path: Path) -> dict[str, Any] | None:
    """Load ``path`` as a JSON object, or ``None`` if absent/unreadable/not a dict.

    A regular-file check keeps us from opening a fifo/device; malformed JSON and
    read errors degrade to ``None`` so a higher-precedence bad file never masks a
    valid lower-precedence one.
    """
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _theme_value(obj: dict[str, Any] | None) -> str | None:
    if obj is None:
        return None
    value = obj.get("theme")
    return value if isinstance(value, str) and value else None


def _settings_home(config_dir: str | None) -> Path:
    return Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"


def _legacy_config_file(config_dir: str | None) -> Path:
    if config_dir:
        return Path(config_dir).expanduser() / ".claude.json"
    return Path.home() / ".claude.json"


def read_theme_preference(config_dir: str | None, cwd: str | None) -> str | None:
    """The first ``theme`` value across the precedence chain, or ``None``.

    Reads project settings only when ``cwd`` is given; always consults the user
    settings and the legacy ``.claude.json`` fallback.
    """
    candidates: list[Path] = []
    if cwd:
        project = Path(cwd).expanduser() / ".claude"
        candidates.append(project / "settings.local.json")
        candidates.append(project / "settings.json")
    candidates.append(_settings_home(config_dir) / "settings.json")
    for path in candidates:
        theme = _theme_value(_read_json_object(path))
        if theme is not None:
            return theme
    return _theme_value(_read_json_object(_legacy_config_file(config_dir)))


def _classify_custom(slug: str, theme_root: Path) -> str:
    """Resolve a user ``custom:<slug>`` theme's declared base.

    Rejects an out-of-spec slug, a file that resolves outside ``theme_root``
    (symlink escape), a non-regular file, and an oversized file — all as
    ``unknown``. Reads only ``base``; an omitted base is Claude's documented
    ``dark`` default, any other value is ``unknown``.
    """
    if not _SLUG_RE.match(slug):
        return UNKNOWN
    path = theme_root / f"{slug}.json"
    try:
        if not path.is_file():
            return UNKNOWN
        resolved = path.resolve()
        root = theme_root.resolve()
        if root not in resolved.parents:
            return UNKNOWN
        if resolved.stat().st_size > _MAX_CUSTOM_THEME_BYTES:
            return UNKNOWN
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UNKNOWN
    if not isinstance(data, dict):
        return UNKNOWN
    if "base" not in data:
        return DARK
    base = data.get("base")
    # ``base`` inherits from a built-in theme, so only the six literal built-in
    # values are accepted — never another ``custom:`` form. Mapping directly
    # (rather than back through ``classify_theme``) also rules out unbounded
    # recursion on a self-referential base.
    if base in _LIGHT_THEMES:
        return LIGHT
    if base in _DARK_THEMES:
        return DARK
    return UNKNOWN


def classify_theme(preference: str | None, theme_root: Path) -> str:
    """Map a theme preference string to ``light`` / ``dark`` / ``unknown``.

    ``theme_root`` is ``<settings home>/themes`` — the directory user custom
    themes live in. ``auto``, an absent/empty preference, plugin-contributed
    ``custom:<plugin>:<slug>`` forms, and any unrecognized value are ``unknown``.
    """
    if not preference:
        return UNKNOWN
    if preference in _LIGHT_THEMES:
        return LIGHT
    if preference in _DARK_THEMES:
        return DARK
    if preference.startswith(_CUSTOM_PREFIX):
        rest = preference[len(_CUSTOM_PREFIX) :]
        # ``custom:<plugin>:<slug>`` (a colon in the remainder) is a
        # plugin-contributed theme; resolving it would require a profile-scoped
        # enabled-plugin cache path Claude does not document. Unknown in v1.
        if ":" in rest:
            return UNKNOWN
        return _classify_custom(rest, theme_root)
    return UNKNOWN


def classify_effective_appearance(config_dir: str | None, cwd: str | None) -> str:
    """Top-level entry: resolve then classify the session's effective theme."""
    preference = read_theme_preference(config_dir, cwd)
    theme_root = _settings_home(config_dir) / "themes"
    return classify_theme(preference, theme_root)


def _main() -> int:
    """Remote-probe entry point: print ``{"appearance": "..."}`` and exit 0.

    ``CLAUDE_CONFIG_DIR`` carries the profile (absent = default home). The cwd is
    passed via ``WAYPOINT_TERMINAL_THEME_CWD`` rather than the process cwd so a
    stale/deleted remote project dir cannot fail the probe — user/legacy settings
    still resolve.
    """
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or None
    cwd = os.environ.get("WAYPOINT_TERMINAL_THEME_CWD") or None
    if cwd is None:
        try:
            cwd = os.getcwd()
        except OSError:
            cwd = None
    try:
        appearance = classify_effective_appearance(config_dir, cwd)
    except Exception:
        appearance = UNKNOWN
    sys.stdout.write(json.dumps({"appearance": appearance}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
