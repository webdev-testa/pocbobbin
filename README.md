# behavior-review

Finds what a change could affect, including callers in files outside the diff, before a PR is
opened. See `FINAL_PLAN.md` for the full design.

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
# Review your branch against main, write the report, print a summary
behavior-review --base main --head HEAD --json report.json

# Any two commits, branches or tags work
behavior-review --base v1.2 --head my-feature --json report.json

# Review a repository in another folder
behavior-review --repo ../other-repo --base main --head HEAD --json report.json
```

With `--json`, the report is written to that file and a summary is printed. Callers outside the
diff are listed first:

```
aa5bc83..630152b: 1 changed symbols, 3 impact paths, 2 non-test callers outside the diff, 0 unknowns
  outside diff: price_total → apply_discount  (sample_project/pricing/invoice.py:5)
  outside diff: checkout → price_total → apply_discount  (sample_project/pricing/checkout.py:5)
```

Without `--json`, the full report is printed to stdout as JSON.

| Option | Default | Meaning |
|---|---|---|
| `--base` | `main` | Revision before the change |
| `--head` | `HEAD` | Revision after the change |
| `--repo` | `.` | Any path inside the git repository |
| `--max-hops` | `2` | How many caller levels to trace back from each changed symbol |
| `--json PATH` | stdout | Where to write the report |

Exit code is `0` on success and `2` if a revision can't be resolved.

The report's shape is defined in `app/schemas.py` (`ReviewReport`); `contracts/report_scenario1.json`
is a labeled example. Anything the analysis can't resolve is listed under `impact.unknowns`, never
treated as safe.

### Run the tests

```bash
pytest -q
```
