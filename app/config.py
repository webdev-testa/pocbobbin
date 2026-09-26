"""Repository-level behavior-review configuration and adapter detection.

Explicit ``behavior.json`` remains authoritative. When it is absent, the
detector selects one or more adapters from repository manifests and source
extensions, while preserving the Python defaults for legacy/empty repositories.
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when behavior.json cannot be used safely."""


SUPPORTED_LANGUAGES = {
    "python",
    "typescript",
    "javascript",
    "java",
    "csharp",
    "go",
    "cpp",
    "c",
    "rust",
    "php",
    "kotlin",
    "ruby",
    "swift",
    "dart",
    "bash",
}

# A `probe_runner` path names the runner: the base revision's copy at that path overrides the
# packaged one of the same name (`app/harness/`), and a copy only the change has is never used.
LANGUAGE_DEFAULTS: dict[str, dict[str, Any]] = {
    "python": {
        "extensions": (".py",),
        "tests_dir": "tests",
        "test_command": ("python", "-m", "pytest", "-q", "--no-header"),
        "test_report": "pytest-text",
        "probe_runner": ("python", "tools/run_probe.py"),
        "test_file_patterns": ("test_*.py", "*_test.py", "conftest.py"),
        "append_tests": True,
    },
    "typescript": {
        "extensions": (".ts", ".tsx"),
        "tests_dir": "tests",
        "test_command": ("npx", "vitest", "run", "--reporter=json"),
        "test_report": "vitest-json",
        "probe_runner": ("npx", "tsx", "tools/run_probe.ts"),
        "test_file_patterns": ("*.test.ts", "*.spec.ts", "*.test.tsx", "*.spec.tsx"),
        "append_tests": True,
    },
    "javascript": {
        "extensions": (".js", ".jsx", ".mjs", ".cjs"),
        "tests_dir": "tests",
        "test_command": ("npx", "vitest", "run", "--reporter=json"),
        "test_report": "vitest-json",
        "probe_runner": ("node", "tools/run_probe.ts"),
        "test_file_patterns": ("*.test.js", "*.spec.js", "*.test.jsx", "*.spec.jsx"),
        "append_tests": True,
    },
    "java": {
        "extensions": (".java",),
        "tests_dir": "src/test",
        "test_command": ("mvn", "-q", "test"),
        "test_report": "junit-xml",
        "probe_runner": ("python", "tools/run_command_probe.py"),
        "test_file_patterns": ("*Test.java", "*Tests.java"),
        "append_tests": False,
    },
    "csharp": {
        "extensions": (".cs",),
        "tests_dir": "tests",
        "test_command": ("dotnet", "test", "--logger", "trx;LogFileName=TestResults.trx"),
        "test_report": "trx-xml",
        "probe_runner": ("python", "tools/run_command_probe.py"),
        "test_file_patterns": ("*Tests.cs", "*Test.cs"),
        "append_tests": False,
    },
    "go": {
        "extensions": (".go",),
        "tests_dir": ".",
        "test_command": ("go", "test", "-json", "./..."),
        "test_report": "go-test-json",
        "probe_runner": ("python", "tools/run_command_probe.py"),
        "test_file_patterns": ("*_test.go",),
        "append_tests": False,
    },
    "cpp": {
        "extensions": (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"),
        "tests_dir": "tests",
        "test_command": ("ctest", "--test-dir", "build", "--output-on-failure"),
        "test_report": "ctest-text",
        "probe_runner": ("python", "tools/run_command_probe.py"),
        "test_file_patterns": ("*.cpp", "*.cc", "*.cxx", "*_test.cpp", "*_test.cc"),
        "append_tests": False,
    },
    "c": {
        "extensions": (".c", ".h"),
        "tests_dir": "tests",
        "test_command": ("ctest", "--test-dir", "build", "--output-on-failure"),
        "test_report": "ctest-text",
        "probe_runner": ("python", "tools/run_command_probe.py"),
        "test_file_patterns": ("*_test.c", "test_*.c"),
        "append_tests": False,
    },
    "rust": {
        "extensions": (".rs",),
        "tests_dir": "tests",
        "test_command": ("cargo", "test", "--quiet"),
        "test_report": "cargo-text",
        "probe_runner": ("python", "tools/run_command_probe.py"),
        "test_file_patterns": ("*.rs",),
        "append_tests": False,
    },
    "php": {
        "extensions": (".php",),
        "tests_dir": "tests",
        "test_command": ("vendor/bin/phpunit", "--testdox"),
        "test_report": "phpunit-text",
        "probe_runner": ("python", "tools/run_command_probe.py"),
        "test_file_patterns": ("*Test.php", "Test*.php"),
        "append_tests": False,
    },
    "kotlin": {
        "extensions": (".kt", ".kts"),
        "tests_dir": "src/test",
        "test_command": ("gradle", "test"),
        "test_report": "junit-xml",
        "probe_runner": ("python", "tools/run_command_probe.py"),
        "test_file_patterns": ("*Test.kt", "*Tests.kt"),
        "append_tests": False,
    },
    "ruby": {
        "extensions": (".rb",),
        "tests_dir": "spec",
        "test_command": ("bundle", "exec", "rspec", "--format", "json"),
        "test_report": "rspec-json",
        "probe_runner": ("python", "tools/run_command_probe.py"),
        "test_file_patterns": ("*_spec.rb", "test_*.rb"),
        "append_tests": False,
    },
    "swift": {
        "extensions": (".swift",),
        "tests_dir": "Tests",
        "test_command": ("swift", "test"),
        "test_report": "swift-text",
        "probe_runner": ("python", "tools/run_command_probe.py"),
        "test_file_patterns": ("*Tests.swift", "*Test.swift"),
        "append_tests": False,
    },
    "dart": {
        "extensions": (".dart",),
        "tests_dir": "test",
        "test_command": ("dart", "test", "--reporter=json"),
        "test_report": "dart-json",
        "probe_runner": ("python", "tools/run_command_probe.py"),
        "test_file_patterns": ("*_test.dart",),
        "append_tests": False,
    },
    "bash": {
        "extensions": (".sh", ".bash"),
        "tests_dir": "tests",
        "test_command": ("bash", "tests/run.sh"),
        "test_report": "shell-text",
        "probe_runner": ("python", "tools/run_command_probe.py"),
        "test_file_patterns": ("*.sh", "test_*.bash"),
        "append_tests": False,
    },
}

SUPPORTED_TEST_REPORTS = {
    "pytest-text",
    "vitest-json",
    "junit-xml",
    "trx-xml",
    "go-test-json",
    "ctest-text",
    "cargo-text",
    "phpunit-text",
    "rspec-json",
    "swift-text",
    "dart-json",
    "shell-text",
}

LANGUAGE_ALIASES = {
    "py": "python", "ts": "typescript", "tsx": "typescript", "js": "javascript", "jsx": "javascript",
    "cs": "csharp", "c#": "csharp", "c-sharp": "csharp", "c++": "cpp", "cxx": "cpp",
    "kt": "kotlin", "rb": "ruby", "sh": "bash", "shell": "bash",
}

DETECTION_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".next", ".pytest_cache", ".mypy_cache", ".turbo", ".cache",
    "__pycache__", "node_modules", "venv", ".venv", "build", "dist", "site-packages",
    "target", "vendor", "coverage", "out", "bin", "obj", "Pods", "DerivedData",
}

DETECTION_MARKERS: dict[str, tuple[tuple[str, int], ...]] = {
    "python": (("pyproject.toml", 8), ("requirements.txt", 6), ("setup.py", 6), ("setup.cfg", 5), ("Pipfile", 5), ("tox.ini", 4)),
    "typescript": (("tsconfig*.json", 12),),
    "javascript": (("package.json", 6),),
    "java": (("pom.xml", 10), ("build.gradle", 7), ("settings.gradle", 5)),
    "kotlin": (("build.gradle.kts", 10), ("settings.gradle.kts", 8)),
    "go": (("go.mod", 12),),
    "cpp": (("CMakeLists.txt", 6),),
    "c": (("CMakeLists.txt", 5),),
    "rust": (("Cargo.toml", 12),),
    "php": (("composer.json", 8), ("phpunit.xml*", 7)),
    "ruby": (("Gemfile", 8), (".rspec", 5)),
    "swift": (("Package.swift", 12),),
    "dart": (("pubspec.yaml", 12),),
}

LANGUAGE_PRIORITY = {
    "typescript": 0, "javascript": 1, "python": 2, "go": 3, "rust": 4,
    "java": 5, "kotlin": 6, "csharp": 7, "cpp": 8, "c": 9, "php": 10,
    "ruby": 11, "swift": 12, "dart": 13, "bash": 14,
}


def _normalize_language(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("behavior.json language values must be non-empty strings")
    language = LANGUAGE_ALIASES.get(value.strip().lower(), value.strip().lower())
    if language not in SUPPORTED_LANGUAGES:
        raise ConfigError(f"unsupported behavior-review language '{language}'")
    return language


def _repository_source_counts(root: Path) -> dict[str, int]:
    extensions = {
        extension.lower()
        for defaults in LANGUAGE_DEFAULTS.values()
        for extension in defaults["extensions"]
    }
    counts = {extension: 0 for extension in extensions}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in DETECTION_SKIP_DIRS]
        for filename in filenames:
            extension = Path(filename).suffix.lower()
            if extension in counts:
                counts[extension] += 1
    return counts


def _marker_score(root: Path, patterns: tuple[tuple[str, int], ...]) -> int:
    return sum(weight for pattern, weight in patterns if any(root.glob(pattern)))


def _detect_languages(root: Path) -> tuple[str, ...]:
    counts = _repository_source_counts(root)
    scores: dict[str, int] = {}
    for language, defaults in LANGUAGE_DEFAULTS.items():
        source_count = sum(counts.get(extension.lower(), 0) for extension in defaults["extensions"])
        marker_score = _marker_score(root, DETECTION_MARKERS.get(language, ()))
        if source_count or marker_score:
            scores[language] = min(source_count, 20) + marker_score

    # A TypeScript repository often has JavaScript config files. Do not add a
    # second adapter just because package.json exists and the JS count is small.
    if "typescript" in scores and scores.get("javascript", 0) <= 6:
        scores.pop("javascript", None)

    return tuple(sorted(scores, key=lambda language: (-scores[language], LANGUAGE_PRIORITY[language], language)))


def detect_tests_dir(
    repo: str | Path, patterns: tuple[str, ...] = ("test_*.py", "*_test.py", "conftest.py")
) -> str | None:
    """Find where a repository keeps its tests, so a real project runs without a behavior.json.

    Returns the shallowest directory (relative posix path) holding a matching file, or "" when
    tests live at the root, or None when nothing matches. Deliberately shallowest-first so a
    nested duplicate cannot shadow the project's real suite.
    """
    root = Path(repo)
    if not root.is_dir():
        return None
    skip = {".git", ".venv", "venv", "node_modules", "build", "dist", "__pycache__", ".tox",
            ".mypy_cache", ".pytest_cache", "site-packages"}
    candidates: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        if any(part in skip for part in path.relative_to(root).parts):
            continue
        if not any(fnmatch.fnmatch(path.name, pattern) for pattern in patterns):
            continue
        relative = path.relative_to(root).parent.as_posix()
        if relative == ".":
            return ""  # tests at the root are the shallowest answer possible
        candidates.add(relative)
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item.count("/"), len(item)))


def resolve_tests_dir(config: "BehaviorConfig", revision: str | Path) -> str:
    """The test directory to use for `revision`: stated, else detected in that revision, else default.

    Detection runs against the revision being frozen (BASE), never the live source tree, so the
    suite and the code always come from the same commit.
    """
    root = Path(revision)
    if (root / config.tests_dir).exists():
        return config.tests_dir
    patterns = config.test_file_patterns or ("test_*.py", "*_test.py", "conftest.py")
    detected = detect_tests_dir(root, patterns)
    if detected is not None:
        return detected
    # Nothing matched the configured adapter. A repository detected as another language must not
    # be forced through Python's layout: try each detected adapter's own patterns before falling
    # back, so a TypeScript repository finds its own specs instead of failing on 'tests'.
    for language in config.languages:
        defaults = LANGUAGE_DEFAULTS.get(language)
        if not defaults or defaults["test_file_patterns"] == patterns:
            continue
        detected = detect_tests_dir(root, defaults["test_file_patterns"])
        if detected is not None:
            return detected
    return config.tests_dir


def _auto_detect_config(root: Path) -> "BehaviorConfig":
    languages = _detect_languages(root)
    if not languages:
        # Preserve the established behavior for empty/legacy Python repositories.
        return BehaviorConfig()
    return replace(BehaviorConfig.from_mapping({"languages": list(languages)}), source="detected")


def _optional_string(value: dict[str, Any], name: str) -> str | None:
    """A config field that may be absent, but is a non-empty string when present."""
    raw = value.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"behavior.json field '{name}' must be a non-empty string")
    return raw.strip()


@dataclass(frozen=True)
class BehaviorConfig:
    language: str = "python"
    languages: tuple[str, ...] = ("python",)
    extensions: tuple[str, ...] = (".py",)
    changed_file_filter: tuple[str, ...] = ()
    tests_dir: str = "tests"
    test_command: tuple[str, ...] = ("python", "-m", "pytest", "-q", "--no-header")
    test_report: str = "pytest-text"
    probe_runner: tuple[str, ...] = ("python", "tools/run_probe.py")
    test_file_patterns: tuple[str, ...] = ("test_*.py", "*_test.py", "conftest.py")
    max_hops: int = 2
    append_tests: bool = True
    # Interpreter for the project's Python tests and probes (app.interpreter); None = detect.
    python: str | None = None
    # The branch reviews compare against by default (`behavior-review run`, the local UI); None = detect.
    base_branch: str | None = None
    source: str = "defaults"  # "defaults", "detected" (no config file), or the config file name
    extra: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not self.languages:
            object.__setattr__(self, "languages", (self.language,))

    def for_language(self, language: str) -> "BehaviorConfig":
        """Return the impact settings for one adapter in a mixed repository."""
        language = _normalize_language(language)
        defaults = LANGUAGE_DEFAULTS[language]
        extensions = self.extensions
        if len(self.languages) > 1:
            configured = tuple(extension for extension in extensions if extension in defaults["extensions"])
            extensions = configured or tuple(defaults["extensions"])
        return replace(self, language=language, languages=(language,), extensions=extensions)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "BehaviorConfig":
        if not isinstance(value, dict):
            raise ConfigError("behavior.json must contain a JSON object")

        def strings(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
            raw = value.get(name, default)
            if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) and item.strip() for item in raw):
                raise ConfigError(f"behavior.json field '{name}' must be a non-empty string array")
            return tuple(item.strip() for item in raw)

        if "languages" in value:
            raw_languages = value["languages"]
            if not isinstance(raw_languages, (list, tuple)) or not raw_languages:
                raise ConfigError("behavior.json field 'languages' must be a non-empty string array")
            languages = tuple(_normalize_language(item) for item in raw_languages)
            if "language" in value:
                primary = _normalize_language(value["language"])
                if primary not in languages:
                    raise ConfigError("behavior.json field 'language' must be included in 'languages'")
                languages = (primary, *(item for item in languages if item != primary))
        else:
            languages = (_normalize_language(value.get("language", cls.language)),)
        languages = tuple(dict.fromkeys(languages))
        language = languages[0]

        defaults = LANGUAGE_DEFAULTS[language]
        default_extensions = tuple(dict.fromkeys(
            extension
            for selected in languages
            for extension in LANGUAGE_DEFAULTS[selected]["extensions"]
        ))
        extensions = tuple(
            extension if extension.startswith(".") else f".{extension}"
            for extension in strings("extensions", default_extensions)
        )
        if not extensions:
            raise ConfigError("behavior.json field 'extensions' must not be empty")
        tests_dir = value.get("tests_dir", defaults["tests_dir"])
        if not isinstance(tests_dir, str) or not tests_dir.strip():
            raise ConfigError("behavior.json field 'tests_dir' must be a non-empty string")

        max_hops = value.get("max_hops", cls.max_hops)
        if not isinstance(max_hops, int) or isinstance(max_hops, bool) or max_hops < 1:
            raise ConfigError("behavior.json field 'max_hops' must be a positive integer")

        python = _optional_string(value, "python")

        known = {
            "language", "languages", "extensions", "changed_file_filter", "tests_dir", "test_command",
            "test_report", "probe_runner", "test_file_patterns", "max_hops", "append_tests", "python", "base_branch",
        }
        test_command = strings("test_command", defaults["test_command"])
        probe_runner = strings("probe_runner", defaults["probe_runner"])
        test_file_patterns = strings("test_file_patterns", defaults["test_file_patterns"])
        if not test_command or not probe_runner or not test_file_patterns:
            raise ConfigError("test_command, probe_runner, and test_file_patterns must not be empty")
        test_report = value.get("test_report", defaults["test_report"])
        if not isinstance(test_report, str) or test_report.strip().lower() not in SUPPORTED_TEST_REPORTS:
            allowed = ", ".join(sorted(SUPPORTED_TEST_REPORTS))
            raise ConfigError(f"behavior.json field 'test_report' must be one of: {allowed}")
        append_tests = value.get("append_tests", defaults["append_tests"])
        if not isinstance(append_tests, bool):
            raise ConfigError("behavior.json field 'append_tests' must be a boolean")
        return cls(
            language=language,
            languages=languages,
            extensions=extensions,
            changed_file_filter=strings("changed_file_filter", cls.changed_file_filter),
            tests_dir=tests_dir.strip().replace("\\", "/"),
            test_command=test_command,
            test_report=test_report.strip().lower(),
            probe_runner=probe_runner,
            test_file_patterns=test_file_patterns,
            max_hops=max_hops,
            append_tests=append_tests,
            python=python,
            base_branch=_optional_string(value, "base_branch"),
            extra={key: item for key, item in value.items() if key not in known},
        )


# Everything a repository keeps for behavior-review lives in one folder (`behavior-review init`
# creates it); the root-level layout it replaced is still read when the folder is absent.
HOME_DIR = ".behavior-review"
CONFIG_FILES = (f"{HOME_DIR}/config.json", "behavior.json")
PROBE_DIRS = (f"{HOME_DIR}/probes", "probes")
DECISION_DIRS = (f"{HOME_DIR}/decisions", "behavior_decisions")


def config_file(repo: str | Path) -> str | None:
    """The repository's config file, preferring `.behavior-review/config.json`; None if it has none."""
    return next((name for name in CONFIG_FILES if (Path(repo) / name).is_file()), None)


def load_config(repo: str | Path, filename: str | None = None) -> BehaviorConfig:
    """Load explicit configuration or detect adapters from a repository root.

    An explicit config file remains authoritative. When there is none, known
    manifests and source extensions select one or more adapters. A repository
    with no detectable language keeps the legacy Python defaults; a detected
    non-Python repository never silently falls back to Python.
    """

    filename = filename or config_file(repo)
    path = Path(repo) / filename if filename else None
    if path is None or not path.exists():
        return _auto_detect_config(Path(repo))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {filename}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {filename}: {exc.msg} at line {exc.lineno}") from exc
    return replace(BehaviorConfig.from_mapping(value), source=filename)


def load_revision_config(base_path: str | Path, head_path: str | Path) -> tuple[BehaviorConfig, list[str]]:
    """The base revision's configuration, plus a note when the change edits (or adds) a config file.

    Like the frozen test suite and probes, configuration comes from the base
    revision: a change must not be able to pick its own test command or scope.
    """
    notes = []
    for name in CONFIG_FILES:
        base_file, head_file = Path(base_path) / name, Path(head_path) / name
        base_bytes = base_file.read_bytes() if base_file.exists() else None
        head_bytes = head_file.read_bytes() if head_file.exists() else None
        if base_bytes != head_bytes:
            notes.append(f"{name} differs in this change; the base revision's configuration was used.")
    return load_config(base_path), notes


__all__ = ["BehaviorConfig", "CONFIG_FILES", "ConfigError", "DECISION_DIRS", "HOME_DIR", "PROBE_DIRS", "config_file",
           "detect_tests_dir", "load_config", "load_revision_config", "resolve_tests_dir"]
