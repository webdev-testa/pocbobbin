"""pipeline(base, head) + the `behavior-review` command. Owner: A.

The pipeline knows nothing about GitHub, Bob or the web; every door calls it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.decisions import lookup
from app.impact import analyze
from app.runner import compare
from app.schemas import Decision, DecisionStatus, ImpactResult, ReviewReport, RevisionPair, SymbolRef
from app.snapshot import SnapshotError, open_pair


def _limits(impact: ImpactResult, executed: bool, unprobed: list[SymbolRef]) -> list[str]:
    limits = [
        f"Callers traced up to {impact.max_hops} hops; deeper callers are not shown.",
        "Impact is static (AST): calls through variables, dynamic dispatch or class hierarchies may be missed. "
        "Unresolved references that could reach a changed symbol are listed as unknowns, not as safe.",
    ]
    if executed:
        limits.append(
            "'same_on_tested_cases' means identical output for these frozen inputs only; it is not proof of equivalence."
        )
    else:
        limits.append("No tests or probes were executed in this run, so no behavior claim is made.")
    if unprobed:
        limits.append(
            f"{len(unprobed)} impacted non-test caller(s) outside the diff have no committed probe; "
            "see needs_bob_action."
        )
    return limits


def _prior_decisions(pair: RevisionPair, impact: ImpactResult) -> list[Decision]:
    """Ledger records at the base revision for any changed or impacted symbol.

    Matches on path + symbol, not the bare name `lookup` keys on, so a same-named
    function in another file never borrows a decision. Superseded records stay
    visible but are relabeled so nobody cites them as current.
    """
    refs = {c.key: c for c in impact.changed_symbols} | {h.key: h for p in impact.paths for h in p.hops}
    matches = lookup(sorted({r.symbol for r in refs.values()}), repo_root=pair.root, branch=pair.revisions.base_sha)
    return [
        m.decision.model_copy(update={"status": DecisionStatus.SUPERSEDED}) if m.match_type == "superseded" else m.decision
        for m in matches
        if m.decision.target.key in refs
    ]


def pipeline(repo: str | Path, base: str, head: str, max_hops: int = 2, run: bool = False,
             prior_report: str | Path | None = None) -> ReviewReport:
    """Snapshot → impact → prior decisions → (with `run`) paired execution of the frozen suite and probes.

    Pass `prior_report` (a path to an earlier report.json) to link a probe that showed a
    delta and now reports no delta to that earlier delta, via `Comparison.reruns`.
    """
    with open_pair(repo, base, head) as pair:
        impact = analyze(pair, max_hops)
        prior = _prior_decisions(pair, impact)
        suites, comparisons, missing, notes = (
            compare(pair, impact=impact, prior_report=prior_report) if run else ([], [], [], [])
        )
    return ReviewReport(
        repo=pair.repo,
        revisions=pair.revisions,
        impact=impact,
        tests=suites,
        comparisons=comparisons,
        needs_bob_action=missing,
        prior_decisions=prior,
        limits=_limits(impact, bool(suites or comparisons), missing) + notes,
    )


def _unique_entry_points(paths: list) -> list:
    """One row per distinct caller site: a caller that reaches several added
    symbols in the same changed file is still a single caller to review.
    """
    seen: dict[tuple[str, str, int], object] = {}
    for path in paths:
        first = path.hops[0]
        seen.setdefault((first.path, first.symbol, first.line), path)
    return list(seen.values())


def _summary(report: ReviewReport) -> str:
    impact = report.impact
    outside = _unique_entry_points([p for p in impact.paths if p.outside_diff and not p.is_test])
    lines = [
        f"{report.revisions.base_sha[:7]}..{report.revisions.head_sha[:7]}: "
        f"{len(impact.changed_symbols)} changed symbols, {len(impact.paths)} impact paths, "
        f"{len(outside)} non-test callers outside the diff, {len(impact.unknowns)} unknowns"
    ]
    lines += [f"  outside diff: {p.render()}  ({p.hops[0].path}:{p.hops[0].line})" for p in outside]
    for suite in report.tests:
        lines.append(
            f"  tests on {suite.revision}: {suite.status} "
            f"({suite.passed} passed, {suite.failed} failed, {suite.errors} errors)"
        )
    for comp in report.comparisons:
        lines.append(
            f"  probe {comp.probe.id}: {comp.outcome}  "
            f"{_render(comp.base.output if comp.base.exception is None else comp.base.exception)}"
            f" -> {_render(comp.head.output if comp.head.exception is None else comp.head.exception)}"
        )
    if report.needs_bob_action:
        lines.append(
            "  needs Bob action (no committed probe): "
            + ", ".join(ref.key for ref in report.needs_bob_action)
        )
    for d in report.prior_decisions:
        lines.append(f"  prior decision {d.id} ({d.status}) on {d.target.key}: {d.intent}"
                     + (f" — {d.rationale}" if d.rationale else ""))
    return "\n".join(lines)


def _render(value) -> str:
    return json.dumps(value, sort_keys=True) if not isinstance(value, str) else value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="behavior-review",
        description="Find what a change between two commits could affect, including callers outside the diff.",
    )
    parser.add_argument("--repo", default=".", help="Path inside the git repository (default: .)")
    parser.add_argument("--base", default="main", help="Base revision (default: main)")
    parser.add_argument("--head", default="HEAD", help="Head revision (default: HEAD)")
    parser.add_argument("--max-hops", type=int, default=2, help="Caller levels to trace back (default: 2)")
    parser.add_argument("--json", type=Path, help="Write the ReviewReport JSON here instead of stdout")
    parser.add_argument(
        "--run",
        action="store_true",
        help="Also run the frozen test suite and committed probes on both revisions (paired execution).",
    )
    parser.add_argument(
        "--prior-report",
        type=Path,
        default=None,
        help="An earlier report.json. A probe that showed a delta there and shows none now is "
             "linked to that delta via Comparison.reruns (the plan's fix-and-rerun step).",
    )
    args = parser.parse_args(argv)
    # Windows pipes default to the ANSI codepage, which can't encode the "→" in impact paths.
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        report = pipeline(args.repo, args.base, args.head, args.max_hops, run=args.run,
                          prior_report=args.prior_report)
    except SnapshotError as exc:
        print(f"behavior-review: {exc}", file=sys.stderr)
        return 2

    payload = report.model_dump_json(indent=2)
    if args.json:
        args.json.write_text(payload + "\n", encoding="utf-8")
        print(_summary(report))
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
