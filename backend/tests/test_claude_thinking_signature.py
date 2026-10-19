"""Tests for recognizing Claude TUI narration thinking signatures."""

import base64
from typing import Any

import pytest

from claude_signatures import (
    envelope,
    field_bytes,
    field_varint,
    signature,
    varint,
)
from waypoint.backends.claude_code.thinking_signature import (
    MAX_SIGNATURE_CHARS,
    is_narration_signature,
)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _header(*fields: bytes) -> bytes:
    return field_bytes(1, b"".join(fields))


def test_accepts_narration_envelope() -> None:
    assert is_narration_signature(signature())


def test_rejects_ordinary_thinking_label() -> None:
    assert not is_narration_signature(signature(b"thinking"))


@pytest.mark.parametrize("version", [None, 2, 5])
def test_rejects_missing_or_unknown_version(version: int | None) -> None:
    assert not is_narration_signature(signature(version=version))


def test_rejects_duplicate_label() -> None:
    header = _header(field_bytes(8, b"narration"), field_bytes(8, b"narration"))
    raw = field_varint(1, 4) + field_bytes(2, header)
    assert not is_narration_signature(_b64(raw))


def test_rejects_duplicate_header() -> None:
    header = _header(field_bytes(8, b"narration"))
    raw = field_varint(1, 4) + field_bytes(2, header + header)
    assert not is_narration_signature(_b64(raw))


def test_rejects_duplicate_body() -> None:
    body = field_bytes(2, _header(field_bytes(8, b"narration")))
    assert not is_narration_signature(_b64(field_varint(1, 4) + body + body))


def test_rejects_duplicate_version() -> None:
    assert not is_narration_signature(_b64(field_varint(1, 4) + envelope()))


def test_rejects_label_with_wrong_wire_type() -> None:
    raw = field_varint(1, 4) + field_bytes(2, _header(field_varint(8, 9)))
    assert not is_narration_signature(_b64(raw))


def test_rejects_label_at_wrong_field_path() -> None:
    raw = field_varint(1, 4) + field_bytes(2, _header(field_bytes(7, b"narration")))
    assert not is_narration_signature(_b64(raw))


def test_rejects_narration_bytes_only_in_opaque_field() -> None:
    body = _header(field_bytes(8, b"thinking")) + field_bytes(5, b"xxnarrationxx")
    raw = field_varint(1, 4) + field_bytes(2, body)
    assert not is_narration_signature(_b64(raw))


def test_rejects_truncated_length() -> None:
    raw = envelope()
    assert not is_narration_signature(_b64(raw[:-5]))


def test_rejects_unterminated_varint() -> None:
    assert not is_narration_signature(_b64(envelope() + b"\x80"))


def test_rejects_overlong_varint() -> None:
    assert not is_narration_signature(_b64(b"\x08" + b"\xff" * 11 + b"\x01"))


def test_rejects_field_number_zero() -> None:
    assert not is_narration_signature(_b64(b"\x00\x00" + envelope()))


@pytest.mark.parametrize("wire_type", [3, 4, 6, 7])
def test_rejects_unsupported_wire_types(wire_type: int) -> None:
    raw = envelope() + varint(9 << 3 | wire_type)
    assert not is_narration_signature(_b64(raw))


def test_skips_fixed_width_fields() -> None:
    fixed = varint(9 << 3 | 1) + b"\x00" * 8 + varint(10 << 3 | 5) + b"\x00" * 4
    assert is_narration_signature(_b64(fixed + envelope()))


@pytest.mark.parametrize(
    "value",
    [None, "", 42, "not base64!", "QUJD=x", signature()[:-3]],
)
def test_rejects_non_signatures(value: Any) -> None:
    assert not is_narration_signature(value)


def test_rejects_oversized_signature() -> None:
    padded = envelope() + field_bytes(20, b"\x00" * MAX_SIGNATURE_CHARS)
    assert not is_narration_signature(_b64(padded))
