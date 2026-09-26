"""Lane B: paired execution. Owner: B."""

import json
from pathlib import Path

from app.runner import compare, pipeline
from app.schemas import Outcome, RunStatus
from app.snapshot import open_pair

REPO = Path(__file__).resolve().parents[1]
BASE, HEAD = "base", "scenario1-head"


def test_frozen_suite_runs_on_both_revisions_and_stays_green():
    with open_pair(REPO, BASE, HEAD) as pair:
        suites, comparisons, _ = compare(pair)
    assert [s.revision.value for s in suites] == ["base", "head"]
    assert all(s.status == RunStatus.OK for s in suites)
    assert all(s.passed == 6 and s.failed == 0 and s.errors == 0 for s in suites)
    # the same frozen suite bytes ran on both sides
    assert len({s.suite_hash for s in suites}) == 1


def test_caller_outside_the_diff_shows_a_real_delta():
    with open_pair(REPO, BASE, HEAD) as pair:
        suites, comparisons, _ = compare(pair)
    by_id = {c.probe.id: c for c in comparisons}
    caller = by_id["price_total_boundary"]
    assert caller.probe.target.symbol == "price_total"
    assert caller.outcome == Outcome.DELTA_OBSERVED
    assert caller.base.output == 100.0 and caller.head.output == 99.99


def test_green_tests_do_not_prevent_a_delta_observation():
    """The demo's premise: the suite is green on head while behavior changed."""
    with open_pair(REPO, BASE, HEAD) as pair:
        suites, comparisons, _ = compare(pair)
    assert all(s.status == RunStatus.OK for s in suites)
    assert any(c.outcome == Outcome.DELTA_OBSERVED for c in comparisons)


def test_refactor_shows_no_delta():
    with open_pair(REPO, BASE, "scenario3-head") as pair:
        _, comparisons, _ = compare(pair)
    assert {c.outcome for c in comparisons} == {Outcome.SAME_ON_TESTED_CASES}


def test_broken_setup_is_inconclusive_never_a_bug():
    with open_pair(REPO, BASE, "scenario4-head") as pair:
        _, comparisons, _ = compare(pair)
    assert {c.outcome for c in comparisons} == {Outcome.INCONCLUSIVE}
    assert not any(c.outcome == Outcome.DELTA_OBSERVED for c in comparisons)


def test_pipeline_populates_the_shared_report():
    report = pipeline(REPO, BASE, HEAD)
    assert report.fixture is False
    assert report.repo.endswith("pocbobbin")
    assert len(report.tests) == 2 and len(report.comparisons) == 2
    assert all(c.probe.authored_by for c in report.comparisons)
    assert "not proof of equivalence" in " ".join(report.limits)
    assert not any("No tests or probes were executed" in limit for limit in report.limits)


def test_report_contains_no_local_paths():
    report = pipeline(REPO, BASE, HEAD)
    text = report.model_dump_json()
    assert str(REPO) not in text
    assert "/tmp/" not in text


def test_impact_finds_the_caller_outside_the_diff():
    report = pipeline(REPO, BASE, HEAD)
    outside = [p for p in report.impact.paths if p.outside_diff and not p.is_test]
    assert [p.hops[0].symbol for p in outside] == ["price_total"]
    assert outside[0].hops[0].path == "sample_project/pricing/invoice.py"
    assert outside[0].render() == "price_total → apply_discount"


def test_impact_graph_traverses_into_added_helpers():
    """A refactor that adds private helpers produces one path per added symbol,
    all sharing the same entry caller. The graph is right; the *count* a
    reviewer reads must be per caller site, not per path."""
    from app.cli import _unique_entry_points
    from app.impact import analyze

    with open_pair(REPO, BASE, "scenario3-head") as pair:
        impact = analyze(pair)
    outside = [p for p in impact.paths if p.outside_diff and not p.is_test]
    assert {p.render() for p in outside} == {
        "price_total → apply_discount",
        "price_total → apply_discount → _discounted_amount",
        "price_total → apply_discount → _EPSILON",
    }
    entry_points = _unique_entry_points(outside)
    assert [p.render() for p in entry_points] == ["price_total → apply_discount"]
    assert "apply_discount → _discounted_amount" in {
        p.render() for p in impact.paths if not p.outside_diff
    }


def test_summary_counts_each_caller_site_once():
    from app.cli import _summary

    report = pipeline(REPO, BASE, "scenario3-head")
    summary = _summary(report)
    assert "1 non-test callers outside the diff" in summary
    assert "3 non-test callers outside the diff" not in summary


def test_needs_bob_action_when_a_caller_has_no_probe(tmp_path):
    """Dropping the caller's probe must surface it as needing Bob, not as safe."""
    import subprocess

    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", "--no-local", str(REPO), str(repo)], check=True)
    # bring over the scenario branches: a plain clone only carries the default branch
    subprocess.run(["git", "-C", str(repo), "fetch", "-q", str(REPO),
                    "+refs/heads/*:refs/remotes/origin/*"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-B", "noprobe", "origin/base"], check=True)
    (repo / "probes" / "price_total_boundary.json").unlink()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "drop caller probe"], check=True, env=env)

    with open_pair(repo, "noprobe", "origin/scenario1-head") as pair:
        _, comparisons, missing = compare(pair)
    assert [c.probe.id for c in comparisons] == ["apply_discount_contract"]
    assert [m.key for m in missing] == ["sample_project/pricing/invoice.py::price_total"]
