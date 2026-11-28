"""A Codex command line, read strictly enough to tell what a summary of it leaves out."""

import os
import re
import shlex

# Shells Codex wraps a command line in.
_SHELLS = frozenset({"bash", "sh", "zsh"})
# The flags that hand a shell its script.
_SCRIPT_FLAGS = frozenset({"-c", "-lc"})

# All a script may hold: blanks, pipes, quoted strings with nothing to expand,
# and unquoted characters that stand for themselves.
_TOKEN = re.compile(
    r"""(?P<blank>[ \t]+)
    |(?P<pipe>\|)
    |'(?P<single>[^']*)'
    |"(?P<double>[^"$`\\]*)"
    |(?P<plain>[\w@%+=:,./~^-]+)""",
    re.VERBOSE,
)

# Filters that only cut down what is piped into them, each with its flags whose
# value is the next word.
_TRIMMERS = {
    "head": frozenset({"-n", "-c", "--lines", "--bytes"}),
    "tail": frozenset({"-n", "-c", "--lines", "--bytes"}),
    "wc": frozenset[str](),
    "nl": frozenset({"-w", "-v", "-i"}),
}
# A trimmer's flag. The long ones are listed: `wc --files0-from` reads a file.
_FLAG = re.compile(
    r"-[A-Za-z]+\d*|-\d+"
    r"|--(?:lines|bytes|chars|words|quiet|silent|verbose)(?:=[+-]?\d+)?"
)
_NUMBER = re.compile(r"[+-]?\d+")


def runs_only(command: str, parsed: str) -> bool:
    """Whether ``parsed`` is all a command line runs, besides trimming its output.

    False for anything not fully understood: another command, a redirection, a
    substitution or subshell, or a trimmer that is given a file."""
    if (stages := _stages(_script(command))) is None:
        return False
    first, *trimmers = stages
    return _stages(parsed) == [first] and all(map(_trims, trimmers))


def _script(command: str) -> str:
    """The script of a shell-wrapped command line, or else the line itself."""
    try:
        words = shlex.split(command)
    except ValueError:
        return command
    match words:
        case [shell, flag, script] if (
            os.path.basename(shell) in _SHELLS and flag in _SCRIPT_FLAGS
        ):
            return script
        case _:
            return command


def _stages(script: str) -> list[list[str]] | None:
    """A script's pipeline as the words of each stage, or ``None`` when it holds
    anything else."""
    stages: list[list[str]] = [[]]
    word: str | None = None
    position = 0
    while position < len(script):
        if (token := _TOKEN.match(script, position)) is None:
            return None
        position = token.end()
        match token.lastgroup:
            case "blank" | "pipe" as separator:
                if word is not None:
                    stages[-1].append(word)
                    word = None
                if separator == "pipe":
                    stages.append([])
            case str(group):
                word = (word or "") + token[group]
    if word is not None:
        stages[-1].append(word)
    return stages if all(stages) else None


def _trims(stage: list[str]) -> bool:
    """Whether a stage is a trimmer whose arguments are only flags and numbers."""
    name, *arguments = stage
    if (valued := _TRIMMERS.get(name)) is None:
        return False
    wants_number = False
    for argument in arguments:
        if wants_number:
            if not _NUMBER.fullmatch(argument):
                return False
            wants_number = False
        elif _FLAG.fullmatch(argument):
            wants_number = argument in valued
        else:
            return False
    return not wants_number
