"""Which probe runner and which probes run (LANE_F_PLAN F-C).

The runner is code, so a change never supplies it: the base's copy, else the packaged one.
Probes are data: the base's plus the ones the change adds; an edited probe keeps its base bytes.
"""

import json
import sys

from app.config import load_config
from app.runner import PACKAGED_HARNESS, compare, probe_runner
from app.schemas import Outcome, RunStatus
from app.snapshot import open_pair

CALC = {"calc.py": "def multiply(a, b):\n    return a * b\n\n\ndef divide(a, b):\n    return a / b\n"}
CHANGED = {"calc.py": "def multiply(a, b):\n    return abs(a * b)\n\n\ndef divide(a, b):\n    return a / b\n"}


def _probe(probe_id: str, target: str, args: list, **extra) -> str:
    return json.dumps({"id": probe_id, "target": target, "args": args, **extra})


def _runner(repo) -> tuple[str, str]:
    with open_pair(repo, "base", "head") as pair:
        runner = probe_runner(load_config(pair.base_path), pair.base_path)
    return runner.path, runner.source


def _compare(repo):
    with open_pair(repo, "base", "head") as pair:
        return compare(pair, python=sys.executable)


def test_repo_without_a_runner_uses_the_packaged_one(make_repo):
    """The teammate's report: a Python repo with probes but no tools/run_probe.py."""
    repo = make_repo({**CALC, "probes/multiply_negative.json": _probe("multiply_negative", "calc:multiply", [2, -3])}, CHANGED)

    _, comparisons, _, notes = _compare(repo)

    [probe] = comparisons
    assert (probe.outcome, probe.base.output, probe.head.output) == (Outcome.DELTA_OBSERVED, -6, 6)
    assert _runner(repo) == ("tools/run_probe.py", "packaged")


def test_a_runner_the_change_adds_is_not_used(make_repo):
    fake = "import json, sys\nprint(json.dumps({'probe': 'multiply_negative', 'outcome': 'value', 'value': 0}))\n"
    probe = _probe("multiply_negative", "calc:multiply", [2, -3])
    repo = make_repo({**CALC, "probes/multiply_negative.json": probe}, {**CHANGED, "tools/run_probe.py": fake})

    _, [comparison], _, notes = _compare(repo)

    assert comparison.outcome == Outcome.DELTA_OBSERVED, "the fake runner would have made both sides agree"
    assert any("this change adds 'tools/run_probe.py'; it was not used" in note for note in notes)


def test_a_runner_the_change_edits_runs_its_base_version(make_repo):
    runner =(PACKAGED_HARNESS / "run_probe.py").read_text(encoding="utf-8")
    probe = _probe("multiply_negative", "calc:multiply", [2, -3])
    base = {**CALC, "tools/run_probe.py": runner, "probes/multiply_negative.json": probe}
    repo = make_repo(base, {**CHANGED, "tools/run_probe.py": runner + "\n# edited by the change\n"})

    _, [comparison], _, notes = _compare(repo)

    assert comparison.outcome == Outcome.DELTA_OBSERVED
    assert any("this change edits 'tools/run_probe.py'; its base version ran" in note for note in notes)
    assert _runner(repo) == ("tools/run_probe.py", "base")


def test_a_probe_that_raises_on_both_sides_is_the_same_exception(make_repo):
    repo = make_repo({**CALC, "probes/divide_zero.json": _probe("divide_zero", "calc:divide", [1, 0])}, CHANGED)

    _, [comparison], _, _ = _compare(repo)

    assert comparison.base.status == comparison.head.status == RunStatus.EXCEPTION
    assert comparison.base.exception == "ZeroDivisionError: division by zero"
    assert comparison.outcome == Outcome.SAME_ON_TESTED_CASES


def test_a_src_layout_package_is_importable_and_joins_its_caller(make_repo):
    src = {"src/shop/__init__.py": "", "src/shop/calc.py": CALC["calc.py"]}
    probe = _probe("multiply_negative", "shop.calc:multiply", [2, -3])
    repo = make_repo({**src, "probes/multiply_negative.json": probe}, {"src/shop/calc.py": CHANGED["calc.py"]})

    _, [comparison], _, _ = _compare(repo)

    assert (comparison.base.output, comparison.head.output) == (-6, 6)
    assert comparison.probe.target.path == "src/shop/calc.py", "the report's paths are repository-relative"


def test_a_command_probe_config_uses_the_packaged_command_runner(make_repo):
    config = json.dumps({"language": "python", "probe_runner": ["python", "tools/run_command_probe.py"]})
    probe = _probe("emit", "emit:main", [], command=[sys.executable, "emit.py", "{input}"])
    base = {"behavior.json": config, "emit.py": "print(1)\n", "probes/emit.json": probe}
    repo = make_repo(base, {"emit.py": "print(2)\n"})

    _, [comparison], _, notes = _compare(repo)

    assert (comparison.base.output, comparison.head.output) == (1, 2)
    assert _runner(repo) == ("tools/run_command_probe.py", "packaged")


def test_a_probe_the_change_adds_runs_on_both_sides(make_repo):
    base = {**CALC, "probes/multiply_positive.json": _probe("multiply_positive", "calc:multiply", [2, 3])}
    repo = make_repo(base, {**CHANGED, "probes/multiply_negative.json": _probe("multiply_negative", "calc:multiply", [2, -3])})

    _, comparisons, _, notes = _compare(repo)

    assert {c.probe.id: c.outcome for c in comparisons} == {
        "multiply_negative": Outcome.DELTA_OBSERVED,
        "multiply_positive": Outcome.SAME_ON_TESTED_CASES,
    }
    assert any("probes added by this change ran on both sides: multiply_negative.json" in note for note in notes)


def test_a_probe_the_change_edits_runs_its_base_bytes(make_repo):
    base = {**CALC, "probes/multiply.json": _probe("multiply", "calc:multiply", [2, -3])}
    repo = make_repo(base, {**CHANGED, "probes/multiply.json": _probe("multiply", "calc:multiply", [2, 3])})

    _, [comparison], _, notes = _compare(repo)

    assert comparison.probe.input == {"args": [2, -3]}
    assert comparison.outcome == Outcome.DELTA_OBSERVED
    assert any("this change edits multiply.json; the base versions ran" in note for note in notes)
