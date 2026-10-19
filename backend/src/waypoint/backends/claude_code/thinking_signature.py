"""Recognize Claude TUI progress narration from a thinking block's signature.

The Claude TUI shows short progress updates ("Found two issues so far…")
between tool calls, but its JSONL stores them as ``thinking`` blocks — the same
type as hidden chain-of-thought. The only observed discriminator is a label
inside the block's base64 protobuf signature envelope: field path
``2 → 1 → 8`` holds ``narration`` or ``thinking``, under a top-level varint
version field ``1 == 4``. This is a private, undocumented format, so the reader
accepts only that exact shape and treats anything else as ordinary thinking:
a missed update is preferable to showing hidden reasoning.
"""

import base64
from typing import Any

# Largest observed decoded signature is ~180 KB; this leaves ample headroom
# while keeping a pathological value from costing an unbounded decode.
MAX_SIGNATURE_CHARS = 1 << 20

_ENVELOPE_VERSION = 4
_NARRATION_LABEL = b"narration"
_MAX_VARINT_BYTES = 10

_WIRE_VARINT = 0
_WIRE_LEN = 2

type _Field = tuple[int, int, int | bytes]


# Subclasses ValueError so one handler also covers b64decode's binascii.Error
# and its ValueError for non-ASCII input.
class _MalformedEnvelope(ValueError):
    pass


def is_narration_signature(signature: Any) -> bool:
    """Whether ``signature`` is a recognized narration envelope. Never raises."""
    if not isinstance(signature, str) or not signature:
        return False
    if len(signature) > MAX_SIGNATURE_CHARS:
        return False
    try:
        raw = base64.b64decode(signature, validate=True)
        envelope = _fields(raw)
        if _only(envelope, 1, _WIRE_VARINT) != _ENVELOPE_VERSION:
            return False
        body = _only(envelope, 2, _WIRE_LEN)
        header = _only(_fields(_as_bytes(body)), 1, _WIRE_LEN)
        label = _only(_fields(_as_bytes(header)), 8, _WIRE_LEN)
    except ValueError:
        return False
    return label == _NARRATION_LABEL


def _only(fields: list[_Field], number: int, wire_type: int) -> int | bytes:
    matches = [field for field in fields if field[0] == number]
    if len(matches) != 1 or matches[0][1] != wire_type:
        raise _MalformedEnvelope(number)
    return matches[0][2]


def _as_bytes(value: int | bytes) -> bytes:
    if not isinstance(value, bytes):
        raise _MalformedEnvelope("expected bytes")
    return value


def _fields(buf: bytes) -> list[_Field]:
    """Split one protobuf message level into ``(number, wire_type, value)``."""
    fields: list[_Field] = []
    pos = 0
    end = len(buf)
    while pos < end:
        key, pos = _varint(buf, pos)
        number, wire_type = key >> 3, key & 0x7
        if number == 0:
            raise _MalformedEnvelope("field number 0")
        value: int | bytes
        match wire_type:
            case 0:
                value, pos = _varint(buf, pos)
            case 1:
                value, pos = _take(buf, pos, 8)
            case 2:
                length, pos = _varint(buf, pos)
                value, pos = _take(buf, pos, length)
            case 5:
                value, pos = _take(buf, pos, 4)
            case _:
                raise _MalformedEnvelope(f"wire type {wire_type}")
        fields.append((number, wire_type, value))
    return fields


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    for index in range(_MAX_VARINT_BYTES):
        if pos >= len(buf):
            raise _MalformedEnvelope("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return result, pos
    raise _MalformedEnvelope("varint too long")


def _take(buf: bytes, pos: int, length: int) -> tuple[bytes, int]:
    if pos + length > len(buf):
        raise _MalformedEnvelope("truncated field")
    return buf[pos : pos + length], pos + length
