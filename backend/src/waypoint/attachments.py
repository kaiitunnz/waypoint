import base64
import json
import mimetypes
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from waypoint.schemas import AttachmentKind, AttachmentSpec

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_ATTACHMENT_ID = re.compile(r"[0-9a-f]{32}")
_DEFAULT_MIME = "application/octet-stream"
_IMAGE_MIME_PREFIX = "image/"


def _sanitize_component(value: str, *, fallback: str) -> str:
    cleaned = _UNSAFE_CHARS.sub("_", Path(value).name).strip("._")
    return (cleaned or fallback)[:128]


def _kind_for(mime: str) -> AttachmentKind:
    if mime.startswith(_IMAGE_MIME_PREFIX):
        return AttachmentKind.IMAGE
    return AttachmentKind.FILE


@dataclass(frozen=True)
class ResolvedAttachment:
    """An uploaded attachment paired with its on-disk host path.

    Backends consume this when delivering input: image-capable backends read
    the bytes (or reference the path) natively, while text-only transports
    fall back to :func:`append_attachment_paths`.
    """

    spec: AttachmentSpec
    path: Path

    @property
    def is_image(self) -> bool:
        return self.spec.kind == AttachmentKind.IMAGE

    def read_base64(self) -> str:
        return base64.b64encode(self.path.read_bytes()).decode("ascii")

    def to_data_url(self) -> str:
        return f"data:{self.spec.mime};base64,{self.read_base64()}"


def append_attachment_paths(text: str, attachments: list[ResolvedAttachment]) -> str:
    """Append absolute attachment paths to ``text`` for text-only transports.

    The underlying CLI agent reads the referenced files itself, so this is the
    universal fallback for backends without native attachment support (tmux)
    and for non-image files on backends that only embed images inline.
    """
    if not attachments:
        return text
    listing = "\n".join(f"- {attachment.path}" for attachment in attachments)
    block = f"Attached files:\n{listing}"
    return f"{text}\n\n{block}" if text else block


class AttachmentStore:
    """Persists uploaded blobs under a per-session directory and resolves
    server-issued ids back to their host path.

    Layout: ``<root>/<session_id>/<id><ext>`` for the blob plus a
    ``<id>.json`` sidecar holding the :class:`AttachmentSpec` and the blob's
    stored name. The id is a server-generated uuid and the only key the
    client ever sends back, so a hostile client cannot point resolution at an
    arbitrary path.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _session_dir(self, session_id: str) -> Path:
        return self._root / _sanitize_component(session_id, fallback="session")

    def save(
        self,
        session_id: str,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
    ) -> AttachmentSpec:
        attachment_id = uuid.uuid4().hex
        clean_name = _sanitize_component(filename, fallback="file")
        mime = content_type or mimetypes.guess_type(clean_name)[0] or _DEFAULT_MIME
        stored_name = f"{attachment_id}{Path(clean_name).suffix}"
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / stored_name).write_bytes(data)
        spec = AttachmentSpec(
            id=attachment_id,
            filename=clean_name,
            mime=mime,
            size=len(data),
            kind=_kind_for(mime),
        )
        sidecar = {**spec.model_dump(mode="json"), "stored_name": stored_name}
        (session_dir / f"{attachment_id}.json").write_text(
            json.dumps(sidecar), encoding="utf-8"
        )
        return spec

    def resolve(
        self, session_id: str, attachment_id: str
    ) -> tuple[AttachmentSpec, Path] | None:
        if not _ATTACHMENT_ID.fullmatch(attachment_id):
            return None
        sidecar = self._session_dir(session_id) / f"{attachment_id}.json"
        if not sidecar.is_file():
            return None
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
        stored_name = raw.pop("stored_name", None)
        if not isinstance(stored_name, str):
            return None
        blob = sidecar.parent / stored_name
        if not blob.is_file():
            return None
        return AttachmentSpec.model_validate(raw), blob

    def discard(self, session_id: str) -> None:
        """Remove a session's attachment dir. Best-effort; never raises."""
        shutil.rmtree(self._session_dir(session_id), ignore_errors=True)
