from pathlib import Path

import pytest

from app.cli import pipeline
from app.impact import analyze
from app.snapshot import open_pair


def git(repo: Path, *args: str) -> str:
    import subprocess

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
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def make_typescript_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "base")
    write_files(
        repo,
        {
            "behavior.json": '{"language":"typescript","extensions":[".ts"],"tests_dir":"tests","test_file_patterns":["*.test.ts"]}',
            "src/discount.ts": "export function applyDiscount(value: number) { return value; }\n",
            "src/invoice.ts": 'import { applyDiscount } from "./discount";\nexport function priceTotal(value: number) { return applyDiscount(value); }\n',
            "src/legacy.ts": "export function legacy(value: number) { return applyDiscount(value); }\n",
            "tests/discount.test.ts": 'import { applyDiscount } from "../src/discount";\ntest("discount", () => applyDiscount(1));\n',
        },
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "head")
    write_files(repo, {"src/discount.ts": "export function applyDiscount(value: number) { return Math.round(value * 100) / 100; }\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "head")
    return repo


def test_typescript_adapter_finds_changed_symbol_and_caller(tmp_path: Path):
    repo = make_typescript_repo(tmp_path)
    with open_pair(repo, "base", "head") as pair:
        impact = analyze(pair)
    assert [(item.path, item.symbol) for item in impact.changed_symbols] == [("src/discount.ts", "applyDiscount")]
    assert any(path.render() == "priceTotal → applyDiscount" and path.outside_diff for path in impact.paths)


def test_typescript_adapter_emits_unknown_for_unresolved_changed_reference(tmp_path: Path):
    repo = make_typescript_repo(tmp_path)
    with open_pair(repo, "base", "head") as pair:
        impact = analyze(pair)
    assert any(unknown.path == "src/legacy.ts" and unknown.reason == "unresolved reference" for unknown in impact.unknowns)


def test_cli_pipeline_uses_repository_language_config(tmp_path: Path):
    repo = make_typescript_repo(tmp_path)
    report = pipeline(repo, "base", "head")
    assert report.impact.changed_symbols[0].symbol == "applyDiscount"
    assert report.impact.paths[0].render() == "priceTotal → applyDiscount"


def test_auto_detected_mixed_adapters_merge_static_evidence(tmp_path: Path):
    repo = tmp_path / "mixed"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "base")
    write_files(
        repo,
        {
            "package.json": "{}",
            "tsconfig.json": "{}",
            "src/discount.ts": "export function apply(value: number) { return value; }\nexport function total(value: number) { return apply(value); }\n",
            "src/discount.py": "def apply(value):\n    return value\n\ndef total(value):\n    return apply(value)\n",
            "src/invoice.ts": "import { apply } from \"./discount\";\nexport function invoice(value: number) { return apply(value); }\n",
            "src/invoice.py": "from .discount import apply\n\ndef invoice(value):\n    return apply(value)\n",
        },
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "head")
    write_files(
        repo,
        {
            "src/discount.ts": "export function apply(value: number) { return value + 1; }\nexport function total(value: number) { return apply(value); }\n",
            "src/discount.py": "def apply(value):\n    return value + 1\n\ndef total(value):\n    return apply(value)\n",
        },
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "head")

    with open_pair(repo, "base", "head") as pair:
        impact = analyze(pair)

    changed = {(item.path, item.symbol) for item in impact.changed_symbols}
    assert changed == {("src/discount.py", "apply"), ("src/discount.ts", "apply")}
    assert any(path.render().endswith("→ apply") and path.outside_diff for path in impact.paths)


LANGUAGE_FIXTURES = {
    "java": (".java", """
package demo;
public class Discount {
    public static int apply(int value) { return value; }
    public static int total(int value) { return apply(value); }
}
""", "public static int apply(int value) { return value + 1; }"),
    "csharp": (".cs", """
namespace Demo;
public class Discount {
    public static int Apply(int value) { return value; }
    public static int Total(int value) { return Apply(value); }
}
""", "public static int Apply(int value) { return value + 1; }"),
    "go": (".go", """
package discount
func Apply(value int) int { return value }
func Total(value int) int { return Apply(value) }
""", "func Apply(value int) int { return value + 1 }"),
    "cpp": (".cpp", """
int apply(int value) { return value; }
int total(int value) { return apply(value); }
""", "int apply(int value) { return value + 1; }"),
    "c": (".c", """
int apply(int value) { return value; }
int total(int value) { return apply(value); }
""", "int apply(int value) { return value + 1; }"),
    "rust": (".rs", """
fn apply(value: i32) -> i32 { value }
fn total(value: i32) -> i32 { apply(value) }
""", "fn apply(value: i32) -> i32 { value + 1 }"),
    "php": (".php", """
<?php
class Discount {
    public function apply($value) { return $value; }
    public function total($value) { return $this->apply($value); }
}
""", "public function apply($value) { return $value + 1; }"),
    "kotlin": (".kt", """
fun apply(value: Int): Int = value
fun total(value: Int): Int = apply(value)
""", "fun apply(value: Int): Int = value + 1"),
    "ruby": (".rb", """
def apply(value)
  value
end
def total(value)
  apply(value)
end
""", "  value + 1"),
    "swift": (".swift", """
func apply(value: Int) -> Int { return value }
func total(value: Int) -> Int { return apply(value) }
""", "func apply(value: Int) -> Int { return value + 1 }"),
    "dart": (".dart", """
int apply(int value) { return value; }
int total(int value) { return apply(value); }
""", "return value + 1;"),
    "bash": (".sh", """
apply() { true; }
total() { apply; }
""", "apply() { false; }"),
}

ORIGINAL_DEFINITIONS = {
    "java": "public static int apply(int value) { return value; }",
    "csharp": "public static int Apply(int value) { return value; }",
    "go": "func Apply(value int) int { return value }",
    "cpp": "int apply(int value) { return value; }",
    "c": "int apply(int value) { return value; }",
    "rust": "fn apply(value: i32) -> i32 { value }",
    "php": "public function apply($value) { return $value; }",
    "kotlin": "fun apply(value: Int): Int = value",
    "ruby": "  value",
    "swift": "func apply(value: Int) -> Int { return value }",
    "dart": "return value;",
    "bash": "apply() { true; }",
}


@pytest.mark.parametrize("language", sorted(LANGUAGE_FIXTURES))
def test_group_two_and_three_adapters_find_changed_symbol_and_caller(tmp_path: Path, language: str):
    extension, base_source, changed_definition = LANGUAGE_FIXTURES[language]
    repo = tmp_path / language
    repo.mkdir()
    git(repo, "init", "-q", "-b", "base")
    write_files(repo, {
        "behavior.json": f'{{"language":"{language}","extensions":["{extension}"],"tests_dir":"tests"}}',
        f"src/discount{extension}": base_source,
    })
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "head")
    write_files(repo, {f"src/discount{extension}": base_source.replace(
        ORIGINAL_DEFINITIONS[language], changed_definition, 1,
    )})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "head")
    with open_pair(repo, "base", "head") as pair:
        impact = analyze(pair)
    assert impact.changed_symbols
    changed = next(item for item in impact.changed_symbols if item.symbol.lower().endswith("apply"))
    assert changed.path == f"src/discount{extension}"
    assert any("total" in path.render().lower() and changed.symbol.lower() in path.render().lower() for path in impact.paths)


@pytest.mark.parametrize(
    "language, files, changed_file, old, new, caller_fragment",
    [
        (
            "java",
            {
                "src/demo/Discount.java": "package demo; public class Discount { public static int apply(int value) { return value; } }",
                "src/demo/Invoice.java": "package demo; import demo.Discount; public class Invoice { public static int total(int value) { return Discount.apply(value); } }",
            },
            "src/demo/Discount.java",
            "return value;",
            "return value + 1;",
            "total",
        ),
        (
            "csharp",
            {
                "src/Demo/Discount.cs": "namespace Demo; public class Discount { public static int Apply(int value) { return value; } }",
                "src/Invoice.cs": "using Demo; public class Invoice { public static int Total(int value) { return Discount.Apply(value); } }",
            },
            "src/Demo/Discount.cs",
            "return value;",
            "return value + 1;",
            "Total",
        ),
        (
            "go",
            {
                "go.mod": "module example.com/demo\n\ngo 1.22\n",
                "discount/discount.go": "package discount\nfunc Apply(value int) int { return value }\n",
                "invoice/invoice.go": "package invoice\nimport \"example.com/demo/discount\"\nfunc Total(value int) int { return discount.Apply(value) }\n",
            },
            "discount/discount.go",
            "return value",
            "return value + 1",
            "Total",
        ),
    ],
)
def test_group_two_cross_file_imports_resolve(
    tmp_path: Path,
    language: str,
    files: dict[str, str],
    changed_file: str,
    old: str,
    new: str,
    caller_fragment: str,
):
    repo = tmp_path / f"cross-{language}"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "base")
    write_files(repo, {
        "behavior.json": f'{{"language":"{language}","extensions":["{Path(changed_file).suffix}"],"tests_dir":"tests"}}',
        **files,
    })
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "head")
    write_files(repo, {changed_file: files[changed_file].replace(old, new, 1)})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "head")
    with open_pair(repo, "base", "head") as pair:
        impact = analyze(pair)
    changed = next(item for item in impact.changed_symbols if item.symbol.lower().endswith("apply"))
    assert any(caller_fragment.lower() in path.render().lower() and path.outside_diff for path in impact.paths), (
        [path.render() for path in impact.paths],
        [unknown.model_dump() for unknown in impact.unknowns],
    )


@pytest.mark.parametrize(
    "language, extension, source, old, new",
    [
        (
            "php", ".php",
            "<?php\nclass Discount {\n    public function apply($value) { return $value; }\n"
            "    public function total($value) { return apply($value); }\n}\n",
            "return $value; }", "return $value + 1; }",
        ),
        (
            "typescript", ".ts",
            "export class Discount {\n  apply(value: number) { return value; }\n"
            "  total(value: number) { return apply(value); }\n}\n",
            "return value; }", "return value + 1; }",
        ),
    ],
)
def test_bare_call_is_not_a_sibling_method_without_implicit_receiver(
    tmp_path: Path, language: str, extension: str, source: str, old: str, new: str
):
    """In PHP and TS a bare `apply()` names a free function, not `$this->apply()`: no edge, only an unknown."""
    repo = tmp_path / f"bare-{language}"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "base")
    write_files(repo, {
        "behavior.json": f'{{"language":"{language}","extensions":["{extension}"],"tests_dir":"tests"}}',
        f"src/discount{extension}": source,
    })
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "head")
    write_files(repo, {f"src/discount{extension}": source.replace(old, new, 1)})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "head")
    with open_pair(repo, "base", "head") as pair:
        impact = analyze(pair)
    assert "Discount.apply" in [c.symbol for c in impact.changed_symbols]
    assert not any("total" in path.render().lower() for path in impact.paths)
    assert any(u.symbol == "Discount.total" and u.expression == "apply" for u in impact.unknowns)

def test_typescript_report_states_language_tier_and_its_limit(tmp_path: Path):
    report = pipeline(make_typescript_repo(tmp_path), "base", "head")

    typescript = {"language": "typescript", "adapter": "tree-sitter", "tier": "static_probe"}
    assert report.analysis.model_dump() == {**typescript, "config_source": "behavior.json", "languages": [typescript], "runtime": None}
    assert any("'typescript' is supported at tier 'static_probe'" in limit for limit in report.limits)

def test_mixed_repository_reports_every_language_and_warns_per_weaker_tier(tmp_path: Path):
    repo = tmp_path / "mixed"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "base")
    write_files(repo, {
        "behavior.json": '{"languages": ["python", "typescript"]}',
        "pkg/core.py": "def f():\n    return 1\n",
        "src/discount.ts": "export function applyDiscount(value: number) { return value; }\n",
    })
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "head")
    write_files(repo, {"pkg/core.py": "def f():\n    return 2\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "head")

    report = pipeline(repo, "base", "head")

    assert (report.analysis.language, report.analysis.tier) == ("python", "full")
    assert [(s.language, s.tier) for s in report.analysis.languages] == [("python", "full"), ("typescript", "static_probe")]
    warnings = [limit for limit in report.limits if "is supported at tier" in limit]
    assert len(warnings) == 1 and "'typescript'" in warnings[0]

def test_typescript_caller_found_through_a_parent_relative_import(tmp_path: Path):
    """`../lib/discount` must resolve; before normalizing `..` it matched no file and the caller was lost."""
    repo = tmp_path / "parent-relative"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "base")
    write_files(repo, {
        "src/lib/discount.ts": "export function applyDiscount(value: number) { return value; }\n",
        "src/components/invoice.ts": 'import { applyDiscount } from "../lib/discount";\n'
                                     "export function priceTotal(value: number) { return applyDiscount(value); }\n",
    })
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "head")
    write_files(repo, {"src/lib/discount.ts": "export function applyDiscount(value: number) { return value + 1; }\n"})
    git(repo, "commit", "-qam", "head")

    with open_pair(repo, "base", "head") as pair:
        impact = analyze(pair)

    assert any(path.render() == "priceTotal → applyDiscount" and path.outside_diff for path in impact.paths)