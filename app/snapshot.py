"""Resolve base/head into isolated, detached checkouts. Owner: A.

Uses `git worktree add --detach` so the user's working tree, index and
branches are never touched.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.schemas import RevisionPair, Revisions


class SnapshotError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise SnapshotError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _rev_parse(repo: Path, ref: str) -> str:
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    except SnapshotError:
        raise SnapshotError(f"cannot resolve revision '{ref}' to a commit") from None


def _changed_files(repo: Path, base_sha: str, head_sha: str) -> list[str]:
    # --no-renames: a rename must show both the old and new path as touched.
    out = _git(repo, "diff", "--name-only", "--no-renames", base_sha, head_sha)
    return sorted(line for line in out.splitlines() if line)


def repo_slug(root: Path) -> str:
    """'owner/name' from the origin remote (https or ssh form), else the checkout folder name."""
    try:
        url = _git(root, "remote", "get-url", "origin")
    except SnapshotError:
        return root.name
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url)
    return match.group(1) if match else root.name


def git(repo: str | Path, *args: str) -> str:
    "`git -C repo <args>`'s output; raises SnapshotError on failure. Arguments are argv, never a shell."
    return _git(Path(repo), *args)


def resolve_commit(repo: str | Path, ref: str) -> str:
    "The commit `ref` names; raises SnapshotError when it names none."
    return _rev_parse(Path(repo), ref)


def repo_root(repo: str | Path) -> Path:
    """The top of the git repository that `repo` is inside."""
    return Path(_git(Path(repo), "rev-parse", "--show-toplevel"))


def resolve_pair(repo: str | Path, base: str, head: str, dest: str | Path) -> RevisionPair:
    root = repo_root(repo)
    base_sha = _rev_parse(root, base)
    head_sha = _rev_parse(root, head)

    dest = Path(dest)
    base_path, head_path = dest / "base", dest / "head"
    _git(root, "worktree", "add", "--detach", "--force", str(base_path), base_sha)
    try:
        _git(root, "worktree", "add", "--detach", "--force", str(head_path), head_sha)
    except SnapshotError:
        _git(root, "worktree", "remove", "--force", str(base_path))
        raise

    return RevisionPair(
        repo=repo_slug(root),
        root=root.as_posix(),
        base_path=base_path.as_posix(),
        head_path=head_path.as_posix(),
        revisions=Revisions(
            base_ref=base,
            head_ref=head,
            base_sha=base_sha,
            head_sha=head_sha,
            changed_files=_changed_files(root, base_sha, head_sha),
        ),
    )


def release_pair(pair: RevisionPair) -> None:
    root = Path(pair.root)
    for path in (pair.base_path, pair.head_path):
        try:
            _git(root, "worktree", "remove", "--force", path)
        except SnapshotError:
            pass  # already gone or locked; the prune below drops the stale registration
    _git(root, "worktree", "prune")

@contextmanager
def open_pair(repo: str | Path, base: str, head: str) -> Iterator[RevisionPair]:
    # Windows can hold handles on freshly removed worktree files; don't fail the run over temp cleanup.
    with tempfile.TemporaryDirectory(prefix="behavior-review-", ignore_cleanup_errors=True) as tmp:
        pair = resolve_pair(repo, base, head, tmp)
        try:
            yield pair
        finally:
            release_pair(pair)
