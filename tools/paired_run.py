"""Lane B — paired execution on two revisions.

Runs on the runner host (no network, no clock):
  1. materializes base and head into isolated git worktrees
  2. freezes the BASE test suite and runs those exact bytes on both revisions
  3. runs the SAME probe file bytes against both revisions and compares output
  4. classifies each observation and writes report.json + report.md

Outcome labels follow FINAL_PLAN.md section 5:
  same_on_tested_cases | delta_observed | inconclusive
Nothing here claims equivalence; only "same output for this frozen input".

Usage:
  python tools/paired_run.py --repo . --base scenario1-base --head scenario1-head \
      --out-dir evidence/scenario1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TOOL_VERSION = "0.1.0"


def sh(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=300)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def rev_parse(repo: Path, ref: str) -> str:
    code, out = sh(["git", "rev-parse", ref], cwd=repo)
    if code != 0:
        raise SystemExit(f"cannot resolve {ref}: {out}")
    return out.splitlines()[0]


def make_worktree(repo: Path, ref: str, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    code, out = sh(["git", "worktree", "add", "--detach", "--force", str(dest), ref], cwd=repo)
    if code != 0:
        raise SystemExit(f"worktree add failed for {ref}: {out}")


def changed_files(repo: Path, base_sha: str, head_sha: str) -> list[str]:
    code, out = sh(["git", "diff", "--name-only", f"{base_sha}..{head_sha}"], cwd=repo)
    return [line for line in out.splitlines() if line.strip()] if code == 0 else []


def run_pytest(checkout: Path, python: str, tests_rel: str) -> dict:
    code, out = sh([python, "-m", "pytest", tests_rel, "-q", "--no-header"], cwd=checkout)
    tail = out.splitlines()[-1] if out.splitlines() else ""
    return {"exit_code": code, "summary": tail, "passed": code == 0}


def run_probe(checkout: Path, python: str, runner: Path, probe: Path) -> dict:
    """Execute one probe against a checkout; the script bytes come from BASE."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            local_runner = Path(tmp) / "run_probe.py"
            shutil.copy2(runner, local_runner)
            code, out = sh(
                [python, str(local_runner), str(probe)],
                cwd=checkout,
            )
    except subprocess.TimeoutExpired:
        return {"outcome": "inconclusive", "reason": "timeout"}
    if code != 0:
        return {"outcome": "inconclusive", "reason": "runner_error", "stderr_tail": out[-400:]}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"outcome": "inconclusive", "reason": "unparsable_output", "raw": out[-400:]}


def classify(base_run: dict, head_run: dict) -> tuple[str, str]:
    """Return (outcome, explanation). Setup failures are inconclusive, never a bug."""
    if base_run.get("outcome") == "inconclusive" or head_run.get("outcome") == "inconclusive":
        return "inconclusive", "one side could not be executed; no behavioural claim is made"
    if base_run.get("outcome") != head_run.get("outcome"):
        return "delta_observed", f"outcome kind changed: {base_run.get('outcome')} -> {head_run.get('outcome')}"
    if base_run == head_run:
        return "same_on_tested_cases", "identical output for this frozen input"
    return "delta_observed", "same input produced a different result on head"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--probes", default="probes")
    ap.add_argument("--tests", default="sample_project/tests")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    base_sha = rev_parse(repo, args.base)
    head_sha = rev_parse(repo, args.head)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        base_wt, head_wt = tmp_path / "base", tmp_path / "head"
        make_worktree(repo, base_sha, base_wt)
        make_worktree(repo, head_sha, head_wt)

        # Freeze the BASE test suite: the same test bytes run on both revisions.
        frozen_src = base_wt / args.tests
        frozen_dir = tmp_path / "frozen_tests"
        shutil.copytree(frozen_src, frozen_dir)
        frozen_tests_hash = hashlib.sha256(
            b"".join(sorted(p.read_bytes() for p in frozen_dir.rglob("*.py")))
        ).hexdigest()[:16]

        results: list[dict] = []
        for label, wt in (("base", base_wt), ("head", head_wt)):
            # overwrite whatever this revision ships with the frozen base suite
            dest = wt / args.tests
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(frozen_dir, dest)
            results.append({"rev": label, "sha": base_sha if label == "base" else head_sha,
                            "tests": run_pytest(wt, args.python, args.tests)})

        # runner bytes also come from BASE, so the harness itself is frozen
        runner_src = base_wt / "tools" / "run_probe.py"
        runner_frozen = tmp_path / "run_probe.py"
        shutil.copy2(runner_src, runner_frozen)
        runner_hash = sha256_file(runner_frozen)

        observations = []
        # probe bytes come from the BASE checkout: never taken from head, never edited
        probes_dir = tmp_path / "probes_frozen"
        shutil.copytree(base_wt / args.probes, probes_dir)
        probe_files = sorted(probes_dir.glob("*.json"))
        for probe in probe_files:
            probe_hash = sha256_file(probe)
            b = run_probe(base_wt, args.python, runner_frozen, probe)
            h = run_probe(head_wt, args.python, runner_frozen, probe)
            outcome, why = classify(b, h)
            observations.append({
                "probe": probe.stem,
                "probe_sha256_16": probe_hash,
                "target": json.loads(probe.read_text()).get("target"),
                "args_sha256_16": hashlib.sha256(
                    json.dumps(json.loads(probe.read_text()).get("args"), sort_keys=True).encode()
                ).hexdigest()[:16],
                "base_output": {k: v for k, v in b.items() if k != "probe"},
                "head_output": {k: v for k, v in h.items() if k != "probe"},
                "outcome": outcome,
                "explanation": why,
            })

        report = {
            "tool": "paired_run",
            "tool_version": TOOL_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "repo": str(repo),
            "base": {"ref": args.base, "sha": base_sha},
            "head": {"ref": args.head, "sha": head_sha},
            "diff_files": changed_files(repo, base_sha, head_sha),
            "frozen_test_suite_sha256_16": frozen_tests_hash,
            "probe_runner_sha256_16": runner_hash,
            "revisions": results,
            "observations": observations,
            "limits": [
                "same_on_tested_cases means identical output for these frozen inputs only; it is not proof of equivalence",
                "no verdict on intent is made here: intent is a human decision",
                "Python only; dynamic or class-heavy code is out of scope for this PoC",
            ],
        }

    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (out_dir / "report.md").write_text(render_markdown(report))
    print(json.dumps({k: report[k] for k in ("base", "head", "diff_files")}, indent=2))
    print(f"\nobservations: {len(observations)}")
    for o in observations:
        print(f"  {o['probe']:28s} {o['outcome']:22s} {o['base_output']} -> {o['head_output']}")
    print(f"\nwrote {out_dir/'report.json'} and {out_dir/'report.md'}")
    return 0


def render_markdown(report: dict) -> str:
    lines = [
        "# Behavior review report",
        "",
        f"- base `{report['base']['ref']}` @ `{report['base']['sha'][:8]}`",
        f"- head `{report['head']['ref']}` @ `{report['head']['sha'][:8]}`",
        f"- diff touches: {', '.join('`%s`' % f for f in report['diff_files']) or '(nothing)'}",
        f"- frozen test suite hash: `{report['frozen_test_suite_sha256_16']}`",
        f"- probe runner hash: `{report['probe_runner_sha256_16']}`",
        "",
        "## Tests (identical frozen suite on both revisions)",
        "",
    ]
    for rev in report["revisions"]:
        lines.append(f"- {rev['rev']} `{rev['sha'][:8]}`: {rev['tests']['summary']}")
    lines += [
        "",
        "## Probes (same bytes, both revisions)",
        "",
        "| probe | outcome | base | head |",
        "|---|---|---|---|",
    ]
    for o in report["observations"]:
        lines.append(
            f"| `{o['probe']}` | **{o['outcome']}** | `{json.dumps(o['base_output'], sort_keys=True)}` "
            f"| `{json.dumps(o['head_output'], sort_keys=True)}` |"
        )
    lines += ["", "## Limits", ""]
    lines += [f"- {item}" for item in report["limits"]]
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
