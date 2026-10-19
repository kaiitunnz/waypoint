"""Synthetic Claude thinking-signature envelopes for tests.

Mirrors the observed shape (top-level version field 1, label at 2 → 1 → 8)
with invented opaque bytes; no real signature material.
"""

import base64


def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def field_varint(number: int, value: int) -> bytes:
    return varint(number << 3) + varint(value)


def field_bytes(number: int, value: bytes) -> bytes:
    return varint(number << 3 | 2) + varint(len(value)) + value


def envelope(label: bytes = b"narration", version: int | None = 4) -> bytes:
    header = field_varint(1, 18) + field_varint(3, 2) + field_bytes(8, label)
    body = (
        field_bytes(1, header)
        + field_bytes(2, b"\x01" * 12)
        + field_bytes(5, b"\x02" * 64)
    )
    prefix = field_varint(1, version) if version is not None else b""
    return prefix + field_bytes(2, body) + field_varint(3, 1)


def signature(label: bytes = b"narration", version: int | None = 4) -> str:
    return base64.b64encode(envelope(label, version)).decode()


def thinking_block(text: str, label: bytes = b"narration") -> dict:
    return {"type": "thinking", "thinking": text, "signature": signature(label)}
