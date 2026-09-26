"""The local web UI: `behavior-review ui` serves the viewer and its API (LANE_F_PLAN F-F §8.4).

It runs reviews of the one repository it was started in, and writes only under
`.behavior-review/`. Safeguards: it binds to 127.0.0.1, every `/api` call carries the token
from the URL it printed, a foreign `Host` header is refused (so another website can't reach it
through DNS rebinding), and it sends no CORS headers.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import webbrowser
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.adapters.registry import get_adapter
from app.config import ConfigError, load_config
from app.decisions import decide_from_report, ledger_dir, load_branch_decisions_via_git, load_ledger
from app.runs import RunBusy, RunStore, review_to_folder
from app.schemas import ReviewReport
from app.snapshot import SnapshotError, default_base, git, repo_slug, resolve_commit

WEB_DIST = Path(__file__).parent / "web_dist"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


class RunRequest(BaseModel):
    base: str
    head: str
    full: bool = False


class DecisionRequest(BaseModel):
    run_id: str
    probe_id: str
    intent: Literal["intended", "unintended", "unresolved"]
    rationale: str | None = None
    requirement_ref: str | None = None


def _commit_ref(root: Path, ref: str) -> str:
    # A leading "-" would reach git as an option; the value is argv, never a shell string.
    if not ref or ref.startswith("-"):
        raise HTTPException(422, f"'{ref}' is not a branch or commit")
    try:
        resolve_commit(root, ref)
    except SnapshotError:
        raise HTTPException(422, f"'{ref}' is not a branch or commit in this repository") from None
    return ref


def _base(root: Path) -> str:
    """The configured base branch if valid, else the detected default (a broken config never blocks this)."""
    try:
        configured = load_config(root).base_branch
    except ConfigError:
        configured = None
    return default_base(root, configured)


def _repo_info(root: Path) -> dict:
    try:
        languages = [{"language": lang, "tier": get_adapter(lang).spec.tier} for lang in load_config(root).languages]
    except ConfigError as exc:
        languages, config_error = [], str(exc)
    else:
        config_error = None
    return {
        "repo": repo_slug(root), "branch": git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "default_base": _base(root), "languages": languages, "config_error": config_error,
    }


def _branches(root: Path) -> list[dict]:
    out = git(root, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)\t%(objectname:short)\t%(committerdate:iso-strict)",
              "refs/heads", "refs/remotes")
    rows = [line.split("\t") for line in out.splitlines() if line and not line.startswith("origin/HEAD")]
    return [{"name": name, "sha": sha, "date": date} for name, sha, date in rows]


def _decisions(root: Path) -> dict:
    """Approved: merged on the default base branch. Proposed: in the working tree, not merged yet."""
    approved = load_branch_decisions_via_git(root, _base(root))
    merged = {d.id for d in approved}
    proposed = [d for d in load_ledger(root) if d.id not in merged]
    return {"approved": [d.model_dump(mode="json") for d in approved],
            "proposed": [d.model_dump(mode="json") for d in proposed],
            "folder": ledger_dir(root).relative_to(root).as_posix()}


def _events(store: RunStore, run_id: str) -> StreamingResponse:
    """Server-sent events: each step as it happens, then `end` with the run's final state."""
    async def stream():
        sent = 0
        while True:
            meta = store.meta(run_id)
            for step in meta["steps"][sent:]:
                yield f"data: {json.dumps(step)}\n\n"
            sent = len(meta["steps"])
            if meta["status"] != "running":
                yield f"event: end\ndata: {json.dumps(meta)}\n\n"
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(stream(), media_type="text/event-stream")


def _guard(app: FastAPI, token: str) -> None:
    @app.middleware("http")
    async def check(request: Request, call_next):
        if request.headers.get("host", "").rsplit(":", 1)[0] not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "this server only answers on 127.0.0.1"}, status_code=403)
        supplied = request.headers.get("x-token") or request.query_params.get("token") or ""
        if request.url.path.startswith("/api/") and not secrets.compare_digest(supplied, token):
            return JSONResponse({"detail": "missing or wrong token; open the URL behavior-review ui printed"}, status_code=401)
        return await call_next(request)


def _repo_routes(app: FastAPI, root: Path) -> None:
    app.get("/api/health")(lambda: {"mode": "local", "repo": repo_slug(root)})
    app.get("/api/repo")(lambda: _repo_info(root))
    app.get("/api/branches")(lambda: _branches(root))
    app.get("/api/decisions")(lambda: _decisions(root))


def _run_routes(app: FastAPI, root: Path, store: RunStore) -> None:
    def found(action):
        try:
            return action()
        except KeyError:
            raise HTTPException(404, "no such run or file") from None

    @app.post("/api/runs", status_code=201)
    def start(request: RunRequest):
        try:
            return {"id": store.start(_commit_ref(root, request.base), _commit_ref(root, request.head), request.full)}
        except RunBusy as busy:
            raise HTTPException(409, f"review {busy} is still running; the UI runs one at a time") from None

    app.get("/api/runs")(store.list)
    app.get("/api/runs/{run_id}")(lambda run_id: found(lambda: store.meta(run_id)))
    app.get("/api/runs/{run_id}/events")(lambda run_id: found(lambda: store.meta(run_id) and _events(store, run_id)))

    @app.get("/api/runs/{run_id}/{kind}")
    def run_file(run_id: str, kind: Literal["report", "repo_map", "markdown"]):
        return found(lambda: FileResponse(store.file(run_id, kind)))

    @app.post("/api/decisions", status_code=201)
    def decide(request: DecisionRequest):
        report_path = found(lambda: store.file(request.run_id, "report"))
        report = ReviewReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        try:
            decision, written = decide_from_report(report, request.probe_id, request.intent, request.rationale,
                                                   root, request.requirement_ref)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"decision": decision.model_dump(mode="json"), "path": written, "git": f"git add {written}"}


def create_app(root: Path, token: str, store: RunStore | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    store = store or RunStore(root, review_to_folder)
    _guard(app, token)
    _repo_routes(app, root)
    _run_routes(app, root, store)
    if (WEB_DIST / "index.html").is_file():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


def serve(root: Path, port: int, open_browser: bool, run_id: str | None = None) -> int:
    import uvicorn

    token = secrets.token_urlsafe(16)
    url = f"http://127.0.0.1:{port}/?token={token}" + (f"&run={run_id}" if run_id else "")
    # Flushed: when output is piped (an IDE task, a script), the URL must show before the server blocks.
    print(f"behavior-review ui for {repo_slug(root)}: {url}", flush=True)
    if not (WEB_DIST / "index.html").is_file():
        print("  (this install has no built web page; the API still answers under /api)", flush=True)
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(root, token), host="127.0.0.1", port=port, log_level="warning")
    return 0


__all__ = ["create_app", "serve"]
