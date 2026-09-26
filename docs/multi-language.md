# Multi-language support: design and plan

How to make Behavior Review analyse and probe a codebase in any language. This is a
**design document plus an ordered plan**, not a description of shipped behaviour: the
implemented analyser is Python-only, and everything below says what changes and in what order.

> Status: **not implemented.** Python is the only supported language today. Claims marked
> *measured* were verified hands-on against this repo or against upstream package metadata;
> claims marked *planned* are design intent.

---

## 1. Why this is a small change and not a rewrite

The engine is already language-neutral except for two components. Measured against this repo:

| File | Lines | Language-specific? |
|---|---|---|
| `app/impact.py` | 427 | **Yes.** `import ast` appears 72×, plus `.py` filters, `__init__.py` package logic |
| `app/runner.py` | 301 | **Yes, thinly.** Runs `python -m pytest`, parses pytest's *text* tail |
| `app/snapshot.py` | 104 | No. Plain `git worktree` |
| `app/decisions.py` | 337 | No. Pure JSON ledger |
| `app/schemas.py` | 223 | No. Paths, symbols, line numbers, JSON diffs |
| `web/` | — | No. Renders paths, symbols and JSON diffs |

The seam is narrower than it looks: `app/impact.py` exposes exactly **one public function** —
`analyze` — with its other 24 module-level functions all underscore-private:

```
analyze(pair: RevisionPair, max_hops: int = 2) -> ImpactResult
```

and every model it returns (`ChangedSymbol`, `Edge`, `ImpactPath`, `Unknown`, `SymbolRef`) is
already language-neutral. So the swap point is one function, not an architecture change.

```
            Language-agnostic
  git worktree ──► impact graph ──► paired runner ──► ledger ──► web UI
                        │                 │
                        ▼                 ▼
              [1] parser adapter   [2] test + probe adapter
                  (per language)       (per language)
```

---

## 2. Verified findings that change the obvious plan

These are the parts a reasonable first draft gets wrong. All *measured*.

### 2.1 Do not use `tree-sitter-languages`

The obvious package is dead. PyPI metadata:

| Package | Latest | Uploaded | Releases in last 12 months |
|---|---|---|---|
| `tree-sitter-languages` | 1.10.2 | 2024-02-04 | **0** |
| `tree-sitter-language-pack` | 1.20.0 | 2026-09-14 | **369** |
| `tree-sitter` (core) | 0.26.0 | 2026-06-30 | 41 |

`tree-sitter-languages` has been unmaintained for roughly two and a half years. Use
**`tree-sitter-language-pack`** (371 pre-compiled grammars). *Measured:* it parsed
TypeScript, TSX, JavaScript, Java, Go and Rust successfully in this environment.

### 2.2 SCIP has no Rust or Go binding

SCIP is the production-grade option and is genuinely good, but its official bindings are:

```
scip-java        Java, Scala, Kotlin
scip-typescript  TypeScript, JavaScript
scip-clang       C++, C
scip-ruby        Ruby
scip-python      Python
scip-dotnet      C#, Visual Basic
scip-dart        Dart
scip-php         PHP
```

**No Rust and no Go** on that list. Rust's real path is `rust-analyzer`, which does emit SCIP
(`crates/rust-analyzer/src/cli/scip.rs` in `rust-lang/rust-analyzer`); Go's is
`scip-code/scip-go`. Both work, but they are integrations to build, not a checkbox.
*Measured:* taken from the SCIP README and repository trees, not assumed.

### 2.3 The probe contract is genuinely language-neutral

A probe is JSON in, JSON out. The entire Python probe surface is **45 lines**
(`tools/run_probe.py`: `importlib.import_module` + `getattr`). The same probe file needs no
language field, because only the harness needs to know how to execute it.

*Measured:* a TypeScript project with the same shape as our Python demo (a helper, and a caller
in another file) resolved `invoice.ts:priceTotal → discount.ts:applyDiscount` under tree-sitter,
and a ~30-line Node harness returned the same contract shape as `tools/run_probe.py`:

```
{"probe":"price_total_boundary","outcome":"value","value":100}
```

### 2.4 The extensionless-import trap

TypeScript imports are conventionally written without an extension (`from "./discount"`).
Node cannot resolve those, and in testing neither `tsx` nor
`node --experimental-strip-types` fixed it for an import *inside* the probed module:

```
{"probe":"price_total_boundary","outcome":"exception","error_type":"Error",
 "error":"Cannot find module '/tmp/.../src/discount' imported from .../invoice.ts"}
```

It succeeded as soon as the import carried `.ts`. *Measured.* Consequence: a TypeScript harness
must run under a tsc-style resolver (or the project must be configured to require explicit
extensions). Without this, probes silently return `inconclusive` and the tool looks like it is
working while proving nothing.

### 2.5 The runner needs two indirections, not one

A `test_command` string is not enough. `run_suite` currently scrapes pytest's **text** output:

```python
if " passed" in tail: passed = int(tail.split(" passed")[0].split()[-1])
if " failed" in tail: failed = int(tail.split(" failed")[0].split()[-1].split("=")[-1].strip(" ,"))
```

`npx vitest run --reporter=json` returns JSON, so the counts need a **result parser per
reporter**, or they silently become zero and the report claims a green suite that never ran.

---

## 3. What a language adapter must preserve

These are the properties that make the report trustworthy. An adapter that breaks any of them
produces a tool that lies, which is worse than one that supports fewer languages.

1. **`Unknown` emission.** Anything the parser cannot resolve must surface as
   `Unknown(reason=...)`, never vanish. A tree-sitter query drops unresolvable references
   silently, which would turn the project's rule *"unknown ≠ no impact"* into *"unknown =
   invisible"* — precisely the failure this tool exists to prevent.
2. **Frozen bytes.** `_hash_files` currently hashes `**/*.py` for the test suite, the harness is
   copied into both worktrees, and rerun linking refuses to link if the probe hash changed.
   An adapter must hash by the **configured extensions** and still inject its harness into both
   revisions, or the anti-tampering guarantee disappears.
3. **The `<module>` symbol** for top-level code, or the added/removed diffing changes shape and
   the impact graph degrades.
4. **`is_test` classification**, currently hardcoded to `test_*.py`, `_test.py`, `conftest.py`.
   It must come from configuration per language.
5. **Symbol shape for the Bob hand-off.** `needs_bob_action` keys on `(path, symbol)` taken from
   probe targets, so every adapter must emit symbols in that same shape or Bob's prompts break.
6. **Inconclusive stays inconclusive.** Import errors, timeouts and unparsable output must remain
   `inconclusive`, never a verdict.

---

## 4. Project configuration

Add `behavior.json` at the repo root. Defaults reproduce today's Python behaviour exactly, so
an existing project needs no file at all.

```json
{
  "language": "typescript",
  "extensions": [".ts", ".tsx", ".js"],
  "changed_file_filter": ["src/**", "lib/**"],
  "tests_dir": "tests",
  "test_command": ["npx", "vitest", "run", "--reporter=json"],
  "test_report": "vitest-json",
  "probe_runner": ["npx", "tsx", "tools/run_probe.ts"],
  "test_file_patterns": ["*.test.ts", "*.spec.ts"],
  "max_hops": 2
}
```

| Field | Purpose | Python default |
|---|---|---|
| `language` | selects the parser adapter | `"python"` |
| `extensions` | which files are analysed, and what `_hash_files` hashes | `[".py"]` |
| `changed_file_filter` | ignore generated/vendored trees | none |
| `tests_dir` | the suite that gets frozen | `sample_project/tests` |
| `test_command` | argv list, not a shell string | `["python","-m","pytest","-q","--no-header"]` |
| `test_report` | which result parser to use | `"pytest-text"` |
| `probe_runner` | argv list for the harness | `["python","tools/run_probe.py"]` |
| `test_file_patterns` | drives `is_test` | `["test_*.py","*_test.py","conftest.py"]` |

`test_command` and `probe_runner` are **argv lists**, never shell strings: no quoting bugs, and
no accidental shell interpolation in a tool that runs untrusted branch code.

---

## 5. Implementation plan

Ordered so that every step is independently verifiable and revertible. Sizes are working
estimates, not commitments.

### Slice 0 — configuration (≈30 min, no risk)

`behavior.json` loader with Python defaults. Nothing dispatches on it yet.
**Acceptance:** existing suite green; new tests cover defaults and loading.

### Slice 1 — the dispatch seam (≈1 h, the risky one)

Split `app/impact.py` into a package:

```
app/impact/__init__.py     analyze() dispatches on config["language"]
app/impact/python_ast.py   the existing code, MOVED, unmodified
app/impact/tree_sitter.py  new adapter (initially a clear "not configured" error)
```

**Acceptance: the existing test suite passes with no test-file edits.** That is the proof the
refactor was behaviour-preserving. If a test needed changing, the refactor was not a refactor.

Note: `app/impact.py` is lane A's file per `AGENTS.md`, so this slice should go through its
owner.

### Slice 2 — TypeScript analyser (≈1.5 h)

Tree-sitter queries for function/method definitions, call sites, imports and exports, feeding
the existing `ImpactResult` models.
**Acceptance:** a real fixture under `tests/fixtures/ts_project/` yields the cross-file caller
(`priceTotal → applyDiscount`), and a test asserts an **unresolvable reference emits `Unknown`**
rather than disappearing.

### Slice 3 — runner adapters (≈1 h)

`test_command` execution plus a result parser per `test_report` value (`pytest-text`,
`vitest-json`), and `tools/run_probe.ts`.
**Acceptance:** a TypeScript probe produces both `value` and `exception` results through the
existing `Comparison` model, and a suite run reports correct pass/fail counts from JSON.

### Slice 4 — documentation (≈30 min)

Correct the ecosystem notes above in the submission docs; document the extensionless-import
trap and the optional dependency.

Total ≈4.5 h. **Slice 2 is a hard dependency for slices that need real TypeScript; slices 0 and 1
stand alone.**

### Cut order if time runs out

1. Slice 4 docs
2. Slice 3 (analysis-only is still demonstrable)
3. Slice 2 entirely, shipping slices 0+1 as *"language-adapter interface; Python implemented"* —
   honest, and still a real architectural result rather than a claim.

---

## 6. Dependencies

`tree-sitter` and `tree-sitter-language-pack` are only needed for non-Python analysis, so they
belong behind an extra to keep the base install light:

```toml
[project.optional-dependencies]
multilang = ["tree-sitter>=0.26", "tree-sitter-language-pack>=1.20"]
```

`pyproject.toml` is lane A's file. The package name matters: `tree-sitter-languages` is the dead
one (§2.1).

---

## 7. What would still not be covered

Stated plainly so the tool does not overclaim:

- **Dynamic dispatch.** Calls through variables, reflection, `getattr`, dependency injection and
  class hierarchies remain unresolvable. They must be reported as `Unknown`, not as safe.
- **Types as call paths.** Interfaces, generics and overload resolution are beyond a syntax-tree
  adapter. SCIP-style indexing is the route to that precision, and only where a SCIP indexer
  actually exists (§2.2).
- **Polyglot repos.** One `behavior.json` per repo means one analysed language. A repo with both
  TypeScript and Python would need per-language config and a merged impact graph, which is not
  designed here.
- **Build-system awareness.** Whether a project even runs under `test_command` is assumed, not
  verified; a failure to run stays `inconclusive`.
- **Non-UTF-8 and exotic encodings.** The current readers assume UTF-8.

---

## 8. Why Python-only remains the default

This document describes a design, and the shipped analyser is Python-only on purpose. The four
demo scenarios are Python revisions with recorded runs, and the deferred-scope note in the
project plan says so openly: *"Deferred (say so honestly): languages other than Python"*. A tool
whose value rests on *"only real process output counts"* is better served by a narrow, verified
capability than a broad, untested one.

If multi-language support is built, build slices 0 and 1 first: they make the architecture
honest at low risk, and they are the parts that stay valuable even if no second language ships.
