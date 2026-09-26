"""What kind of PR this is, and so which checks it needs (LANE_F_PLAN F-A). Deterministic, no AI.

Safety rule: triage may only skip a step when there is provably nothing for it to compare, which
is only true when a PR changes nothing but docs. Everywhere else it only labels and warns;
`--full` runs everything regardless.
"""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

from app.config import DECISION_DIRS, PROBE_DIRS, BehaviorConfig
from app.schemas import ImpactResult, Triage

DOC_PATTERNS = ("*.md", "*.rst", "*.txt", "*.adoc", "LICENSE*", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.webp", "*.ico")
DOC_FOLDERS = ("docs/", "doc/")

# Files whose effect static analysis can't see: dependencies, build and CI configuration.
CONFIG_PATTERNS = (
    "pyproject.toml", "setup.cfg", "setup.py", "requirements*.txt", "Pipfile", "Pipfile.lock", "poetry.lock", "uv.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "tsconfig*.json",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "pom.xml", "build.gradle*", "settings.gradle*", "*.csproj", "*.sln",
    "Gemfile", "Gemfile.lock", "composer.json", "composer.lock", "Dockerfile*", "docker-compose*.yml",
    "behavior.json",
)
CONFIG_FOLDERS = (".github/workflows/",)
CONFIG_FILES = (".behavior-review/config.json",)
# Probes and decisions are review data: a PR adding a probe must still run it, so they count like tests.
REVIEW_DATA = tuple(f"{folder}/" for folder in (*PROBE_DIRS, *DECISION_DIRS))

DOCS_ONLY_SKIPS = ["tests", "probes"]


def _matches(path: str, patterns: tuple[str, ...], folders: tuple[str, ...]) -> bool:
    name = PurePosixPath(path).name
    return path.startswith(folders) or any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _is_test(path: str, config: BehaviorConfig) -> bool:
    name = PurePosixPath(path).name
    in_tests_dir = config.tests_dir not in {"", "."} and path.startswith(f"{config.tests_dir.rstrip('/')}/")
    return in_tests_dir or any(fnmatch.fnmatch(name, pattern) for pattern in config.test_file_patterns)


def _kind(path: str, config: BehaviorConfig) -> str:
    """'config', 'docs', 'tests', 'code' (an adapter parses it) or 'other' (none does: treated as code)."""
    if path in CONFIG_FILES or _matches(path, CONFIG_PATTERNS, CONFIG_FOLDERS):
        return "config"
    if path.startswith(REVIEW_DATA):
        return "tests"
    if _matches(path, DOC_PATTERNS, DOC_FOLDERS):
        return "docs"
    if _is_test(path, config):
        return "tests"
    return "code" if path.endswith(config.extensions) else "other"


def _profile(kinds: dict[str, str], impact: ImpactResult) -> str:
    """A mixed PR takes its most thorough profile: config > code > tests > docs.

    "No semantic change" needs proof: every code file parsed and no symbol changed. A file no
    adapter parses could hide anything, so it counts as a code change.
    """
    present = set(kinds.values())
    if not present:
        return "no_semantic_change"
    if "config" in present:
        return "config_or_deps"
    if "other" in present or ("code" in present and impact.changed_symbols):
        return "code_change"
    if "code" in present:
        return "no_semantic_change"
    return "tests_only" if "tests" in present else "docs_only"



def _reasons(profile: str, kinds: dict[str, str], impact: ImpactResult) -> list[str]:
    by_kind = lambda kind: sorted(path for path, found in kinds.items() if found == kind)
    reasons = {
        "docs_only": [f"only documentation changed: {', '.join(by_kind('docs'))}"],
        "tests_only": [f"only test files changed: {', '.join(by_kind('tests'))}"],
        "config_or_deps": [f"dependency, build or CI configuration changed: {', '.join(by_kind('config'))}"],
        "no_semantic_change": [
            "code files changed, but no function, class or import changed (formatting or comments only)"
            if kinds else "no files differ between the base and head revisions"
        ],
        "code_change": [f"{len(impact.changed_symbols)} changed symbol(s)"] if impact.changed_symbols else [],
    }[profile]
    if unparsed := by_kind("other"):
        reasons.append(f"no adapter parses {', '.join(unparsed)}, so its effect is unknown")
    return reasons


def triage(changed_files: list[str], impact: ImpactResult, config: BehaviorConfig) -> Triage:
    """Classify a PR from its changed files and the impact analysis; `skipped_steps` is what it may skip."""
    kinds = {path: _kind(path, config) for path in changed_files}
    profile = _profile(kinds, impact)
    return Triage(
        profile=profile,
        reasons=_reasons(profile, kinds, impact),
        skipped_steps=list(DOCS_ONLY_SKIPS) if profile == "docs_only" else [],
    )
