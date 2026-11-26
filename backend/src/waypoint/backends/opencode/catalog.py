"""OpenCode's model catalog (``GET /api/model``)."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Model:
    provider_id: str
    model_id: str
    name: str
    variants: tuple[str, ...] = ()
    context_window: int | None = None

    @property
    def id(self) -> str:
        """Waypoint's id for the model."""
        return f"{self.provider_id}/{self.model_id}"


def model_ref(model: str, variant: str | None = None) -> dict[str, str] | None:
    """OpenCode's reference for a ``provider/model`` id."""
    provider_id, _, model_id = model.partition("/")
    if not provider_id or not model_id:
        return None
    ref = {"providerID": provider_id, "id": model_id}
    if variant:
        ref["variant"] = variant
    return ref


def model_id(ref: Any) -> str | None:
    """Waypoint's id for an OpenCode model reference."""
    if not isinstance(ref, dict):
        return None
    provider_id, name = ref.get("providerID"), ref.get("id")
    if not isinstance(provider_id, str) or not isinstance(name, str):
        return None
    return f"{provider_id}/{name}"


def parse_models(data: Any) -> list[Model]:
    """The chat models a session can select."""
    models: list[Model] = []
    for raw in data if isinstance(data, list) else ():
        if (
            not isinstance(raw, dict)
            or not raw.get("enabled", True)
            or raw.get("status") == "deprecated"
            or raw.get("type") not in (None, "chat")
        ):
            continue
        provider_id, name = raw.get("providerID"), raw.get("id")
        if not isinstance(provider_id, str) or not isinstance(name, str):
            continue
        limit = raw.get("limit")
        window = limit.get("context") if isinstance(limit, dict) else None
        models.append(
            Model(
                provider_id=provider_id,
                model_id=name,
                name=str(raw.get("name") or name),
                variants=tuple(
                    str(variant["id"])
                    for variant in raw.get("variants") or ()
                    if isinstance(variant, dict) and variant.get("id")
                ),
                context_window=window if isinstance(window, int) and window else None,
            )
        )
    return models
