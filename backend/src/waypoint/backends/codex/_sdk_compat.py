"""Runtime tolerance shims for a codex CLI newer than the pinned SDK.

The pinned ``openai-codex`` SDK ships generated Pydantic models whose enums are
frozen at the SDK's release. A codex binary ahead of that release (e.g. a system
install selected via ``local_bin``) can return enum values the SDK has never
heard of. The SDK's typed RPC path validates responses strictly
(``client.request`` -> ``model_validate`` with no fallback), so an unknown value
raises ``ValidationError`` and breaks model discovery, launch, and turns.

``ReasoningEffort`` is the enum that grows in practice (codex 0.144.0 added
``max`` and ``ultra``). We make it an "open" enum: an unknown string value
resolves to a freshly registered member that preserves the string, instead of
raising. The value flows through Waypoint as a plain string, so nothing else
needs to change.

Scoped deliberately to ``ReasoningEffort`` -- the only enum observed to skew --
rather than every SDK enum, so a genuinely malformed response still fails loudly.
Extending to another enum later is a one-line ``_open_enum`` call. Remove this
module once the pinned SDK catches up to the CLI.
"""

import threading
from enum import Enum

from openai_codex.generated.v2_all import ReasoningEffort

_lock = threading.Lock()
_SENTINEL = "_waypoint_open_enum"

# Effort values known to exist in newer codex CLIs but missing from the pinned
# SDK enum. Pre-seeded eagerly (single-threaded, at install) so the common case
# never mutates the enum from the SDK reader thread at runtime.
_KNOWN_NEW_REASONING_EFFORTS = ("max", "ultra")


def _open_enum(enum_cls: type[Enum]) -> None:
    """Make *enum_cls* accept unknown string values by registering them lazily.

    Idempotent. The registration write is guarded by ``_lock`` with a
    double-check so concurrent first-sights of a new value from the SDK reader
    thread don't double-insert.
    """
    if getattr(enum_cls, _SENTINEL, False):
        return

    def _missing_(cls: type[Enum], value: object) -> "Enum | None":
        if not isinstance(value, str):
            return None
        with _lock:
            existing = cls._value2member_map_.get(value)
            if existing is not None:
                return existing
            member = object.__new__(cls)
            member._name_ = value.upper().replace("-", "_")
            member._value_ = value
            cls._value2member_map_[value] = member
            cls._member_map_.setdefault(member._name_, member)
            return member

    enum_cls._missing_ = classmethod(_missing_)  # type: ignore[assignment]
    setattr(enum_cls, _SENTINEL, True)


def install_reasoning_effort_tolerance() -> None:
    """Open ``ReasoningEffort`` and pre-seed the known-new effort values.

    Idempotent. Invoked on import of this module (below) so the shim is active
    before any ``CodexClient`` validates a response.
    """
    _open_enum(ReasoningEffort)
    for value in _KNOWN_NEW_REASONING_EFFORTS:
        ReasoningEffort(value)


install_reasoning_effort_tolerance()
