import base64
import os
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


def test_save_dedupes_colliding_filenames(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.save(
        "s", data=b"one", filename="report.pdf", content_type="application/pdf"
    )
    second = store.save(
        "s", data=b"two", filename="report.pdf", content_type="application/pdf"
    )

    resolved_first = store.resolve("s", first.id)
    resolved_second = store.resolve("s", second.id)
    assert resolved_first is not None and resolved_second is not None
    path_first = resolved_first[1]
    path_second = resolved_second[1]

    # Blobs are stored under the legible name; the collision gets a `(1)` suffix.
    assert path_first.name == "report.pdf"
    assert path_second.name == "report (1).pdf"
    assert path_first.read_bytes() == b"one"
    assert path_second.read_bytes() == b"two"
    # The display filename now matches the de-duplicated on-disk name, so what
    # the user sees is unique and collision-free.
    assert first.filename == "report.pdf"
    assert second.filename == "report (1).pdf"
    assert first.filename == path_first.name
    assert second.filename == path_second.name


def test_entries_orders_newest_first_and_skips_non_sidecars(tmp_path: Path) -> None:
    store = _store(tmp_path)
    old = store.save("s", data=b"old", filename="old.txt", content_type="text/plain")
    new = store.save("s", data=b"new", filename="new.txt", content_type="text/plain")
    resolved = store.resolve("s", old.id)
    assert resolved is not None
    session_dir = resolved[1].parent
    os.utime(session_dir / f"{old.id}.json", (1000, 1000))
    os.utime(session_dir / f"{new.id}.json", (2000, 2000))
    # A stray user-uploaded JSON blob must not be mistaken for a sidecar.
    (session_dir / "notes.json").write_text('{"hello": 1}', encoding="utf-8")

    listed = store.entries("s")
    assert [spec.id for spec, _ in listed] == [new.id, old.id]
    assert all(mtime > 0 for _, mtime in listed)


def test_entries_empty_for_unknown_session(tmp_path: Path) -> None:
    assert _store(tmp_path).entries("never-existed") == []


def test_entries_empty_after_discard(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("s", data=PNG_BYTES, filename="a.png", content_type="image/png")
    store.discard("s")
    assert store.entries("s") == []


def test_sweep_reaps_unsent_orphan(tmp_path: Path) -> None:
    store = _store(tmp_path)
    spec = store.save("s", data=PNG_BYTES, filename="a.png", content_type="image/png")
    resolved = store.resolve("s", spec.id)
    assert resolved is not None
    # Age the blob past the TTL (epoch 1000 is well before now - 60s).
    os.utime(resolved[1].parent / f"{spec.id}.json", (1000, 1000))

    assert store.sweep("s", ttl_seconds=60) == 1
    assert store.resolve("s", spec.id) is None


def test_sweep_keeps_recent_and_sent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    recent = store.save("s", data=PNG_BYTES, filename="r.png", content_type="image/png")
    sent = store.save("s", data=PNG_BYTES, filename="s.png", content_type="image/png")
    resolved = store.resolve("s", sent.id)
    assert resolved is not None
    # The sent blob is old but referenced; the recent blob is young.
    os.utime(resolved[1].parent / f"{sent.id}.json", (1000, 1000))
    store.mark_sent("s", [sent.id])

    assert store.sweep("s", ttl_seconds=60) == 0
    assert store.resolve("s", recent.id) is not None
    assert store.resolve("s", sent.id) is not None


def test_sweep_keeps_pinned(tmp_path: Path) -> None:
    store = _store(tmp_path)
    spec = store.save("s", data=PNG_BYTES, filename="a.png", content_type="image/png")
    resolved = store.resolve("s", spec.id)
    assert resolved is not None
    os.utime(resolved[1].parent / f"{spec.id}.json", (1000, 1000))
    store.mark_pinned("s", [spec.id])

    # Pinned survives the sweep even though it is old and unsent.
    assert store.sweep("s", ttl_seconds=60) == 0
    assert store.resolve("s", spec.id) is not None


def test_pinned_ids_reports_current_pins(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.save("s", data=PNG_BYTES, filename="a.png", content_type="image/png")
    b = store.save("s", data=PNG_BYTES, filename="b.png", content_type="image/png")
    assert store.pinned_ids("s") == set()
    store.mark_pinned("s", [a.id])
    assert store.pinned_ids("s") == {a.id}
    store.unmark_pinned("s", [a.id])
    store.mark_pinned("s", [b.id])
    assert store.pinned_ids("s") == {b.id}


def test_unpin_re_exposes_to_sweep(tmp_path: Path) -> None:
    store = _store(tmp_path)
    spec = store.save("s", data=PNG_BYTES, filename="a.png", content_type="image/png")
    resolved = store.resolve("s", spec.id)
    assert resolved is not None
    os.utime(resolved[1].parent / f"{spec.id}.json", (1000, 1000))
    store.mark_pinned("s", [spec.id])
    store.unmark_pinned("s", [spec.id])

    assert store.sweep("s", ttl_seconds=60) == 1
    assert store.resolve("s", spec.id) is None


def test_sweep_missing_session_is_noop(tmp_path: Path) -> None:
    assert _store(tmp_path).sweep("never-existed", ttl_seconds=60) == 0


def test_delete_removes_blob_and_sidecar(tmp_path: Path) -> None:
    store = _store(tmp_path)
    spec = store.save("s", data=PNG_BYTES, filename="a.png", content_type="image/png")
    resolved = store.resolve("s", spec.id)
    assert resolved is not None
    path = resolved[1]

    assert store.delete("s", spec.id) is True
    assert not path.exists()
    assert store.resolve("s", spec.id) is None
    # Deleting again, an unknown id, or a traversal-style id is a no-op.
    assert store.delete("s", spec.id) is False
    assert store.delete("s", "../../etc/passwd") is False


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
