import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from app.impact import analyze
from app.schemas import ImpactResult
from app.snapshot import open_pair


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def write_files(root: Path, files: dict[str, str | None]) -> None:
    for rel, content in files.items():
        path = root / rel
        if content is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


@pytest.fixture
def make_repo(tmp_path: Path) -> Callable[[dict[str, str], dict[str, str | None]], Path]:
    """Repo with a `base` branch holding `base_files` and `head` = base + `head_changes` (None deletes)."""

    def build(base_files: dict[str, str], head_changes: dict[str, str | None]) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "base")
        write_files(repo, base_files)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base")
        git(repo, "checkout", "-q", "-b", "head")
        write_files(repo, head_changes)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "head")
        return repo

    return build


@pytest.fixture
def impact_of(make_repo) -> Callable[..., ImpactResult]:
    def run(base_files: dict[str, str], head_changes: dict[str, str | None], max_hops: int = 2) -> ImpactResult:
        repo = make_repo(base_files, head_changes)
        with open_pair(repo, "base", "head") as pair:
            return analyze(pair, max_hops)

    return run
