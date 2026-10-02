"""Runtime tolerance shim for a codex CLI newer than the pinned SDK.

The pinned ``openai-codex`` SDK ships generated Pydantic models frozen at the
SDK's release. A codex binary ahead of that release (e.g. a system install
selected via ``local_bin``) can return values the SDK has never heard of. The
SDK's typed RPC path validates responses strictly (``client.request`` ->
``model_validate`` with no fallback), so an unknown value raises
``ValidationError`` and breaks model discovery, launch, turns, and resume.

``ThreadItem`` -- the discriminated-ish union of thread item types. A newer CLI
emits item types the pinned union does not model, which makes ``thread/resume``,
``thread/read``, and ``thread/fork`` responses fail to validate and reattach
return 400. We widen the union in place with an ``UnknownThreadItem`` fallback
that preserves the whole item payload, so an unknown type degrades to a generic
passthrough instead of failing the response. An after-validator rejects the
*known* type literals so a genuinely malformed known item still fails loudly.

The ``ReasoningEffort`` enum grows in practice (codex 0.144.0 added ``max`` and
``ultra``), but the SDK's own ``ReasoningEffort`` is now an open ``str`` enum
whose ``_missing_`` preserves any unknown string value, so no shim is needed.

``SubAgentActivityKind`` -- the nested ``kind`` enum of the modeled
``subAgentActivity`` item. A newer CLI persists ``kind: "completed"``, which the
pinned enum (``started``/``interacted``/``interrupted``) lacks, so the resumed
thread fails ``thread/resume`` validation and reattach returns 400. We tolerate
the single value ``completed``; every other value stays rejected.

The shim is scoped deliberately -- a genuinely malformed response still fails
loudly. Remove the ``ThreadItem`` widening once the pinned SDK's union catches up
to the CLI, and the ``SubAgentActivityKind`` fallback once the pinned SDK models
``completed`` natively.

``install_ended_turn_rerouting`` -- a notification can name a turn that has
already completed: a subagent that finishes after its parent's turn ended
reports ``subAgentActivity`` ``completed`` on that ended turn. The router
buffers it for a turn nobody streams any more and then drops it. The shim
sends any notification for a turn whose ``turn/completed`` was already routed
to the global queue instead, where Waypoint's notification pump reads it.
Delete it once the SDK delivers late turn notifications.

``start_compaction_turn`` -- ``thread/compact/start`` returns ``{}``, yet the
compaction runs as an ordinary turn whose notifications carry a fresh turn id.
The helper reuses the router's own turn-start buffering (``pending_turn`` /
``prepare_turn``, as ``CodexClient._start_turn`` does around ``turn/start``) to
learn that id and subscribe to the turn. Delete it once ``thread/compact/start``
returns the turn id.
"""

import threading
import time
import typing
from collections import OrderedDict

import openai_codex.generated.v2_all as _v2
from openai_codex._message_router import MessageRouter
from openai_codex.client import CodexClient
from openai_codex.generated.v2_all import SubAgentActivityKind, ThreadItem
from pydantic import BaseModel, ConfigDict, model_validator

_THREAD_ITEM_SENTINEL = "_waypoint_thread_item_tolerant"
_ACTIVITY_KIND_SENTINEL = "_waypoint_activity_kind_tolerant"
_TOLERATED_ACTIVITY_KIND = "completed"
_REROUTING_SENTINEL = "_waypoint_ended_turn_rerouting"
_ENDED_TURNS_ATTR = "_waypoint_ended_turns"
# Ended turns remembered per client; a late notification for an older one is
# dropped as before.
_ENDED_TURNS_LIMIT = 256


def _known_thread_item_types(union: typing.Any) -> frozenset[str]:
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


def _thread_item_containers() -> tuple[list[type[BaseModel]], int]:
    """Every generated model transitively embedding ``ThreadItem``, plus the
    longest containment chain length from ``ThreadItem`` to a container.

    Only these models need rebuilding when the union is widened; the SDK ships
    ~660 models but ~25 contain a thread item, so scoping the rebuild keeps the
    import-time cost negligible. The chain length is the number of rebuild passes
    needed to propagate the widened union to the deepest container, so callers
    size the rebuild to the actual graph rather than a fixed guess.
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

    depth_memo: dict[type[BaseModel], int] = {}

    def _chain_depth(node: type[BaseModel], on_path: frozenset[type[BaseModel]]) -> int:
        longest = 0
        for parent in parents.get(node, ()):
            if parent in on_path:  # cycle guard; the container graph is a DAG
                continue
            if parent not in depth_memo:
                depth_memo[parent] = _chain_depth(parent, on_path | {parent})
            longest = max(longest, 1 + depth_memo[parent])
        return longest

    depth = _chain_depth(ThreadItem, frozenset({ThreadItem}))
    return list(containers), depth


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
    containers, depth = _thread_item_containers()
    # ThreadItem sits at the bottom of a containment DAG
    # (ThreadItem -> Turn -> Thread -> *Response / *Notification). Rebuilding all
    # containers once propagates the widened union one level up; repeating for the
    # DAG's depth reaches a fixpoint regardless of iteration order. Deriving the
    # pass count from the measured depth means a future SDK that deepens the graph
    # cannot silently outrun a hard-coded count.
    for _ in range(max(1, depth)):
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


def install_activity_kind_tolerance() -> None:
    """Tolerate ``SubAgentActivityKind.completed`` from a CLI ahead of the SDK.

    Idempotent, and inert when the pinned SDK already models ``completed``. The
    fallback mints a member only for the exact string ``"completed"`` and
    delegates every other value to the SDK's original ``_missing_``, so unknown,
    empty, and non-string kinds stay rejected. Enum membership is resolved at
    validation time, so no generated-model rebuild is needed.
    """
    if any(member.value == _TOLERATED_ACTIVITY_KIND for member in SubAgentActivityKind):
        return
    if getattr(SubAgentActivityKind, _ACTIVITY_KIND_SENTINEL, False):
        return

    original_missing = SubAgentActivityKind._missing_.__func__  # type: ignore[attr-defined]

    def _missing_(cls: type[SubAgentActivityKind], value: object) -> object:
        if value == _TOLERATED_ACTIVITY_KIND:
            # Plain (non-str) Enum, so the pseudo member uses object.__new__;
            # _value_ carries the wire string so model_dump re-emits "completed".
            member = object.__new__(cls)
            member._name_ = _TOLERATED_ACTIVITY_KIND
            member._value_ = _TOLERATED_ACTIVITY_KIND
            return member
        return original_missing(cls, value)

    SubAgentActivityKind._missing_ = classmethod(_missing_)  # type: ignore[assignment]
    setattr(SubAgentActivityKind, _ACTIVITY_KIND_SENTINEL, True)


def install_ended_turn_rerouting() -> None:
    """Route notifications for an already-completed turn to the global queue.

    Idempotent. A thread with a goal operation keeps the SDK's routing, since
    the goal consumes its turns' notifications itself.
    """
    if getattr(MessageRouter, _REROUTING_SENTINEL, False):
        return
    original = MessageRouter.route_notification

    def route_notification(self: typing.Any, notification: typing.Any) -> None:
        turn_id = self._notification_turn_id(notification)
        if turn_id is not None:
            thread_id = self._notification_thread_id(notification)
            with self._lock:
                ended = self.__dict__.setdefault(_ENDED_TURNS_ATTR, OrderedDict())
                rerouted = (
                    turn_id in ended
                    and notification.method != "turn/completed"
                    and (thread_id is None or thread_id not in self._goal_operations)
                )
                if notification.method == "turn/completed":
                    ended[turn_id] = None
                    while len(ended) > _ENDED_TURNS_LIMIT:
                        ended.popitem(last=False)
            if rerouted:
                self._global_notifications.put(notification)
                return
        original(self, notification)

    MessageRouter.route_notification = route_notification  # type: ignore[method-assign]
    setattr(MessageRouter, _REROUTING_SENTINEL, True)


_COMPACTION_POLL_SECONDS = 0.05


def start_compaction_turn(
    client: CodexClient,
    thread_id: str,
    timeout: float,
    cancelled: threading.Event | None = None,
) -> str | None:
    """Start a manual compaction and register its turn for streaming.

    Blocking; run it on a worker thread. Returns the compaction turn's id, after
    which ``client.next_turn_notification(turn_id)`` replays the buffered
    ``turn/started`` and everything after it, or ``None`` when no new turn for
    the thread appears within ``timeout`` seconds or ``cancelled`` is set.
    """
    router = client._router
    with client._thread_start_lock(thread_id):
        # Holds off pruning of this thread's turn events until a consumer
        # attaches, so nothing the compaction emits early is lost.
        with router.pending_turn(thread_id) as cursors:
            client.thread_compact(thread_id)
            turn_id = _await_new_turn(router, thread_id, cursors, timeout, cancelled)
            if turn_id is not None:
                router.prepare_turn(turn_id, thread_id, cursors, for_handle=False)
            return turn_id


def _await_new_turn(
    router: typing.Any,
    thread_id: str,
    cursors: dict[str, int],
    timeout: float,
    cancelled: threading.Event | None,
) -> str | None:
    deadline = time.monotonic() + timeout
    while True:
        with router._lock:
            for turn_id, state in router._turn_states.items():
                if (
                    turn_id not in cursors
                    and state.thread_id == thread_id
                    and _opens_with_turn_started(state)
                ):
                    return str(turn_id)
        if (cancelled is not None and cancelled.is_set()) or (
            time.monotonic() >= deadline
        ):
            return None
        time.sleep(_COMPACTION_POLL_SECONDS)


def _opens_with_turn_started(state: typing.Any) -> bool:
    """A new turn's buffer starts at its ``turn/started``. A late event for a
    finished turn also creates an entry while pruning is held, but never
    with that first event."""
    first = state.events.get(state.first_event)
    return getattr(first, "method", None) == "turn/started"


# The pinned SDK's thread item ``type`` literals, captured before widening.
KNOWN_THREAD_ITEM_TYPES = _known_thread_item_types(
    ThreadItem.model_fields["root"].annotation
)

install_thread_item_tolerance()
install_activity_kind_tolerance()
install_ended_turn_rerouting()
