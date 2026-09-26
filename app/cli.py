"""pipeline(base, head) + the `behavior-review` command. Owner: A.

The pipeline knows nothing about GitHub, Bob or the web; every door calls it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.impact import analyze
from app.schemas import ImpactResult, ReviewReport
from app.snapshot import SnapshotError, open_pair


def _limits(impact: ImpactResult, ran_execution: bool = False, probed: set[str] | None = None) -> list[str]:
    limits = [
        f"Callers traced up to {impact.max_hops} hops; deeper callers are not shown.",
        "Impact is static (AST): calls through variables, dynamic dispatch or class hierarchies may be missed. "
        "Unresolved references that could reach a changed symbol are listed as unknowns, not as safe.",
    ]
    if ran_execution:
        limits.append(
            "'same_on_tested_cases' means identical output for these frozen inputs only; it is not proof of equivalence."
        )
    else:
        limits.append("No tests or probes were executed in this run, so no behavior claim is made.")
    unprobed = [p for p in impact.paths if p.outside_diff and not p.is_test
                and (probed is not None and p.hops[0].key not in probed)]
    if unprobed:
        limits.append(
            f"{len(unprobed)} impacted non-test caller(s) outside the diff have no committed probe; "
            "see needs_bob_action."
        )
    return limits


def pipeline(repo: str | Path, base: str, head: str, max_hops: int = 2) -> ReviewReport:
    with open_pair(repo, base, head) as pair:
        impact = analyze(pair, max_hops)
    return ReviewReport(repo=pair.repo, revisions=pair.revisions, impact=impact, limits=_limits(impact))


def _summary(report: ReviewReport) -> str:
    impact = report.impact
    outside = [p for p in impact.paths if p.outside_diff and not p.is_test]
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
    args = parser.parse_args(argv)
    # Windows pipes default to the ANSI codepage, which can't encode the "→" in impact paths.
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        if args.run:
            from app.runner import pipeline as run_pipeline

            report = run_pipeline(args.repo, args.base, args.head, args.max_hops)
        else:
            report = pipeline(args.repo, args.base, args.head, args.max_hops)
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
