"""Server-side port of the dashboard's session search (``frontend/src/lib/search.ts``).

A query is OR-separated groups of AND terms. A term is an optional ``field:``
prefix (with the dashboard's aliases) and a bare word, a quoted phrase, or a
``/regex/flags`` literal. Plain terms are case-insensitive substring matches;
regex terms always match case-insensitively. Matching runs over the plain
session columns as the dashboard would see them in the session's JSON dump, so
structured fields (usage, transport state, tags) are not searchable. Regexes use
Python's ``regex`` dialect, which accepts the common JavaScript syntax.

Regex evaluation is bounded by a per-search deadline so a pathological pattern
fails the request instead of stalling the server.
"""

import enum
import json
import re
import time
import types
import typing
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import regex

from waypoint.schemas import SessionRecord, SessionStatus

DEFAULT_SEARCH_FIELDS = (
    "title",
    "cwd",
    "repo_name",
    "branch",
    "backend",
    "search_status",
)
SEARCH_BUDGET_SECONDS = 0.25

_ALIASES = {
    "repo": "repo_name",
    "dir": "cwd",
    "directory": "cwd",
    "path": "cwd",
    "app": "backend",
    "agent": "backend",
    "state": "search_status",
    "status": "search_status",
}
_TOKEN = re.compile(
    r"""(?:([a-zA-Z0-9_]+):)?(/(?:\\/|[^/])+/[a-z]*|"[^"]*"|'[^']*'|\S+)"""
)
_JS_REGEX_FLAGS = frozenset("dgimsuyv")
_VIRTUAL_STATUS_FIELD = "search_status"


class FieldKind(enum.Enum):
    TEXT = "text"
    BOOL = "bool"
    DATETIME = "datetime"
    LIST = "list"


class SearchBudgetExceeded(Exception):
    """The search could not finish within its time budget."""


@dataclass(frozen=True)
class SearchTerm:
    field: str | None
    text: str
    pattern: regex.Pattern[str] | None = None


type ParsedQuery = list[list[SearchTerm]]


def _field_kind(annotation: Any) -> FieldKind | None:
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return FieldKind.TEXT
    if origin is list:
        return FieldKind.LIST
    if isinstance(annotation, types.UnionType) or origin is typing.Union:
        kinds = {
            _field_kind(arg)
            for arg in typing.get_args(annotation)
            if arg is not type(None)
        }
        return kinds.pop() if len(kinds) == 1 else None
    if annotation is bool:
        return FieldKind.BOOL
    if annotation is datetime:
        return FieldKind.DATETIME
    if annotation in (str, int) or (
        isinstance(annotation, type) and issubclass(annotation, enum.Enum)
    ):
        return FieldKind.TEXT
    return None


SEARCHABLE_FIELDS: dict[str, FieldKind] = {
    name: kind
    for name, info in SessionRecord.model_fields.items()
    if not info.exclude and (kind := _field_kind(info.annotation)) is not None
}


def _compile_regex(raw: str) -> regex.Pattern[str] | None:
    last_slash = raw.rindex("/")
    source, flags = raw[1:last_slash], raw[last_slash + 1 :]
    if len(set(flags)) != len(flags) or not set(flags) <= _JS_REGEX_FLAGS:
        return None
    options = regex.IGNORECASE
    if "m" in flags:
        options |= regex.MULTILINE
    if "s" in flags:
        options |= regex.DOTALL
    try:
        return regex.compile(source, options)
    except regex.error:
        return None


def parse_query(query: str) -> ParsedQuery:
    groups: ParsedQuery = []
    current: list[SearchTerm] = []
    for match in _TOKEN.finditer(query):
        field = match.group(1)
        raw = match.group(2)
        if field is None:
            keyword = raw.upper()
            if keyword == "OR":
                if current:
                    groups.append(current)
                    current = []
                continue
            if keyword == "AND":
                continue
        else:
            field = field.lower()
            field = _ALIASES.get(field, field)
        if raw.startswith("/") and raw.rfind("/") > 0:
            if (pattern := _compile_regex(raw)) is not None:
                current.append(SearchTerm(field, raw, pattern))
                continue
            current.append(SearchTerm(field, raw.lower()))
        elif len(raw) > 1 and raw[0] in "\"'" and raw[-1] == raw[0]:
            current.append(SearchTerm(field, raw[1:-1].lower()))
        else:
            current.append(SearchTerm(field, raw.lower()))
    if current:
        groups.append(current)
    return groups


def required_columns(
    parsed: ParsedQuery, default_fields: Sequence[str] = DEFAULT_SEARCH_FIELDS
) -> list[str]:
    """Storage columns a match pass over ``parsed`` needs to read."""
    fields = set(default_fields)
    fields.update(term.field for group in parsed for term in group if term.field)
    if _VIRTUAL_STATUS_FIELD in fields:
        fields.add("status")
    return sorted(field for field in fields if field in SEARCHABLE_FIELDS)


def field_text(row: Mapping[str, Any], field: str) -> str | None:
    """Render a stored column the way the dashboard stringifies its JSON value."""
    if field == _VIRTUAL_STATUS_FIELD:
        status = row.get("status")
        if not status:
            return None
        return status if status == SessionStatus.EXITED else f"{status} active"
    kind = SEARCHABLE_FIELDS.get(field)
    value = row.get(field)
    if kind is None or value is None:
        return None
    match kind:
        case FieldKind.BOOL:
            return "true" if value else "false"
        case FieldKind.DATETIME:
            text = str(value)
            return f"{text[:-6]}Z" if text.endswith("+00:00") else text
        case FieldKind.LIST:
            try:
                items = json.loads(value)
            except (TypeError, ValueError):
                return None
            return ",".join(str(item) for item in items)
    return str(value)


def _term_matches(term: SearchTerm, value: str, deadline: float) -> bool:
    if term.pattern is None:
        return term.text in value.lower()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SearchBudgetExceeded
    try:
        return (
            term.pattern.search(value, timeout=remaining, concurrent=True) is not None
        )
    except TimeoutError as exc:
        raise SearchBudgetExceeded from exc


def matches(
    row: Mapping[str, Any],
    parsed: ParsedQuery,
    deadline: float,
    default_fields: Sequence[str] = DEFAULT_SEARCH_FIELDS,
) -> bool:
    """Whether ``row`` matches; raises ``SearchBudgetExceeded`` past ``deadline``."""
    if not parsed:
        return True
    for group in parsed:
        if all(
            any(
                (value := field_text(row, field)) is not None
                and _term_matches(term, value, deadline)
                for field in ((term.field,) if term.field else default_fields)
            )
            for term in group
        ):
            return True
    return False
