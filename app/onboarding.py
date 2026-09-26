"""`behavior-review init` and `doctor` (LANE_F_PLAN F-D §7.2, §7.4): set a repository up, then check it.

`init` writes only what the repository keeps — config, probes, decisions, and optionally a GitHub
Action and the Bob mode. It never overwrites a file it didn't create in this run; the probe
runners and the web page ship with the tool.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.adapters.registry import get_adapter
from app.config import HOME_DIR, PROBE_DIRS, BehaviorConfig, ConfigError, config_file, load_config, resolve_tests_dir
from app.interpreter import display_path, python_version, resolve_python
from app.snapshot import SnapshotError, default_base, git

TEMPLATES = Path(__file__).parent / "templates"
WORKFLOW = ".github/workflows/behavior-review.yml"
BOB_MODES = ".bob/custom_modes.yaml"
PROBES_README = """# Probes

A probe is one JSON file here: a deterministic call into this repository whose output is compared
on the base and head revisions. `/behavior-review` in Bob IDE writes one for every impacted caller
the review lists under "needs a probe". The format per language is in behavior-review's README.
"""


@dataclass
class Setup:
    """What `init` detected, which the prompts then confirm or change."""

    base_branch: str
    tests_dir: str | None
    python: str | None
    action: bool = True
    bob: bool = True


def installed_ref() -> str:
    """The commit this tool was installed from (so the Action installs the same one), else 'main'."""
    try:
        direct = json.loads(importlib.metadata.distribution("behavior-review").read_text("direct_url.json") or "{}")
    except importlib.metadata.PackageNotFoundError:
        return "main"
    return direct.get("vcs_info", {}).get("commit_id") or "main"


def _tests_dir(root: Path, config: BehaviorConfig) -> str | None:
    """The folder reviews will freeze (as the runner resolves it), or None when there is none."""
    tests = resolve_tests_dir(config, root)
    return tests if (root / tests).is_dir() else None


def detect(root: Path) -> Setup:
    config = load_config(root)
    # No detected interpreter is written: a repository's .venv/ is found on every run, in its POSIX or
    # Windows layout, and a committed path like .venv/Scripts/python.exe would break other platforms.
    return Setup(base_branch=default_base(root, config.base_branch), tests_dir=_tests_dir(root, config), python=config.python)


def _config(root: Path, setup: Setup) -> dict:
    """A legacy behavior.json carries over, so running `init` never changes how reviews behave."""
    legacy = root / "behavior.json"
    value = json.loads(legacy.read_text(encoding="utf-8")) if legacy.is_file() else {}
    if not value:
        languages = list(load_config(root).languages)
        value = {"languages": languages} if len(languages) > 1 else {"language": languages[0]}
    value["base_branch"] = setup.base_branch
    if setup.tests_dir is not None:
        value["tests_dir"] = setup.tests_dir or "."
    if setup.python:
        value["python"] = setup.python
    return value


def _write_new(path: Path, content: str, created: list[str], root: Path) -> None:
    if path.exists():
        created.append(f"kept {path.relative_to(root).as_posix()} (already there)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    created.append(f"wrote {path.relative_to(root).as_posix()}")


def _merge_bob_mode(root: Path, created: list[str]) -> None:
    """Add our mode to an existing `.bob/custom_modes.yaml` instead of replacing the author's modes."""
    path, template = root / BOB_MODES, (TEMPLATES / "custom_modes.yaml").read_text(encoding="utf-8")
    if not path.exists():
        return _write_new(path, template, created, root)
    existing = path.read_text(encoding="utf-8")
    if "slug: behavior-review" in existing:
        return created.append(f"kept {BOB_MODES} (it already has the behavior-review mode)")
    if not existing.lstrip().startswith("customModes:"):
        return created.append(f"skipped {BOB_MODES}: add the mode from behavior-review's templates by hand")
    entry = template.split("\n", 1)[1]
    path.write_text(existing.rstrip("\n") + "\n" + entry, encoding="utf-8")
    created.append(f"added the behavior-review mode to {BOB_MODES}")


def init_repo(root: Path, setup: Setup) -> list[str]:
    """Write the repository's setup; returns one line per file written or kept."""
    created: list[str] = []
    home = root / HOME_DIR
    _write_new(home / "config.json", json.dumps(_config(root, setup), indent=2) + "\n", created, root)
    if not (root / PROBE_DIRS[-1]).is_dir():  # a legacy probes/ stays where it is and is still read
        _write_new(home / "probes" / "README.md", PROBES_README, created, root)
    _write_new(home / "decisions" / ".gitkeep", "", created, root)
    _write_new(home / ".gitignore", "runs/\n", created, root)
    if setup.action:
        workflow = (TEMPLATES / "behavior-review.yml").read_text(encoding="utf-8")
        _write_new(root / WORKFLOW, workflow.replace("__BEHAVIOR_REVIEW_REF__", installed_ref()), created, root)
    if setup.bob:
        _merge_bob_mode(root, created)
    return created


# --- doctor ---------------------------------------------------------------------------------------

@dataclass
class Check:
    status: str  # "ok", "warn" or "fail" (fail blocks a review)
    text: str


def _config_checks(root: Path) -> list[Check]:
    try:
        config = load_config(root)
    except ConfigError as exc:
        return [Check("fail", f"config: {exc}")]
    source = config_file(root) or "none (detected from the repository)"
    tiers = ", ".join(f"{lang} ({get_adapter(lang).spec.tier})" for lang in config.languages)
    tests = _tests_dir(root, config)
    return [
        Check("ok", f"config: {source}; languages: {tiers}"),
        Check("ok", f"tests: {tests or '.'}/") if tests is not None else Check("warn", "tests: none found; reviews still show impact"),
        _base_check(root, config.base_branch),
        _python_check(root, config),
    ]


def _base_check(root: Path, configured: str | None) -> Check:
    base = default_base(root, configured)
    if configured and base != configured:
        return Check("warn", f"base branch: '{configured}' in the config doesn't exist; using {base}")
    return Check("ok", f"base branch: {base}")


def _python_check(root: Path, config: BehaviorConfig) -> Check:
    interpreter = resolve_python(root, configured=config.python)
    version = python_version(interpreter)
    name = f"{display_path(interpreter.path, root)} {version or ''} ({interpreter.source})".replace("  ", " ")
    if version is None:
        return Check("fail", f"python: {name} does not start")
    if config.test_report == "pytest-text":
        found = subprocess.run([interpreter.path, "-c", "import pytest"], capture_output=True).returncode == 0
        if not found:
            return Check("warn", f"python: {name} has no pytest, so the test suite can't run")
    if interpreter.source == "fallback":
        return Check("warn", f"python: {name}: behavior-review's own; create a .venv or set 'python' in the config")
    return Check("ok", f"python: {name}")


def doctor(root: Path) -> list[Check]:
    try:
        git(root, "rev-parse", "--show-toplevel")
    except SnapshotError:
        return [Check("fail", "not a git repository")]
    probes = sum(len(list((root / d).glob("*.json"))) for d in PROBE_DIRS if (root / d).is_dir())
    return [
        Check("ok", f"git repository: {root.name}"),
        *_config_checks(root),
        Check("ok", f"probes: {probes}") if probes else Check("warn", "probes: none yet; /behavior-review in Bob IDE writes them"),
        Check("ok", "Tree-sitter: installed") if importlib.util.find_spec("tree_sitter_language_pack")
        else Check("warn", "Tree-sitter: missing, so only Python is analyzed"),
        Check("ok", "web page: packaged") if (Path(__file__).parent / "web_dist" / "index.html").is_file()
        else Check("warn", "web page: not in this install; `behavior-review ui` serves the API only"),
    ]
