from pathlib import Path

import pytest

from waypoint.settings import Settings, _env_overrides
from waypoint.workspace_preview import (
    WorkspacePathError,
    is_denied,
    list_dir,
    rank_files,
    read_text_capped,
    resolve_in_base,
    sniff_text,
    walk_files,
)


def test_list_dir_orders_dirs_first_and_skips_denied(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    (tmp_path / "zeta.txt").write_text("z", encoding="utf-8")
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta.txt").write_text("b", encoding="utf-8")
    (tmp_path / ".git").mkdir()  # denied by the default denylist

    entries, truncated, overflow, resolved_dir = list_dir(
        tmp_path,
        "",
        10,
        denylist=settings.workspace_denylist,
        follow_symlinks=settings.workspace_follow_symlinks,
    )

    assert resolved_dir == tmp_path.resolve()
    assert [entry["name"] for entry in entries] == ["alpha", "beta.txt", "zeta.txt"]
    assert [entry["kind"] for entry in entries] == ["dir", "file", "file"]
    assert all(entry["size"] >= 0 for entry in entries)
    assert all(entry["mtime"] > 0 for entry in entries)
    assert truncated is False
    assert overflow is None


def test_list_dir_reports_overflow(tmp_path: Path) -> None:
    for index in range(4):
        (tmp_path / f"file-{index}.txt").write_text(str(index), encoding="utf-8")

    entries, truncated, overflow, _ = list_dir(tmp_path, "", 2)

    assert [entry["name"] for entry in entries] == ["file-0.txt", "file-1.txt"]
    assert truncated is True
    assert overflow == 2


def test_list_dir_pages_with_offset(tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / f"file-{index}.txt").write_text(str(index), encoding="utf-8")

    # The stable sort makes offset paging deterministic across requests.
    first, first_trunc, first_overflow, _ = list_dir(tmp_path, "", 2, offset=0)
    second, _, second_overflow, _ = list_dir(tmp_path, "", 2, offset=2)
    last, last_trunc, last_overflow, _ = list_dir(tmp_path, "", 2, offset=4)

    assert [entry["name"] for entry in first] == ["file-0.txt", "file-1.txt"]
    assert [entry["name"] for entry in second] == ["file-2.txt", "file-3.txt"]
    assert [entry["name"] for entry in last] == ["file-4.txt"]
    assert first_trunc is True and first_overflow == 3
    assert second_overflow == 1
    assert last_trunc is False and last_overflow is None


def test_rank_files_matches_subsequence_and_ranks_basename() -> None:
    paths = [
        "src/components/WorkspaceExplorer.tsx",
        "src/lib/explorer-helpers.ts",
        "docs/explore.md",
        "README.md",
    ]
    matches, truncated = rank_files("explorer", paths, limit=10)

    # "README.md" and "docs/explore.md" lack an "explorer" subsequence (no
    # trailing "r") and are dropped.
    assert matches == [
        "src/lib/explorer-helpers.ts",  # query leads the basename → top
        "src/components/WorkspaceExplorer.tsx",
    ]
    assert truncated is False


def test_rank_files_empty_query_matches_nothing() -> None:
    assert rank_files("", ["a.py", "b.py"]) == ([], False)


def test_rank_files_reports_truncation() -> None:
    paths = [f"file-{i}-match.txt" for i in range(5)]
    matches, truncated = rank_files("match", paths, limit=2)
    assert len(matches) == 2
    assert truncated is True


def test_walk_files_prunes_denied_dirs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")

    found, truncated = walk_files(tmp_path, denylist=[".git"])
    assert "src/app.py" in found
    assert all(".git" not in path for path in found)
    assert truncated is False


def test_walk_files_honors_visit_cap(tmp_path: Path) -> None:
    for index in range(10):
        (tmp_path / f"f{index}.txt").write_text("x", encoding="utf-8")
    found, truncated = walk_files(tmp_path, visit_cap=4)
    assert truncated is True
    assert len(found) <= 4


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
    # The default denylist hides .git and .ssh but leaves other dotfiles
    # (e.g. .env) previewable.
    assert is_denied(".git/config")
    assert is_denied("nested/.ssh/id_rsa")
    assert not is_denied(".env")
    assert not is_denied("nested/.env")
    # Custom globs match case-insensitively; ".*" hides every dotfile.
    assert is_denied("certs/private.pem", ["*.pem"])
    assert is_denied("certs/PRIVATE.PEM", ["*.pem"])
    assert is_denied(".env", [".*"])
    # An explicit empty denylist disables all filtering.
    assert not is_denied(".git/config", [])
    assert not is_denied(".env", [])

    (tmp_path / "visible.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "private.pem").write_text("secret", encoding="utf-8")

    entries, _, _, _ = list_dir(tmp_path, "", 10, denylist=["*.pem"])

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


def test_env_overrides_parse_workspace_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WAYPOINT_WORKSPACE_PREVIEW_ENABLED", "false")
    monkeypatch.setenv("WAYPOINT_WORKSPACE_MAX_FILE_BYTES", "1024")
    monkeypatch.setenv("WAYPOINT_WORKSPACE_DENYLIST", ".git, *.pem ,")
    monkeypatch.setenv("WAYPOINT_WORKSPACE_FOLLOW_SYMLINKS", "1")

    overrides = _env_overrides({})

    assert overrides["workspace_preview_enabled"] is False
    assert overrides["workspace_max_file_bytes"] == 1024
    assert overrides["workspace_denylist"] == [".git", "*.pem"]
    assert overrides["workspace_follow_symlinks"] is True
