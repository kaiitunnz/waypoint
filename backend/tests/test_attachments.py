import base64
from pathlib import Path

from waypoint.attachments import (
    AttachmentStore,
    ResolvedAttachment,
    append_attachment_paths,
)
from waypoint.backends.claude_code.adapter import _user_content
from waypoint.backends.codex.transport import _input_items
from waypoint.schemas import AttachmentKind, AttachmentSpec

PNG_BYTES = b"\x89PNG\r\n\x1a\n fake image bytes"


def _store(tmp_path: Path) -> AttachmentStore:
    return AttachmentStore(tmp_path / "attachments")


def test_save_and_resolve_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    spec = store.save(
        "sess-1", data=PNG_BYTES, filename="shot.png", content_type="image/png"
    )
    assert spec.kind == AttachmentKind.IMAGE
    assert spec.mime == "image/png"
    assert spec.size == len(PNG_BYTES)

    resolved = store.resolve("sess-1", spec.id)
    assert resolved is not None
    resolved_spec, path = resolved
    assert resolved_spec.id == spec.id
    assert path.read_bytes() == PNG_BYTES
    # The blob keeps the original extension so path-based agents can sniff it.
    assert path.suffix == ".png"


def test_mime_inferred_from_extension_when_missing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    spec = store.save("s", data=b"%PDF-1.4", filename="doc.pdf", content_type=None)
    assert spec.mime == "application/pdf"
    assert spec.kind == AttachmentKind.FILE


def test_resolve_rejects_non_uuid_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # Path-traversal-style ids never match the uuid-hex guard.
    assert store.resolve("s", "../../etc/passwd") is None
    assert store.resolve("s", "deadbeef") is None


def test_resolve_unknown_returns_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.resolve("s", "0" * 32) is None


def test_attachments_are_session_scoped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    spec = store.save(
        "owner", data=PNG_BYTES, filename="a.png", content_type="image/png"
    )
    assert store.resolve("owner", spec.id) is not None
    # A different session can't resolve another session's attachment id.
    assert store.resolve("intruder", spec.id) is None


def _resolved(tmp_path: Path, name: str, mime: str, data: bytes) -> ResolvedAttachment:
    path = tmp_path / name
    path.write_bytes(data)
    kind = AttachmentKind.IMAGE if mime.startswith("image/") else AttachmentKind.FILE
    spec = AttachmentSpec(id="x", filename=name, mime=mime, size=len(data), kind=kind)
    return ResolvedAttachment(spec=spec, path=path)


def test_append_attachment_paths(tmp_path: Path) -> None:
    att = _resolved(tmp_path, "notes.txt", "text/plain", b"hi")
    out = append_attachment_paths("look at this", [att])
    assert "look at this" in out
    assert str(att.path) in out
    assert append_attachment_paths("solo", []) == "solo"


def test_claude_user_content_plain_without_attachments() -> None:
    assert _user_content("hello", None) == "hello"


def test_claude_user_content_embeds_image_block(tmp_path: Path) -> None:
    image = _resolved(tmp_path, "p.png", "image/png", PNG_BYTES)
    content = _user_content("describe", [image])
    assert isinstance(content, list)
    text_blocks = [b for b in content if b["type"] == "text"]
    image_blocks = [b for b in content if b["type"] == "image"]
    assert text_blocks[0]["text"] == "describe"
    assert image_blocks[0]["source"]["media_type"] == "image/png"
    assert image_blocks[0]["source"]["data"] == base64.b64encode(PNG_BYTES).decode()


def test_claude_user_content_appends_file_path(tmp_path: Path) -> None:
    doc = _resolved(tmp_path, "d.pdf", "application/pdf", b"%PDF")
    content = _user_content("read it", [doc])
    assert isinstance(content, list)
    text = next(b["text"] for b in content if b["type"] == "text")
    assert str(doc.path) in text
    # A non-image file produces no image block.
    assert all(b["type"] != "image" for b in content)


def test_codex_input_items_image_and_file(tmp_path: Path) -> None:
    image = _resolved(tmp_path, "p.png", "image/png", PNG_BYTES)
    doc = _resolved(tmp_path, "d.pdf", "application/pdf", b"%PDF")
    items = _input_items("hello", [image, doc])
    text_item = items[0]
    assert text_item["type"] == "text"
    assert "hello" in text_item["text"]
    # The non-image path is appended to the text item...
    assert str(doc.path) in text_item["text"]
    # ...while the image rides as a native localImage item.
    local_images = [i for i in items if i["type"] == "localImage"]
    assert local_images[0]["path"] == str(image.path)


def test_codex_input_items_omits_empty_text(tmp_path: Path) -> None:
    image = _resolved(tmp_path, "p.png", "image/png", PNG_BYTES)
    items = _input_items("", [image])
    # An image-only turn must not emit a blank text item.
    assert all(item["type"] != "text" for item in items)
    assert items == [{"type": "localImage", "path": str(image.path)}]


def test_discard_removes_session_dir(tmp_path: Path) -> None:
    store = _store(tmp_path)
    spec = store.save("s", data=PNG_BYTES, filename="a.png", content_type="image/png")
    store.discard("s")
    assert store.resolve("s", spec.id) is None
    # Discarding a session with no attachments is a no-op, never raises.
    store.discard("never-existed")
