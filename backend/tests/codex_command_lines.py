"""Command lines captured from live Codex sessions that it parses as one read or
search although their program runs more, or reads more than the file it names."""

import shlex
from typing import Any

# Each as Codex wrapped it, with the action it parsed and the heading that gives.
PROGRAM_NAMED: list[tuple[str, dict[str, str], str]] = [
    (
        '/bin/bash -lc "awk \'BEGIN{system(\\"echo ran\\")}\' README.md"',
        {"type": "read", "name": "README.md"},
        "Read `README.md`",
    ),
    (
        "/bin/bash -lc \"sed -n -e 1p -e 'e echo ran' README.md\"",
        {"type": "read", "name": "README.md"},
        "Read `README.md`",
    ),
    (
        "/bin/bash -lc 'rg --pre=cat hello .'",
        {"type": "search", "query": "hello"},
        "Search `hello`",
    ),
    (
        "/bin/bash -lc 'head -n 2 README.md notes.txt'",
        {"type": "read", "name": "README.md"},
        "Read `README.md`",
    ),
]


def parsed_action(command: str, action: dict[str, str]) -> dict[str, Any]:
    """The action as Codex reported it, with the script it parsed it from."""
    return {**action, "command": shlex.split(command)[2]}
