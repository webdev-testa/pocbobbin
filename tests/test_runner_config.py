import json
import sys
from pathlib import Path

import pytest

from app.config import BehaviorConfig
from app.runner import _target_ref, run_probe_configured, run_suite
from app.schemas import RunStatus


def test_run_suite_parses_vitest_json_counts(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "emit_results.py").write_text(
        "import json; print(json.dumps({'numPassedTests': 2, 'numFailedTests': 1, 'numRuntimeErrorTestSuites': 0}))",
        encoding="utf-8",
    )
    config = BehaviorConfig(test_command=("python", "emit_results.py"), test_report="vitest-json")
    result = run_suite(tmp_path, "tests", "suite-hash", sys.executable, "base", "sha", config)
    assert result.status == RunStatus.OK
    assert (result.passed, result.failed, result.errors) == (2, 1, 0)


def test_configured_probe_uses_frozen_runner_and_probe_bytes(tmp_path: Path):
    (tmp_path / "sample.py").write_text("def value(number):\n    return number + 1\n", encoding="utf-8")
    probe = tmp_path / "probe.json"
    probe.write_text(json.dumps({"id": "value", "target": "sample:value", "args": [4]}), encoding="utf-8")
    runner = Path(__file__).parents[1] / "tools" / "run_probe.py"
    result = run_probe_configured(tmp_path, ("python", "{runner}"), runner, probe, sys.executable)
    assert result[0] == RunStatus.OK
    assert result[1] == 5


def test_javascript_probe_harness_returns_value(tmp_path: Path):
    import subprocess

    (tmp_path / "sample.mjs").write_text("export function value(number) { return number + 2; }\n", encoding="utf-8")
    probe = tmp_path / "probe.json"
    probe.write_text(json.dumps({"id": "value", "target": {"path": "sample.mjs", "symbol": "value"}, "args": [4]}), encoding="utf-8")
    harness = Path(__file__).parents[1] / "tools" / "run_probe.ts"
    completed = subprocess.run(
        ["node", "--experimental-strip-types", str(harness), str(probe)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout)
    assert payload == {"probe": "value", "outcome": "value", "value": 6}


def test_generic_command_probe_returns_value(tmp_path: Path):
    (tmp_path / "emit.py").write_text("print(7)\n", encoding="utf-8")
    probe = tmp_path / "probe.json"
    probe.write_text(json.dumps({
        "id": "command-value",
        "command": [sys.executable, "emit.py", "{input}"],
        "input": {"value": 4},
    }), encoding="utf-8")
    runner = Path(__file__).parents[1] / "tools" / "run_command_probe.py"
    result = run_probe_configured(tmp_path, ("python", "{runner}"), runner, probe, sys.executable)
    assert result[0] == RunStatus.OK
    assert result[1] == 7


@pytest.mark.parametrize(
    "report, output, expected",
    [
        ("junit-xml", "<testsuites><testsuite tests='3' failures='1' errors='1' skipped='0'/></testsuites>", (1, 1, 1)),
        ("trx-xml", "<TestRun><ResultSummary><Counters total='3' passed='2' failed='1' error='0'/></ResultSummary></TestRun>", (2, 1, 0)),
        ("go-test-json", "{\"Test\":\"TestApply\",\"Action\":\"pass\"}\n{\"Test\":\"TestTotal\",\"Action\":\"fail\"}", (1, 1, 0)),
        ("ctest-text", "100% tests passed, 0 tests failed out of 4", (4, 0, 0)),
        ("cargo-text", "test result: ok. 3 passed; 0 failed; 1 ignored", (3, 1, 0)),
        ("phpunit-text", "Tests: 4, Assertions: 5, Failures: 1, Errors: 1.", (2, 1, 1)),
        ("rspec-json", "{\"summary\":{\"example_count\":3,\"failure_count\":1}}", (2, 1, 0)),
        ("swift-text", "Executed 3 tests, with 1 failure (0 unexpected)", (2, 1, 0)),
        ("dart-json", "{\"type\":\"testDone\",\"result\":\"success\"}\n{\"type\":\"testDone\",\"result\":\"failure\"}", (1, 1, 0)),
        ("shell-text", "PASS apply\nFAIL total", (1, 1, 0)),
    ],
)
def test_configured_reporters_parse_common_language_output(tmp_path: Path, report: str, output: str, expected: tuple[int, int, int]):
    (tmp_path / "tests").mkdir()
    (tmp_path / "emit.py").write_text(f"print({output!r})\n", encoding="utf-8")
    config = BehaviorConfig(
        test_command=("python", "emit.py"),
        test_report=report,
        append_tests=False,
    )
    result = run_suite(tmp_path, "tests", "suite-hash", sys.executable, "base", "sha", config)
    assert result.status == RunStatus.OK
    assert (result.passed, result.failed, result.errors) == expected


@pytest.mark.parametrize("language", ["python", "typescript", "java", "csharp", "go", "rust", "php", "ruby", "bash"])
def test_unparseable_test_output_is_an_error_never_a_pass(tmp_path: Path, language: str):
    (tmp_path / "tests").mkdir()
    (tmp_path / "emit.py").write_text("print('ok  example.com/pricing 0.01s')\n", encoding="utf-8")
    config = BehaviorConfig.from_mapping(
        {"language": language, "test_command": ["python", "emit.py"], "append_tests": False, "tests_dir": "tests"}
    )
    result = run_suite(tmp_path, "tests", "suite-hash", sys.executable, "base", "sha", config)
    assert result.status == RunStatus.ERROR
    assert (result.passed, result.failed, result.errors) == (0, 0, 1)


@pytest.mark.parametrize(
    "language, target, expected",
    [
        ("python", "sample_project.pricing.invoice:price_total", ("sample_project/pricing/invoice.py", "price_total")),
        ("java", {"path": "src/Discount.java", "symbol": "Discount.apply"}, ("src/Discount.java", "Discount.apply")),
        ("typescript", {"path": "src/cart.tsx", "symbol": "priceTotal"}, ("src/cart.tsx", "priceTotal")),
    ],
)
def test_probe_target_joins_on_the_adapter_spelling(language: str, target, expected: tuple[str, str]):
    config = BehaviorConfig.from_mapping({"language": language})
    assert _target_ref({"id": "p", "target": target}, config) == expected