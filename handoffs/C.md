# Handoff — Lane C (Demo UI & report rendering)

**Status:** renderer on `main` since PR #12; the viewer was rebuilt on shadcn/ui with both maps
(#26) and can open any run and download ledger decisions (#27). Detailed design notes:
`PERSON_C_PLAN.md`.

## Works
- `app/report.py` — `render_markdown(report)` (PR-comment Markdown) and `to_web_data(report)`.
  Accepts a `ReviewReport` or a plain dict, redacts local paths and secrets, never labels a delta
  a bug or a same-on-tested-cases result safe. Renders impact `hops` as
  `caller (file:line) → changed (file:line)`, per-revision suite counts, prior ledger decisions and
  `needs_bob_action`. Exposed by the CLI as `behavior-review ... --markdown report.md`.
- `web/` — Vite + React + TypeScript + Tailwind v4 + shadcn/ui viewer (`web/README.md`). It reads
  the engine's `ReviewReport` as-is (`src/lib/review-report.ts`) and rejects anything else with a
  readable error. Two tabs: **PR review** (summary with language · tier badges and verdict counts,
  evidence map, behavior differences, needs attention, decisions, tests, limits) and **Repo map**.
  Status is always icon + text + color; one token set for light and dark.
- **Open report…** (header) — a dialog with one drop zone for `report.json` (required) and one for
  `repo_map.json` (optional). Files are read in the browser only; nothing is uploaded.
- **Download decision** — writes the same `behavior_decisions/<id>.json` record as
  `app.decisions.validate_and_save` (same id, `supersedes` rule and rationale minimum), always
  `proposed`; committing it on the PR branch and merging approves it. **Download all ready
  decisions** does the same for every filled-in difference.
- `web/public/data/report.json` + `repo_map.json` — the unmodified artifact of the
  `behavior-review` Action run on draft PR #19 (Scenario 1, do not merge), stamped with
  `links.action_run`. `npm test` (`scripts/check-report.mjs`) fails if the report is a fixture,
  isn't stamped with an Actions run URL, lacks real SHAs, a non-test caller outside the diff or
  probe comparisons, or contains a local path.
- Libraries and licenses: `web/THIRD_PARTY.md` (`elkjs` is EPL-2.0 OR GPL-3.0-or-later, the only
  non-MIT/ISC/Apache one).

## Commands
```bash
cd web && npm install && npm test && npm run typecheck && npm run build
# refresh the page data from a newer Action run on the demo PR
gh run download <run-id> --repo webdev-testa/pocbobbin --name behavior-review-report --dir web/public/data
```
Vercel: root `web`, build `npm run build`, output `dist`.

## Checks run
`npm test`, `npm run typecheck`, `npm run build` pass on `main`. Checked in a browser at 1280 and
390 px, light and dark: the shipped Scenario 1 report, a locally generated report opened through
the dialog, and a downloaded decision that validates as `Decision` in Python with the id
`generate_decision_id` computes.

## Next
- Deploy to Vercel and test the URL from a fresh browser.

## Open question for the team
`PERSON_C_PLAN.md` scopes out the video, slides and presentation, but `FINAL_PLAN.md` §10 gives
lane C the video, slides and both 500-word statements. Someone needs to own them before 14:00.
