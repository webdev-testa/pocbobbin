# Lane B handoff — execution engine

Owner: Wipiii (B). Working branch: `feat/execution-engine` (`efa88f4`), based on `origin/main` (`c0d9885`) with A's engine merged.
Repo: `~/projects/pocbobbin` on wipiii-server. Every number below is real command output, no fixtures.

## Open PR

Pushed and verified:

```bash
cd ~/projects/pocbobbin
export GIT_SSH_COMMAND='ssh -i /home/wipiii/.ssh/id_ed25519 -o IdentitiesOnly=yes'
git push git@github.com:webdev-testa/pocbobbin.git feat/execution-engine:feat/execution-engine
git push git@github.com:webdev-testa/pocbobbin.git base:base scenario1-head:scenario1-head scenario3-head:scenario3-head scenario4-head:scenario4-head
```

Branch `feat/execution-engine` at `7240d4d`; scenario branches `base` `60d933a2`,
`scenario1-head` `7b686d8c`, `scenario3-head` `f5054caf`, `scenario4-head` `f7a09999`.

**The scenario branches must be public**, otherwise the PR is not runnable: the tests and
the CLI resolve them as `origin/base`, `origin/scenario1-head` and so on. Verified on a fresh
clone of the pushed branch: 27 passed, and the scenario 1 delta reproduces.

Opening the PR needs a GitHub session (not available to the agent). One click:
`https://github.com/webdev-testa/pocbobbin/compare/main...feat/execution-engine?expand=1`
The prepared body is in `handoffs/pr-lane-b.md`.

## What works

One command runs the whole engine, impact plus paired execution. In a fresh clone the
scenario branches exist only as remote-tracking refs, so pass `origin/<name>`:

```bash
python -m app.cli --repo . --base origin/base --head origin/scenario1-head --run
```

In this checkout, where the branches also exist locally, `--base base --head scenario1-head`
works the same.

`app/runner.py` implements A's contract `compare(pair, bundle) -> (tests, comparisons,
needs_bob_action)` plus `pipeline()`, so the CLI emits one `ReviewReport` with impact
paths, frozen-suite results, probe comparisons and honest limits. `--run` opts into
execution; the default CLI path stays impact-only, as A built it.

Order of operations (plan P0.1 to P0.5): worktree both revisions, freeze the BASE test
suite and the probe runner, run those identical bytes on both sides, run identical probe
bytes on both sides, classify, one report.

Outcome labels: `same_on_tested_cases` | `delta_observed` | `inconclusive`.
Import error, timeout or unparsable output is `inconclusive`, never "bug".

`tools/paired_run.py` stays as the standalone harness with the same classification plus a
Markdown report writer, for regenerating evidence without the CLI.

## Real results (integrated CLI, `--run`)

| Scenario | diff | frozen suite | probe | base to head | outcome |
|---|---|---|---|---|---|
| 1 (rounding change) | 1 file, `pricing/discount.py` | 6 passed base, 6 passed head | `price_total_boundary` | 100.0 to 99.99 | `delta_observed` |
| 1 | | | `apply_discount_contract` | 100.0 to 99.99 | `delta_observed` |
| 3 (refactor) | 1 file | 6 passed both | both probes | 100.0 to 100.0 | `same_on_tested_cases` |
| 4 (broken setup) | 1 file | 6 passed base, 1 error head | both probes | value to runner failure | `inconclusive` |

Scenario 1 is the demo: the suite is green on both revisions while a caller in another file
returns a different number for the same input. The impact graph names that caller,
`price_total → apply_discount` at `sample_project/pricing/invoice.py:25`, outside the diff.

`needs_bob_action` fires correctly: with the caller's probe removed, the report lists
`sample_project/pricing/invoice.py::price_total` and adds the limit "1 impacted non-test
caller(s) outside the diff have no committed probe".

Tests: **27 passed** (A's 15 plus 12 from `tests/test_runner.py`), verified on a fresh clone
of the pushed branch, not only in this checkout. Every scenario revision is resolved as
`origin/<name>` so the suite runs anywhere.

SHAs: base `60d933a2`; s1 head `7b686d8c`; s3 head `f5054caf`; s4 head `f7a09999`.
Each scenario-head diff is exactly one file.

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

`target` is `module:function`, `args` is the positional argument list. The runner prints
canonical JSON: `{"outcome": "value", "value": ...}` or `{"outcome": "exception",
"error_type": ..., "error": ...}`. Bob writes probes in exactly this shape and
`tools/run_probe.py` runs them with no changes.

`contracts/probebundle_scenario1.json` is the same two probes in A's `ProbeBundle` schema
(`target: {path, symbol}`, `input: {args}`, `hash`), so C and D build against the shared
contract while the executable copies stay in `probes/`.

## Demo input that matters

`price_total_boundary` uses subtotal 105.26 at 5% off = 99.997, exactly on the rounding
boundary, so base (round half-up on the total) gives 100.00 and head (truncate) gives
99.99. That input pair is what makes the "same input, different answer" moment reproducible.

## Review of lane A

See `handoffs/B-a-review.md`. Ran A's engine against B's real scenarios: it finds the caller
outside the diff correctly and keeps the working tree isolated. One real bug fixed: the CLI
summary counted impact *paths*, so a refactor that adds private helpers reported "3 non-test
callers outside the diff" when there is one (the extra paths are the same caller reaching
the added helpers). Now counted per caller site.

## Files B owns

`sample_project/**`, `probes/**`, `tools/**`, `app/runner.py`, `tests/test_runner.py`,
`evidence/**` (gitignored, regenerate).

`sample_project/pricing/invoice.py` deliberately calls the changed helper and stays **out of
the diff**; that caller path is what lane A's impact graph must surface.

## Blockers

- **Push access** (above): needs a token, collaborator rights, or Wipiii pushes.
- Scenario 2 (intended change plus rationale) and Scenario 5 (lookup cites the approved
  decision) are lane D. B supplies the probes and runner for both.

## Not done / limits (say so honestly)

- Python only; no class-heavy or dynamic code.
- No equivalence proof: `same_on_tested_cases` means identical output for those frozen inputs only.
- No sandboxing beyond a 300 s subprocess timeout.
