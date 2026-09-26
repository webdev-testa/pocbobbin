"""Which Python runs the reviewed project's tests and probes (LANE_F_PLAN F-B)."""

import json
import platform
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from app.cli import pipeline
from app.config import BehaviorConfig, ConfigError
from app.interpreter import FALLBACK_LIMIT, Interpreter, display_path, python_version, resolve_python, venv_python
from app.schemas import Outcome


def _fake_venv(root: Path, layout: str) -> Path:
    """An interpreter file where a venv keeps it: 'posix' (bin/python) or 'windows' (Scripts/python.exe)."""
    path = root / ("bin/python" if layout == "posix" else "Scripts/python.exe")
    path.parent.mkdir(parents=True)
    path.write_text("")
    return path


@pytest.mark.parametrize("layout", ["posix", "windows"])
def test_a_venv_is_found_in_either_layout(tmp_path, layout):
    assert venv_python(tmp_path) is None
    expected = _fake_venv(tmp_path, layout)
    assert venv_python(tmp_path) == expected


def test_the_flag_wins_then_the_config(tmp_path):
    flagged, configured = _fake_venv(tmp_path / "a", "posix"), _fake_venv(tmp_path / "b", "windows")
    _fake_venv(tmp_path / ".venv", "posix")
    env = {"VIRTUAL_ENV": str(tmp_path / "a")}

    assert resolve_python(tmp_path, "a/bin/python", "b/Scripts/python.exe", env) == Interpreter(str(flagged), "flag")
    assert resolve_python(tmp_path, None, "b/Scripts/python.exe", env) == Interpreter(str(configured), "config")


def test_then_virtual_env_then_dot_venv_then_venv(tmp_path):
    active, dot_venv, plain_venv = (_fake_venv(tmp_path / name, "posix") for name in ("active", ".venv", "venv"))

    assert resolve_python(tmp_path, environ={"VIRTUAL_ENV": str(tmp_path / "active")}) == Interpreter(str(active), "virtual_env")
    assert resolve_python(tmp_path, environ={}) == Interpreter(str(dot_venv), "venv")
    dot_venv.unlink()
    assert resolve_python(tmp_path, environ={}) == Interpreter(str(plain_venv), "venv")


def test_the_tools_own_interpreter_is_the_last_resort(tmp_path):
    assert resolve_python(tmp_path, environ={}) == Interpreter(sys.executable, "fallback")


def test_an_explicit_interpreter_must_exist(tmp_path):
    with pytest.raises(ConfigError, match="--python names 'nowhere/python'"):
        resolve_python(tmp_path, "nowhere/python", environ={})
    with pytest.raises(ConfigError, match="'python' in the config names"):
        resolve_python(tmp_path, None, "nowhere/python", environ={})


def test_the_report_names_an_interpreter_without_a_local_path(tmp_path):
    assert display_path(str(tmp_path / ".venv" / "bin" / "python"), tmp_path) == ".venv/bin/python"
    assert display_path(sys.executable, tmp_path) == Path(sys.executable).name


def test_the_version_comes_from_the_interpreter_itself(tmp_path):
    assert python_version(Interpreter(sys.executable, "fallback")) == platform.python_version()
    assert python_version(Interpreter(str(tmp_path / "missing"), "flag")) is None


def test_the_config_accepts_a_python_path():
    assert BehaviorConfig.from_mapping({"python": " .venv/bin/python "}).python == ".venv/bin/python"
    with pytest.raises(ConfigError, match="'python' must be a non-empty string"):
        BehaviorConfig.from_mapping({"python": ""})


# --- end to end: a dependency only the project's environment has ------------------------------

PROJECT = {
    "shop.py": "import projdep\n\n\ndef total(value):\n    return projdep.scale(value)\n",
    "probes/total.json": json.dumps({"id": "total", "target": "shop:total", "args": [2]}),
}


def _project_venv(repo: Path) -> None:
    """A real .venv holding `projdep`, which behavior-review's own environment doesn't have."""
    venv.create(repo / ".venv", with_pip=False)
    python = venv_python(repo / ".venv")
    purelib = subprocess.run([str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                             capture_output=True, text=True, check=True).stdout.strip()
    Path(purelib, "projdep.py").write_text("def scale(value):\n    return value * 10\n", encoding="utf-8")


def test_the_projects_venv_runs_its_probes(make_repo, monkeypatch):
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    repo = make_repo(PROJECT, {"shop.py": PROJECT["shop.py"].replace("value)\n", "value) + 1\n")})
    _project_venv(repo)

    report = pipeline(repo, "base", "head", run=True)

    [comparison] = report.comparisons
    assert (comparison.outcome, comparison.base.output, comparison.head.output) == (Outcome.DELTA_OBSERVED, 20, 21)
    runtime = report.analysis.runtime
    assert (runtime.python, runtime.source) in {(".venv/bin/python", "venv"), (".venv/Scripts/python.exe", "venv")}
    assert runtime.version and runtime.probe_runner.source == "packaged"
    assert FALLBACK_LIMIT not in report.limits


def test_without_the_projects_venv_the_run_is_inconclusive_and_says_why(make_repo, monkeypatch):
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    repo = make_repo(PROJECT, {"shop.py": PROJECT["shop.py"].replace("value)\n", "value) + 1\n")})

    report = pipeline(repo, "base", "head", run=True)

    assert [c.outcome for c in report.comparisons] == [Outcome.INCONCLUSIVE]
    assert report.analysis.runtime.source == "fallback"
    assert FALLBACK_LIMIT in report.limits
