"""pipeline(base, head) + the `behavior-review` command. Owner: A.

The pipeline knows nothing about GitHub, Bob or the web; every door calls it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.adapters.registry import TIER_LIMITS, get_adapter
from app.config import BehaviorConfig, ConfigError, load_revision_config
from app.decisions import load_branch_decisions_via_git, lookup
from app.interpreter import FALLBACK_LIMIT, display_path, python_version, resolve_python
from app.impact import analyze
from app.repo_map import build as build_repo_map
from app.report import render_markdown
from app.runner import compare, probe_runner
from app.schemas import (
    Analysis,
    Decision,
    LanguageSupport,
    DecisionStatus,
    ImpactResult,
    ReviewReport,
    RevisionPair,
    Runtime,
    SymbolRef,
)
from app.snapshot import SnapshotError, open_pair


def _analysis(config: BehaviorConfig, runtime: Runtime | None) -> Analysis:
    supports = [
        LanguageSupport(language=spec.language, adapter=spec.kind, tier=spec.tier)
        for spec in (get_adapter(language).spec for language in config.languages)
    ]
    return Analysis(**supports[0].model_dump(), config_source=config.source, languages=supports, runtime=runtime)


def _uses_python(config: BehaviorConfig) -> bool:
    return any(token in {"python", "python3", "{python}"} for token in (*config.test_command, *config.probe_runner))


def _execute(pair: RevisionPair, impact: ImpactResult, config: BehaviorConfig, python: str | None, prior_report):
    """Paired execution with the project's interpreter; also returns what ran, for the report."""
    root = Path(pair.root)
    interpreter = resolve_python(root, python, config.python) if _uses_python(config) else None
    suites, comparisons, missing, notes = compare(
        pair, python=interpreter.path if interpreter else None, impact=impact, config=config, prior_report=prior_report,
    )
    runtime = Runtime(
        python=display_path(interpreter.path, root) if interpreter else None,
        version=python_version(interpreter) if interpreter else None,
        source=interpreter.source if interpreter else None,
        probe_runner=probe_runner(config, pair.base_path) if comparisons else None,
    )
    if interpreter and interpreter.source == "fallback":
        notes = [*notes, FALLBACK_LIMIT]
    return suites, comparisons, missing, notes, runtime


def _limits(impact: ImpactResult, analysis: Analysis, executed: bool, unprobed: list[SymbolRef]) -> list[str]:
    limits = [
        f"Callers traced up to {impact.max_hops} hops; deeper callers are not shown.",
        "Impact is static parser analysis: calls through variables, dynamic dispatch or class hierarchies may be missed. "
        "Unresolved references that could reach a changed symbol are listed as unknowns, not as safe.",
    ]
    limits += [
        f"Language '{support.language}' is supported at tier '{support.tier}': {TIER_LIMITS[support.tier]}. "
        "Treat missing paths as unknown."
        for support in analysis.languages
        if support.tier in TIER_LIMITS
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


def _symbol_refs(impact: ImpactResult) -> dict[str, SymbolRef]:
    return {c.key: c for c in impact.changed_symbols} | {h.key: h for p in impact.paths for h in p.hops}


def _prior_decisions(pair: RevisionPair, impact: ImpactResult) -> list[Decision]:
    """Ledger records at the base revision for any changed or impacted symbol.

    Matches on path + symbol, not the bare name `lookup` keys on, so a same-named
    function in another file never borrows a decision. The status is `lookup`'s,
    not the file's: a record on the base branch was merged, which is what approves
    it, though the file still says `proposed`; superseded records stay visible but
    are relabeled so nobody cites them as current.
    """
    refs = _symbol_refs(impact)
    matches = lookup(sorted({r.symbol for r in refs.values()}), repo_root=pair.root, branch=pair.revisions.base_sha)
    status = {"approved": DecisionStatus.APPROVED, "superseded": DecisionStatus.SUPERSEDED}
    return [
        m.decision.model_copy(update={"status": status.get(m.match_type, m.decision.status)})
        for m in matches
        if m.decision.target.key in refs
    ]


def _decisions_in_change(pair: RevisionPair, impact: ImpactResult) -> list[Decision]:
    """Ledger records this change adds or edits for its changed or impacted symbols.

    They are proposed whatever the file says: only merging them into the base approves them.
    """
    refs = _symbol_refs(impact)
    base = {d.id: d for d in load_branch_decisions_via_git(pair.root, pair.revisions.base_sha)}
    return [
        d.model_copy(update={"status": DecisionStatus.PROPOSED})
        for d in load_branch_decisions_via_git(pair.root, pair.revisions.head_sha)
        if d.target.key in refs and base.get(d.id) != d
    ]


def pipeline(repo: str | Path, base: str, head: str, max_hops: int | None = None, run: bool = False,
             prior_report: str | Path | None = None, python: str | None = None) -> ReviewReport:
    """Snapshot → impact → prior decisions → (with `run`) paired execution of the frozen suite and probes.

    Pass `prior_report` (a path to an earlier report.json) to link a probe that showed a
    delta and now reports no delta to that earlier delta, via `Comparison.reruns`. `python`
    overrides the project interpreter that runs Python tests and probes (app.interpreter).
    """
    with open_pair(repo, base, head) as pair:
        config, config_notes = load_revision_config(pair.base_path, pair.head_path)
        effective_hops = config.max_hops if max_hops is None else max_hops
        impact = analyze(pair, effective_hops, config)
        prior = _prior_decisions(pair, impact)
        decided = _decisions_in_change(pair, impact)
        suites, comparisons, missing, notes, runtime = (
            _execute(pair, impact, config, python, prior_report) if run else ([], [], [], [], None)
        )
    analysis = _analysis(config, runtime)
    return ReviewReport(
        repo=pair.repo,
        revisions=pair.revisions,
        analysis=analysis,
        impact=impact,
        tests=suites,
        comparisons=comparisons,
        needs_bob_action=missing,
        decisions=decided,
        prior_decisions=prior,
        limits=_limits(impact, analysis, bool(suites or comparisons), missing) + config_notes + notes,
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
    analysis = report.analysis
    if analysis and any(support.tier != "full" for support in analysis.languages):
        tiers = ", ".join(f"{support.language} ({support.tier})" for support in analysis.languages)
        lines.append(f"  analyzed as {tiers} — see limits")
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
    for label, decisions in (("prior decision", report.prior_decisions), ("decision in this change", report.decisions)):
        for d in decisions:
            lines.append(f"  {label} {d.id} ({d.status}) on {d.target.key}: {d.intent}"
                         + (f" — {d.rationale}" if d.rationale else ""))
    return "\n".join(lines)


def _render(value) -> str:
    return json.dumps(value, sort_keys=True) if not isinstance(value, str) else value


def _link(value: str) -> tuple[str, str]:
    name, sep, url = value.partition("=")
    if not (sep and name and url.startswith(("https://", "http://"))):
        raise argparse.ArgumentTypeError(f"expected NAME=URL, got {value!r}")
    return name, url


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="behavior-review",
        description="Find what a change between two commits could affect, including callers outside the diff.",
    )
    parser.add_argument("--repo", default=".", help="Path inside the git repository (default: .)")
    parser.add_argument("--base", default="main", help="Base revision (default: main)")
    parser.add_argument("--head", default="HEAD", help="Head revision (default: HEAD)")
    parser.add_argument("--max-hops", type=int, default=None, help="Caller levels to trace back (default: behavior.json or 2)")
    parser.add_argument("--json", type=Path, help="Write the ReviewReport JSON here instead of stdout")
    parser.add_argument("--markdown", type=Path, help="Also write the report as Markdown (the PR comment body) here")
    parser.add_argument(
        "--run",
        action="store_true",
        help="Also run the frozen test suite and committed probes on both revisions (paired execution).",
    )
    parser.add_argument(
        "--python",
        default=None,
        metavar="PATH",
        help="Interpreter for the project's Python tests and probes (default: 'python' in behavior.json, "
             "then $VIRTUAL_ENV, then .venv/ or venv/ in the repository, then behavior-review's own)",
    )
    parser.add_argument(
        "--prior-report",
        type=Path,
        default=None,
        help="An earlier report.json. A probe that showed a delta there and shows none now is "
             "linked to that delta via Comparison.reruns (the plan's fix-and-rerun step).",
    )
    parser.add_argument(
        "--link",
        type=_link,
        action="append",
        default=[],
        metavar="NAME=URL",
        help="Record where this run's evidence lives, e.g. action_run=<CI run URL> (repeatable)",
    )
    return parser


def map_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="behavior-review map",
        description="Write a whole-repository module map: files, their imports, and each file's last commit/PR.",
    )
    parser.add_argument("--repo", default=".", help="Path inside the git repository (default: .)")
    parser.add_argument("--ref", default="HEAD", help="Revision to map (default: HEAD)")
    parser.add_argument("--out", type=Path, default=Path("repo_map.json"), help="Output file (default: repo_map.json)")
    args = parser.parse_args(argv)
    try:
        with open_pair(args.repo, args.ref, args.ref) as pair:
            repo_map = build_repo_map(Path(pair.head_path), Path(pair.root), pair.repo, pair.revisions.head_sha)
    except (SnapshotError, ConfigError, RuntimeError) as exc:
        print(f"behavior-review map: {exc}", file=sys.stderr)
        return 2
    args.out.write_text(repo_map.model_dump_json(indent=2, by_alias=True) + "\n", encoding="utf-8")
    print(f"{repo_map.sha[:7]}: {len(repo_map.modules)} modules, {len(repo_map.edges)} import edges, "
          f"{len(repo_map.unknowns)} unknowns -> {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # Windows pipes default to the ANSI codepage, which can't encode the "→" in impact paths.
    sys.stdout.reconfigure(encoding="utf-8")
    if argv[:1] == ["map"]:
        return map_main(argv[1:])
    args = _parser().parse_args(argv)

    try:
        report = pipeline(args.repo, args.base, args.head, args.max_hops, run=args.run,
                          prior_report=args.prior_report, python=args.python)
    except (SnapshotError, ConfigError, RuntimeError) as exc:
        print(f"behavior-review: {exc}", file=sys.stderr)
        return 2
    report.links = dict(args.link)

    payload = report.model_dump_json(indent=2)
    if args.json:
        args.json.write_text(payload + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(_summary(report) if args.json or args.markdown else payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
