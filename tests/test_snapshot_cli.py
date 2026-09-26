import json
from pathlib import Path

import pytest

from app.cli import main
from app.snapshot import SnapshotError, open_pair
from tests.conftest import git

FILES = {"pkg/__init__.py": "", "pkg/core.py": "def f():\n    return 1\n", "pkg/use.py": "from pkg.core import f\n\n\ndef g():\n    return f()\n"}
CHANGE = {"pkg/core.py": "def f():\n    return 2\n"}


def test_pair_is_isolated_and_cleaned_up(make_repo):
    repo = make_repo(FILES, CHANGE)
    (repo / "pkg/core.py").write_text("uncommitted edit\n", encoding="utf-8")

    with open_pair(repo, "base", "head") as pair:
        assert Path(pair.base_path, "pkg/core.py").read_text() == "def f():\n    return 1\n"
        assert Path(pair.head_path, "pkg/core.py").read_text() == "def f():\n    return 2\n"
        assert pair.revisions.changed_files == ["pkg/core.py"]

    assert (repo / "pkg/core.py").read_text() == "uncommitted edit\n"
    assert git(repo, "branch", "--show-current") == "head"
    assert len(git(repo, "worktree", "list").splitlines()) == 1


def test_unknown_revision_is_a_clear_error(make_repo):
    repo = make_repo(FILES, CHANGE)
    with pytest.raises(SnapshotError, match="cannot resolve revision"):
        with open_pair(repo, "base", "no-such-branch"):
            pass


def test_cli_writes_report_without_local_paths(make_repo, tmp_path, capsys):
    repo = make_repo(FILES, CHANGE)
    out = tmp_path / "report.json"

    assert main(["--repo", str(repo), "--base", "base", "--head", "head", "--json", str(out)]) == 0

    text = out.read_text(encoding="utf-8")
    report = json.loads(text)
    assert report["repo"] == "repo"
    assert report["fixture"] is False
    assert [p["hops"][0]["symbol"] for p in report["impact"]["paths"]] == ["g"]
    assert str(tmp_path) not in text and tmp_path.as_posix() not in text
    assert "g → f" in capsys.readouterr().out
