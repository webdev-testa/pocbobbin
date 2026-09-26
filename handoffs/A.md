# Handoff — Lane A (Analysis)

**Branch / SHA:** not committed yet (repo had no commits at start).

## Works
- `app/schemas.py` — all shared models (`ReviewReport` is the single object every door consumes).
  `RevisionPair` is runtime-only: `repo` slug, local `root`/`base_path`/`head_path`, plus
  `revisions` (refs, SHAs, changed files) — only `repo` and `revisions` go into a report.
  `Comparison` embeds its `Probe`; `Decision` uses `target: SymbolRef`.
- `app/snapshot.py` — `open_pair(repo, base, head)` context manager: detached `git worktree`s in a temp
  dir, removed afterwards. Working tree, index and current branch untouched (tested).
- `app/impact.py` — `analyze(pair, max_hops=2)`:
  - Symbols: functions, methods (`Class.method`), module/class-level assigned names, and `<module>`
    (remaining top-level code + imports). Tags: added / removed / signature_changed / body_changed /
    decorators_changed / imports_changed. Docstring-only edits are not changes.
  - Edges: every load of a resolvable symbol (calls, callback references, `X = helper()`), absolute +
    relative imports, `import pkg.mod` chains, `self.method`, star imports, package re-exports.
    Modules are named from their package root (`sample_project/pricing/discount.py` → `pricing.discount`);
    a name claimed by two files is ambiguous and never guessed.
  - Paths: reverse callers up to `max_hops`, union of base+head edges; `outside_diff`, `is_test` flags;
    non-test callers outside the diff sort first.
  - Unknowns: any unresolved reference or `getattr(x, "name")` whose name matches a changed symbol;
    unparseable files.
- `app/cli.py` — `pipeline(repo, base, head)` + `behavior-review --base --head --json`.
- `contracts/report_scenario1.json` — fixture for C and D (validated by `tests/test_contracts.py`).

## Checks run
`pytest -q` → 15 passed (assigned-name caller, ambiguous module → unknown, scenario 1 caller outside diff, 2 hops, max-hops bound, constant change,
relative import + callback, unknowns, syntax error, add/remove/signature, docstring-only, snapshot
isolation, bad revision error, CLI report has no local paths).
Manual: CLI on a throwaway repo with the scenario 1 change printed `price_total → apply_discount`
(invoice.py:5) and `checkout → price_total → apply_discount` as callers outside the diff.

## Next
- Sync 1: call B's `runner.compare(pair, bundle)` inside `pipeline`'s `with open_pair(...)` block;
  fill `tests`, `comparisons`, `needs_bob_action`; drop the "no tests or probes executed" limit when they ran.
- Run on B's real `sample_project/` scenario branches once they exist.
- Confirm with B/D: exact sample functions (§15.5) — fixture assumes `apply_discount(prices, pct)` and
  `price_total(prices, pct)`.

## Blockers
None.
