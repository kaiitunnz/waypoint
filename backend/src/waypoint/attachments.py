import base64
import json
import mimetypes
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from waypoint.schemas import AttachmentKind, AttachmentSpec

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_ATTACHMENT_ID = re.compile(r"[0-9a-f]{32}")
_DEFAULT_MIME = "application/octet-stream"
_IMAGE_MIME_PREFIX = "image/"
# Index of ids referenced by a sent message; its stem isn't a uuid so it's
# transparent to entries()/sweep, which only consider sidecars.
_SENT_INDEX = "_sent.json"


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
    universal fallback for transports without native attachment support (the
    tmux/Terminal transport) and for non-image files on transports that only
    embed images inline.
    """
    if not attachments:
        return text
    listing = "\n".join(f"- {attachment.path}" for attachment in attachments)
    block = f"Attached files:\n{listing}"
    return f"{text}\n\n{block}" if text else block


def _write_unique(session_dir: Path, name: str, data: bytes) -> str:
    """Write ``data`` under ``name`` in ``session_dir``, suffixing `` (1)``,
    `` (2)`` … on collision, and return the stored filename. Uses exclusive
    create so two concurrent uploads of the same name never clobber each other.
    """
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate = name
    counter = 1
    while True:
        try:
            with (session_dir / candidate).open("xb") as handle:
                handle.write(data)
            return candidate
        except FileExistsError:
            candidate = f"{stem} ({counter}){suffix}"
            counter += 1


class AttachmentStore:
    """Persists uploaded blobs under a per-session directory and resolves
    server-issued ids back to their host path.

    Layout: ``<root>/<session_id>/<name>`` for the blob — the sanitized
    original filename, de-duplicated with `` (1)``, `` (2)`` … so the path
    handed to path-based agents stays legible — plus a ``<id>.json`` sidecar
    holding the :class:`AttachmentSpec` and that stored name. The id is a
    server-generated uuid and the only key the client ever sends back, so a
    hostile client cannot point resolution at an arbitrary path.
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
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        stored_name = _write_unique(session_dir, clean_name, data)
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

    def entries(self, session_id: str) -> list[tuple[AttachmentSpec, float]]:
        """Every attachment in the session, each paired with its upload time
        (sidecar mtime), newest first. Skips anything that isn't a valid
        sidecar so a user-uploaded ``*.json`` blob is never mistaken for one."""
        session_dir = self._session_dir(session_id)
        if not session_dir.is_dir():
            return []
        out: list[tuple[AttachmentSpec, float]] = []
        for sidecar in session_dir.glob("*.json"):
            if not _ATTACHMENT_ID.fullmatch(sidecar.stem):
                continue
            try:
                raw = json.loads(sidecar.read_text(encoding="utf-8"))
                raw.pop("stored_name", None)
                spec = AttachmentSpec.model_validate(raw)
                mtime = sidecar.stat().st_mtime
            except (OSError, json.JSONDecodeError, ValidationError):
                continue
            out.append((spec, mtime))
        out.sort(key=lambda item: item[1], reverse=True)
        return out

    def delete(self, session_id: str, attachment_id: str) -> bool:
        """Remove a single attachment's blob and sidecar. Returns whether it
        existed; best-effort on the file removals, so it never raises."""
        if not _ATTACHMENT_ID.fullmatch(attachment_id):
            return False
        session_dir = self._session_dir(session_id)
        sidecar = session_dir / f"{attachment_id}.json"
        if not sidecar.is_file():
            return False
        try:
            stored_name = json.loads(sidecar.read_text(encoding="utf-8")).get(
                "stored_name"
            )
        except (OSError, json.JSONDecodeError):
            stored_name = None
        if isinstance(stored_name, str):
            (session_dir / stored_name).unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        return True

    def mark_sent(self, session_id: str, attachment_ids: list[str]) -> None:
        """Record ids carried by a sent message so the sweep never reaps them
        (a sent file is referenced by the transcript and must survive)."""
        ids = [aid for aid in attachment_ids if _ATTACHMENT_ID.fullmatch(aid)]
        if not ids:
            return
        session_dir = self._session_dir(session_id)
        if not session_dir.is_dir():
            return
        current = self._sent_ids(session_dir)
        current.update(ids)
        (session_dir / _SENT_INDEX).write_text(
            json.dumps(sorted(current)), encoding="utf-8"
        )

    def sweep(self, session_id: str, ttl_seconds: float) -> int:
        """Reap eager uploads that were never sent — blobs older than
        ``ttl_seconds`` that no sent message references. Returns the count
        removed. Best-effort; never raises."""
        session_dir = self._session_dir(session_id)
        if not session_dir.is_dir():
            return 0
        sent = self._sent_ids(session_dir)
        cutoff = time.time() - ttl_seconds
        removed = 0
        for sidecar in session_dir.glob("*.json"):
            attachment_id = sidecar.stem
            if not _ATTACHMENT_ID.fullmatch(attachment_id) or attachment_id in sent:
                continue
            try:
                if sidecar.stat().st_mtime >= cutoff:
                    continue
            except OSError:
                continue
            if self.delete(session_id, attachment_id):
                removed += 1
        return removed

    def _sent_ids(self, session_dir: Path) -> set[str]:
        try:
            data = json.loads((session_dir / _SENT_INDEX).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        return set(data) if isinstance(data, list) else set()

    def discard(self, session_id: str) -> None:
        """Remove a session's attachment dir. Best-effort; never raises."""
        shutil.rmtree(self._session_dir(session_id), ignore_errors=True)
