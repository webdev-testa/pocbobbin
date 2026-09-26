"""The `.behavior-review/` folder (LANE_F_PLAN F-D) and `behavior-review decide`.

One folder holds a repository's config, probes and decisions; the root-level layout it replaced
(`behavior.json`, `probes/`, `behavior_decisions/`) keeps working when the folder is absent.
"""

import json

from app.cli import main, pipeline
from app.config import load_config
from app.decisions import ledger_dir, validate_and_save
from app.schemas import DecisionStatus, Outcome

CALC = "def multiply(a, b):\n    return a * b\n"
CHANGED = "def multiply(a, b):\n    return abs(a * b)\n"
PROBE = json.dumps({"id": "multiply_negative", "target": "calc:multiply", "args": [2, -3]})
CONFIG = json.dumps({"language": "python", "max_hops": 1})


def test_the_folders_config_wins_over_behavior_json(tmp_path):
    (tmp_path / ".behavior-review").mkdir()
    (tmp_path / ".behavior-review/config.json").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "behavior.json").write_text(json.dumps({"language": "python", "max_hops": 3}), encoding="utf-8")

    config = load_config(tmp_path)

    assert (config.max_hops, config.source) == (1, ".behavior-review/config.json")


def test_probes_and_config_in_the_folder_are_used(make_repo):
    base = {"calc.py": CALC, ".behavior-review/config.json": CONFIG, ".behavior-review/probes/multiply.json": PROBE}
    repo = make_repo(base, {"calc.py": CHANGED})

    report = pipeline(repo, "base", "head", run=True)

    assert report.analysis.config_source == ".behavior-review/config.json"
    assert [(c.probe.id, c.outcome) for c in report.comparisons] == [("multiply_negative", Outcome.DELTA_OBSERVED)]


def test_the_legacy_layout_still_works(make_repo):
    repo = make_repo({"calc.py": CALC, "behavior.json": CONFIG, "probes/multiply.json": PROBE}, {"calc.py": CHANGED})

    report = pipeline(repo, "base", "head", run=True)

    assert report.analysis.config_source == "behavior.json"
    assert [c.outcome for c in report.comparisons] == [Outcome.DELTA_OBSERVED]


def test_new_decisions_go_to_the_folder_and_history_is_read_from_both(tmp_path):
    delta = {"symbol": "multiply", "path": "calc.py", "probe_hash": "p1", "before": -6, "after": 6}
    old = validate_and_save(delta, "intended", "An earlier policy on signs.", repo_root=tmp_path, base_sha="a", head_sha="b")
    (tmp_path / ".behavior-review").mkdir()

    new = validate_and_save({**delta, "probe_hash": "p2"}, "intended", "Products are now absolute.", repo_root=tmp_path,
                            base_sha="b", head_sha="c")

    assert (tmp_path / "behavior_decisions" / f"{old.id}.json").is_file()
    assert (tmp_path / ".behavior-review" / "decisions" / f"{new.id}.json").is_file()
    assert new.supersedes == old.id, "the legacy folder's decision is still part of the history"


def test_merged_decisions_in_the_folder_are_prior_decisions(make_repo):
    record = {
        "id": "d1", "repo": "repo", "target": {"path": "calc.py", "symbol": "multiply"}, "base_sha": "b",
        "head_sha": "h", "probe_hash": "p", "before": 1, "after": 2, "intent": "intended",
        "rationale": "policy change", "status": "proposed",
    }
    base = {"calc.py": CALC, ".behavior-review/decisions/d1.json": json.dumps(record)}
    repo = make_repo(base, {"calc.py": CHANGED})

    report = pipeline(repo, "base", "head")

    assert [(d.id, d.status) for d in report.prior_decisions] == [("d1", DecisionStatus.APPROVED)]


def test_decide_records_a_proposed_decision_from_a_report(make_repo, tmp_path, capsys):
    base = {"calc.py": CALC, ".behavior-review/config.json": CONFIG, ".behavior-review/probes/multiply.json": PROBE}
    repo = make_repo(base, {"calc.py": CHANGED})
    report = tmp_path / "report.json"
    assert main(["--repo", str(repo), "--base", "base", "--head", "head", "--run", "--json", str(report)]) == 0

    args = ["decide", "--repo", str(repo), "--report", str(report), "--probe", "multiply_negative", "--intent", "intended"]
    assert main([*args, "--rationale", "too short"]) == 2
    assert "at least 10 characters" in capsys.readouterr().err
    assert main([*args, "--rationale", "Products are absolute from now on."]) == 0

    [written] = ledger_dir(repo).glob("*.json")
    decision = json.loads(written.read_text(encoding="utf-8"))
    assert written.parent.as_posix().endswith(".behavior-review/decisions")
    assert (decision["status"], decision["before"], decision["after"]) == ("proposed", -6, 6)
    assert "git add .behavior-review/decisions/" in capsys.readouterr().out


def test_decide_names_an_unknown_probe(make_repo, tmp_path, capsys):
    repo = make_repo({"calc.py": CALC, "probes/multiply.json": PROBE}, {"calc.py": CHANGED})
    report = tmp_path / "report.json"
    assert main(["--repo", str(repo), "--base", "base", "--head", "head", "--run", "--json", str(report)]) == 0

    assert main(["decide", "--repo", str(repo), "--report", str(report), "--probe", "nope", "--intent", "unresolved"]) == 2
    assert "has no comparison for probe 'nope'" in capsys.readouterr().err
