# Review of lane A — analysis engine (PR #1, merged as c0d9885)

Reviewer: Wipiii (lane B). Method: read the code, then ran it against B's real
scenario branches, not against A's fixtures.

**Verdict: solid, merge was correct, one real bug found and fixed in the count line.**
The AST work is the hard part of this project and it is done properly.

## What I checked and what came back

| Check | Result |
|---|---|
| `pytest` on A's suite after the merge | 15 passed |
| CLI finds the caller outside the diff, scenario 1 | `price_total → apply_discount` at `invoice.py:25`. Correct. |
| Same on scenarios 3 and 4 | Same caller found, 0 unknowns. Correct. |
| Snapshot isolation | Uncommitted edits in the working tree survive a run; branch unchanged. A's own test also covers it. |
| Report hygiene | No local paths (`/home/...`, `/tmp/...`) in the JSON. |
| `unknowns` when it cannot see | Scenario 4's broken import is *not* reported as unknown impact, correctly: the module parses, the call edge resolves. |
| Full 24-test suite after B's lane landed on top | 24 passed (A's 15 + B's 9). |

## Bug found: the caller count was inflated

A refactor adds private helpers, so those get `added` and become changed symbols.
Every caller of `apply_discount` therefore also reaches `_discounted_amount` and
`_EPSILON`, and each of those produced its own path. The summary counted **paths**,
not caller sites, so scenario 3 printed **"3 non-test callers outside the diff"**
when there is exactly one (`price_total`).

That number is what a reviewer reads first, and it is the wrong number. Fixed in
`app/cli.py` by counting distinct caller sites (path + symbol + line), which is
also what makes it line up with `needs_bob_action`, which already de-duped. The
full path list is untouched, so the extra hop paths are still in the JSON for
anyone who wants them.

The graph output itself is right: `apply_discount → _discounted_amount` is reported
as *inside* the diff, which is correct. Only the summary arithmetic was wrong.

## Notes on `app/impact.py` (the parts worth knowing)

- Reference collection walks **every name and attribute load**, not just calls, so
  callbacks, `X = helper()` and decorators resolve too. That is what makes the
  2-hop path `price_total → apply_discount → _discounted_amount` show up.
- Module naming (`sample_project/pricing/discount.py` → `pricing.discount` **and**
  `sample_project.pricing.discount`) is why B's nested `sample_project` layout still
  resolves. A name claimed by two files becomes `None` and surfaces as an `Unknown`
  instead of a guess. Good call.
- `_limits()` was the only place B's lane had to change: it always said "No tests or
  probes were executed in this run, so no behavior claim is made", which becomes a
  false statement once paired execution runs. It now says so only when nothing ran,
  and reports unprobed impacted callers when `needs_bob_action` is non-empty.

## Not verified by me

- The `ambiguous module` and star-import paths: no such shape in B's scenario yet.
- Windows behaviour of `sys.stdout.reconfigure` and worktree cleanup: Linux only here.
- `max_hops > 2` on a real codebase: B's sample is too small to stress it.

## Follow-up for A

1. Sync 1 wiring is **done**: `app/runner.py` implements `compare(pair, bundle)`, and
   `behavior-review --run` now produces the first complete real `ReviewReport`
   (tests + comparisons + needs_bob_action + limits) for scenario 1.
2. Confirm the signature you froze: `apply_discount(amount: float, pct)` and
   `price_total(lines, pct)`. Your fixture assumes `(prices, pct)`; the real sample
   takes an order-line list plus a percentage. The fixture is still schema-valid,
   but C and D should build against the real one.
