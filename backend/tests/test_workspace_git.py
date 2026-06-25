import subprocess
from pathlib import Path

from waypoint.workspace_git import (
    git_file_diff,
    git_list_files,
    git_status,
    is_git_repo,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


async def test_git_status_classifies_changes(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "doomed.txt").write_text("bye\n", encoding="utf-8")
    _commit(tmp_path, "init")

    (tmp_path / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")  # modified
    (tmp_path / "doomed.txt").unlink()  # deleted
    (tmp_path / "fresh.txt").write_text("new\n", encoding="utf-8")  # untracked
    (tmp_path / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(tmp_path, "add", "staged.txt")  # staged add

    status = await git_status(tmp_path)
    assert status is not None
    by_path = {entry.path: entry for entry in status.files}

    assert by_path["tracked.txt"].worktree_status == "M"
    assert by_path["doomed.txt"].worktree_status == "D"
    assert by_path["fresh.txt"].untracked is True
    assert by_path["staged.txt"].index_status == "A"


async def test_git_status_translates_subdir_prefix(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    sub = tmp_path / "backend"
    sub.mkdir()
    (sub / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "root.txt").write_text("root\n", encoding="utf-8")
    _commit(tmp_path, "init")
    (sub / "app.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "root.txt").write_text("changed\n", encoding="utf-8")

    # Browsing from the subdir, paths are reported relative to it and entries
    # outside the subtree are dropped.
    status = await git_status(sub)
    assert status is not None
    paths = {entry.path for entry in status.files}
    assert "app.py" in paths
    assert all("root.txt" not in path for path in paths)


async def test_git_file_diff_tracked_modification(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "f.txt").write_text("a\nb\n", encoding="utf-8")
    _commit(tmp_path, "init")
    (tmp_path / "f.txt").write_text("a\nc\n", encoding="utf-8")

    preview = await git_file_diff(tmp_path, "f.txt", staged=False)
    assert preview is not None
    assert len(preview.files) == 1
    diff = preview.files[0].diff
    assert "-b" in diff and "+c" in diff
    assert preview.total_additions == 1
    assert preview.total_deletions == 1


async def test_git_file_diff_includes_full_file_context(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "f.txt").write_text("l1\nl2\nl3\nl4\nl5\nl6\n", encoding="utf-8")
    _commit(tmp_path, "init")
    (tmp_path / "f.txt").write_text("l1\nl2\nl3\nCHANGED\nl5\nl6\n", encoding="utf-8")

    preview = await git_file_diff(tmp_path, "f.txt", staged=False)
    assert preview is not None
    diff = preview.files[0].diff
    assert "-l4" in diff and "+CHANGED" in diff
    # Unchanged first and last lines are emitted as context, not elided.
    assert " l1" in diff and " l6" in diff


async def test_git_file_diff_staged_only(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")
    _commit(tmp_path, "init")
    (tmp_path / "f.txt").write_text("a\nstaged\n", encoding="utf-8")
    _git(tmp_path, "add", "f.txt")
    (tmp_path / "f.txt").write_text("a\nstaged\nunstaged\n", encoding="utf-8")

    staged = await git_file_diff(tmp_path, "f.txt", staged=True)
    assert staged is not None
    assert "+staged" in staged.files[0].diff
    assert "unstaged" not in staged.files[0].diff


async def test_git_file_diff_untracked_synthesizes_add(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "seed.txt").write_text("seed\n", encoding="utf-8")
    _commit(tmp_path, "init")
    (tmp_path / "new.txt").write_text("hello\nworld\n", encoding="utf-8")

    preview = await git_file_diff(tmp_path, "new.txt", staged=False)
    assert preview is not None
    assert preview.files[0].change_type == "add"
    assert "+hello" in preview.files[0].diff


async def test_git_file_diff_unchanged_returns_none(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")
    _commit(tmp_path, "init")

    assert await git_file_diff(tmp_path, "f.txt", staged=False) is None


async def test_git_status_reports_branch(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _git(tmp_path, "checkout", "-q", "-b", "feature/x")
    (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")
    _commit(tmp_path, "init")

    status = await git_status(tmp_path)
    assert status is not None
    assert status.branch == "feature/x"
    assert status.detached is False


async def test_git_status_detached_on_tag(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")
    _commit(tmp_path, "init")
    _git(tmp_path, "tag", "v1.0.0")
    _git(tmp_path, "checkout", "-q", "v1.0.0")  # detaches HEAD onto the tag

    status = await git_status(tmp_path)
    assert status is not None
    assert status.detached is True
    assert status.branch == "v1.0.0"  # the tag, never the literal "HEAD"


async def test_git_status_detached_on_commit(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")
    _commit(tmp_path, "init")
    sha = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "--short", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _git(tmp_path, "checkout", "-q", sha)  # detaches onto a bare commit (no tags)

    status = await git_status(tmp_path)
    assert status is not None
    assert status.detached is True
    assert status.branch is not None and status.branch != "HEAD"


async def test_git_list_files_includes_untracked_skips_ignored(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "tracked.py").write_text("x\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    _commit(tmp_path, "init")
    (tmp_path / "fresh.txt").write_text("new\n", encoding="utf-8")  # untracked, listed
    (tmp_path / "ignored.txt").write_text("no\n", encoding="utf-8")  # ignored, skipped

    listed = await git_list_files(tmp_path)
    assert listed is not None
    assert "tracked.py" in listed
    assert "fresh.txt" in listed
    assert "ignored.txt" not in listed


async def test_git_list_files_non_repo_returns_none(tmp_path: Path) -> None:
    assert await git_list_files(tmp_path) is None


async def test_non_repo_returns_none(tmp_path: Path) -> None:
    assert await is_git_repo(tmp_path) is False
    assert await git_status(tmp_path) is None
