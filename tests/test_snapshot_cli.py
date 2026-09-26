import json
from pathlib import Path

import pytest

from app.cli import main, pipeline
from app.schemas import Decision, DecisionStatus, Intent, SymbolRef
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


def _decision(id_: str, path: str, symbol: str, supersedes: str | None = None,
              status: DecisionStatus = DecisionStatus.APPROVED) -> str:
    return Decision(
        id=id_, repo="repo", target=SymbolRef(path=path, symbol=symbol), base_sha="b", head_sha="h",
        probe_hash="p", before=1, after=2, intent=Intent.INTENDED, rationale="policy change",
        status=status, supersedes=supersedes,
    ).model_dump_json()


def test_prior_decisions_match_path_and_symbol_and_flag_superseded(make_repo, capsys):
    ledger = {
        "behavior_decisions/d1.json": _decision("d1", "pkg/core.py", "f"),
        "behavior_decisions/d2.json": _decision("d2", "other/core.py", "f"),
        "behavior_decisions/d3.json": _decision("d3", "pkg/use.py", "g"),
        "behavior_decisions/d4.json": _decision("d4", "pkg/use.py", "g", supersedes="d3"),
    }
    repo = make_repo({**FILES, **ledger}, CHANGE)

    report = pipeline(repo, "base", "head")

    assert {(d.id, d.status) for d in report.prior_decisions} == {
        ("d1", DecisionStatus.APPROVED),
        ("d3", DecisionStatus.SUPERSEDED),
        ("d4", DecisionStatus.APPROVED),
    }
    assert main(["--repo", str(repo), "--base", "base", "--head", "head", "--json", str(repo / "r.json")]) == 0
    assert "prior decision d1 (approved) on pkg/core.py::f: intended — policy change" in capsys.readouterr().out

def test_decision_history_across_three_decisions_on_one_function(make_repo, capsys):
    """Download → commit → merge → next PR, with the third decision on the same function.

    Every record keeps `status: proposed` in its file, as the web viewer and Bob write it; merging
    is what approves it. Ids sort newest-first, so ledger order can't be mistaken for age.
    """
    proposed = DecisionStatus.PROPOSED
    merged = {
        "behavior_decisions/ffff00000000.json": _decision("ffff00000000", "pkg/core.py", "f", status=proposed),
        "behavior_decisions/888800000000.json": _decision("888800000000", "pkg/core.py", "f", "ffff00000000", proposed),
    }
    in_this_pr = {"behavior_decisions/000000000000.json": _decision("000000000000", "pkg/core.py", "f", "888800000000", proposed)}
    repo = make_repo({**FILES, **merged}, {**CHANGE, **in_this_pr})

    report = pipeline(repo, "base", "head")

    assert {(d.id, d.status) for d in report.prior_decisions} == {
        ("ffff00000000", DecisionStatus.SUPERSEDED),
        ("888800000000", DecisionStatus.APPROVED),
    }
    assert [(d.id, d.status, d.supersedes) for d in report.decisions] == [("000000000000", proposed, "888800000000")]
    assert main(["--repo", str(repo), "--base", "base", "--head", "head", "--json", str(repo / "r.json")]) == 0
    assert "decision in this change 000000000000 (proposed) on pkg/core.py::f" in capsys.readouterr().out


def test_unchanged_ledger_records_are_not_decisions_of_this_change(make_repo):
    ledger = {"behavior_decisions/d1.json": _decision("d1", "pkg/core.py", "f", status=DecisionStatus.PROPOSED)}
    repo = make_repo({**FILES, **ledger}, CHANGE)

    report = pipeline(repo, "base", "head")

    assert report.decisions == []
    assert [(d.id, d.status) for d in report.prior_decisions] == [("d1", DecisionStatus.APPROVED)]


def test_cli_writes_markdown_report(make_repo, tmp_path, capsys):
    repo = make_repo(FILES, CHANGE)
    md = tmp_path / "report.md"

    assert main(["--repo", str(repo), "--base", "base", "--head", "head", "--markdown", str(md)]) == 0

    text = md.read_text(encoding="utf-8")
    assert text.startswith("## Behavior Review")
    assert "pkg/core.py" in text and tmp_path.as_posix() not in text
    assert "g → f" in capsys.readouterr().out

def test_cli_records_links_in_json_and_markdown(make_repo, tmp_path):
    repo = make_repo(FILES, CHANGE)
    out, md = tmp_path / "report.json", tmp_path / "report.md"
    url = "https://github.com/owner/repo/actions/runs/123"

    args = ["--repo", str(repo), "--base", "base", "--head", "head", "--json", str(out), "--markdown", str(md)]
    assert main([*args, "--link", f"action_run={url}"]) == 0

    assert json.loads(out.read_text(encoding="utf-8"))["links"] == {"action_run": url}
    assert url in md.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        main([*args, "--link", "not-a-link"])

def test_report_names_its_analysis_and_ignores_config_changed_by_the_change(make_repo):
    repo = make_repo(FILES, {**CHANGE, "behavior.json": '{"language": "python", "max_hops": 1}'})

    report = pipeline(repo, "base", "head")

    python = {"language": "python", "adapter": "python-ast", "tier": "full"}
    assert report.analysis.model_dump() == {**python, "config_source": "detected", "languages": [python]}
    assert report.impact.max_hops == 2  # the head's behavior.json did not take effect
    assert any("behavior.json differs in this change" in limit for limit in report.limits)
    assert not any("is supported at tier" in limit for limit in report.limits)