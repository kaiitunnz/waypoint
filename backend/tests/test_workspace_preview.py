from pathlib import Path

import pytest

from waypoint.settings import Settings
from waypoint.workspace_preview import (
    WorkspacePathError,
    is_denied,
    list_dir,
    read_text_capped,
    resolve_in_base,
    sniff_text,
)


def test_list_dir_orders_dirs_first_and_skips_denied(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    (tmp_path / "zeta.txt").write_text("z", encoding="utf-8")
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta.txt").write_text("b", encoding="utf-8")
    (tmp_path / ".env").write_text("secret", encoding="utf-8")

    entries, truncated, overflow = list_dir(
        tmp_path,
        "",
        10,
        denylist=settings.workspace_denylist,
        follow_symlinks=settings.workspace_follow_symlinks,
    )

    assert [entry["name"] for entry in entries] == ["alpha", "beta.txt", "zeta.txt"]
    assert [entry["kind"] for entry in entries] == ["dir", "file", "file"]
    assert all(entry["size"] >= 0 for entry in entries)
    assert all(entry["mtime"] > 0 for entry in entries)
    assert truncated is False
    assert overflow is None


def test_list_dir_reports_overflow(tmp_path: Path) -> None:
    for index in range(4):
        (tmp_path / f"file-{index}.txt").write_text(str(index), encoding="utf-8")

    entries, truncated, overflow = list_dir(tmp_path, "", 2)

    assert [entry["name"] for entry in entries] == ["file-0.txt", "file-1.txt"]
    assert truncated is True
    assert overflow == 2


def test_resolve_in_base_rejects_parent_traversal(tmp_path: Path) -> None:
    with pytest.raises(WorkspacePathError):
        resolve_in_base(tmp_path, "../outside.txt")


def test_resolve_in_base_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "outside-link"
    link.symlink_to(outside)

    with pytest.raises(WorkspacePathError):
        resolve_in_base(tmp_path, link.name)


def test_denied_dotfile_and_custom_glob(tmp_path: Path) -> None:
    assert is_denied(".ssh/config", ["*.pem"])
    assert is_denied("certs/private.pem", ["*.pem"])

    (tmp_path / "visible.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "private.pem").write_text("secret", encoding="utf-8")

    entries, _, _ = list_dir(tmp_path, "", 10, denylist=["*.pem"])

    assert [entry["name"] for entry in entries] == ["visible.txt"]


def test_read_text_capped_returns_placeholder_over_limit(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, workspace_max_file_bytes=3)
    path = tmp_path / "large.txt"
    path.write_text("hello", encoding="utf-8")

    content, truncated, binary, encoding = read_text_capped(
        path, settings.workspace_max_file_bytes
    )

    assert content is None
    assert truncated is True
    assert binary is False
    assert encoding == "utf-8"


def test_read_text_capped_returns_placeholder_for_binary(tmp_path: Path) -> None:
    path = tmp_path / "binary.bin"
    path.write_bytes(b"abc\x00def")

    content, truncated, binary, encoding = read_text_capped(path, 100)

    assert content is None
    assert truncated is False
    assert binary is True
    assert encoding == "utf-8"


def test_sniff_text_rejects_invalid_utf8() -> None:
    assert sniff_text(b"hello") is True
    assert sniff_text(b"\xff") is False
