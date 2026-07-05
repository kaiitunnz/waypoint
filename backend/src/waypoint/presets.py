"""Session presets: reusable launch defaults resolved at request boundaries.

Presets are Waypoint-level launch defaults, not backend-native templates. They
are resolved into the existing launch-request shape before the runtime's own
validation, so backend plugins, transports, the runtime, and the scheduler's
``_fire`` path stay entirely preset-agnostic.

Two concerns live here:

* :class:`PresetManager` — CRUD orchestration over storage, id generation, and
  the single-default invariant.
* :func:`resolve_session_create_request` / :func:`resolve_schedule_create_request`
  — the boundary merge: overlay a preset under the explicit request fields and
  re-validate into a strict request.
"""

import secrets
import sqlite3
from datetime import UTC, datetime

from fastapi import HTTPException, status
from pydantic import ValidationError

from waypoint.schemas import (
    ScheduleCreateRequest,
    ScheduleLaunchRequest,
    SessionCreateRequest,
    SessionLaunchRequest,
    SessionPresetCreateRequest,
    SessionPresetRecord,
    SessionPresetSpec,
    SessionPresetSpecSummary,
    SessionPresetSummary,
    SessionPresetUpdateRequest,
)
from waypoint.storage import Storage

# Spec fields grouped by merge rule. A field is overlaid from the preset only
# when the request omitted it (not in ``model_fields_set``) and the preset value
# is *meaningful*: non-``None`` for scalars, non-empty for lists/maps. Gating on
# emptiness (not mere presence) is load-bearing: a persisted preset always
# deserializes with empty containers present, and blindly overlaying an empty
# ``launch_env`` would mark it as explicitly set and suppress the backend's
# default launch env.
_SCALAR_FIELDS = (
    "backend",
    "cwd",
    "launch_target_id",
    "launch_mode",
    "transport",
    "title",
    "permission_mode",
    "model",
    "effort",
)
_LIST_FIELDS = ("args", "config_overrides")
_MAP_FIELDS = ("launch_env", "tags")

# Request-only control fields that must never flow into the resolved launch.
_CONTROL_FIELDS = ("preset_id", "use_default_preset")


def redact_preset(record: SessionPresetRecord) -> SessionPresetSummary:
    """Public, secret-free view of a preset: env values dropped, keys kept."""
    spec = record.spec
    summary_spec = SessionPresetSpecSummary(
        backend=spec.backend,
        cwd=spec.cwd,
        launch_target_id=spec.launch_target_id,
        launch_mode=spec.launch_mode,
        transport=spec.transport,
        title=spec.title,
        args=list(spec.args),
        config_overrides=list(spec.config_overrides),
        launch_env_keys=sorted(spec.launch_env.keys()),
        permission_mode=spec.permission_mode,
        model=spec.model,
        effort=spec.effort,
        tags=dict(spec.tags),
    )
    return SessionPresetSummary(
        id=record.id,
        name=record.name,
        description=record.description,
        spec=summary_spec,
        is_default=record.is_default,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class PresetManager:
    """CRUD orchestration for session presets, owning the default invariant."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    @staticmethod
    def _new_id() -> str:
        return f"preset-{secrets.token_hex(4)}"

    @staticmethod
    def _reject_reserved_name(name: str | None) -> None:
        # "default" is a route segment on /api/session-presets/{id}; a preset by
        # that name would be unreachable by name for GET/PATCH/DELETE.
        if name is not None and name.strip().lower() == "default":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'default' is a reserved preset name",
            )

    def resolve_ref(self, ref: str) -> SessionPresetRecord | None:
        """Look a preset up by id, falling back to its (unique) name."""
        return self._storage.get_session_preset(
            ref
        ) or self._storage.get_session_preset_by_name(ref)

    def require_ref(self, ref: str) -> SessionPresetRecord:
        record = self.resolve_ref(ref)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown preset: {ref!r}",
            )
        return record

    def list(self) -> list[SessionPresetRecord]:
        return self._storage.list_session_presets()

    def default(self) -> SessionPresetRecord | None:
        return self._storage.get_default_session_preset()

    def create(self, request: SessionPresetCreateRequest) -> SessionPresetRecord:
        self._reject_reserved_name(request.name)
        now = datetime.now(UTC)
        record = SessionPresetRecord(
            id=self._new_id(),
            name=request.name,
            description=request.description,
            spec=request.spec,
            is_default=False,
            created_at=now,
            updated_at=now,
        )
        try:
            self._storage.create_session_preset(record)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"a preset named {request.name!r} already exists",
            ) from exc
        if request.is_default:
            self._storage.set_default_session_preset(record.id)
            record = self.require_ref(record.id)
        return record

    def update(
        self, ref: str, request: SessionPresetUpdateRequest
    ) -> SessionPresetRecord:
        existing = self.require_ref(ref)
        fields: dict[str, object] = {}
        given = request.model_fields_set
        if "name" in given and request.name is not None:
            self._reject_reserved_name(request.name)
            fields["name"] = request.name
        if "description" in given:
            fields["description"] = request.description
        if "spec" in given and request.spec is not None:
            fields["spec"] = _merge_spec(existing.spec, request.spec)
        if fields:
            try:
                existing = self._storage.update_session_preset(existing.id, **fields)
            except sqlite3.IntegrityError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"a preset named {request.name!r} already exists",
                ) from exc
        if "is_default" in given and request.is_default is not None:
            if request.is_default:
                self._storage.set_default_session_preset(existing.id)
            elif existing.is_default:
                self._storage.set_default_session_preset(None)
            existing = self.require_ref(existing.id)
        return existing

    def delete(self, ref: str) -> bool:
        record = self.resolve_ref(ref)
        if record is None:
            return False
        # Removing the row also clears the default when it was the default; the
        # partial unique index simply has no ``is_default = 1`` row afterwards.
        return self._storage.delete_session_preset(record.id)

    def set_default(self, ref: str | None) -> SessionPresetRecord | None:
        if ref is None:
            self._storage.set_default_session_preset(None)
            return None
        record = self.require_ref(ref)
        self._storage.set_default_session_preset(record.id)
        return self.require_ref(record.id)


def _merge_spec(
    existing: SessionPresetSpec, incoming: SessionPresetSpec
) -> SessionPresetSpec:
    """Overlay only the fields the client explicitly sent, preserving the rest."""
    data = existing.model_dump()
    for name in incoming.model_fields_set:
        data[name] = getattr(incoming, name)
    return SessionPresetSpec.model_validate(data)


def _select_preset(
    storage: Storage, preset_id: str | None, use_default: bool
) -> SessionPresetRecord | None:
    if preset_id is not None:
        record = storage.get_session_preset(
            preset_id
        ) or storage.get_session_preset_by_name(preset_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown preset: {preset_id!r}",
            )
        return record
    if use_default:
        return storage.get_default_session_preset()
    return None


def _merge(request: object, preset: SessionPresetRecord | None) -> dict[str, object]:
    req_fields = type(request).model_fields  # type: ignore[attr-defined]
    explicit = request.model_fields_set  # type: ignore[attr-defined]
    merged: dict[str, object] = {
        name: getattr(request, name)
        for name in req_fields
        if name in explicit and name not in _CONTROL_FIELDS
    }
    if preset is None:
        return merged
    spec = preset.spec
    for name in _SCALAR_FIELDS:
        if name in req_fields and name not in explicit:
            value = getattr(spec, name)
            if value is not None:
                merged[name] = value
    for name in _LIST_FIELDS:
        if name in req_fields and name not in explicit:
            value = getattr(spec, name)
            if value:
                merged[name] = list(value)
    for name in _MAP_FIELDS:
        if name in req_fields and name not in explicit:
            value = getattr(spec, name)
            if value:
                merged[name] = dict(value)
    return merged


def _validation_detail(exc: ValidationError, preset: SessionPresetRecord | None) -> str:
    prefix = (
        f"invalid launch after applying preset {preset.name!r}: "
        if preset is not None
        else "invalid launch: "
    )
    return prefix + "; ".join(
        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
    )


def resolve_session_create_request(
    storage: Storage, request: SessionLaunchRequest
) -> tuple[SessionCreateRequest, SessionPresetRecord | None]:
    preset = _select_preset(storage, request.preset_id, request.use_default_preset)
    merged = _merge(request, preset)
    try:
        resolved = SessionCreateRequest.model_validate(merged)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_validation_detail(exc, preset),
        ) from exc
    return resolved, preset


def resolve_schedule_create_request(
    storage: Storage, request: ScheduleLaunchRequest
) -> tuple[ScheduleCreateRequest, SessionPresetRecord | None]:
    preset = _select_preset(storage, request.preset_id, request.use_default_preset)
    merged = _merge(request, preset)
    try:
        resolved = ScheduleCreateRequest.model_validate(merged)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_validation_detail(exc, preset),
        ) from exc
    return resolved, preset
