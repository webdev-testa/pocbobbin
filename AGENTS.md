# AGENTS.md

Behavior review before and after a PR. Full plan: `FINAL_PLAN.md` (§8 scope, §10 lanes).

## Rules
- AI proposes, algorithms verify, humans decide. Only real process output counts as a result.
- Unknown edge ≠ no impact. Setup/import error, timeout, nondeterminism → `inconclusive`, never "bug".
- Never edit a probe to make a difference disappear. Fixes rerun the unchanged probe.
- Edit only your lane's files. Schema changes go through A (`app/schemas.py` + `contracts/` in one commit).
- Fixtures carry `"fixture": true` and never appear in the final demo.
- No local paths (`C:/Users/...`) in anything published — reports carry repo-relative paths only.

## Contracts (`app/schemas.py`)
| Function | Input → Output | Owner |
|---|---|---|
| `snapshot.open_pair` / `resolve_pair` | repo, base, head → `RevisionPair` (detached worktrees; `pair.revisions` has SHAs + changed files) | A |
| `impact.analyze` | `RevisionPair` → `ImpactResult` (changed symbols, edges, paths, unknowns) | A |
| `runner.compare` | `RevisionPair`, `ProbeBundle` → `Comparison`s, `SuiteRun`s | B |
| `decisions.validate_and_save` | delta, disposition, rationale → `Decision` or error | D |
| `decisions.lookup` | approved records, symbols → matches / stale | D |
| `report.render` | `ReviewReport` → Markdown, web data | C |

Example of the finished output: `contracts/report_scenario1.json`.

## Ownership
- A: `app/schemas.py snapshot.py impact.py cli.py`, `pyproject.toml`, `contracts/`, `tests/` (engine tests)
- B: `app/runner.py`, `probes/`, `sample_project/`
- C: `app/report.py`, `web/`
- D: `app/decisions.py`, `behavior_decisions/`, `.bob/`, `.github/workflows/`

## Commands
```
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # Linux/macOS: .venv/bin/pip
behavior-review --base main --head HEAD --json report.json
pytest -q
```
