# Handoff — Lane E (Maps)

**Status:** E1 evidence map and E2 repo map are on `main` (#25 backend, #26 web). E3
(`CHANGE_NOTES.md` template) is step 7 of the Bob mode in `.bob/custom_modes.yaml`.

## Works
- `app/repo_map.py` — `build(checkout, repo_root, repo, sha) -> RepoMap` (`map-0.1`, FINAL_PLAN §16.3):
  every source module with language + tier (from `AdapterSpec.tier`), file-level import edges
  (`from`/`to`/`kind`/`line`), unknown imports, and each module's last mainline commit
  (`git log --first-parent --diff-merges=first-parent`, one pass) with the PR number parsed from
  "Merge pull request #N" or a squash "(#N)" subject. No author names in the file.
- Imports come from the adapters, not a copy: `impact.import_graph(root)` (Python, records each
  import statement's line and `importlib.import_module`/`__import__`) and
  `impact_treesitter.import_graph(root, config)` (other languages). Dynamic, ambiguous and
  unresolved relative imports are unknowns; external packages and TS path aliases are left out
  and said so in `limits`.
- CLI `behavior-review map --ref HEAD --out repo_map.json` (reads the committed revision through
  `open_pair`, never the working tree). The Action uploads `repo_map.json` in the same artifact
  as `report.json`.
- Web, evidence map (`web/src/components/EvidenceMap.tsx`, `web/src/lib/evidence-map.ts`): React
  Flow + ELK, folder → file → function, callers left of the changed code, each node colored by its
  own probe outcome (icon + text, never color only). Draggable with Reset layout, bezier edges with
  one port per edge, call sites in edge tooltips and the details Sheet, test callers grouped per
  target, unknown edges dashed, a tier badge per file and a tier legend.
- Web, Repo map tab (`web/src/components/RepoMapTab.tsx`, `web/src/lib/repo-map.ts`): the same
  nesting at file level with imports, tier and last commit/PR per file. It opens on the PR's files
  and their import neighbors; a touched file links back to the evidence map. Unknown imports are
  listed below the map.
- Both maps take any run: **Open report…** reads a local `report.json` + `repo_map.json` in the
  browser (a map of another commit than the report's head is rejected).

## Checks run
`pytest -q` → 124 passed (at #25). Web: `npm run typecheck`, `npm run build`, `npm run check:report`
pass on `main`. In a browser: the shipped Scenario 1 report shows `price_total` as outside the diff
and behavior differs; a locally generated report with 330 impact paths lays out 318 functions in
about a second, and its repo map highlights the 22 touched files.

## Next
- Large diffs: the evidence map fits every node, which is unreadable past ~50 functions; open it on
  the changed functions first, as the repo map already does.
