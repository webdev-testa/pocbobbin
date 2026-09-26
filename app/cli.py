"""pipeline(base, head) + the `behavior-review` command. Owner: A.

The pipeline knows nothing about GitHub, Bob or the web; every door calls it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.impact import analyze
from app.schemas import ImpactResult, ReviewReport
from app.snapshot import SnapshotError, open_pair


def _limits(impact: ImpactResult) -> list[str]:
    return [
        f"Callers traced up to {impact.max_hops} hops; deeper callers are not shown.",
        "Impact is static (AST): calls through variables, dynamic dispatch or class hierarchies may be missed. "
        "Unresolved references that could reach a changed symbol are listed as unknowns, not as safe.",
        "No tests or probes were executed in this run, so no behavior claim is made.",
    ]


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
    return "\n".join(lines)


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
    args = parser.parse_args(argv)
    # Windows pipes default to the ANSI codepage, which can't encode the "→" in impact paths.
    sys.stdout.reconfigure(encoding="utf-8")

    try:
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
