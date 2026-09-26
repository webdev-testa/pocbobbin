# AGENTS.md

Behavior review before and after a PR. `FINAL_PLAN.md` is authoritative (§8 scope, §10 lanes).
Work on a short-lived branch off `main` and merge through a PR.

## Non-negotiable rules

- AI proposes, algorithms verify, humans decide. Only real process output counts as evidence.
- Unknown edge is not no impact: show it as unknown, never as safe.
- Setup/import error, timeout or nondeterminism is `inconclusive`, never a bug.
- Never edit a probe to make a difference disappear; fixes rerun the unchanged probe.
- Bob never picks intent or writes a rationale the author didn't confirm.
- Fixtures carry `"fixture": true` and never appear as final demo evidence.
- Do not publish local absolute paths (`C:/Users/...`, `/home/...`) or secrets.
- Shared schema changes go through A (`app/schemas.py` + `contracts/` in one commit).

## Contracts (`app/schemas.py`)

| Function | Input → Output | Owner |
|---|---|---|
| `cli.pipeline(repo, base, head, max_hops, run, prior_report)` | → `ReviewReport`; every door calls this | A |
| `snapshot.open_pair(repo, base, head)` | → `RevisionPair` (detached worktrees; `pair.revisions` has SHAs + changed files) | A |
| `impact.analyze(pair, max_hops)` | → `ImpactResult` (changed symbols, edges, paths, unknowns) | A |
| `runner.compare(pair, impact=..., prior_report=...)` | → `SuiteRun`s, `Comparison`s (with `reruns`), `needs_bob_action`, notes | B |
| `decisions.validate_and_save` / `lookup` | delta + disposition → `Decision`; symbols → prior matches | D |
| `report.render_markdown` / `to_web_data` | `ReviewReport` → Markdown (PR comment) / web data | C |

A real report looks like `contracts/report_scenario1.json` (generated from a `--run`, labeled fixture).

## Ownership

- A: `app/schemas.py`, `app/snapshot.py`, `app/impact.py`, `app/impact_treesitter.py`, `app/adapters/`,
  `app/config.py`, `app/cli.py`, `pyproject.toml`, `contracts/`
- B: `app/runner.py`, `app/harness/` (the probe runners), `probes/`, `sample_project/`, scenario branches (`handoffs/scenario-refs.json`)
- C: `app/report.py`, `web/` (except E's map files)
- D: `app/decisions.py`, `behavior_decisions/`, `.bob/`, `.github/workflows/`
- E: `app/repo_map.py`, `web/src/components/EvidenceMap.tsx`, `RepoMap.tsx`, `web/src/lib/evidence-map.ts`,
  `repo-map.ts` (FINAL_PLAN §16)

Language support tiers live only in `app/adapters/registry.py` (`AdapterSpec.tier`); reports carry them in
`analysis`, and `tests/test_contracts.py` fails if the README tier table disagrees.

Each lane keeps `handoffs/<lane>.md` current (about one page).

## Commands

```bash
python -m venv .venv                       # then activate it
pip install -e ".[dev]"
pytest -q
behavior-review --base main --head HEAD --run --json report.json --markdown report.md
behavior-review --base ref/base --head origin/scenario1-head --run --json report.json   # demo
behavior-review map --ref HEAD --out repo_map.json   # whole-repo module map (lane E)
```

Web viewer (from `web/`): `npm install`, `npm run dev`, `npm run typecheck`, `npm run build`.
Vercel: root `web`, build `npm run build`, output `dist`.

## Web viewer direction (C)

A restrained IBM/Carbon-informed evidence dossier: neutral surfaces with one primary blue accent;
clear hierarchy for evidence, paths, hashes and outputs; no fake terminal or dashboard screenshots;
responsive, accessible contrast, visible focus, light/dark and reduced-motion support. It shows run
metadata, changed symbols, impact paths, unknowns, probe inputs, base/head outputs, outcomes, human
dispositions, limits and Action/artifact links. Visitor decisions are session-only, never approvals.
