# Mutation testing findings — for lanes A and D

Lane B ran mutation testing (`mutmut`) over the engine on `main`. This note records what was
found and what is worth fixing. **No code changes are proposed here for lanes A and D**: these
are their files, and the findings are handed over rather than applied unilaterally.

## How it was run

```bash
pip install mutmut            # dev-time only, not a project dependency
python -m mutmut run          # config in setup.cfg
python -m mutmut results      # per-mutant status
```

Measured on `main` at the time of writing. Two caveats that affect how the numbers should be
read:

1. **`not checked` is not zero.** A mutant is `not checked` when the tool never reached it.
   `snapshot.py` shows 236 not-checked and therefore an unmeasurable score, not a bad one.
   The same applies to a large block of `impact.py`.
2. **Run-to-run totals are not comparable.** mutmut caches and its selection shifts when the
   test suite changes, so compare mutants by id between runs rather than by totals.

## Headline numbers

| Module | Owner | Killed | Survived | Score on evaluated mutants | Not checked |
|---|---|---|---|---|---|
| `app/runner.py` | B | 465 | 142 | 76.6% | 0 |
| `app/impact.py` | A | 55 | 20 | 73.3% | 580 |
| `app/cli.py` | A | 176 | 159 | 52.5% | 0 |
| `app/decisions.py` | D | 282 | 294 | **49.0%** | 0 |
| `app/snapshot.py` | A | 0 | 0 | not measurable | 236 |

## What is not worth chasing

These are deliberately excluded in `setup.cfg`, with the reasoning inline. Killing them would
mean writing worse tests:

- **Cosmetic.** `_sha8` truncating to `[:16]` vs `[:17]` has no observable behaviour. The only
  kill is asserting the digest length, which pins an implementation detail.
- **Timing.** Mutants that drop a subprocess timeout are only catchable by asserting wall-clock
  duration, which is flaky under load.
- **Defensive branches.** `except subprocess.TimeoutExpired: counts = ...` can only be killed by
  monkeypatching an error path that is built never to fire.
- **Message prose.** A large share of survivors are reworded/uppercased copies of the human-facing
  `limits` and summary strings. Killing them means asserting exact wording, so honest rewording
  would break the build while proving nothing about behaviour.

**A 100% score is not a realistic target and should not be the goal.** It is only reachable by
adding flaky, implementation-coupled or prose-brittle tests, which contradicts the project's own
rule that only real behaviour counts. A defensible target is roughly 85–90% with the exclusions
above documented and reviewable.

## For lane D: `app/decisions.py` (49%, 294 survivors)

The lowest score in the engine, and the highest-value remaining work. Worth checking:

- `lookup` match classification across `approved` / `stale` / `superseded`, including the
  precedence between them.
- `load_branch_decisions_via_git`: the git-failure paths, and behaviour when the branch has no
  ledger directory at all.
- `validate_and_save`: field extraction from the many accepted delta shapes (dict vs object,
  `target` vs bare `symbol`, `before` vs `base_output`). Most of these branches look untested.
- `generate_decision_id`: what happens with empty or whitespace inputs.

The trust rule (merge-to-main means approved) now has a guard test; the rest of the module does
not have equivalent coverage.

## For lane A: `app/cli.py` (52.5%, 159 survivors) and `app/impact.py`

`app/cli.py`:

- `_limits` branch selection and the unprobed-caller sentence.
- `_summary` formatting for every section.
- `_prior_decisions`: the path/symbol matching, and the superseded relabelling.
- The `--run` / `--prior-report` / `--markdown` argument handling.

`app/impact.py`:

- Its score is computed on a small evaluated subset (580 not checked), so the real coverage is
  **unknown rather than 73%**. This is the component carrying the project's central claim that
  unknown edges are never shown as safe, so it deserves direct attention.
- Specifically: `Codebase.scan`'s unresolved-reference path (the `Unknown` emission), `_diff`'s
  added/removed/signature handling, and `_import_names`' package-root resolution.

`app/snapshot.py` has 236 not-checked mutants: nothing in the suite exercises its mutants. Its
worktree logic appears to run only through fixture tests. Worth a dedicated test that runs
`open_pair` over a repo with an unusual layout.

## Suggested next step

Fix by value, not by score:

1. `app/decisions.py` — lowest score, and governance is a demo claim (D).
2. `app/impact.py` — unknown coverage on the component carrying the safety claim (A).
3. `app/snapshot.py` — measure it before judging it (A).

Lane B's own gap (`run_suite` result parsing) was closed: 31 mutants newly killed, 29 of them in
`run_suite`, taking `runner.py` from 71.1% to 76.6%.
