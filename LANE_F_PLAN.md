# Lane F — Usability, install, and PR triage

Companion to `FINAL_PLAN.md` (§20 points here). Written 27 Sept 2026 from reading `main` at `993d8b9` (PR #31) plus a teammate's bug report on the probe runner; checked against the code at `4de377f` (PR #32), which added friction #10 and deferred F-E. **Updated 27 Sept (later)** from the repo at `feat/pr-triage` (`3209212`): F-C and F-B are merged (#34, #35); F-A is done on `feat/pr-triage` (PR #36, not on `main` yet at the time of reading); F-D and the new **F-F (local web UI, §8)** are not started; F-E stays deferred. Timeline is ignored on purpose: this lane assumes there is time to do all of it.

> Goal: a developer on **any** repository types a few commands, answers a few questions, and gets a behavior review — without cloning our repo, copying our scripts, or learning our folder layout.

---

## 1. Why this lane exists (current friction, from the code)

| # | Friction | Evidence in the code |
|---|---|---|
| 1 | Install only works for our own repo | README: `git clone` our repo → `pip install -e .`. The Action runs `pip install -e ".[dev,multilang]"` on the checkout it reviews (FINAL_PLAN §19.3 row 6, still open). |
| 2 | Other repos must copy our scripts | `app/runner.py` looks for the probe runner (`tools/run_probe.py`) **inside the reviewed repo** via `source_of()`; `pyproject.toml` only packages `app*`, so `tools/` is never installed. |
| 3 | A PR can supply its own probe runner | `source_of()` is "BASE is authoritative; fall back to HEAD". If the base has no runner and the PR adds one, **the PR's runner is used on both sides** — a PR could make every probe look "same". |
| 4 | Tests run with the tool's Python, not the project's | `runner.py`: `python = python or sys.executable`. With an isolated install (pipx / `uv tool`), the project's own dependencies are missing → import errors → `inconclusive`. |
| 5 | Package name `app` collides | Many projects have their own `app/` package; in a shared venv `import app` is ambiguous. The Bob mode even tells Bob to call `app.decisions.validate_and_save`. |
| 6 | No setup command, no prompts | CLI = flags (`--base`, `--head`, `--run`, …) + `map`. No `init`, `doctor`, or interactive mode; default base is hard-coded `main`. |
| 7 | Only one test folder | `config.detect_tests_dir()` finds tests by **file name** (good: any folder name works) but returns only the **shallowest** folder; `tests_dir` is a single string. |
| 8 | Files scattered at repo root | `behavior.json`, `probes/`, `behavior_decisions/`, `tools/`, `.bob/`. |
| 9 | Every PR runs the same pipeline | No PR-level classification. Only per-symbol tags (`signature_changed`, `body_changed`, …) exist. The early "routing policy" idea (Jev) was dropped with "No Jev in the POC" and never replaced. |
| 10 | A probe the PR adds is never run | `compare()` copies the **whole** `probes/` folder from `source_of(probes_dir)`, i.e. from the base whenever the base has one (`runner.py`, `source_of` + `copytree`). A probe Bob writes on the PR branch — what the Bob mode tells it to do — runs only after the PR is merged, so its caller stays in `needs_bob_action`. Live today: our base has `probes/`. |
| 11 | The viewer is a manual upload/download loop | The web is static (`web/src/lib/report-source.ts` reads `public/data/reports/…`; **Open report…** loads files the user picks; **Download decision** in `decision-file.ts` saves a JSON the user must move into the ledger and commit). Nothing connects the page to the repository the user is working in. |

---

## 2. Features

| ID | Feature | Solves | Size |
|---|---|---|---|
| **F-E** | Rename package `app` → `behavior_review` | 5 | Small but touches every import — **deferred** (see §3) |
| **F-B** | Use the project's interpreter (`--python`, auto-detect venv) | 4 | Small |
| **F-C** | Probe runners shipped in the package (+ teammate's fix, hardened); run the PR's new probes | 2, 3, 10 | Small–medium |
| **F-A** | PR triage: a deterministic change profile that can route steps | 9 | Small–medium |
| **F-D** | `init` / `run` / `doctor`, interactive; `.behavior-review/` layout; multi test folders; reusable Action | 1, 6, 7, 8 | Medium–large |
| **F-F** | Local web UI: `behavior-review ui` serves the viewer on localhost, wired to the repo's real data — run reviews, history, save decisions (no upload/download) | 11 | Medium–large |

**Order:** F-C → F-B → F-A → F-D → F-F, then F-E. F-C first because it also fixes friction #10, a live bug in the Bob loop. F-D after the others because it builds on them. F-F needs F-D's `.behavior-review/` layout and `decide` command, but its web data-source work (§8.5) can start in parallel. F-E waits (§3).

**Status (27 Sept):**

| ID | Status |
|---|---|
| F-C | ✅ merged (#34; provenance #35) |
| F-B | ✅ merged (#35) |
| F-A | ✅ merged (#36), web badge `TODO(C)-7` open |
| F-D | 🟨 in progress: `.behavior-review/` folder + `decide` (#37); `init` / `run` / `doctor` / Action next |
| F-F | 🟨 in progress: server and API (#38), web local mode (#39); packaged web next |
| F-E | ⏸ deferred (§3) |

---

## 3. F-E — Rename `app` → `behavior_review`

**Deferred.** The collision only happens when the tool is installed into the project's own environment, or run as `python -m app.cli` from a repo that has its own `app/`. With the isolated install F-D recommends (`uv tool` / `pipx`), the `behavior-review` command's `sys.path` starts at the tool's own environment, not the repo, so `import app` is ours — the first-time user flow doesn't need the rename. Do it before recommending any install into a project environment (or a PyPI release), at a moment with no open PRs touching `app/`.

- Move `app/` → `behavior_review/`; update every import (83 in 22 files at `4de377f`), `pyproject.toml` (`[tool.setuptools.packages.find] include = ["behavior_review*"]`, `[project.scripts] behavior-review = "behavior_review.cli:main"`), tests, `contracts/` tests, `setup.cfg` (mutmut paths), `.bob/custom_modes.yaml` (`app.decisions.validate_and_save`), the run commands in `handoffs/scenario-refs.json`, `handoffs/B.md` and `handoffs/D.md`, `web/README.md` and the comment in `web/src/lib/decision-file.ts`, README, `AGENTS.md`. The Action needs no change: since #29 it calls the `behavior-review` command, not `python -m app.cli`.
- One atomic PR, announced in the group; other lanes rebase right after.
- **Done when:** `pytest -q` green, `behavior-review --help` works from a fresh `uv tool install`, and `grep -rn "from app\|import app\|app\.cli\|app\.decisions"` finds nothing outside history files.

---

## 4. F-B — Run tests and probes with the project's interpreter

**Status: done** (#35): `app/interpreter.py`, `--python`, `"python"` in `behavior.json`, and `analysis.runtime` (interpreter shown repository-relative or by file name only, so a published report holds no local path); tests in `tests/test_interpreter.py`, including a real `.venv` holding a dependency the tool's environment lacks. The interpreter is resolved only when the configured test or probe command uses Python. The Action passes `--python` (its tool and project share one interpreter), so our own reports don't carry the fallback limit.

**Why:** the tool should live in its own isolated environment (§7.1), but the project's tests need the project's libraries.

Resolution order (first hit wins), for Python test/probe execution:

1. `--python PATH` flag
2. `python` in `.behavior-review/config.json` (or `behavior.json`)
3. `$VIRTUAL_ENV` if set
4. `.venv/` or `venv/` in the repo root (`bin/python` or `Scripts/python.exe`)
5. Fallback: the tool's own interpreter — **with a limit line**: "Ran with behavior-review's own Python; if the project's dependencies are not installed there, results may be inconclusive. Pass `--python` or create `.venv`."

Notes:
- The interpreter path is absolute, so it works from the temporary `git worktree` checkouts.
- Record what was used: optional `analysis.runtime = {"python": "<path>", "version": "3.12.4", "source": "venv"}` (schema change → Lane A, fixtures updated in the same commit).
- Non-Python languages keep using their configured argv commands.

**Done when:** a repo with `requests` in its `.venv` (and not in the tool's env) runs its tests `ok` via auto-detection; the fallback case prints the limit line; unit tests cover each resolution step on Linux and Windows path shapes.

---

## 5. F-C — Probe runners shipped in the package (teammate's fix, hardened)

**Status: done** (#34, provenance in #35's `analysis.runtime.probe_runner`), runners in `app/harness/` (inside the wheel), tests in `tests/test_probe_sources.py`. The optional `python_path` config is not added: the checkout root and `src/` cover the layouts seen so far.

### 5.1 The teammate's report (agreed)

`runner.py` searches the reviewed repo for `tools/run_probe.py` and never uses a bundled runner. Proposed: add a packaged generic Python runner and use it when the repo has none; keep custom runners for other languages; add a regression test (Python repo, `probes/multiply_negative.json`, no `tools/run_probe.py`, base `-6`, head `6` → `delta_observed`). Their local run: `probe line_total_boundary: delta_observed -6 -> 6`. Focused tests passed; full suite not yet run; nothing pushed.

### 5.2 Required changes on top of that proposal

1. **Close the head-runner hole (friction #3).** For the probe **runner** (code), resolution is: base copy of the configured runner → packaged runner → **never** a head-only copy. If the PR adds or changes the runner, the report says so ("this PR adds `tools/run_probe.py`; it was not used").
2. **Run the PR's new probes (friction #10).** **Probes** (data) must include head, because Bob writes new probes on the PR branch — today they are ignored whenever the base has a `probes/` folder. Freeze the base's probes **plus** every probe file that exists only in head; the same frozen bytes run on both sides, which is what matters. A probe the PR **edits** runs with its base bytes, and the report says the edit was not used (the "rerun the unchanged probe" rule).
3. **Packaged runners are stdlib-only.** They must not import `behavior_review` or Pydantic: with F-B they run under the *project's* interpreter.
4. **Ship all harnesses as package data:** `behavior_review/harness/run_probe.py`, `run_command_probe.py`, `run_probe.ts`. A configured runner path that doesn't exist in base falls back to the packaged file of the same name.
5. **One source of truth.** Delete `tools/run_probe.py` etc. from our repo root after the move (our scenario base tag `ref/base` still contains them, so historical runs stay byte-identical), or keep them as copies guarded by a test that asserts identical bytes.
6. **`src/` layout.** The runner puts the checkout root **and** `src/` (if present) at the front of `sys.path`; an optional `python_path: []` in config adds more.
7. **Split the `try` block.** Today copying the runner and copying `probes/` share one `try`; a missing runner must not stop probes from being frozen, and vice versa.
8. **Provenance.** Record the runner used: `analysis.runtime.probe_runner = {"source": "base" | "packaged", "sha256": "…"}`.

### 5.3 Tests

| Test | Expect |
|---|---|
| Teammate's regression (no runner in repo, −6 → 6) | `delta_observed`, note says packaged runner used |
| Base has no runner, **head adds a fake runner** | packaged runner used; note says the head runner was ignored |
| Base has `probes/`, **head adds a probe** | the new probe runs on both sides; its caller leaves `needs_bob_action` |
| Head **edits** an existing probe | base bytes run; note says the edit was not used |
| Probe raises on both sides | `outcome: "exception"` with the same shape as today |
| `src/` layout package | imports resolve, real values compared |
| TypeScript / command-probe config | their own runners, no fallback applied |
| Full suite + Scenarios 1–4 | unchanged results |

---

## 6. F-A — PR triage (change profile)

**Status: done** (#36), `app/triage.py`, tests in `tests/test_triage.py`. Trimmed from the design below: **no review depth** (it restated what the report already shows — callers outside the diff and unknowns) and **`tests_only` doesn't skip probes** (probes are cheap; the skip needed extra plumbing for no reviewer benefit). Only `docs_only` skips anything. Added: a file no adapter parses counts as code, so `no_semantic_change` is only claimed when every code file was parsed. Web badge (`TODO(C)-7`) not done yet; the PR comment and Bob mode show the profile.

**Deterministic rules, no AI.** Runs at the start of **every** review (CLI, Action, Bob mode) — not during `init`, because each PR changes different files.

### 6.1 Profiles

| Profile | Detected when **all** changed files are… | Steps |
|---|---|---|
| `docs_only` | docs: `*.md`, `*.rst`, `*.txt`, `docs/**`, `LICENSE*`, images | Skip execution. Report: "No code or configuration changed; no behavior check needed." |
| `tests_only` | test files (configured test folders/patterns) | Run the test suite; skip probes; flag "tests changed — the frozen base suite is what's compared". |
| `config_or_deps` | *any* file is config/dependency/CI: `pyproject.toml`, `setup.cfg`, `requirements*.txt`, `package.json`, lockfiles, `go.mod`, `pom.xml`, `build.gradle*`, `*.csproj`, `Dockerfile`, `.github/workflows/**`, our own config | Run **everything**; high-attention banner: "Environment/dependency change — static analysis can't see its effects." |
| `no_semantic_change` | source files whose AST is identical on both sides (formatting/comments only) | Run tests + probes (cheap); label "no structural change detected". |
| `code_change` | anything else | Full pipeline. |

Mixed PRs take the **most thorough** profile (e.g. docs + code = `code_change`; any config file = `config_or_deps`). Unknown file types count as code.

**Review depth** (for `code_change` / `config_or_deps`): `light` / `standard` / `deep`, from changed symbols, non-test callers outside the diff, and unknowns (thresholds in config, e.g. deep if any caller outside the diff or any unknown). Shown to the author and reviewer; it never skips checks.

### 6.2 Safety rule

Triage may only **skip** execution when it can prove there is nothing executable to compare (`docs_only`). Everywhere else it can only **add** attention. `--full` forces the whole pipeline regardless of profile.

### 6.3 Output

- Optional `ReviewReport.triage = {"profile": "...", "depth": "...", "reasons": ["only docs/*.md changed"], "skipped_steps": ["tests", "probes"]}` (schema change → Lane A).
- Markdown/PR comment: first line after the title. Web: badge in the summary header. Bob mode: states the profile first.

**Done when:** each profile has a fixture PR (docs-only, tests-only, lockfile bump, formatting-only, real change) with the expected profile and steps; a docs-only PR finishes without running tests; `--full` overrides.

---

## 7. F-D — Easy install, interactive setup, one folder

### 7.1 Install (industry practice)

CLI tools written in Python are normally installed into their **own isolated environment** with **pipx** or **`uv tool`**, so they don't mix with the project's dependencies; `uvx` runs a tool once without installing. Installing straight from GitHub is supported — no PyPI release needed:

```bash
pip install uv                                   # once
uv tool install git+https://github.com/webdev-testa/pocbobbin
# or: pipx install git+https://github.com/webdev-testa/pocbobbin
```

The repository must be public. The built web ships with it (§8.2, Option A). A PyPI release later shortens it to `uv tool install behavior-review` (roadmap, §8.2).

### 7.2 `behavior-review init` (interactive, once per repo)

Detect first, then ask — every question has a default, so Enter-Enter-Enter works:

```
✔ Repo: my-shop (git)   current branch: feat/discount-rounding
? Default base branch:                    › main  (from origin/HEAD) / other…
✔ Languages: Python (full), TypeScript (static_probe)
? Test folders (space to toggle):         › [x] tests/ (12, pytest)  [x] billing/tests/ (4)  [ ] scripts/ (1)  [ ] skip tests
? Python for running project tests:       › .venv/bin/python (detected) / other…
? Create .behavior-review/ (config, probes, decisions)?  › Yes
? Add GitHub Action (.github/workflows/behavior-review.yml)?  › Yes / No
? Add Bob custom mode (/behavior-review)?  › Yes / No   (merged into .bob/custom_modes.yaml if it exists)
✔ Done. Next: behavior-review run
  No probes yet → in Bob IDE type /behavior-review to create them.
```

Rules:
- Never overwrite an existing file without showing what changes and asking.
- Non-interactive for CI/scripts: `--yes` plus flags (`--base-branch`, `--tests`, `--python`, `--no-action`, `--no-bob`); prompts are disabled automatically when stdin is not a TTY.
- Prompt library: `questionary` (MIT) — or stdlib `input()` if we want zero extra deps. Decide in F-D kickoff.

What `init` creates in the user's repo (the same pattern as `npm init playwright@latest`, which asks questions then writes a config, a tests folder and an optional CI workflow):

```
.behavior-review/
  config.json          # answers above (replaces behavior.json)
  probes/README.md     # what a probe is; Bob writes probes here
  decisions/           # ledger (replaces behavior_decisions/)
.github/workflows/behavior-review.yml   # optional; installs the tool from a pinned git tag
.bob/custom_modes.yaml                  # optional; merged, not overwritten
```

Harness scripts are **not** copied — they ship in the package (F-C). Only the repo's own data (config, probes, decisions) lives in the repo.

**Recording decisions from Bob:** the Bob mode today calls `app.decisions.validate_and_save` from Python. With an isolated install the package isn't importable from the project's interpreter, whatever its name, so add a command — e.g. `behavior-review decide --probe <id> --intent intended --rationale "…"` writing `.behavior-review/decisions/<id>.json` — and point the mode at it.

**Backward compatibility:** `behavior.json`, `probes/`, and `behavior_decisions/` keep working (read when `.behavior-review/` is absent), with a one-line note suggesting `init`. Config is still read from the **base** revision (a PR can't pick its own test command).

### 7.3 `behavior-review run` (interactive when run by hand)

- No `--base`? Ask: "You're on `feat/x`. Compare with `main` (default) / another branch?"
- Uncommitted edits? Warn: "Uncommitted changes are not analyzed — commit or stash first? [continue anyway]".
- `--run` is the default for `run`; prints the summary and where `report.json` / `report.md` were written, plus the web viewer's **Open report…** hint.
- All current flags keep working unchanged (the Action and Bob mode use them).

### 7.4 `behavior-review doctor`

Checks and explains, one line each: git repo; Python interpreter + can it import the project; test command runs (`--collect-only` style dry run where possible); number of probes; last report's `needs_bob_action`; Tree-sitter installed; config valid. Exit code ≠ 0 if something blocks a review.

### 7.5 Multiple test folders

- `detect_tests_dir()` returns **all** candidate folders (ranked, with counts) instead of only the shallowest.
- Config accepts `tests_dirs: [...]` (keep `tests_dir` as a one-item alias). The runner freezes and runs all selected folders from the base revision.
- Tests stay **optional**: with none, the report says so and still gives impact analysis. Probes stay optional to *run* but are required for any behavior claim — uncovered callers go to `needs_bob_action` for Bob.

### 7.6 Reusable Action for other repos (closes FINAL_PLAN §19.3 row 6, `TODO(D)-3`)

The workflow written by `init` installs the tool from a pinned tag (`uv tool install git+https://github.com/webdev-testa/pocbobbin@v0.2.0`), sets up the project's own environment (`pip install -r requirements.txt` / `npm ci` as detected), then runs `behavior-review run --base origin/<base> --python <venv> --json … --markdown …` and posts the comment. Our own repo's workflow keeps installing from its checkout.

**Done when (whole of F-D):** on a **fresh public dummy repo** we don't own the layout of (Python `src/` package, tests in `tests/`, deps in `requirements.txt`), a newcomer runs `uv tool install git+…` → `behavior-review init` (defaults only) → `behavior-review run` and gets a correct report; `/behavior-review` in Bob writes a probe into `.behavior-review/probes/`; the generated Action posts a comment on a PR in that repo.

---

## 8. F-F — Local web UI: the whole loop from one terminal command

**Status: in progress.** Friction #11. Done in #38: `behavior-review ui`, `app/server.py` (API and safeguards, §8.4), `app/runs.py` (history, one run at a time, steps in `meta.json`), `pipeline(on_progress=…)` (`TODO(A)-9`), and `decide_from_report`, shared by `decide` and `POST /api/decisions`. Done in #39: the web's local mode (§8.5): `LocalApiSource` as `web/src/lib/local-api.ts`, run history in the header, New review with live steps, Save decision through the API; static mode unchanged. Next: the packaged web (§8.2).

**Goal:** one command opens the viewer on localhost, already connected to the repository's real data — run a review, browse history, save a decision straight into the repo. Same pattern as `jupyter lab`, `mlflow ui`, `tensorboard`, `dbt docs serve`: a Python CLI starts a small local server and opens the browser.

```bash
behavior-review ui              # → http://127.0.0.1:8765/?token=…  (browser opens)
behavior-review run --open      # run a review, then open it in the UI
```

Options: `--port`, `--no-browser`, `--repo`.

### 8.1 Decisions taken (27 Sept)

| Question | Decision |
|---|---|
| Server | **FastAPI + uvicorn** (Pydantic is already a dependency) |
| "Choose a PR" in local mode | **Local branches + review history.** No GitHub account needed. (The static site keeps its PR picker from #32.) |
| Run history | `.behavior-review/runs/` is a **local cache, git-ignored** (`init` adds the entry). Only decisions and probes are committed |
| Start reviews from the web | **Allowed, with safeguards** (§8.4) |
| How the built web reaches users | **Option A: commit the built web** into the package (§8.2). PyPI is roadmap |

### 8.2 No Node.js on the user's machine

The web app is built **by us, not by the user**. Measured on 27 Sept: `web/node_modules` is **190 MB** (only needed to develop the web); the built `web/dist` is **2.5 MB** (all a browser needs). Only the 2.5 MB build ships.

**Where it lives:** inside the installed tool (uv's tool folder, once per computer, shared by every repo) — **never in the user's repository**. Jupyter, MLflow UI, TensorBoard and Streamlit ship their web frontends inside the Python package the same way.

**Distribution — decided: Option A (commit the build).** `uv tool install git+https://…` builds the package from source on the user's machine, so the source must already contain the built web (otherwise the install would need Node):

1. `npm ci && npm run build` in `web/`, then copy `web/dist/` → `app/web_dist/` (or `behavior_review/web_dist/` after F-E) and **commit it** (~2.5 MB). One script does both: `npm run build:package`.
2. Declare it as package data in `pyproject.toml` (`[tool.setuptools.package-data] app = ["web_dist/**/*"]`).
3. **CI guard:** a job rebuilds the web and fails if the committed `web_dist/` differs from the fresh build (so nobody forgets to rebuild), and a test asserts the built wheel contains `web_dist/index.html`.

**Roadmap (not now):** publish to **PyPI** with Trusted Publishing (GitHub Actions → PyPI via short-lived OIDC tokens, no stored secret; practice on TestPyPI first; a version number can be uploaded only once). Users then run `uv tool install behavior-review`, the release workflow builds the web in CI, and `web_dist/` no longer needs committing. Requires F-E first (no top-level `app` module on PyPI). The name `behavior-review` returned 404 on PyPI on 27 Sept (likely free, not reserved).

Developer mode for us: `npm run dev` in `web/` with a Vite proxy `/api → http://127.0.0.1:8765`, and `behavior-review ui --no-browser` running the API.

### 8.3 Storage

```
.behavior-review/
  config.json, probes/, decisions/        # committed (F-D)
  runs/                                   # git-ignored local cache
    20260927-1412_main..feat-x_1a2b3c4/
      meta.json      # base/head refs + SHAs, status (queued|running|done|failed), times, triage profile
      report.json    # ReviewReport
      report.md
      repo_map.json
      run.log
```

Before F-D lands, the UI reads/writes the legacy paths (`behavior_decisions/`, and `runs/` under a git-ignored `.behavior-review/`).

### 8.4 Local API

| Method & path | Does |
|---|---|
| `GET /api/health` | Tells the web it is in local mode |
| `GET /api/repo` | Repo name, current branch, default base branch, languages + tiers, config summary |
| `GET /api/branches` | Local and remote-tracking branches with SHA and last commit date |
| `GET /api/runs` | Review history (from `runs/*/meta.json`), newest first |
| `GET /api/runs/{id}/report` · `/repo_map` · `/markdown` · `/log` | One run's files |
| `POST /api/runs` `{base, head, full?}` | Starts a review in a background worker; **one at a time** (409 if one is running); returns the run id. `full` maps to the CLI's `--full` (F-A) |
| `GET /api/runs/{id}/events` | Server-Sent Events progress: triage → impact → tests (base, head) → probes → repo map → done/failed |
| `GET /api/decisions` | The ledger: this branch's proposed records + approved ones from the base branch |
| `POST /api/decisions` `{run_id, target, intent, rationale, requirement_ref?}` | Same code path as the `decide` command (§7.2): validates with `decisions.validate_and_save`, writes the decision file with status **proposed**, returns the path and the suggested `git add/commit` command. Rejects "intended" without a rationale. **Never approves** — approval is still "merged to main" |

**Safeguards (required):**
- Binds to **127.0.0.1 only**.
- A random **token** is generated at start and shown in the URL (like Jupyter); every `/api` call must carry it. The server also checks the `Host` header (`127.0.0.1` / `localhost`) and sends no CORS headers, so other websites can't call it.
- Reviews run only for the repository the server was started in — no arbitrary paths, no shell strings, the same argv commands as the CLI (including the F-B interpreter and F-C runners).
- The web may write **only** under `.behavior-review/` (and the legacy decisions folder until F-D).
- The "New review" dialog states plainly that the review runs the project's tests and probes on this computer.

**Engine change (Lane A):** `pipeline(...)` gets an optional `on_progress(step, detail)` callback and must never `print`/`sys.exit` when called as a function, so the worker can report progress and errors.

### 8.5 Web: one app, two data sources

- Today `web/src/lib/report-source.ts` is the static source (`/data/reports/index.json` + `pr-<N>/…`, or the single report). Add a **`LocalApiSource`** next to it with the same shape. At start the app calls `/api/health`; if it answers, local mode.
- Local mode adds: a **"Local · <repo>"** banner; **run history** in place of `PrPicker`; a **"New review"** dialog (shadcn Dialog + Select: base defaults to the configured branch, head to the current branch) with step-by-step progress; **"Save decision"** instead of "Download decision", followed by a toast with the file path and the git command.
- Static mode (Vercel) keeps `PrPicker`, **Open report…** and **Download decision** unchanged — the judges' demo still works without any server.

### 8.6 Bob mode

After running the CLI, the `/behavior-review` mode suggests: "For the visual view run `behavior-review ui`" (or runs `behavior-review run --open` if the author agrees). Probes Bob writes on the PR branch run in the next review (F-C, friction #10) and show up in the UI.

### 8.7 Tests

- API (FastAPI `TestClient`): missing/wrong token → 401; foreign `Host` → 403; decision path confined to the decisions folder; "intended" without rationale → 422; second concurrent run → 409.
- Package: the built wheel contains `web_dist/index.html`; CI fails if the committed `web_dist/` is stale.
- End to end on the sample repo: start `ui`, `POST /api/runs` Scenario 1, wait for `done`, report shows `delta_observed` on `price_total`, `POST /api/decisions` writes a proposed file.
- Web: both data sources render the same Scenario 1 report identically.

**Done when:** on a machine **without Node.js**, a newcomer runs `uv tool install git+…` → `behavior-review init` → `behavior-review ui`, clicks **New review**, watches it finish, sees the evidence and repo maps, clicks **Save decision**, and finds the JSON in `.behavior-review/decisions/` ready to commit — no uploads, no downloads.

---

## 9. Overlaps with other lanes

| ID | Owner | Task | Status |
|---|---|---|---|
| `TODO(A)-7` | A | Approve optional schema fields `ReviewReport.triage` and `analysis.runtime`; update `contracts/` fixtures + `tests/test_contracts.py` in the same commit | [ ] |
| `TODO(B)-5` | B | Review F-C (runner resolution, head-runner rule, stdlib-only harness) and F-B (interpreter) — both live in `runner.py` | [ ] |
| `TODO(C)-7` | C | Show `triage.profile` and `analysis.runtime` (interpreter, runner source) in the web summary; markdown line already from `report.render` | [ ] |
| `TODO(D)-6` | D | Bob mode: state the triage profile first; write new probes to `.behavior-review/probes/` (fallback `probes/`); record decisions with the `decide` command (§7.2) instead of importing the package | [ ] |
| `TODO(D)-3` | D | Reusable Action template for other repos (§7.6) | [ ] (was open) |
| `TODO(A)-9` | A | `pipeline(...)` callable with an `on_progress` callback, no `print`/`sys.exit` in the function path (F-F §8.4) | [ ] |
| `TODO(C)-8` | C | `LocalApiSource` next to `report-source.ts`, local-mode run history, "New review" dialog with progress, "Save decision" (F-F §8.5); static mode unchanged | [ ] |
| `TODO(D)-7` | D | `POST /api/decisions` shares the `decide` command's code path; Bob mode suggests `behavior-review ui` / `run --open` (F-F §8.6) | [ ] |
| `TODO(ALL)-1` | all | Rebase right after the F-E rename PR merges (when it happens; deferred) | [ ] |

---

## 10. Trying the tool in Claude Code or Codex (local testing only)

The engine is a CLI, so any agent that can run shell commands can use it. To save Bobcoins while experimenting:

- **Claude Code:** a local skill, e.g. `~/.claude/skills/behavior-review/SKILL.md`, whose body is the instructions from `.bob/custom_modes.yaml`.
- **Codex:** it reads `AGENTS.md` and supports custom prompts in the user's home folder — check Codex's docs for the exact location.

Keep these files **outside the repository** (or git-ignored): the product ships with Bob only (FINAL_PLAN §18). Hackathon evidence (probes and fixes shown in the demo, `bob_sessions/` screenshots) must come from Bob; any other assistant used during development is disclosed in the Bob Usage statement.

---

## 11. Bob prompts (one bounded task each)

Use with the FINAL_PLAN §10 starter prompt. Each ends with: *run the relevant tests and the full suite, report real results, update `handoffs/F.md`; do not claim anything you did not run.*

- **F-E:** "Rename the Python package `app` to `behavior_review` exactly as in LANE_F_PLAN §3. Update every import, pyproject, the Action, `.bob/custom_modes.yaml`, README and AGENTS.md. No behavior changes."
- **F-B:** "Implement the interpreter resolution order in LANE_F_PLAN §4 for Python test and probe execution, with the fallback limit line and unit tests for each step (Linux and Windows path shapes)."
- **F-C:** "Ship the probe harnesses as package data and implement runner resolution per LANE_F_PLAN §5.2 (base → packaged, never head-only), and freeze the base's probes plus head-only probe files (an edited probe keeps its base bytes). Keep harnesses stdlib-only, add `src/` to sys.path, split the try block, record provenance. Add every test in §5.3, including the teammate's −6 → 6 regression and the fake head-runner case."
- **F-A:** "Implement the deterministic triage in LANE_F_PLAN §6 as `behavior_review/triage.py`, wire it into the pipeline with the safety rule and `--full`, and add one fixture PR per profile."
- **F-F:** "Implement `behavior-review ui` per LANE_F_PLAN §8: FastAPI + uvicorn API with the safeguards in §8.4, run history under `.behavior-review/runs/` (git-ignored), background runs with SSE progress, decisions saved as proposed through the `decide` code path; commit the prebuilt web as package data with the CI staleness guard (§8.2); add `LocalApiSource` so the Vercel static mode keeps working. Add every test in §8.7."
- **F-D:** "Implement `init`, interactive `run`, and `doctor` per LANE_F_PLAN §7, the `.behavior-review/` layout with backward compatibility, multi-folder tests, and the reusable Action template. Prove it on a fresh public dummy repo as in §7 'Done when'."

---

## 12. Sources

- Playwright installation (`npm init playwright@latest` asks, then writes config, tests folder, optional workflow): https://playwright.dev/docs/intro
- uvx / `uv tool` for isolated CLI tools: https://pydevtools.com/handbook/reference/uvx/
- `uv tool` vs pipx: https://pydevtools.com/handbook/explanation/how-do-uv-tool-and-pipx-compare/
- pipx comparisons: https://pipx.pypa.io/latest/explanation/comparisons.html
