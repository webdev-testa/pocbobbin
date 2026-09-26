"""Which Python runs the reviewed project's tests and probes (LANE_F_PLAN F-B).

The tool lives in its own environment (pipx / `uv tool`), but the project's tests need the
project's libraries, so they run with the project's interpreter: `--python`, then `python` in
the config, then `$VIRTUAL_ENV`, then the repository's `.venv/` or `venv/`, and only then the
tool's own interpreter, which the report states as a limit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.config import ConfigError

FALLBACK_LIMIT = (
    "Tests and probes ran with behavior-review's own Python; if the project's dependencies are not "
    "installed there, results may be inconclusive. Pass --python or create a .venv in the repository."
)


@dataclass(frozen=True)
class Interpreter:
    path: str
    """Absolute, so it runs the same from the temporary worktrees."""
    source: str
    """'flag', 'config', 'virtual_env', 'venv' or 'fallback'."""


def venv_python(venv: Path) -> Path | None:
    """The interpreter inside a virtual environment, in its POSIX or Windows layout."""
    for candidate in (venv / "bin" / "python", venv / "Scripts" / "python.exe"):
        if candidate.is_file():
            return candidate
    return None


def _explicit(value: str, repo_root: Path, source: str) -> Interpreter:
    """A path relative to the repository, an absolute path, or a command on PATH; it must exist."""
    in_repo = repo_root / value
    found = str(in_repo) if in_repo.is_file() else shutil.which(value)
    if not found:
        where = "--python" if source == "flag" else "'python' in the config"
        raise ConfigError(f"{where} names '{value}', which is not an interpreter that exists")
    # absolute, not resolved: a venv's python is a symlink, and following it would lose the venv.
    return Interpreter(os.path.abspath(found), source)


def resolve_python(repo_root: Path, flag: str | None = None, configured: str | None = None,
                   environ: dict[str, str] | None = None) -> Interpreter:
    """The first of: flag, config, $VIRTUAL_ENV, .venv/ or venv/ in the repository, the tool's own."""
    if flag:
        return _explicit(flag, repo_root, "flag")
    if configured:
        return _explicit(configured, repo_root, "config")
    active = (os.environ if environ is None else environ).get("VIRTUAL_ENV")
    if active and (found := venv_python(Path(active))):
        return Interpreter(str(found), "virtual_env")
    for name in (".venv", "venv"):
        if found := venv_python(Path(os.path.abspath(repo_root)) / name):
            return Interpreter(str(found), "venv")
    return Interpreter(sys.executable, "fallback")


def python_version(interpreter: Interpreter) -> str | None:
    """"3.12.4", or None when the interpreter can't even start (its runs will then be inconclusive)."""
    try:
        proc = subprocess.run(
            [interpreter.path, "-c", "import platform; print(platform.python_version())"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def display_path(path: str, repo_root: Path) -> str:
    """Repository-relative when inside it, else only the file name: a report never holds a local path."""
    absolute = Path(os.path.abspath(path))
    try:
        return absolute.relative_to(os.path.abspath(repo_root)).as_posix()
    except ValueError:
        return absolute.name
