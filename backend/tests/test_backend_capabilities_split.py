"""Capability split: agent vs transport axes, and the flat compat aggregate.

``BackendCapabilities`` is the flat descriptor every plugin declares and the
API serialises. It is the thin compatibility layer over the conceptual split
into :class:`AgentCapabilities` (CLI/protocol traits) and
:class:`TransportCapabilities` (how the agent is driven). These tests pin the
partition (disjoint + total) and the byte-identical ``GET /api/backends``
payload so the split can't silently reshape the wire contract.
"""

import json
from pathlib import Path

from waypoint.api import _backend_descriptors
from waypoint.backends.bootstrap import build_default_registry
from waypoint.backends.capabilities import (
    AgentCapabilities,
    BackendCapabilities,
    TransportCapabilities,
)

_GOLDEN = Path(__file__).parent / "golden" / "backends_payload.json"


def test_split_is_a_disjoint_total_partition() -> None:
    """Every BackendCapabilities field belongs to exactly one axis.

    Guards against a future field landing only on the flat model (and so
    being invisible to the split) or being double-counted on both axes.
    """
    agent_fields = set(AgentCapabilities.model_fields)
    transport_fields = set(TransportCapabilities.model_fields)
    backend_fields = set(BackendCapabilities.model_fields)

    assert agent_fields.isdisjoint(transport_fields)
    assert agent_fields | transport_fields == backend_fields


def test_split_round_trips_for_every_backend() -> None:
    """``from_split(*caps.split())`` reconstructs the flat descriptor exactly.

    A lossless round-trip proves the projections carry every field's value,
    not just its presence.
    """
    registry = build_default_registry()
    for plugin in registry.all():
        caps = plugin.capabilities
        assert BackendCapabilities.from_split(*caps.split()) == caps


def test_backends_payload_matches_golden() -> None:
    """``GET /api/backends`` matches the pinned superset payload.

    The frontend reads model sources, permission modes, and capability flags
    from this payload; the descriptor keeps the flat ``capabilities`` object
    and now also emits ``agent_capabilities`` / ``transport_capabilities``
    sub-objects so the frontend can migrate to the split. Regenerate the
    fixture deliberately if the contract intentionally changes.
    """
    registry = build_default_registry()
    live = _backend_descriptors(registry)
    expected = json.loads(_GOLDEN.read_text())
    assert live == expected


def test_backends_payload_carries_split_subobjects() -> None:
    """Each descriptor adds the split sub-objects additively, without
    disturbing the flat ``capabilities`` block (kept byte-identical for
    existing consumers) — and the sub-objects are exactly the projections."""
    registry = build_default_registry()
    descriptors = _backend_descriptors(registry)
    for plugin, entry in zip(registry.all(), descriptors, strict=True):
        caps = plugin.capabilities
        assert entry["capabilities"] == caps.model_dump(mode="json")
        assert entry["agent_capabilities"] == caps.agent_capabilities().model_dump(
            mode="json"
        )
        transport = caps.transport_capabilities().model_dump(mode="json")
        assert entry["transport_capabilities"] == transport
        # The split is a total cover, so recomposing the two halves
        # reconstructs the flat descriptor the frontend sees today.
        assert BackendCapabilities.from_split(*caps.split()) == caps
