"""PR triage (LANE_F_PLAN F-A): one fixture PR per profile, the safety rule, and --full."""

import json

import pytest

from app.cli import TRIAGE_LIMITS, pipeline
from app.config import BehaviorConfig
from app.schemas import ChangedSymbol, ChangeTag, ImpactResult
from app.triage import triage

CALC = "def multiply(a, b):\n    return a * b\n"
USE = "from calc import multiply\n\n\ndef area(w, h):\n    return multiply(w, h)\n"
TEST = "from calc import multiply\n\n\ndef test_multiply():\n    assert multiply(2, 3) == 6\n"
PROJECT = {
    "calc.py": CALC,
    "shapes.py": USE,
    "tests/test_calc.py": TEST,
    "probes/multiply.json": json.dumps({"id": "multiply", "target": "calc:multiply", "args": [2, -3]}),
    "README.md": "# calc\n",
    "requirements.txt": "",
}


def _impact(changed: int = 0) -> ImpactResult:
    symbols = [ChangedSymbol(path="calc.py", symbol=f"f{i}", tags=[ChangeTag.BODY_CHANGED]) for i in range(changed)]
    return ImpactResult(changed_symbols=symbols, edges=[], paths=[], unknowns=[], max_hops=2)


@pytest.mark.parametrize(
    "files, changed, profile",
    [
        (["README.md", "docs/guide.rst", "LICENSE"], 0, "docs_only"),
        (["tests/test_calc.py", "README.md"], 0, "tests_only"),
        (["src/app.py", "requirements-dev.txt"], 1, "config_or_deps"),
        (["package-lock.json"], 0, "config_or_deps"),
        ([".github/workflows/ci.yml", "README.md"], 0, "config_or_deps"),
        (["calc.py"], 0, "no_semantic_change"),
        (["calc.py", "README.md", "tests/test_calc.py"], 1, "code_change"),
        (["data/schema.graphql"], 0, "code_change"),
        (["data/schema.graphql", "calc.py"], 0, "code_change"),
        ([], 0, "no_semantic_change"),
    ],
)
def test_a_mixed_pr_takes_its_most_thorough_profile(files, changed, profile):
    assert triage(files, _impact(changed), BehaviorConfig()).profile == profile


def test_a_file_no_adapter_parses_is_named_as_unknown():
    result = triage(["data/schema.graphql"], _impact(0), BehaviorConfig())
    assert "no adapter parses data/schema.graphql, so its effect is unknown" in result.reasons


def test_a_docs_only_pr_runs_nothing_and_says_so(make_repo):
    repo = make_repo(PROJECT, {"README.md": "# calc\n\nNow with docs.\n"})

    report = pipeline(repo, "base", "head", run=True)

    assert (report.triage.profile, report.triage.skipped_steps) == ("docs_only", ["tests", "probes"])
    assert report.tests == [] and report.comparisons == []
    assert report.limits[0] == TRIAGE_LIMITS["docs_only"]


def test_full_runs_every_step_anyway(make_repo):
    repo = make_repo(PROJECT, {"README.md": "# calc\n\nNow with docs.\n"})

    report = pipeline(repo, "base", "head", run=True, full=True)

    assert report.triage.skipped_steps == []
    assert "--full: every step ran anyway" in report.triage.reasons
    assert report.tests and report.comparisons


def test_a_tests_only_pr_is_labelled_and_still_runs_everything(make_repo):
    repo = make_repo(PROJECT, {"tests/test_calc.py": TEST + "\n\ndef test_zero():\n    assert multiply(0, 5) == 0\n"})

    report = pipeline(repo, "base", "head", run=True)

    assert (report.triage.profile, report.triage.skipped_steps) == ("tests_only", [])
    assert report.tests and report.comparisons


def test_a_dependency_bump_runs_everything_with_a_warning(make_repo):
    repo = make_repo(PROJECT, {"requirements.txt": "requests==2.32.3\n"})

    report = pipeline(repo, "base", "head", run=True)

    assert (report.triage.profile, report.triage.skipped_steps) == ("config_or_deps", [])
    assert report.tests and report.comparisons
    assert report.limits[0] == TRIAGE_LIMITS["config_or_deps"]


def test_a_formatting_only_change_is_no_semantic_change(make_repo):
    repo = make_repo(PROJECT, {"calc.py": "# Arithmetic helpers.\n\n\ndef multiply(a, b):\n    return a * b  # plain product\n"})

    report = pipeline(repo, "base", "head", run=True)

    assert report.triage.profile == "no_semantic_change"
    assert report.comparisons, "checks are cheap, so they still run"


def test_a_real_change_is_a_code_change(make_repo):
    repo = make_repo(PROJECT, {"calc.py": "def multiply(a, b):\n    return abs(a * b)\n"})

    report = pipeline(repo, "base", "head", run=True)

    assert (report.triage.profile, report.triage.reasons, report.triage.skipped_steps) == ("code_change", ["1 changed symbol(s)"], [])
    assert report.comparisons[0].outcome.value == "delta_observed"
