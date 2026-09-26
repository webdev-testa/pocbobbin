"""Paired execution: identical bytes on both revisions. Owner: B.

Implements A's contract `runner.compare(pair, bundle) -> tests, comparisons,
needs_bob_action`; `app.cli.pipeline(..., run=True)` calls it inside
`with open_pair(...)` and builds the one `ReviewReport` every door consumes.

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
import fnmatch
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from app.config import PROBE_DIRS, BehaviorConfig, load_config, resolve_tests_dir

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
        ProbeRunner,
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
# Legacy sentinel meaning "resolve the test directory from the repository under review".
TESTS_DIR = "sample_project/tests"


# --- process plumbing ---------------------------------------------------------


def _run(cmd: list[str], cwd: Path, timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _hash_files(root: Path, pattern: str) -> str:
    payload = b"".join(sorted(p.read_bytes() for p in root.rglob(pattern)))
    return hashlib.sha256(payload).hexdigest()[:16]


def _hash_extensions(root: Path, extensions: tuple[str, ...]) -> str:
    payload = b"".join(
        sorted(path.read_bytes() for path in root.rglob("*") if path.is_file() and path.suffix in extensions)
    )
    return hashlib.sha256(payload).hexdigest()[:16]


def _resolve_command(template: tuple[str, ...], python: str, tests: str | None = None) -> list[str]:
    """Expand the configured argv without invoking a shell."""

    return [
        python if token in {"python", "python3", "{python}"}
        else tests if tests is not None and token in {"{tests}", "${TESTS}"}
        else token
        for token in template
    ]


def _pytest_counts(stdout: str) -> tuple[int, int, int] | None:
    tail = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    match = lambda word: int(re.search(rf"(\d+)\s+{word}", tail).group(1)) if re.search(rf"(\d+)\s+{word}", tail) else 0
    if not re.search(r"\d+\s+(?:passed|failed|error|errors?)", tail):
        return None
    return match("passed"), match("failed"), match("error") or match("errors")


def _vitest_counts(stdout: str) -> tuple[int, int, int] | None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    passed = int(payload.get("numPassedTests", 0) or 0)
    failed = int(payload.get("numFailedTests", 0) or 0)
    errors = int(payload.get("numRuntimeErrorTestSuites", 0) or 0)
    if not (passed or failed or errors):
        for result in payload.get("testResults", []) if isinstance(payload, dict) else []:
            for assertion in result.get("assertionResults", []) if isinstance(result, dict) else []:
                status = assertion.get("status")
                if status == "passed":
                    passed += 1
                elif status == "failed":
                    failed += 1
    return (passed, failed, errors) if (passed or failed or errors or "testResults" in payload) else None


def _go_test_counts(stdout: str) -> tuple[int, int, int] | None:
    passed = failed = skipped = 0
    saw_json = saw_test = False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        saw_json = True
        if "Test" not in event:
            continue
        saw_test = True
        action = event.get("Action")
        if action == "pass":
            passed += 1
        elif action == "fail":
            failed += 1
        elif action == "skip":
            skipped += 1
    if not saw_json:
        return None
    return (passed, failed + skipped, 0) if saw_test else (0, 0, 0)


def _xml_counts(text: str) -> tuple[int, int, int] | None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    counters = next(
        (node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Counters"),
        None,
    )
    if counters is not None and any(key in counters.attrib for key in ("total", "passed", "failed", "error")):
        passed = int(counters.attrib.get("passed", 0) or 0)
        failed = int(counters.attrib.get("failed", 0) or 0)
        errors = int(counters.attrib.get("error", counters.attrib.get("errors", 0)) or 0)
        return passed, failed, errors
    suites = [root] if root.tag.rsplit("}", 1)[-1] == "testsuite" else list(root.iter())
    test_suites = [node for node in suites if node.tag.rsplit("}", 1)[-1] == "testsuite"]
    if not test_suites:
        return None
    total = failures = errors = skipped = 0
    for suite in test_suites:
        total += int(suite.attrib.get("tests", 0) or 0)
        failures += int(suite.attrib.get("failures", 0) or 0)
        errors += int(suite.attrib.get("errors", 0) or 0)
        skipped += int(suite.attrib.get("skipped", 0) or 0)
    return max(0, total - failures - errors - skipped), failures + skipped, errors


def _ctest_counts(stdout: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)% tests passed,\s+(\d+) tests failed out of (\d+)", stdout)
    if match:
        failed = int(match.group(2))
        total = int(match.group(3))
        return total - failed, failed, 0
    match = re.search(r"(\d+) tests passed", stdout)
    if match:
        return int(match.group(1)), 0, 0
    return None


def _cargo_counts(stdout: str) -> tuple[int, int, int] | None:
    match = re.search(r"test result:\s+\w+\.\s+(\d+) passed;\s+(\d+) failed;\s+(\d+) ignored", stdout)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)) + int(match.group(3)), 0


def _phpunit_counts(stdout: str) -> tuple[int, int, int] | None:
    match = re.search(r"Tests:\s+(\d+).*?Failures:\s+(\d+)(?:,\s*Errors:\s+(\d+))?", stdout, flags=re.DOTALL)
    if not match:
        return None
    total, failures, errors = (int(value or 0) for value in match.groups())
    return max(0, total - failures - errors), failures, errors


def _rspec_counts(stdout: str) -> tuple[int, int, int] | None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict) or "example_count" not in summary:
        return None
    total = int(summary.get("example_count", 0) or 0)
    failed = int(summary.get("failure_count", 0) or 0)
    return max(0, total - failed), failed, 0


def _swift_counts(stdout: str) -> tuple[int, int, int] | None:
    match = re.search(r"Executed\s+(\d+)\s+tests?,\s+with\s+(\d+)\s+failures?", stdout, flags=re.IGNORECASE)
    if not match:
        return None
    total, failed = (int(value) for value in match.groups())
    return max(0, total - failed), failed, 0


def _dart_counts(stdout: str) -> tuple[int, int, int] | None:
    passed = failed = skipped = 0
    saw_event = False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = str(event.get("type", "")).lower()
        result = str(event.get("result", event.get("status", ""))).lower()
        if kind in {"testdone", "test_done", "test"}:
            saw_event = True
            if result in {"success", "passed", "pass"}:
                passed += 1
            elif result in {"skipped", "skip"}:
                skipped += 1
            else:
                failed += 1
    return (passed, failed + skipped, 0) if saw_event else None


def _shell_counts(stdout: str) -> tuple[int, int, int] | None:
    passed = len(re.findall(r"^\s*PASS(?:\s|$)", stdout, flags=re.MULTILINE | re.IGNORECASE))
    failed = len(re.findall(r"^\s*FAIL(?:\s|$)", stdout, flags=re.MULTILINE | re.IGNORECASE))
    return (passed, failed, 0) if passed or failed else None


def _report_file(checkout: Path, report: str) -> str:
    suffixes = {"junit-xml": ("*.xml",), "trx-xml": ("*.trx", "*.xml")}
    roots = ("target", "build", "test-results", "TestResults", ".")
    for root in roots:
        base = checkout / root
        if not base.exists():
            continue
        for pattern in suffixes.get(report, ()):
            for path in base.rglob(pattern):
                if path.is_file() and not any(part in {"node_modules", ".venv", ".git"} for part in path.parts):
                    try:
                        return path.read_text(encoding="utf-8")
                    except OSError:
                        pass
    return ""


def _parse_report(checkout: Path, stdout: str, report: str) -> tuple[int, int, int] | None:
    if report == "pytest-text":
        return _pytest_counts(stdout)
    if report == "vitest-json":
        return _vitest_counts(stdout)
    if report == "go-test-json":
        return _go_test_counts(stdout)
    if report in {"junit-xml", "trx-xml"}:
        return _xml_counts(stdout) or _xml_counts(_report_file(checkout, report))
    if report == "ctest-text":
        return _ctest_counts(stdout)
    if report == "cargo-text":
        return _cargo_counts(stdout)
    if report == "phpunit-text":
        return _phpunit_counts(stdout)
    if report == "rspec-json":
        return _rspec_counts(stdout)
    if report == "swift-text":
        return _swift_counts(stdout)
    if report == "dart-json":
        return _dart_counts(stdout)
    if report == "shell-text":
        return _shell_counts(stdout)
    return None


def _freeze_tests(source: Path, tests_rel: str, frozen: Path, config: BehaviorConfig) -> None:
    destination = frozen / "tests"
    # Check existence first, for every shape of tests_rel: the copytree branch would otherwise
    # raise a raw OSError whose message embeds a temporary worktree path, which must never be
    # published in a report.
    if not source.exists():
        raise FileNotFoundError(
            f"no test directory at '{tests_rel or '.'}' in the base revision; "
            "set 'tests_dir' in .behavior-review/config.json (or behavior.json) to point at the suite to run"
        )
    if tests_rel not in {"", "."}:
        shutil.copytree(source, destination)
        return
    for path in source.rglob("*"):
        if not path.is_file() or any(part in {".git", ".venv", "node_modules", "build", "dist"} for part in path.parts):
            continue
        relative = path.relative_to(source).as_posix()
        if not any(fnmatch.fnmatch(path.name, pattern) for pattern in config.test_file_patterns):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _install_frozen_tests(checkout: Path, tests_rel: str, frozen: Path) -> None:
    source = frozen / "tests"
    if tests_rel not in {"", "."}:
        destination = checkout / tests_rel
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        return
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        destination = checkout / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


# --- the two things we execute ------------------------------------------------


def run_suite(checkout: Path, tests_rel: str, suite_hash: str, python: str, revision: str, sha: str,
              config: BehaviorConfig | None = None):
    """Run the frozen test suite and parse the configured reporter."""
    settings = config or BehaviorConfig()
    command = _resolve_command(settings.test_command, python, tests_rel)
    if settings.append_tests and "{tests}" not in settings.test_command and "${TESTS}" not in settings.test_command and tests_rel not in command:
        command.append(tests_rel)
    try:
        proc = _run(command, checkout, timeout=TIMEOUT_S)
    except FileNotFoundError:
        counts = dict(status=RunStatus.ERROR, passed=0, failed=0, errors=1)
        return _suite_run(revision, sha, suite_hash, **counts)
    except subprocess.TimeoutExpired:
        counts = dict(status=RunStatus.TIMEOUT, passed=0, failed=0, errors=0)
        return _suite_run(revision, sha, suite_hash, **counts)
    parsed = _parse_report(checkout, proc.stdout, settings.test_report)
    if parsed is None:
        return _suite_run(revision, sha, suite_hash, RunStatus.ERROR, 0, 0, 1)
    passed, failed, errors = parsed
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
    except FileNotFoundError:
        return RunStatus.ERROR, None, "probe runner was not found", _ms(started)
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
    if payload.get("outcome") in {"error", "inconclusive"}:
        return RunStatus.ERROR, None, "probe reported an execution/setup error", _ms(started)
    return RunStatus.OK, payload.get("value"), None, _ms(started)


def run_probe_configured(checkout: Path, command_template: tuple[str, ...], runner: Path,
                         probe_file: Path, python: str):
    """Run one frozen probe using a configured argv template."""
    started = datetime.now(timezone.utc)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / runner.name
            shutil.copy2(runner, local)
            command = _resolve_command(command_template, python)
            has_probe = False
            for index, token in enumerate(command):
                if token in {"{runner}", "${RUNNER}"} or Path(token).name == runner.name:
                    command[index] = str(local)
                elif token in {"{probe}", "${PROBE}"}:
                    command[index] = str(probe_file)
                    has_probe = True
            if not has_probe:
                command.append(str(probe_file))
            proc = _run(command, checkout, timeout=TIMEOUT_S)
    except FileNotFoundError:
        return RunStatus.ERROR, None, "probe runner was not found", _ms(started)
    except subprocess.TimeoutExpired:
        return RunStatus.TIMEOUT, None, "probe exceeded the time limit", _ms(started)
    if proc.returncode != 0:
        return RunStatus.ERROR, None, proc.stdout.strip()[-400:] or proc.stderr.strip()[-400:] or "runner failed", _ms(started)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return RunStatus.ERROR, None, f"unparsable probe output: {proc.stdout.strip()[-200:]}", _ms(started)
    if payload.get("outcome") == "exception":
        return RunStatus.EXCEPTION, None, f"{payload.get('error_type')}: {payload.get('error')}", _ms(started)
    if payload.get("outcome") in {"error", "inconclusive"}:
        return RunStatus.ERROR, None, "probe reported an execution/setup error", _ms(started)
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


# --- what runs: the probe runner (code) and the probes (data) ----------------------------

# Runners shipped with the tool; a repository only commits its own to override one.
PACKAGED_HARNESS = Path(__file__).parent / "harness"


def runner_path(config: BehaviorConfig) -> str:
    """The runner the config names, e.g. 'tools/run_probe.py' in `("python", "tools/run_probe.py")`."""
    return next(
        (token for token in config.probe_runner if token.endswith((".py", ".ts", ".tsx", ".js", ".mjs"))),
        "tools/run_probe.py",
    )


def _runner_source(runner_rel: str, base_wt: Path) -> tuple[Path, str]:
    """The base's copy of the configured runner, else the packaged one of the same name.

    Never a copy only the PR has: the runner decides what "same" means, so a change that could
    supply its own could make every probe agree.
    """
    base_copy = base_wt / runner_rel
    if base_copy.is_file():
        return base_copy, "base"
    return PACKAGED_HARNESS / Path(runner_rel).name, "packaged"


def probe_runner(config: BehaviorConfig, base_wt: str | Path) -> "ProbeRunner | None":
    """Which runner the probes run with, for the report; None when there is none."""
    runner_rel = runner_path(config)
    source, origin = _runner_source(runner_rel, Path(base_wt))
    if not source.is_file():
        return None
    return ProbeRunner(path=runner_rel, source=origin, sha256="sha256:" + _sha8(source.read_text(encoding="utf-8")))


def _freeze_runner(runner_rel: str, base_wt: Path, head_wt: Path, frozen: Path, notes: list[str]) -> Path | None:
    """Copy the runner that will run; the report says when a PR's copy was set aside."""
    source, origin = _runner_source(runner_rel, base_wt)
    head_copy = head_wt / runner_rel
    if origin == "base" and head_copy.is_file() and head_copy.read_bytes() != source.read_bytes():
        notes.append(f"this change edits '{runner_rel}'; its base version ran on both sides.")
    if origin == "packaged" and head_copy.is_file():
        notes.append(f"this change adds '{runner_rel}'; it was not used, since a change can't supply its own runner.")
    if not source.is_file():
        notes.append(f"no probe runner '{runner_rel}' was found, so no behaviour claim is made from probe execution.")
        return None
    frozen_runner = frozen / source.name
    shutil.copy2(source, frozen_runner)
    return frozen_runner


def _freeze_probes(base_dir: Path, head_dir: Path, frozen_dir: Path, notes: list[str]) -> None:
    """The base's probes plus the ones this change adds; an edited probe keeps its base bytes.

    New probes are how an impacted caller gets covered (Bob writes them on the PR branch), so they
    must run before the merge. The same frozen bytes run on both sides either way.
    """
    frozen_dir.mkdir(parents=True, exist_ok=True)
    base = {p.name: p for p in base_dir.glob("*.json")} if base_dir.is_dir() else {}
    head = {p.name: p for p in head_dir.glob("*.json")} if head_dir.is_dir() else {}
    for name, path in {**head, **base}.items():
        shutil.copy2(path, frozen_dir / name)
    added = sorted(head.keys() - base.keys())
    edited = sorted(n for n in head.keys() & base.keys() if head[n].read_bytes() != base[n].read_bytes())
    if added:
        notes.append(f"probes added by this change ran on both sides: {', '.join(added)}.")
    if edited:
        notes.append(
            f"this change edits {', '.join(edited)}; the base versions ran, since a rerun must use the unchanged probe."
        )


# --- the contract -------------------------------------------------------------


def _probes_dir(base_wt: Path, head_wt: Path) -> str:
    """`.behavior-review/probes`, else the legacy `probes/`: whichever either revision has."""
    return next((d for d in PROBE_DIRS if (base_wt / d).is_dir() or (head_wt / d).is_dir()), PROBE_DIRS[-1])


def compare(pair, bundle=None, python: str | None = None, probes_dir: str | None = None,
            tests_rel: str = TESTS_DIR, impact=None, config: BehaviorConfig | None = None,
            prior_report=None, on_progress=None):
    """A's contract: RevisionPair + ProbeBundle -> (suite runs, comparisons, needs_bob_action).

    `bundle` is accepted for A's signature; the probes actually executed are the
    committed files, so the Action and the IDE cannot diverge on which bytes ran.

    Sources: the frozen test suite always comes from BASE. The probe runner comes from
    BASE, else the package (never HEAD alone); the probes are BASE's plus the ones HEAD
    adds. The report says what ran and what was set aside.

    `prior_report` is an earlier ReviewReport (a dict or a path to one). When a probe
    that showed a delta then shows no delta now, the new comparison is linked to that
    earlier delta via `Comparison.reruns`: same probe, fixed code (plan section 5 rule 4
    and scenario 1). Rerun linking only ever applies to the *unchanged* probe id; a probe
    whose recorded hash differs is a different probe and is not linked.
    """
    python = python or sys.executable
    settings = config or load_config(pair.base_path)
    if tests_rel == TESTS_DIR:
        # Resolve against BASE, not the live tree: the frozen suite and the code under review must
        # come from the same commit. A foreign repo has no sample_project, so detection decides.
        tests_rel = resolve_tests_dir(settings, pair.base_path)
    base_wt, head_wt = Path(pair.base_path), Path(pair.head_path)
    probes_dir = probes_dir or _probes_dir(base_wt, head_wt)
    notes: list[str] = []
    prior_deltas = _prior_delta_probes(prior_report)

    # Freeze the base test suite and the probe runner before touching any checkout.
    with tempfile.TemporaryDirectory(prefix="behavior-review-frozen-") as tmp:
        frozen = Path(tmp)
        # A repository with no discoverable suite is still reviewable: impact analysis and its
        # unknowns are reported and the missing suite is stated as a limit. It is never silently
        # replaced by another project's tests, and never reported as a clean result.
        freeze_error: str | None = None
        try:
            _freeze_tests(base_wt / tests_rel, tests_rel, frozen, settings)
        except FileNotFoundError as exc:
            freeze_error = str(exc)
            notes.append(f"{exc} No paired test run was performed, so no test-based claim is made.")
        suite_hash = _hash_extensions(frozen / "tests", settings.extensions) if freeze_error is None else ""
        runner_rel = runner_path(settings)
        # Probes are opt-in: without any, the paired test-suite comparison still runs and is still
        # evidence; no behaviour claim is made from probes, and the report says so.
        _freeze_probes(base_wt / probes_dir, head_wt / probes_dir, frozen / "probes", notes)
        frozen_runner = None
        if any((frozen / "probes").glob("*.json")):
            frozen_runner = _freeze_runner(runner_rel, base_wt, head_wt, frozen, notes)
        else:
            notes.append("no committed probes were found; no behavior claim is made from execution.")

        progress = on_progress or (lambda step, detail: None)
        suites = []
        if freeze_error is None:
            for revision, wt, sha in (("base", base_wt, pair.revisions.base_sha),
                                      ("head", head_wt, pair.revisions.head_sha)):
                progress("tests", f"running the frozen suite on {revision}")
                _install_frozen_tests(wt, tests_rel, frozen)
                suites.append(run_suite(wt, tests_rel, suite_hash, python, revision, sha, settings))

        comparisons, probed = [], set()
        if frozen_runner is not None:
            probe_files = sorted((frozen / "probes").glob("*.json"))
            progress("probes", f"running {len(probe_files)} probe(s) on both revisions")
            for probe_file in probe_files:
                spec = json.loads(probe_file.read_text())
                b = run_probe_configured(base_wt, settings.probe_runner, frozen_runner, probe_file, python)
                h = run_probe_configured(head_wt, settings.probe_runner, frozen_runner, probe_file, python)
                outcome = classify(b, h)
                probe_hash = "sha256:" + _sha8(probe_file.read_text())
                reruns = None
                if outcome == Outcome.SAME_ON_TESTED_CASES and spec["id"] in prior_deltas:
                    # same probe, now clean: link it to the earlier delta it resolves.
                    # The hash guard keeps this honest: only the unchanged probe counts.
                    if prior_deltas[spec["id"]] == probe_hash:
                        reruns = spec["id"]
                        notes.append(
                            f"probe '{spec['id']}' now reports {outcome} against the earlier "
                            f"delta it resolves (same probe bytes, hash {probe_hash})."
                        )
                    else:
                        notes.append(
                            f"probe '{spec['id']}' changed bytes since the earlier delta, so this run "
                            "is NOT linked to it: a rerun must use the unchanged probe."
                        )
                comparisons.append(
                    _comparison(probe_file, spec, pair, b, h, outcome, settings, reruns=reruns)
                )
                target_path, target_symbol = _target_ref(spec, settings)
                probed.add((target_path, target_symbol))

    resolved_impact = impact or analyze(pair, settings.max_hops, settings)
    return suites, comparisons, needs_bob_action(resolved_impact, probed), notes


def _target_ref(spec: dict, config: BehaviorConfig) -> tuple[str, str]:
    target = spec["target"]
    if isinstance(target, dict):
        return str(target["path"]).replace("\\", "/"), str(target["symbol"])
    path, _, symbol = str(target).partition(":")
    if "/" in path or path.endswith(config.extensions):
        if not path.endswith(config.extensions):
            path += config.extensions[0]
        return path, symbol
    language_extension = {
        "python": ".py",
        "typescript": ".ts",
        "javascript": ".js",
        "java": ".java",
        "csharp": ".cs",
        "go": ".go",
        "cpp": ".cpp",
        "c": ".c",
        "rust": ".rs",
        "php": ".php",
        "kotlin": ".kt",
        "ruby": ".rb",
        "swift": ".swift",
        "dart": ".dart",
        "bash": ".sh",
    }.get(config.language, config.extensions[0])
    return path.replace(".", "/") + language_extension, symbol


def _prior_delta_probes(prior_report) -> dict[str, str]:
    """probe id -> probe hash, for every probe that showed a delta in an earlier report.

    Accepts a ReviewReport, a plain dict, or a path to a report JSON. Unknown shapes
    yield an empty mapping rather than an error: rerun linking is an enhancement, and a
    malformed history must not turn a normal run into a failure.
    """
    if prior_report is None:
        return {}
    if isinstance(prior_report, (str, Path)):
        try:
            prior_report = json.loads(Path(prior_report).read_text(encoding="utf-8"))
        except (OSError, ValueError):  # ValueError covers bad JSON and undecodable bytes
            return {}
    if HAS_SCHEMA and isinstance(prior_report, ReviewReport):  # noqa: F821 - guarded by HAS_SCHEMA
        prior_report = json.loads(prior_report.model_dump_json())
    if not isinstance(prior_report, dict):
        return {}
    deltas: dict[str, str] = {}
    for comp in prior_report.get("comparisons") or []:
        if not isinstance(comp, dict) or comp.get("outcome") != Outcome.DELTA_OBSERVED.value:
            continue
        probe = comp.get("probe") or {}
        probe_id = probe.get("id")
        if probe_id:
            deltas[probe_id] = probe.get("hash") or ""
    return deltas


def _comparison(probe_file: Path, spec: dict, pair, b, h, outcome,
                config: BehaviorConfig | None = None, reruns: str | None = None):
    b_status, b_out, b_exc, b_ms = b
    h_status, h_out, h_exc, h_ms = h
    if not HAS_SCHEMA:
        return dict(probe=spec["id"], outcome=str(outcome), base=b_out, head=h_out, reruns=reruns)
    settings = config or BehaviorConfig()
    target_path, target_symbol = _target_ref(spec, settings)
    return Comparison(
        probe=Probe(
            id=spec["id"],
            target=SymbolRef(path=target_path, symbol=target_symbol),
            input=spec.get("input", {"args": spec.get("args", [])}),
            hash="sha256:" + _sha8(probe_file.read_text()),
            authored_by=spec.get("authored_by", "human"),
        ),
        base=Observation(revision=Revision.BASE, sha=pair.revisions.base_sha, status=b_status,
                         output=b_out, exception=b_exc, duration_ms=b_ms),
        head=Observation(revision=Revision.HEAD, sha=pair.revisions.head_sha, status=h_status,
                         output=h_out, exception=h_exc, duration_ms=h_ms),
        outcome=Outcome(outcome),
        ran_at=datetime.now(timezone.utc),
        reruns=reruns,
    )


def needs_bob_action(impact, probed):
    """Impacted non-test callers outside the diff that no committed probe covers."""
    if not HAS_SCHEMA:
        return []
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
