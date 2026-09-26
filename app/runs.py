"""Review history for `behavior-review ui` (LANE_F_PLAN F-F §8.3).

One folder per review under `.behavior-review/runs/` (git-ignored: a local cache, unlike probes
and decisions), and one review at a time in a background thread. Each run's steps live in its
`meta.json`, so progress survives a page reload and a finished run replays the same way.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.config import HOME_DIR

RUNS_DIR = f"{HOME_DIR}/runs"
RUN_FILES = {"report": "report.json", "repo_map": "repo_map.json", "markdown": "report.md"}
_RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")

Review = Callable[[Path, str, str, bool, Path, Callable[[str, str], None]], dict]
"""`review(root, base, head, full, folder, emit)` writes the run's files and returns meta to add."""


def review_to_folder(root: Path, base: str, head: str, full: bool, folder: Path, emit) -> dict:
    """One review, the same as `behavior-review --run`, written into a run folder with its repo map."""
    from app.cli import pipeline  # the CLI imports this module
    from app.repo_map import build as build_repo_map
    from app.report import render_markdown
    from app.snapshot import open_pair

    report = pipeline(root, base, head, run=True, full=full, on_progress=emit)
    (folder / "report.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (folder / "report.md").write_text(render_markdown(report), encoding="utf-8")
    emit("repo map", f"mapping {head}")
    with open_pair(root, head, head) as pair:
        repo_map = build_repo_map(Path(pair.head_path), Path(pair.root), pair.repo, pair.revisions.head_sha)
    (folder / "repo_map.json").write_text(repo_map.model_dump_json(indent=2, by_alias=True) + "\n", encoding="utf-8")
    return {"base_sha": report.revisions.base_sha, "head_sha": report.revisions.head_sha,
            "triage": report.triage.profile if report.triage else None}


class RunBusy(Exception):
    """A review is already running; the UI runs one at a time."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, value: dict) -> None:
    """Atomic, so a page polling the file never reads half of it."""
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temp.replace(path)


class RunStore:
    def __init__(self, root: Path, review: Review):
        self.root, self._review = root, review
        self.folder = root / RUNS_DIR
        self._lock = threading.Lock()
        self._active: str | None = None
        self._interrupt_stale_runs()

    def _run_folder(self, run_id: str) -> Path:
        # Run ids are made here and checked here, so a request can never name a path outside runs/.
        if not _RUN_ID.match(run_id) or not (self.folder / run_id / "meta.json").is_file():
            raise KeyError(run_id)
        return self.folder / run_id

    def meta(self, run_id: str) -> dict:
        return json.loads((self._run_folder(run_id) / "meta.json").read_text(encoding="utf-8"))

    def file(self, run_id: str, kind: str) -> Path:
        path = self._run_folder(run_id) / RUN_FILES[kind]
        if not path.is_file():
            raise KeyError(kind)
        return path

    def list(self) -> list[dict]:
        if not self.folder.is_dir():
            return []
        metas = [json.loads(p.read_text(encoding="utf-8")) for p in self.folder.glob("*/meta.json")]
        return sorted(metas, key=lambda meta: meta["id"], reverse=True)

    def _create(self, base: str, head: str, full: bool) -> str:
        with self._lock:
            if self._active:
                raise RunBusy(self._active)
            run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
            self._ignore_runs()
            (self.folder / run_id).mkdir(parents=True)
            _write_json(self.folder / run_id / "meta.json", {
                "id": run_id, "base": base, "head": head, "full": full, "status": "running",
                "started_at": _now(), "finished_at": None, "steps": [], "error": None,
            })
            self._active = run_id
        return run_id

    def start(self, base: str, head: str, full: bool = False) -> str:
        """A review in the background (the UI); its steps land in the run's meta.json."""
        run_id = self._create(base, head, full)
        threading.Thread(target=self._execute, args=(run_id,), daemon=True).start()
        return run_id

    def run_now(self, base: str, head: str, full: bool = False, echo: Callable[[str, str], None] | None = None) -> str:
        """A review in this thread (`behavior-review run`), into the same history the UI shows."""
        run_id = self._create(base, head, full)
        self._execute(run_id, echo)
        return run_id

    def _update(self, run_id: str, **changes) -> None:
        path = self.folder / run_id / "meta.json"
        _write_json(path, {**json.loads(path.read_text(encoding="utf-8")), **changes})

    def _execute(self, run_id: str, echo: Callable[[str, str], None] | None = None) -> None:
        folder, meta = self.folder / run_id, self.meta(run_id)
        steps: list[dict] = []

        def emit(step: str, detail: str) -> None:
            steps.append({"step": step, "detail": detail, "at": _now()})
            self._update(run_id, steps=steps)
            if echo:
                echo(step, detail)

        try:
            extra = self._review(self.root, meta["base"], meta["head"], meta["full"], folder, emit)
            self._update(run_id, status="done", finished_at=_now(), **extra)
        except Exception as exc:  # noqa: BLE001 - any failure is reported on the run, never raised
            self._update(run_id, status="failed", finished_at=_now(), error=str(exc))
        finally:
            with self._lock:
                self._active = None

    def _ignore_runs(self) -> None:
        """Runs are a local cache: git-ignore them inside `.behavior-review/`, the only place the UI writes."""
        ignore = self.root / HOME_DIR / ".gitignore"
        ignore.parent.mkdir(parents=True, exist_ok=True)
        existing = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
        if "runs/" not in existing.split():
            ignore.write_text(existing + ("" if not existing or existing.endswith("\n") else "\n") + "runs/\n", encoding="utf-8")

    def _interrupt_stale_runs(self) -> None:
        """A run still marked running when the server starts was cut off by the last shutdown."""
        for meta in self.list():
            if meta["status"] == "running":
                self._update(meta["id"], status="failed", error="interrupted: the server stopped during this run")
