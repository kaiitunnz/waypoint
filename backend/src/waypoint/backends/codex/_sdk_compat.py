"""Runtime tolerance shims for a codex CLI newer than the pinned SDK.

The pinned ``openai-codex`` SDK ships generated Pydantic models frozen at the
SDK's release. A codex binary ahead of that release (e.g. a system install
selected via ``local_bin``) can return values the SDK has never heard of. The
SDK's typed RPC path validates responses strictly (``client.request`` ->
``model_validate`` with no fallback), so an unknown value raises
``ValidationError`` and breaks model discovery, launch, turns, and resume.

Two skews are handled here:

``ReasoningEffort`` -- the enum that grows in practice (codex 0.144.0 added
``max`` and ``ultra``). We make it an "open" enum: an unknown string value
resolves to a freshly registered member that preserves the string, instead of
raising.

``ThreadItem`` -- the discriminated-ish union of thread item types. A newer CLI
emits item types the pinned union does not model (e.g. ``subAgentActivity``),
which makes ``thread/resume``, ``thread/read``, and ``thread/fork`` responses
fail to validate and reattach return 400. We widen the union in place with an
``UnknownThreadItem`` fallback that preserves the whole item payload, so an
unknown type degrades to a generic passthrough instead of failing the response.
An after-validator rejects the *known* type literals so a genuinely malformed
known item still fails loudly.

Both shims are scoped deliberately -- a genuinely malformed response still fails
loudly. Remove this module once the pinned SDK catches up to the CLI.
"""

import threading
import typing
from enum import Enum

import openai_codex.generated.v2_all as _v2
from openai_codex.generated.v2_all import ReasoningEffort, ThreadItem
from pydantic import BaseModel, ConfigDict, model_validator

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
            # Keep iteration (`list(cls)`) consistent with lookup so a
            # fabricated member is a first-class value, not just resolvable.
            if member._name_ not in cls._member_names_:
                cls._member_names_.append(member._name_)
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


_THREAD_ITEM_SENTINEL = "_waypoint_thread_item_tolerant"


def _known_thread_item_types(union: object) -> frozenset[str]:
    """Collect the ``type`` literal of every member of the ``ThreadItem`` union.

    Raises if any member contributes no string literal -- the set is load-bearing
    (it is the only thing that stops a malformed *known* item from binding to the
    ``UnknownThreadItem`` fallback), so a broken derivation must fail loudly at
    install time rather than silently degrade the guarantee.
    """
    members = typing.get_args(union)
    known: set[str] = set()
    for member in members:
        field = getattr(member, "model_fields", {}).get("type")
        literals = typing.get_args(field.annotation) if field is not None else ()
        contributed = {value for value in literals if isinstance(value, str)}
        if not contributed:
            raise RuntimeError(
                f"ThreadItem union member {member!r} exposes no 'type' literal; "
                "cannot distinguish known from unknown thread items."
            )
        known |= contributed
    if len(known) < len(members):
        raise RuntimeError(
            f"ThreadItem type-literal derivation is incomplete "
            f"({len(known)} literals for {len(members)} members)."
        )
    return frozenset(known)


def _rebuild(cls: type[BaseModel]) -> None:
    cls.__pydantic_complete__ = False
    if "__pydantic_core_schema__" in cls.__dict__:
        delattr(cls, "__pydantic_core_schema__")
    cls.model_rebuild(force=True)


def _referenced_models(cls: type[BaseModel]) -> set[type[BaseModel]]:
    """The generated models a model refers to directly in its field annotations."""
    out: set[type[BaseModel]] = set()
    for field in cls.model_fields.values():
        stack: list[object] = [field.annotation]
        while stack:
            annotation = stack.pop()
            if (
                isinstance(annotation, type)
                and issubclass(annotation, BaseModel)
                and annotation.__module__ == _v2.__name__
            ):
                out.add(annotation)
            stack.extend(typing.get_args(annotation))
    return out


def _thread_item_containers() -> list[type[BaseModel]]:
    """Every generated model that transitively embeds ``ThreadItem``.

    Only these need rebuilding when the union is widened; the SDK ships ~660
    models but ~25 contain a thread item, so scoping the rebuild keeps the
    import-time cost negligible.
    """
    models = [
        obj
        for name in dir(_v2)
        for obj in (getattr(_v2, name),)
        if isinstance(obj, type)
        and issubclass(obj, BaseModel)
        and obj.__module__ == _v2.__name__
    ]
    parents: dict[type[BaseModel], set[type[BaseModel]]] = {}
    for model in models:
        for child in _referenced_models(model):
            parents.setdefault(child, set()).add(model)

    containers: set[type[BaseModel]] = set()
    frontier: list[type[BaseModel]] = [ThreadItem]
    while frontier:
        for parent in parents.get(frontier.pop(), ()):
            if parent not in containers:
                containers.add(parent)
                frontier.append(parent)
    return list(containers)


_THREAD_ITEM_REBUILD_PASSES = 6
_PROBE_UNKNOWN_TYPE = "_waypointUnknownItemProbe"


def install_thread_item_tolerance() -> None:
    """Widen ``ThreadItem`` in place so unknown item types survive validation.

    Idempotent. The union is mutated on the existing ``ThreadItem`` class (its
    identity is preserved) so every parent model's already-resolved
    ``list[ThreadItem]`` annotation keeps pointing at it; a forced rebuild of the
    parents then recompiles them against the widened union.
    """
    if getattr(ThreadItem, _THREAD_ITEM_SENTINEL, False):
        return

    original_union = ThreadItem.model_fields["root"].annotation
    known_types = _known_thread_item_types(original_union)

    class UnknownThreadItem(BaseModel):
        """Fallback member preserving a thread item whose ``type`` the pinned SDK
        union does not model. ``extra='allow'`` keeps the entire payload so the
        item round-trips through ``model_dump`` for downstream rendering."""

        model_config = ConfigDict(extra="allow", populate_by_name=True)
        type: str

        @model_validator(mode="after")
        def _reject_known_types(self) -> "UnknownThreadItem":
            if self.type in known_types:
                raise ValueError(f"{self.type!r} is a known thread item type")
            return self

    # Runtime union widening: the annotation swap is invisible to static typing.
    widened_union = original_union | UnknownThreadItem
    ThreadItem.model_fields["root"].annotation = widened_union  # type: ignore[assignment]
    _v2.UnknownThreadItem = UnknownThreadItem  # type: ignore[attr-defined]

    _rebuild(ThreadItem)
    containers = _thread_item_containers()
    # ThreadItem sits at the bottom of a containment DAG
    # (ThreadItem -> Turn -> Thread -> *Response / *Notification). Each pass
    # propagates the widened union one level up; the SDK's depth is ~4, so a few
    # passes reach a fixpoint. Order-independent (and cycle-safe) rather than
    # relying on a topological sort of the SDK's shape.
    for _ in range(_THREAD_ITEM_REBUILD_PASSES):
        for cls in containers:
            _rebuild(cls)

    # Tripwire: prove propagation actually took. ``Turn`` embeds
    # ``list[ThreadItem]`` and needs no enum-heavy payload to validate.
    ThreadItem.model_validate({"type": _PROBE_UNKNOWN_TYPE, "id": "_probe"})
    _v2.Turn.model_validate(
        {
            "id": "_probe",
            "status": "completed",
            "items": [{"type": _PROBE_UNKNOWN_TYPE, "id": "_probe"}],
        }
    )

    setattr(ThreadItem, _THREAD_ITEM_SENTINEL, True)


install_thread_item_tolerance()
