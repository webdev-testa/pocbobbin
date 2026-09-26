## What this adds (lane B: execution engine)

Paired execution on two revisions, wired into lane A's shared engine so one command produces the first complete real `ReviewReport`.

```bash
pip install -e ".[dev]"
python -m app.cli --repo . --base origin/base --head origin/scenario1-head --run
```

The scenario revisions are published as branches: `base`, `scenario1-head`, `scenario3-head`, `scenario4-head`. Resolve them as `origin/<name>` in a fresh clone.

Verified on a fresh clone of this branch: **27 passed**, and the CLI produces the report below.

### Changes

- **`app/runner.py`** — implements the frozen contracts `compare(pair, bundle) -> (tests, comparisons, needs_bob_action)` and `pipeline()`. Worktrees both revisions, freezes the BASE test suite and the probe runner, runs those identical bytes on both sides, runs identical probe bytes on both sides, classifies, returns one report.
- **`app/cli.py`** — `--run` flag. `_limits()` no longer claims "no tests or probes were executed" once paired execution has actually run, and reports unprobed impacted callers. Also counts distinct caller sites in the summary (bug fix, below).
- **`sample_project/`** — the demo package: `apply_discount` (the helper the demo PR touches), `invoice.price_total` (the caller deliberately left out of the diff), and a frozen test suite that stays green on both revisions.
- **`probes/` + `tools/run_probe.py`** — the probe contract for the Bob mode and the GitHub Action: `{id, target: module:function, args, note}` in, `{outcome, value|error}` out.
- **`contracts/probebundle_scenario1.json`** — the probes expressed in lane A's `ProbeBundle` schema.
- **`tests/test_runner.py`** — 9 tests covering the report, `needs_bob_action`, and impact behaviour.

### Real results, not fixtures

| Scenario | diff | frozen suite | probe | base to head | outcome |
|---|---|---|---|---|---|
| 1 rounding change | 1 file | 6 passed base, 6 passed head | `price_total_boundary` | 100.0 to 99.99 | `delta_observed` |
| 1 | | | `apply_discount_contract` | 100.0 to 99.99 | `delta_observed` |
| 3 refactor | 1 file | 6 passed both | both probes | 100.0 to 100.0 | `same_on_tested_cases` |
| 4 broken setup | 1 file | 6 passed base, 1 error head | both probes | value to runner failure | `inconclusive` |

Scenario 1 is the demo: the suite is green on both revisions while a caller in another file returns a different number for the same input. The impact graph names it, `price_total → apply_discount` at `sample_project/pricing/invoice.py:25`, outside the diff.

`needs_bob_action` fires when a caller has no committed probe. Import errors, timeouts and unparsable output classify as `inconclusive`, never as a bug.

`python -m pytest` → **27 passed** (lane A's 15 plus 12 here).

Verified on a fresh clone of this branch, not just a working copy: `git clone`, `pip install -e ".[dev]"`, `pytest` → 27 passed, and the CLI run above reproduces the scenario 1 delta.

### Bug fix in lane A's summary line

The CLI summary counted impact **paths**, not caller sites. When a refactor adds private helpers those become changed symbols, so the same caller yields extra paths through them: scenario 3 reported "3 non-test callers outside the diff" when there is exactly one (`price_total`). Now counted per distinct caller site (path + symbol + line), which also makes it agree with `needs_bob_action`. The full path list is unchanged in the JSON.

### Follow-up for lane A

The fixture assumes `apply_discount(prices, pct)` and `price_total(prices, pct)`. The real sample takes an order-line list: `apply_discount(amount, pct)`, `price_total(lines, pct)`. The fixture stays schema-valid, but lanes C and D should build against the real shape.

### Limits (honest)

- Python only; class-heavy or dynamic code is out of scope for this PoC.
- No equivalence proof: `same_on_tested_cases` means identical output for those frozen inputs only.
- No sandboxing beyond a 300 s subprocess timeout.

Review notes: `handoffs/B-a-review.md`. Lane status: `handoffs/B.md`.
