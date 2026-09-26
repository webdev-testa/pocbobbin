"""End-to-end integration tests for multi-language behavior review with real toolchains.

Verifies:
  1. TypeScript paired execution with Node.js probe runner observing delta.
  2. Safe handling of missing compiler toolchains as inconclusive / error (never unhandled crash).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.cli import pipeline
from app.runner import PACKAGED_HARNESS
from app.schemas import Outcome, RunStatus


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def write_files(root: Path, files: dict[str, str | None]) -> None:
    for rel, content in files.items():
        path = root / rel
        if content is None:
            if path.exists():
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed on this system")
def test_typescript_e2e_paired_execution(tmp_path: Path):
    """Verify TypeScript repo impact graph and paired execution with Node probe runner."""
    repo = tmp_path / "ts-repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "base")

    # Copy the tools/run_probe.ts runner into the repo
    runner_src = PACKAGED_HARNESS / "run_probe.ts"
    tools_dir = repo / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(runner_src, tools_dir / "run_probe.ts")

    write_files(
        repo,
        {
            "behavior.json": json.dumps({
                "language": "typescript",
                "extensions": [".ts"],
                "tests_dir": "tests",
                "test_command": ["node", "-e", "console.log(JSON.stringify({numPassedTests: 1, numFailedTests: 0, numRuntimeErrorTestSuites: 0}))"],
                "test_report": "vitest-json",
                "probe_runner": ["node", "--experimental-strip-types", "tools/run_probe.ts"],
                "test_file_patterns": ["*.test.ts"],
                "append_tests": False,
            }),
            "src/discount.ts": "export function applyDiscount(value: number) { return value; }\n",
            "src/invoice.ts": 'import { applyDiscount } from "./discount.ts";\nexport function priceTotal(value: number) { return applyDiscount(value); }\n',
            "tests/discount.test.ts": 'test("dummy", () => {});\n',
            "probes/invoice_boundary.json": json.dumps({
                "id": "invoice_boundary",
                "target": "src/invoice.ts:priceTotal",
                "args": [100],
            }),
        },
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")

    # Head changes discount calculation
    git(repo, "checkout", "-q", "-b", "head")
    write_files(repo, {
        "src/discount.ts": "export function applyDiscount(value: number) { return value * 0.9; }\n",
    })
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "head")

    report = pipeline(repo, "base", "head", run=True)

    # 1. AST Impact verification
    assert len(report.impact.changed_symbols) == 1
    assert report.impact.changed_symbols[0].symbol == "applyDiscount"
    assert any(p.render() == "priceTotal → applyDiscount" and p.outside_diff for p in report.impact.paths)

    # 2. Paired execution verification
    assert len(report.comparisons) == 1
    comp = report.comparisons[0]
    assert comp.probe.id == "invoice_boundary"
    assert comp.base.output == 100
    assert comp.head.output == 90
    assert comp.outcome == Outcome.DELTA_OBSERVED


def test_missing_toolchain_fails_cleanly_as_inconclusive(tmp_path: Path):
    """Verify that when a toolchain command does not exist on PATH, it does not crash unhandled."""
    repo = tmp_path / "custom-repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "base")

    command_runner = PACKAGED_HARNESS / "run_command_probe.py"
    tools_dir = repo / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(command_runner, tools_dir / "run_command_probe.py")

    write_files(
        repo,
        {
            "behavior.json": json.dumps({
                "language": "go",
                "extensions": [".go"],
                "tests_dir": "tests",
                "test_command": ["non_existent_compiler_xyz_123", "test", "-json"],
                "test_report": "go-test-json",
                "probe_runner": ["python", "tools/run_command_probe.py"],
                "test_file_patterns": ["*_test.go"],
                "append_tests": False,
            }),
            "discount/discount.go": "package discount\nfunc Apply(value int) int { return value }\n",
            "tests/dummy_test.go": "package tests\n",
            "probes/probe1.json": json.dumps({
                "id": "probe1",
                "target": "discount/discount.go:Apply",
                "command": ["non_existent_binary_xyz_123", "{input}"],
                "input": {"value": 10},
            }),
        },
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "head")
    write_files(repo, {
        "discount/discount.go": "package discount\nfunc Apply(value int) int { return value + 5 }\n",
    })
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "head")

    report = pipeline(repo, "base", "head", run=True)

    # Runner marks missing toolchain executions as RunStatus.ERROR, which runner.classify maps to INCONCLUSIVE
    assert len(report.tests) == 2
    for t in report.tests:
        assert t.status == RunStatus.ERROR

    assert len(report.comparisons) == 1
    assert report.comparisons[0].outcome == Outcome.INCONCLUSIVE
