# behavior-review

Green tests and a clean diff can still hide a broken caller. `behavior-review` compares two
commits and answers, with real execution evidence rather than AI opinion:

1. **What else could this change affect?** An AST impact graph finds callers up to 2 hops away,
   including callers in files the diff never touched.
2. **Did behavior actually change?** The base revision's test suite and the committed probes run
   with identical bytes on both revisions, and their outputs are compared.
3. **Was the change intended?** Each difference gets a human disposition; intended ones are recorded
   in a decision ledger and cited by later reviews.

> AI proposes. Algorithms verify. Humans decide.

See `FINAL_PLAN.md` for the full design.

## Install

Requires Python 3.11+ and git.

```bash
git clone https://github.com/webdev-testa/pocbobbin.git
cd pocbobbin
python -m venv .venv
```

Activate the environment, then install:

| Shell | Activate |
|---|---|
| Windows PowerShell | `.venv\Scripts\Activate.ps1` |
| Windows cmd | `.venv\Scripts\activate.bat` |
| Linux / macOS | `source .venv/bin/activate` |

```bash
pip install -e ".[dev]"    # drop [dev] if you don't need pytest
behavior-review --help
```

## Run

Run it from inside the repository you want to review. Both revisions must be commits:
uncommitted edits are not analyzed, and your working tree, index and current branch are never
touched (each revision is checked out into a temporary `git worktree`).

```bash
# Impact only: what changed and who calls it
behavior-review --base main --head HEAD --json report.json

# Plus paired execution: run the frozen tests and committed probes on both revisions
behavior-review --base main --head HEAD --run --json report.json --markdown report.md
```

With `--json` or `--markdown`, the files are written and a summary is printed. Without either,
the full report is printed to stdout as JSON.

### Try it on the demo scenarios

The sample package in `sample_project/` has one "before" revision (the `ref/base` tag) and one
"after" branch per scenario:

```bash
behavior-review --base ref/base --head origin/scenario1-head --run --json report.json
```

```
1cb1511..13ffdbb: 2 changed symbols, 5 impact paths, 1 non-test callers outside the diff, 0 unknowns
  outside diff: price_total → apply_discount  (sample_project/pricing/invoice.py:25)
  tests on base: ok (6 passed, 0 failed, 0 errors)
  tests on head: ok (6 passed, 0 failed, 0 errors)
  probe apply_discount_contract: delta_observed  100.0 -> 99.99
  probe apply_discount_policy_cap: same_on_tested_cases  60.0 -> 60.0
  probe price_total_boundary: delta_observed  100.0 -> 99.99
  prior decision 951cc25e49ee (approved) on sample_project/pricing/discount.py::apply_discount: intended — Business policy update: maximum allowable discount capped at 30% per Q3 pricing review.
```

The existing tests stay green on both sides, yet `price_total` in `invoice.py`, a file the diff never
touched, returns a different number for the same input.

| Head | Scenario | Expected result |
|---|---|---|
| `origin/scenario1-head` | Rounding "cleanup" breaks a caller in another file | Tests green, `delta_observed` on `price_total` |
| `origin/scenario2-head` | Intentional policy change (max discount 50% → 30%) | `delta_observed`, cites the approved decision |
| `origin/scenario3-head` | Behavior-preserving refactor | `same_on_tested_cases` |
| `origin/scenario4-head` | Broken setup | `inconclusive`, never "bug" |

### Repo map

```bash
behavior-review map --ref HEAD --out repo_map.json
```

Writes a file-level map of the whole repository at that revision: every source module with its
language and support tier, the import edges between modules, and each module's last mainline
commit (with its PR number when the subject names one). Imports that can't be resolved statically
are listed under `unknowns`. The GitHub Action uploads it next to `report.json`.

### Options

| Option | Default | Meaning |
|---|---|---|
| `--base` | `main` | Revision before the change |
| `--head` | `HEAD` | Revision after the change |
| `--repo` | `.` | Any path inside the git repository |
| `--max-hops` | `2` | How many caller levels to trace back from each changed symbol |
| `--run` | off | Also run the frozen base test suite and `probes/*.json` on both revisions |
| `--prior-report PATH` | — | An earlier `report.json`: a probe that showed a delta there and shows none now is linked to it (`reruns`), but only if the probe bytes are unchanged |
| `--json PATH` | stdout | Where to write the report JSON |
| `--markdown PATH` | — | Also write the report as Markdown (the PR comment body) |
| `--link NAME=URL` | — | Record where this run's evidence lives, e.g. `action_run=<CI run URL>` (repeatable) |

Exit code is `0` on success and `2` if a revision can't be resolved.

The report's shape is defined in `app/schemas.py` (`ReviewReport`); `contracts/report_scenario1.json`
is a labeled example.

## In Bob IDE: `/behavior-review`

`.bob/custom_modes.yaml` registers a **Behavior Review** custom mode. In it, Bob runs the CLI with
`--run` against the base branch you name (`/behavior-review release/2.0`; it asks if you don't),
first states each analyzed language's tier and what that tier can't show, explains the evidence,
writes a probe in that language's format (see the probe table below) for any impacted caller listed
in `needs_bob_action`, and helps fix unintended changes by rerunning the **unchanged** probe. It also
drafts `CHANGE_NOTES.md`, labeled "Drafted by Bob — not evidence", ending in questions only you can
answer. Bob never chooses the intent or writes a rationale for the author; for intended changes the
author's rationale is recorded with `app.decisions.validate_and_save`.

## On every PR: GitHub Action

`.github/workflows/behavior-review.yml` runs on each pull request push: it runs the CLI with `--run`
against the PR's base branch, uploads `report.json`, `report.md` and `repo_map.json` as a build
artifact (stamped with `links.action_run`, the URL of that run's public log), and creates or updates
one PR comment from `report.md` (the same renderer as `--markdown`). A comment over GitHub's size
limit is cut and points to the artifact; a failed install fails the job rather than reviewing with a
half-installed tool. It reuses committed probes and never calls Bob; if an impacted caller has no
probe, the comment says so (`needs_bob_action`).

## Decision ledger

Intended behavior changes are stored as JSON in `behavior_decisions/`, one file per decision
(written by Bob, or by **Download decision** in the web viewer). A decision committed on a PR
branch is listed in that PR's own report under `decisions` as proposed; it becomes approved when
that PR is merged, since every review reads the ledger from its base revision. Every later review
lists matching decisions (same file path and symbol) under `prior_decisions`, including superseded
ones, labeled as such. A new decision supersedes the current one for its function: the record no
other record supersedes (file names are hashes, so their order says nothing about age). History
informs a review; it never approves a new difference.

## Web evidence viewer

`web/` is a static React viewer for a report: an evidence map (callers → changed code, nested by
folder and file, colored by what execution showed), old vs new outputs, decisions, and a repo map
of every file's imports. It ships the unmodified `report.json` and `repo_map.json` artifact of the
Action run on the Scenario 1 demo PR, and links to that run's public log. **Open report…** views any
other run's files in the browser, without uploading them. **Download decision** writes the ledger
record for `behavior_decisions/`; the page itself saves and approves nothing. See `web/README.md`
to run or deploy it.

## Limits

- **Static impact.** Calls through variables, dynamic dispatch or class hierarchies may be missed;
  unresolved references that could reach a changed symbol are listed as `unknowns`, never as safe.
- **Bounded depth.** Callers are traced up to `--max-hops` levels.
- **Tested cases only.** `same_on_tested_cases` means identical output for the frozen inputs, not
  proof of equivalence for all inputs.
- **Honest failures.** Setup errors, import errors and timeouts are `inconclusive`, never "bug".
- **Scope.** Static support covers the configured language adapters; real behavior evidence still
  depends on the repository's installed toolchain and committed deterministic probes.

## Run the tests

```bash
pytest -q
```

## Language support

The CLI auto-detects supported languages from repository manifests and source extensions. A
missing `behavior.json` selects one or more static adapters; a mixed repository is analyzed by
each detected adapter and the evidence is merged. Python remains the fallback only when no
language evidence is detectable. Explicit `behavior.json` always wins and is recommended when a
repository has a custom test/probe toolchain. Tree-sitter is installed with the package because
detection may select a non-Python adapter.

Each language has one support tier, declared in `app/adapters/registry.py`. Every report states
the languages analyzed and their tiers under `analysis`, and adds a warning to `limits` for any
language below `full`.

| Tier | Languages | What is verified today |
|---|---|---|
| `full` | Python | Cross-file impact, paired execution, the real demo scenarios end to end |
| `static_probe` | TypeScript, JavaScript | Cross-file impact and unknowns; a TS probe harness and Vitest parser exist, no real end-to-end run yet |
| `static_cross_file` | Java, C#, Go | Cross-file caller resolution (tested) |
| `experimental` | C, C++, Rust, PHP, Kotlin, Ruby, Swift, Dart, Bash | Same-file callers only; name-based matching can miss or invent edges |

Add `behavior.json` at the repository root to override detection, select a primary test profile,
or provide real test/probe commands. Like the frozen tests and probes, it is read from (and
detection runs on) the **base** revision, so a change cannot pick its own test command; if the
change edits it, the report says so. Commands are argument arrays, not shell strings:

```json
{
  "language": "typescript",
  "extensions": [".ts", ".tsx", ".js"],
  "tests_dir": "tests",
  "test_command": ["npx", "vitest", "run", "--reporter=json"],
  "test_report": "vitest-json",
  "probe_runner": ["npx", "tsx", "tools/run_probe.ts"],
  "test_file_patterns": ["*.test.ts", "*.spec.ts"],
  "max_hops": 2
}
```

For a mixed repository, use `languages` instead of `language`; the first entry is the primary
runtime/test profile and all entries participate in static impact analysis:

```json
{
  "languages": ["typescript", "python"],
  "test_command": ["npm", "test", "--", "--reporter=json"],
  "test_report": "vitest-json"
}
```

The adapter resolves direct, statically visible calls. Dynamic dispatch, reflection, unresolved
imports, generated code, macros, and unsupported build behavior remain unknown or inconclusive;
they are never treated as proof of no impact. The configured runtime and build tool must be
installed by the reviewed repository.

### Probe formats

A probe is one JSON file in `probes/`; the same bytes run on both revisions. Its `target` joins
the outcome to a node in the impact graph by `(path, symbol)`, so the symbol must be spelled the way
the report spells it in `impact`: `Class.method` for a method, the bare name for a top-level
function. Outside Python, use the object form `{"path", "symbol"}`: the `"module:symbol"` string
form guesses the file extension from the first configured one.

| Language | Runner (`probe_runner`) | Probe |
|---|---|---|
| Python | `tools/run_probe.py` (default) | `{"id": "price_total_boundary", "target": "sample_project.pricing.invoice:price_total", "args": [[...], 5.0]}` |
| TypeScript, JavaScript | `["npx", "tsx", "tools/run_probe.ts"]` | `{"id": "price_total", "target": {"path": "src/invoice.ts", "symbol": "priceTotal"}, "args": [[...], 5]}` (an exported function) |
| Java, C#, Go and the rest | `tools/run_command_probe.py` | `{"id": "apply", "target": {"path": "src/Discount.java", "symbol": "Discount.apply"}, "command": ["java", "-cp", "build", "ProbeMain", "{input}"], "input": {"args": [105.26, 5.0]}}` |

The command form runs your own entry point with the probe's `input` as JSON in place of `{input}`
and expects the result as JSON on stdout; no shell is involved. A probe whose code can't be loaded
is inconclusive, not a behavior difference.

Language-specific test report names include `pytest-text`, `vitest-json`, `junit-xml`, `trx-xml`,
`go-test-json`, `ctest-text`, `cargo-text`, `phpunit-text`, `rspec-json`, `swift-text`,
`dart-json`, and `shell-text`. An unparseable report is inconclusive rather than a passing
result (the suite is recorded as `error`; `tests/test_runner_config.py` checks this per language). Every configured language has an explicit entry in `app/adapters/registry.py`; the
Tree-sitter implementation is shared, but language selection is not implicit.

## License

MIT — see `LICENSE`.
