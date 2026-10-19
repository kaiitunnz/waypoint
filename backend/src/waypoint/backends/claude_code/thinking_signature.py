"""Recognize Claude TUI progress narration from a thinking block's signature.

The Claude TUI shows progress updates between tool calls but stores them as
``thinking`` blocks, like hidden reasoning. The only discriminator is a label
in the block's base64 protobuf signature: field ``2 → 1 → 8`` is ``narration``
under a top-level version field ``1 == 4``. Any other shape counts as ordinary
thinking: missing an update is safer than showing hidden reasoning.
"""

import base64
from typing import Any

_ENVELOPE_VERSION = 4
_NARRATION_LABEL = b"narration"
_MAX_VARINT_BYTES = 10

type _Fields = list[tuple[int, int | bytes]]


def is_narration_signature(signature: Any) -> bool:
    """Whether ``signature`` is a recognized narration envelope. Never raises."""
    if not isinstance(signature, str):
        return False
    # b64decode raises binascii.Error or ValueError, both ValueError subclasses.
    try:
        envelope = _fields(base64.b64decode(signature, validate=True))
        if _only(envelope, 1, int) != _ENVELOPE_VERSION:
            return False
        header = _only(_fields(_only(envelope, 2, bytes)), 1, bytes)
        return _only(_fields(header), 8, bytes) == _NARRATION_LABEL
    except ValueError:
        return False


def _only[T: (int, bytes)](fields: _Fields, number: int, kind: type[T]) -> T:
    values = [value for n, value in fields if n == number]
    if len(values) != 1 or not isinstance(value := values[0], kind):
        raise ValueError(f"field {number}")
    return value


def _fields(buf: bytes) -> _Fields:
    """Split one protobuf message level into ``(number, value)`` pairs."""
    fields: _Fields = []
    pos = 0
    while pos < len(buf):
        key, pos = _varint(buf, pos)
        if not (number := key >> 3):
            raise ValueError("field number 0")
        value: int | bytes
        match key & 0x7:
            case 0:
                value, pos = _varint(buf, pos)
            case 1:
                value, pos = _take(buf, pos, 8)
            case 2:
                length, pos = _varint(buf, pos)
                value, pos = _take(buf, pos, length)
            case 5:
                value, pos = _take(buf, pos, 4)
            case wire_type:
                raise ValueError(f"wire type {wire_type}")
        fields.append((number, value))
    return fields


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    for index in range(_MAX_VARINT_BYTES):
        if pos >= len(buf):
            raise ValueError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return result, pos
    raise ValueError("varint too long")


def _take(buf: bytes, pos: int, length: int) -> tuple[bytes, int]:
    if pos + length > len(buf):
        raise ValueError("truncated field")
    return buf[pos : pos + length], pos + length
