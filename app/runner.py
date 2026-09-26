"""Paired execution: identical bytes on both revisions. Owner: B.

Implements A's contract `runner.compare(pair, bundle) -> tests, comparisons,
needs_bob_action` and emits A's `ReviewReport`, so the CLI can call this inside
`with open_pair(...)` and every door then consumes one object.

Rules this file must keep (FINAL_PLAN.md section 5):
  * the frozen BASE test suite runs on both revisions, same bytes;
  * probe bytes and the probe runner come from BASE and are never edited to
    make a difference disappear;
  * import error / timeout / unparsable output are `inconclusive`, never "bug";
  * no intent is decided here; that is a human decision (owner D).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# A's shared schema is the source of truth. Import it when it is on the path;
# fall back to plain dicts so this module stays usable standalone.
try:  # pragma: no cover - exercised by whichever entry point runs first
    from app.impact import analyze
    from app.schemas import (
        Comparison,
        Observation,
        Outcome,
        Probe,
        ProbeBundle,
        ReviewReport,
        Revision,
        RunStatus,
        SuiteRun,
        SymbolRef,
    )

    HAS_SCHEMA = True
except ImportError:  # pragma: no cover
    HAS_SCHEMA = False

TIMEOUT_S = 300
TESTS_DIR = "sample_project/tests"
PROBES_DIR = "probes"


# --- process plumbing ---------------------------------------------------------


def _run(cmd: list[str], cwd: Path, timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _hash_files(root: Path, pattern: str) -> str:
    payload = b"".join(sorted(p.read_bytes() for p in root.rglob(pattern)))
    return hashlib.sha256(payload).hexdigest()[:16]


# --- the two things we execute ------------------------------------------------


def run_suite(checkout: Path, tests_rel: str, suite_hash: str, python: str, revision: str, sha: str):
    """Run the frozen test suite at `checkout` and summarise the pytest result."""
    try:
        proc = _run([python, "-m", "pytest", tests_rel, "-q", "--no-header", "-p", "no:cacheprovider"],
                    checkout, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        counts = dict(status=RunStatus.TIMEOUT, passed=0, failed=0, errors=0)
        return _suite_run(revision, sha, suite_hash, **counts)
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    passed = failed = errors = 0
    if " passed" in tail:
        passed = int(tail.split(" passed")[0].split()[-1])
    if " failed" in tail:
        failed = int(tail.split(" failed")[0].split()[-1].split("=")[-1].strip(" ,"))
    if " error" in tail:
        errors = int(tail.split(" error")[0].split()[-1].split("=")[-1].strip(" ,"))
    status = RunStatus.OK if proc.returncode == 0 else RunStatus.ERROR
    return _suite_run(revision, sha, suite_hash, status, passed, failed, errors)


def _suite_run(revision, sha, suite_hash, status, passed, failed, errors):
    if HAS_SCHEMA:
        return SuiteRun(revision=Revision(revision), sha=sha, status=status,
                        passed=passed, failed=failed, errors=errors, suite_hash=suite_hash)
    return dict(revision=revision, sha=sha, status=str(status), passed=passed,
                failed=failed, errors=errors, suite_hash=suite_hash)


def run_probe_once(checkout: Path, python: str, runner: Path, probe_file: Path):
    """Execute one probe against one checkout. Returns (status, output, exception, duration_ms)."""
    started = datetime.now(timezone.utc)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "run_probe.py"
            shutil.copy2(runner, local)
            proc = _run([python, str(local), str(probe_file)], checkout, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return RunStatus.TIMEOUT, None, "probe exceeded the time limit", _ms(started)
    if proc.returncode != 0:
        return RunStatus.ERROR, None, proc.stdout.strip()[-400:] or "runner failed", _ms(started)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return RunStatus.ERROR, None, f"unparsable probe output: {proc.stdout.strip()[-200:]}", _ms(started)
    if payload.get("outcome") == "exception":
        return RunStatus.EXCEPTION, None, f"{payload.get('error_type')}: {payload.get('error')}", _ms(started)
    return RunStatus.OK, payload.get("value"), None, _ms(started)


def _ms(started: datetime) -> int:
    return int((datetime.now(timezone.utc) - started).total_seconds() * 1000)


def classify(base, head) -> str:
    """Setup failures are inconclusive; only real execution differences are deltas."""
    b_status, b_out, _, _ = base
    h_status, h_out, _, _ = head
    if RunStatus.ERROR == b_status or RunStatus.ERROR == h_status:
        return Outcome.INCONCLUSIVE
    if RunStatus.TIMEOUT in (b_status, h_status):
        return Outcome.INCONCLUSIVE
    if b_status != h_status:
        return Outcome.DELTA_OBSERVED
    if b_status == RunStatus.EXCEPTION:
        return Outcome.SAME_ON_TESTED_CASES if b_out == h_out else Outcome.DELTA_OBSERVED
    return Outcome.SAME_ON_TESTED_CASES if _canon(b_out) == _canon(h_out) else Outcome.DELTA_OBSERVED


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


# --- the contract -------------------------------------------------------------


def compare(pair, bundle=None, python: str | None = None, probes_dir: str = PROBES_DIR,
            tests_rel: str = TESTS_DIR):
    """A's contract: RevisionPair + ProbeBundle -> (suite runs, comparisons, needs_bob_action).

    `bundle` is accepted for A's signature; the probes actually executed are the
    committed files from the BASE checkout, so the Action and the IDE cannot
    diverge on which bytes ran.
    """
    python = python or sys.executable
    base_wt, head_wt = Path(pair.base_path), Path(pair.head_path)

    # Freeze the base test suite and the probe runner before touching any checkout.
    with tempfile.TemporaryDirectory(prefix="behavior-review-frozen-") as tmp:
        frozen = Path(tmp)
        shutil.copytree(base_wt / tests_rel, frozen / "tests")
        suite_hash = _hash_files(frozen / "tests", "*.py")
        shutil.copy2(base_wt / "tools" / "run_probe.py", frozen / "run_probe.py")
        shutil.copytree(base_wt / probes_dir, frozen / "probes")

        suites = []
        for revision, wt, sha in (("base", base_wt, pair.revisions.base_sha),
                                  ("head", head_wt, pair.revisions.head_sha)):
            dest = wt / tests_rel
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(frozen / "tests", dest)
            suites.append(run_suite(wt, tests_rel, suite_hash, python, revision, sha))

        comparisons, probed = [], set()
        for probe_file in sorted((frozen / "probes").glob("*.json")):
            spec = json.loads(probe_file.read_text())
            b = run_probe_once(base_wt, python, frozen / "run_probe.py", probe_file)
            h = run_probe_once(head_wt, python, frozen / "run_probe.py", probe_file)
            outcome = classify(b, h)
            comparisons.append(
                _comparison(probe_file, spec, pair, b, h, outcome)
            )
            probed.add((_target_path(spec), spec["target"].partition(":")[2]))

    return suites, comparisons, needs_bob_action(pair, comparisons, probed)


def _target_path(spec: dict) -> str:
    return spec["target"].partition(":")[0].replace(".", "/") + ".py"


def _comparison(probe_file: Path, spec: dict, pair, b, h, outcome):
    b_status, b_out, b_exc, b_ms = b
    h_status, h_out, h_exc, h_ms = h
    if not HAS_SCHEMA:
        return dict(probe=spec["id"], outcome=str(outcome), base=b_out, head=h_out)
    return Comparison(
        probe=Probe(
            id=spec["id"],
            target=SymbolRef(path=_target_path(spec), symbol=spec["target"].partition(":")[2]),
            input={"args": spec["args"]},
            hash="sha256:" + _sha8(probe_file.read_text()),
            authored_by=spec.get("authored_by", "human"),
        ),
        base=Observation(revision=Revision.BASE, sha=pair.revisions.base_sha, status=b_status,
                         output=b_out, exception=b_exc, duration_ms=b_ms),
        head=Observation(revision=Revision.HEAD, sha=pair.revisions.head_sha, status=h_status,
                         output=h_out, exception=h_exc, duration_ms=h_ms),
        outcome=Outcome(outcome),
        ran_at=datetime.now(timezone.utc),
    )


def needs_bob_action(pair, comparisons, probed):
    """Impacted non-test callers outside the diff that no committed probe covers."""
    if not HAS_SCHEMA:
        return []
    impact = analyze(pair)
    covered = set(probed)
    missing = []
    for path in impact.paths:
        first = path.hops[0]
        if not path.outside_diff or path.is_test:
            continue
        if (first.path, first.symbol) not in covered:
            ref = SymbolRef(path=first.path, symbol=first.symbol)
            if ref.key not in {m.key for m in missing}:
                missing.append(ref)
    return missing


def pipeline(repo, base: str, head: str, max_hops: int = 2, python: str | None = None):
    """Full engine: snapshot -> impact -> paired execution -> one ReviewReport."""
    from app.cli import _limits
    from app.impact import analyze
    from app.snapshot import open_pair

    with open_pair(repo, base, head) as pair:
        impact = analyze(pair, max_hops)
        suites, comparisons, missing = compare(pair, python=python)
        probed = {c.probe.target.key for c in comparisons if HAS_SCHEMA}
        return ReviewReport(
            repo=pair.repo,
            revisions=pair.revisions,
            impact=impact,
            tests=suites,
            comparisons=comparisons,
            needs_bob_action=missing,
            limits=_limits(impact, ran_execution=bool(suites or comparisons), probed=probed),
        )
