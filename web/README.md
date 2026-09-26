# Behavior Review evidence viewer

Static Vite + React + TypeScript viewer built with shadcn/ui and React Flow. It shows the
`behavior-review` Action run of any published PR, chosen in the header (the newest by default;
`?pr=24` links to one), in two tabs:

- **PR review:** summary with language/tier and verdict counts, the evidence map (callers → changed
  code, nested by folder and file, colored by what execution showed), behavior differences, items
  that need attention, a decision form with prior ledger decisions, tests and limits. **Download
  decision** writes the same `behavior_decisions/<id>.json` record as `app.decisions.validate_and_save`
  (status `proposed`); commit it on the PR's branch, and it counts as approved once that PR is merged.
- **Repo map:** every file by folder with its imports, tier and last commit/PR, each marked
  *changed*, *impacted* (calls changed code), *imports a changed file* or *unrelated*. Folders open
  only down to changed and impacted files; the rest are collapsed into one box each (with their
  file count, what they contain and their latest commit), and their imports merge into one edge
  with a count. Expand all, Collapse all, and *Changed & impacted only* change the view; hovering a
  file highlights its imports.

## Data

```
public/data/reports/index.json        # which PRs to offer, newest first (generated)
public/data/reports/pr-<N>/report.json
public/data/reports/pr-<N>/repo_map.json
public/data/report.json, repo_map.json # single-report fallback when there is no index
```

Every report is the unmodified artifact of a real Action run, stamped with that run's URL. To
publish another PR's run, download its artifact into a new folder and rebuild the index:

```bash
gh run download <run-id> --repo webdev-testa/pocbobbin --name behavior-review-report --dir public/data/reports/pr-<N>
npm run reports:index   # validates each report; skips and warns about any that can't be shipped
```

`npm test` rejects a fixture, a report without an Actions run link, or one containing local
paths, and checks that the index matches its folders. The single-report fallback must also show a
caller outside the diff and probe comparisons, since it is the demo story on its own.

## Packaged page (`behavior-review ui`)

The Python package serves this app from `app/web_dist/`, committed because `uv tool install
git+…` builds the package from source and users need no Node. After changing anything under
`web/`, run `npm run build:package` (build, then copy without the demo `data/`) and commit
`app/web_dist/`; the `Web` CI workflow fails when the committed copy is stale. For development,
run `behavior-review ui --no-browser` and `npm run dev` (it proxies `/api` to port 8765), and open
the dev page with the server's `?token=`.

## Local commands

```bash
npm install
npm test          # the report checks above
npm run dev
npm run typecheck
npm run build
npm run preview
```

To view a run that isn't published, generate its files and pick them with **Open report…**:

```bash
behavior-review --base main --head HEAD --json report.json   # add --run to execute tests and probes
behavior-review map --ref HEAD --out repo_map.json            # optional; must be the report's head commit
```

The files are read in the browser and never uploaded; reloading returns to the published reports.

Libraries and licenses: `THIRD_PARTY.md`.

## Vercel

- Root Directory: `web`
- Build Command: `npm run build`
- Output Directory: `dist`
