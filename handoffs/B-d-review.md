# Review of lane D — Bob mode, GitHub Action, decision ledger

Reviewer: Wipiii (lane B). Method: read the code, then executed it. Every claim below is a
command I ran, not an impression.

**Verdict: the ledger logic is good and worth keeping. The Action did not work at all, and the
trust rule was not implemented. Both are fixed; the fixes are mechanical, not a redesign.**

## What I checked and what came back

| Check | Result |
|---|---|
| D's suite on D's branch | 25 passed, as his handoff says |
| `decisions.validate_and_save` | Works. Persists schema-valid JSON, `supersedes` chain auto-detected |
| Rationale rule | Enforced: `intended` with a short rationale raises |
| `approve_decision` | Works, flips `proposed` to `approved` |
| Ledger + my engine + A's engine merged | 39 passed |
| **The GitHub Action** | **Broken. Exits 2, then posts a placeholder comment** |
| **The plan's trust rule (§7)** | **Not implemented: unapproved records read as approved** |

## Defect 1: the Action never ran the tool (critical)

The workflow calls the CLI with flags that do not exist:

```bash
behavior-review --base ... --head HEAD --format markdown --out report.md --json report.json
```

Real CLI flags are `--base/--head/--json/--run`. There is no `--format` and no `--out`. Under
GitHub's `bash -e`, the step exits 2 (`unrecognized arguments`), so:

- the fallback branch never runs (the `||` chains are inside a single `run:`, and `-e` kills
  the step at the first failure);
- `report.md` is never created;
- the comment step posts the placeholder, *"Review finished without markdown output."*

It also had a fallback that copied `contracts/report_scenario1.json` and posted
*"Automated paired execution completed against contract fixture."* A fixture shown as a
result is exactly what the plan forbids: fixtures must be gone from anything the demo shows.

Rewritten: real flags, `--run` so paired execution actually happens, the PR comment rendered
from the executed `report.json`, no fixture path at all, `github.sha` instead of `HEAD` as the
head revision, and the head revision's own probes. Verified by running the workflow's own step
under `bash -e` in a scratch repo: exit 0, and the generated comment contains real impact,
suite and probe data.

## Defect 2: the trust rule was not implemented

Plan §7: *a decision in a PR branch is only proposed; it becomes approved when it is merged
into `main`.* D's `lookup` decided approval from the stored `status` field, so:

- a decision merged into `main` but still carrying `status: proposed` was reported
  `match_type=stale, is_stale=True`. The normal flow, merge a PR with a proposed decision,
  produced a record the tool called stale, and the plan's Scenario 5 ("the report cites the
  approved decision from Scenario 2") could not work.
- the local-directory fallback was `[... approved] or all_local`, so an unmerged, unreviewed
  record sitting in a feature branch's working tree was surfaced as history.

Fixed in `app/decisions.py`: a record read off the target branch counts as approved *because
it is on that branch* (approval is derived from where it came from), and the local fallback
admits only records that are explicitly `approved`. Verified: before merge `NONE`, after merge
`match_type=approved`.

## Defect 3 (mine, found by D's Action shape): the runner crashed

Testing the Action surfaced a real bug in my own `app/runner.py`: when the base revision has no
`tools/` or `probes/`, `compare()` raised `FileNotFoundError` and the whole run died. A PR that
*adds* the harness hit this on every run. Fixed: the probe runner and probes fall back to the
head revision, and the report says so in `limits` rather than pretending BASE supplied them.
Regression test added.

## Kept as-is (no changes needed)

- `app/decisions.py` structure, `DecisionMatch`, `generate_decision_id`, `load_branch_decisions_via_git`.
  The git-based ledger read is the right idea: it never needs the branch checked out.
- `.bob/custom_modes.yaml`. The MOC loop and the anti-hallucination constraints match the plan
  clause for clause. One fix: step 1 told Bob to run `behavior-review ... --format json --out /tmp/report.json`,
  the same non-existent flags, so the mode's very first instruction would have failed. Now it
  matches the real CLI.
- Markdown-link paths in `handoffs/D.md` point at D's local `D:/Coding Turu/pocbobbin` checkout.
  Harmless in a handoff, but the submission checklist forbids local paths in published files, so
  they should be relative links before upload.

## Open item for D

Scenario 2 and Scenario 5 are yours and now unblocked: the ledger works, my runner supplies the
comparisons, and `lookup` returns approved records correctly after a merge. Suggested demo flow:
propose on a branch, merge, then run a later change through `lookup` and show it citing the
approved record.
