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
from waypoint.backends.claude_code.thinking_signature import is_narration_signature


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _header(*fields: bytes) -> bytes:
    return field_bytes(1, b"".join(fields))


_LABEL = field_bytes(8, b"narration")
_VERSION = field_varint(1, 4)


def test_accepts_narration_envelope() -> None:
    assert is_narration_signature(signature())


def test_rejects_ordinary_thinking_label() -> None:
    assert not is_narration_signature(signature(b"thinking"))


@pytest.mark.parametrize("version", [None, 2, 5])
def test_rejects_missing_or_unknown_version(version: int | None) -> None:
    assert not is_narration_signature(signature(version=version))


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(
            _VERSION + field_bytes(2, _header(_LABEL, _LABEL)), id="duplicate-label"
        ),
        pytest.param(
            _VERSION + field_bytes(2, _header(_LABEL) + _header(_LABEL)),
            id="duplicate-header",
        ),
        pytest.param(
            _VERSION + field_bytes(2, _header(_LABEL)) * 2, id="duplicate-body"
        ),
        pytest.param(_VERSION + envelope(), id="duplicate-version"),
        pytest.param(
            _VERSION + field_bytes(2, _header(field_varint(8, 9))),
            id="label-wrong-wire-type",
        ),
        pytest.param(
            _VERSION + field_bytes(2, _header(field_bytes(7, b"narration"))),
            id="label-wrong-field",
        ),
        pytest.param(
            _VERSION
            + field_bytes(
                2,
                _header(field_bytes(8, b"thinking")) + field_bytes(5, b"xxnarrationxx"),
            ),
            id="label-only-in-opaque-bytes",
        ),
        pytest.param(envelope()[:-5], id="truncated-length"),
        pytest.param(envelope() + b"\x80", id="unterminated-varint"),
        pytest.param(b"\x08" + b"\xff" * 11 + b"\x01", id="overlong-varint"),
        pytest.param(b"\x00\x00" + envelope(), id="field-number-zero"),
        *(
            pytest.param(envelope() + varint(9 << 3 | wire), id=f"wire-type-{wire}")
            for wire in (3, 4, 6, 7)
        ),
    ],
)
def test_rejects_malformed_envelope(raw: bytes) -> None:
    assert not is_narration_signature(_b64(raw))


def test_skips_fixed_width_fields() -> None:
    fixed = varint(9 << 3 | 1) + b"\x00" * 8 + varint(10 << 3 | 5) + b"\x00" * 4
    assert is_narration_signature(_b64(fixed + envelope()))


@pytest.mark.parametrize(
    "value",
    [None, "", 42, "not base64!", "QUJD=x", "QUJDé", signature()[:-3]],
)
def test_rejects_non_signatures(value: Any) -> None:
    assert not is_narration_signature(value)
