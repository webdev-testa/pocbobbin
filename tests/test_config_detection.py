"""Tests for per-repository configuration: the tool must adapt to the repo under review.

These guard the defect that made `--run` unusable on any repository other than this one:
the test directory used to default to this project's own `sample_project/tests`, so a foreign
repo crashed with FileNotFoundError instead of running its own suite.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.config import BehaviorConfig, detect_tests_dir, load_config, resolve_tests_dir

REPO_ROOT = Path(__file__).resolve().parent.parent


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "foreign"
    root.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return root


def test_detects_a_nested_test_directory(tmp_path):
    root = _repo(tmp_path, {
        "backend/app/main.py": "def f():\n    return 1\n",
        "backend/tests/test_main.py": "def test_f():\n    assert True\n",
    })
    assert detect_tests_dir(root) == "backend/tests"


def test_detects_root_level_tests_as_empty_string(tmp_path):
    root = _repo(tmp_path, {"test_root.py": "def test_ok():\n    assert True\n"})
    assert detect_tests_dir(root) == ""


def test_reports_no_tests_rather_than_guessing(tmp_path):
    root = _repo(tmp_path, {"src/app.py": "def f():\n    return 1\n"})
    assert detect_tests_dir(root) is None


def test_detection_ignores_virtualenvs_and_node_modules(tmp_path):
    root = _repo(tmp_path, {
        ".venv/lib/site-packages/test_vendored.py": "def test_v():\n    assert True\n",
        "tests/test_real.py": "def test_r():\n    assert True\n",
    })
    assert detect_tests_dir(root) == "tests"


def test_a_stated_tests_dir_is_never_overridden(tmp_path):
    """An explicit behavior.json wins: the repo knows its own layout better than we do."""
    root = _repo(tmp_path, {
        "behavior.json": '{"language": "python", "tests_dir": "spec"}',
        "spec/test_thing.py": "def test_t():\n    assert True\n",
        "tests/test_other.py": "def test_o():\n    assert True\n",
    })
    config = load_config(root)
    assert config.tests_dir == "spec"
    assert resolve_tests_dir(config, root) == "spec"


def test_missing_config_detects_instead_of_using_our_sample_project(tmp_path):
    """The regression: a foreign repo must never resolve to this project's sample suite."""
    root = _repo(tmp_path, {"tests/test_x.py": "def test_x():\n    assert True\n"})
    assert resolve_tests_dir(load_config(root), root) == "tests"
    assert BehaviorConfig().tests_dir != "sample_project/tests"


def test_runner_resolves_the_target_repos_tests_not_ours(tmp_path):
    """compare() with default arguments must run the repo-under-review's suite."""
    source = _repo(tmp_path, {
        "pkg/calc.py": "def add(a, b):\n    return a + b\n",
        "tests/test_calc.py": "from pkg.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    })
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "one"]):
        subprocess.run(cmd, cwd=source, check=True, capture_output=True)
    (source / "pkg/calc.py").write_text("def add(a, b):\n    return a + b + 0\n")
    subprocess.run(["git", "add", "-A"], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "two"],
                   cwd=source, check=True, capture_output=True)

    sys.path.insert(0, str(REPO_ROOT))
    from app.runner import compare
    from app.snapshot import open_pair

    with open_pair(source, "HEAD~1", "HEAD") as pair:
        suites, comparisons, missing, notes = compare(pair, python=sys.executable)
    assert [suite.revision for suite in suites] == ["base", "head"]
    assert all(suite.status == "ok" for suite in suites), [s.status for s in suites]
    # no probes in a foreign repo: the report says so instead of inventing evidence
    assert comparisons == []
    assert any("no probe runner" in note or "no committed probes" in note for note in notes)


def test_a_repo_with_no_tests_reports_a_limit_instead_of_crashing(tmp_path):
    """No suite is a limit to state, not an error to raise."""
    source = _repo(tmp_path, {"pkg/only.py": "def f():\n    return 1\n"})
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "one"]):
        subprocess.run(cmd, cwd=source, check=True, capture_output=True)
    (source / "pkg/only.py").write_text("def f():\n    return 2\n")
    subprocess.run(["git", "add", "-A"], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "two"],
                   cwd=source, check=True, capture_output=True)

    sys.path.insert(0, str(REPO_ROOT))
    from app.runner import compare
    from app.snapshot import open_pair

    with open_pair(source, "HEAD~1", "HEAD") as pair:
        suites, comparisons, missing, notes = compare(pair, python=sys.executable)
    assert suites == []
    assert comparisons == []
    assert any("no test directory" in note for note in notes)


def test_this_repository_states_its_own_layout():
    """This repo ships behavior.json so the demo keeps running sample_project/tests."""
    assert load_config(REPO_ROOT).tests_dir == "sample_project/tests"
    assert resolve_tests_dir(load_config(REPO_ROOT), REPO_ROOT) == "sample_project/tests"


# --- probe runner target forms ---------------------------------------------------------------

def _run_probe(repo: Path, probe: dict, *extra: str):
    import json as _json
    probe_file = repo / "probe.json"
    probe_file.write_text(_json.dumps(probe))
    runner = Path(__file__).resolve().parents[1] / "tools" / "run_probe.py"
    result = subprocess.run(
        [sys.executable, str(runner), str(probe_file), *extra],
        cwd=repo, capture_output=True, text=True, timeout=120,
    )
    return result, _json.loads(result.stdout or "{}")


def test_probe_runner_accepts_the_string_target_form(tmp_path):
    """The concise 'module:function' form must keep working."""
    repo = _repo(tmp_path, {"plainmod.py": "def double(n):\n    return n * 2\n"})
    _, payload = _run_probe(repo, {"id": "p", "target": "plainmod:double", "args": [21]})
    assert payload["outcome"] == "value" and payload["value"] == 42


def test_probe_runner_accepts_the_object_target_form(tmp_path):
    """The Bob mode documents {"path", "symbol"}; following it must not crash the runner."""
    repo = _repo(tmp_path, {"pkg/deep.py": "def three():\n    return 3\n"})
    _, payload = _run_probe(
        repo, {"id": "p", "target": {"path": "pkg/deep.py", "symbol": "three"}, "args": []}
    )
    assert payload["outcome"] == "value" and payload["value"] == 3


def test_probe_runner_resolves_a_class_method_symbol(tmp_path):
    """Symbols are spelled 'Class.method' in reports, so the runner must resolve that too."""
    repo = _repo(tmp_path, {
        "pkg/calc.py": "class Calc:\n    def add(self, a, b):\n        return a + b\n"
    })
    _, payload = _run_probe(
        repo, {"id": "p", "target": {"path": "pkg/calc.py", "symbol": "Calc.add"}, "args": [2, 3]}
    )
    assert payload["outcome"] == "value" and payload["value"] == 5


def test_probe_runner_imports_a_monorepo_package_by_its_real_name(tmp_path):
    """A module nested under backend/ must import with backend/ as the root, or its own
    internal `from app... import ...` lines fail even though the file is right there."""
    repo = _repo(tmp_path, {
        "backend/app/__init__.py": "",
        "backend/app/engine/__init__.py": "",
        "backend/app/engine/inner.py": "VALUE = 11\n",
        "backend/app/engine/outer.py": (
            "from app.engine.inner import VALUE\n\n"
            "def read():\n    return VALUE\n"
        ),
    })
    _, payload = _run_probe(
        repo, {"id": "p", "target": {"path": "backend/app/engine/outer.py", "symbol": "read"}, "args": []}
    )
    assert payload["outcome"] == "value", payload
    assert payload["value"] == 11


def test_probe_runner_reports_a_missing_target_rather_than_raising(tmp_path):
    """A broken probe is an outcome the report can carry, not a crash for the runner."""
    repo = _repo(tmp_path, {"pkg/only.py": "def f():\n    return 1\n"})
    _, payload = _run_probe(
        repo, {"id": "p", "target": {"path": "pkg/missing.py", "symbol": "f"}, "args": []}
    )
    assert payload["outcome"] == "exception"
    assert payload["error_type"] == "FileNotFoundError"
