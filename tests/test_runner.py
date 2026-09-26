"""Lane B: paired execution. Owner: B.

The scenario revisions are resolved as `origin/...` remote-tracking refs, not bare
local branch names. A fresh clone only creates a local branch for the default
branch, so `git rev-parse base` fails there while `git rev-parse origin/base`
works both in this repo and in any clone.
"""

import json
from pathlib import Path

from app.cli import pipeline
from app.runner import compare
from app.schemas import Outcome, RunStatus
from app.snapshot import open_pair

REPO = Path(__file__).resolve().parents[1]
BASE, HEAD = "ref/base", "origin/scenario1-head"
S3, S4 = "origin/scenario3-head", "origin/scenario4-head"


def test_scenario_revisions_are_reachable_from_this_repo():
    """Guard: these tests are only meaningful if the scenario refs resolve."""
    with open_pair(REPO, BASE, HEAD) as pair:
        assert pair.revisions.changed_files == ["sample_project/pricing/discount.py"]


def test_frozen_suite_runs_on_both_revisions_and_stays_green():
    with open_pair(REPO, BASE, HEAD) as pair:
        suites, comparisons, _, _ = compare(pair)
    assert [s.revision.value for s in suites] == ["base", "head"]
    assert all(s.status == RunStatus.OK for s in suites)
    assert all(s.passed == 6 and s.failed == 0 and s.errors == 0 for s in suites)
    # the same frozen suite bytes ran on both sides
    assert len({s.suite_hash for s in suites}) == 1


def test_caller_outside_the_diff_shows_a_real_delta():
    with open_pair(REPO, BASE, HEAD) as pair:
        suites, comparisons, _, _ = compare(pair)
    by_id = {c.probe.id: c for c in comparisons}
    caller = by_id["price_total_boundary"]
    assert caller.probe.target.symbol == "price_total"
    assert caller.outcome == Outcome.DELTA_OBSERVED
    assert caller.base.output == 100.0 and caller.head.output == 99.99


def test_green_tests_do_not_prevent_a_delta_observation():
    """The demo's premise: the suite is green on head while behavior changed."""
    with open_pair(REPO, BASE, HEAD) as pair:
        suites, comparisons, _, _ = compare(pair)
    assert all(s.status == RunStatus.OK for s in suites)
    assert any(c.outcome == Outcome.DELTA_OBSERVED for c in comparisons)


def test_refactor_shows_no_delta():
    with open_pair(REPO, BASE, S3) as pair:
        _, comparisons, _, _ = compare(pair)
    assert {c.outcome for c in comparisons} == {Outcome.SAME_ON_TESTED_CASES}


def test_broken_setup_is_inconclusive_never_a_bug():
    with open_pair(REPO, BASE, S4) as pair:
        _, comparisons, _, _ = compare(pair)
    assert {c.outcome for c in comparisons} == {Outcome.INCONCLUSIVE}
    assert not any(c.outcome == Outcome.DELTA_OBSERVED for c in comparisons)


def test_pipeline_populates_the_shared_report():
    report = pipeline(REPO, BASE, HEAD, run=True)
    assert report.fixture is False
    assert report.repo.endswith("pocbobbin")
    assert len(report.tests) == 2
    assert len(report.comparisons) == len(sorted((REPO / "probes").glob("*.json")))
    assert all(c.probe.authored_by for c in report.comparisons)
    assert "not proof of equivalence" in " ".join(report.limits)
    assert not any("No tests or probes were executed" in limit for limit in report.limits)


def test_report_contains_no_local_paths():
    report = pipeline(REPO, BASE, HEAD, run=True)
    text = report.model_dump_json()
    assert str(REPO) not in text
    assert "/tmp/" not in text


def test_impact_finds_the_caller_outside_the_diff():
    report = pipeline(REPO, BASE, HEAD, run=True)
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

    with open_pair(REPO, BASE, S3) as pair:
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

    report = pipeline(REPO, BASE, S3, run=True)
    summary = _summary(report)
    assert "1 non-test callers outside the diff" in summary
    assert "3 non-test callers outside the diff" not in summary


def test_compare_survives_a_base_without_the_harness(tmp_path):
    """A PR that ADDS tools/ and probes/ must still produce a report.

    The probe runner and probes come from HEAD in that case, and the report has to
    say so rather than crash or silently pretend BASE supplied them.
    """
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}
    run = lambda *args: subprocess.run(["git", "-C", str(repo), *args], check=True, env=env)
    run("init", "-q")
    # base: the package, with the tests and the demo caller, but no tools/ and no probes/
    run("fetch", "-q", str(REPO), "refs/tags/ref/base:refs/heads/base")
    run("checkout", "-q", "base")
    run("rm", "-r", "-q", "tools", "probes")
    run("commit", "-qm", "base without the harness")
    # head: the harness added by the PR
    run("fetch", "-q", str(REPO), "refs/remotes/origin/scenario1-head:refs/heads/head")
    run("checkout", "-q", "head")

    with open_pair(repo, "base", "HEAD") as pair:
        suites, comparisons, _, notes = compare(pair)
    assert suites and all(s.status == RunStatus.OK for s in suites)
    assert comparisons, "probes added by the head revision must still run"
    assert any("head revision supplied it" in note for note in notes)
    assert any("tools/run_probe.py" in note for note in notes)


def test_scenario2_policy_change_produces_a_delta():
    """Scenario 2 (intended policy change) must yield real evidence.

    The plan's P1.2 depends on it: the author needs a delta to attach an intended
    rationale to, and a later change must be able to cite the approved record. With
    only the arithmetic probes a 50% -> 30% cap change produced NO delta at all, so
    the decision ledger had nothing to record. The policy-cap probe fails on one side
    only, which is the honest shape for a policy change.
    """
    with open_pair(REPO, BASE, "origin/scenario2-head") as pair:
        _, comparisons, _, _ = compare(pair)
    by_id = {c.probe.id: c for c in comparisons}
    cap = by_id["apply_discount_policy_cap"]
    assert cap.outcome == Outcome.DELTA_OBSERVED
    assert cap.base.status == RunStatus.OK and cap.base.output == 60.0
    assert cap.head.status == RunStatus.EXCEPTION
    assert "out of range" in (cap.head.exception or "")
    # the arithmetic probes stay quiet: this change is about policy, not rounding
    assert by_id["price_total_boundary"].outcome == Outcome.SAME_ON_TESTED_CASES


def test_needs_bob_action_when_a_caller_has_no_probe(tmp_path):
    """Dropping the caller's probe must surface it as needing Bob, not as safe.

    Builds a throwaway repo by fetching this repo's remote-tracking refs, so it
    works the same in a developer checkout and in a fresh CI clone.
    """
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}
    run = lambda *args: subprocess.run(["git", "-C", str(repo), *args], check=True, env=env)
    run("init", "-q")
    run("fetch", "-q", str(REPO), "refs/tags/ref/base:refs/heads/noprobe")
    run("fetch", "-q", str(REPO), "refs/remotes/origin/scenario1-head:refs/heads/head")
    run("checkout", "-q", "noprobe")
    (repo / "probes" / "price_total_boundary.json").unlink()
    run("commit", "-qam", "drop caller probe")

    with open_pair(repo, "noprobe", "head") as pair:
        _, comparisons, missing, _ = compare(pair)
    # the caller probe is gone; the other committed probes still run
    assert [c.probe.id for c in comparisons] == [
        "apply_discount_contract", "apply_discount_policy_cap"]
    assert [m.key for m in missing] == ["sample_project/pricing/invoice.py::price_total"]
