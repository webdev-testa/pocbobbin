# Lane B handoff — execution engine

Owner: Wipiii (B). Branches: `base` (frozen base) plus `scenario1-head`, `scenario3-head`, `scenario4-head`.
Repo: `~/projects/pocbobbin` on wipiii-server. Every number below is real command output, no fixtures.

## What works

`tools/paired_run.py` runs the whole B contract end to end:

```bash
.venv/bin/python tools/paired_run.py --repo . --base base --head scenario1-head --out-dir evidence/scenario1
```

In order it:

1. resolves both refs to SHAs and materializes them into throwaway `git worktree`s (working tree untouched, plan P0.1);
2. copies the **base** test suite to a frozen temp dir and overwrites the test dir in *both* worktrees with those same bytes (plan §5: freeze the base suite);
3. runs that frozen suite on both revisions;
4. copies the **base** probe runner and the **base** probe files, runs each probe against both revisions with identical bytes, compares JSON output;
5. classifies and writes `evidence/<scenario>/report.json` + `report.md`.

Outcome labels: `same_on_tested_cases` | `delta_observed` | `inconclusive`. Import error or timeout = `inconclusive`, never "bug".

## Real results

| Scenario | diff | probe | base to head | outcome |
|---|---|---|---|---|
| 1 (rounding change) | 1 file, `pricing/discount.py` | `price_total_boundary` | 100.0 to 99.99 | `delta_observed` |
| 1 | | `apply_discount_contract` | 100.0 to 99.99 | `delta_observed` |
| 3 (refactor) | 1 file | `price_total_boundary` | 100.0 to 100.0 | `same_on_tested_cases` |
| 3 | | `apply_discount_contract` | 100.0 to 100.0 | `same_on_tested_cases` |
| 4 (broken setup) | 1 file | both probes | value to ModuleNotFoundError | `inconclusive` |

The existing suite is **6 passed on base and 6 passed on head** for scenarios 1 and 3: green tests with a real behavior delta, which is the demo's whole point.

SHAs: base `60d933a2`; s1 head `7b686d8c`; s3 head `f5054caf`; s4 head `f7a09999`.

## Probe format (contract for D's Bob mode and the Action)

One JSON file per probe in `probes/`:

```json
{
  "id": "price_total_boundary",
  "target": "sample_project.pricing.invoice:price_total",
  "args": [ [ {"unit_price": 39.46, "qty": 2}, {"unit_price": 8.78, "qty": 3} ], 5.0 ],
  "note": "why this boundary matters"
}
```

`target` is `module:function`, `args` is the positional argument list. The runner prints canonical JSON: `{"outcome": "value", "value": ...}` or `{"outcome": "exception", "error_type": ..., "error": ...}`. Bob writes probes in exactly this shape and `tools/run_probe.py` runs them with no changes.

## Demo input that matters

`price_total_boundary` uses subtotal 105.26 at 5% off = 99.997, exactly on the rounding boundary, so base (round half-up on the total) gives 100.00 and head (truncate) gives 99.99. That input pair is what makes the "same input, different answer" moment reproducible.

## Files B owns

`sample_project/**`, `probes/**`, `tools/paired_run.py`, `tools/run_probe.py`, `evidence/**` (gitignored, regenerate).

`sample_project/pricing/invoice.py` deliberately calls the changed helper and stays **out of the diff**; that caller path is what lane A's impact graph must surface.

## Blockers

- Push access: the sandbox has no GitHub credentials for `webdev-testa/pocbobbin` (`git push --dry-run` fails with "could not read Username", `gh` not logged in, no credential helper). Needs a token, or Wipiii pushes.
- Scenario 2 (intended policy change + rationale) and Scenario 5 (lookup cites the approved decision) are lane D. B supplies the probes and runner for them.

## Not done / limits (say so honestly)

- Python only; no class-heavy or dynamic code.
- No equivalence proof: `same_on_tested_cases` means identical output for those frozen inputs only.
- No sandboxing beyond a 300 s subprocess timeout.
