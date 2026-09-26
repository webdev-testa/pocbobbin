"""`behavior-review ui`'s API (LANE_F_PLAN F-F §8.4, §8.7): safeguards, runs, decisions."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.runs import RunStore
from app.server import create_app

TOKEN = "t0ken"
CALC = "def multiply(a, b):\n    return a * b\n"
CHANGED = "def multiply(a, b):\n    return abs(a * b)\n"
PROBE = json.dumps({"id": "multiply_negative", "target": "calc:multiply", "args": [2, -3]})


@pytest.fixture
def repo(make_repo):
    return make_repo({"calc.py": CALC, ".behavior-review/probes/multiply.json": PROBE}, {"calc.py": CHANGED})


def _client(app, host="127.0.0.1") -> TestClient:
    return TestClient(app, base_url=f"http://{host}", headers={"x-token": TOKEN})


def _wait(client: TestClient, run_id: str) -> dict:
    for _ in range(300):
        meta = client.get(f"/api/runs/{run_id}").json()
        if meta["status"] != "running":
            return meta
        time.sleep(0.2)
    raise AssertionError("the review did not finish")


def test_every_api_call_needs_the_token(repo):
    app = create_app(repo, TOKEN)
    assert TestClient(app, base_url="http://127.0.0.1").get("/api/health").status_code == 401
    assert TestClient(app, base_url="http://127.0.0.1", headers={"x-token": "wrong"}).get("/api/health").status_code == 401
    assert TestClient(app, base_url="http://127.0.0.1").get(f"/api/health?token={TOKEN}").json()["mode"] == "local"


def test_the_packaged_page_is_served(repo):
    page = TestClient(create_app(repo, TOKEN), base_url="http://127.0.0.1").get("/")
    assert page.status_code == 200 and '<div id="root">' in page.text


def test_a_foreign_host_is_refused(repo):
    assert _client(create_app(repo, TOKEN), host="evil.example").get("/api/health").status_code == 403


def test_a_review_runs_in_the_background_and_lands_in_the_git_ignored_history(repo):
    client = _client(create_app(repo, TOKEN))

    run_id = client.post("/api/runs", json={"base": "base", "head": "head"}).json()["id"]
    meta = _wait(client, run_id)

    assert (meta["status"], meta["triage"]) == ("done", "code_change"), meta.get("error")
    assert [s["step"] for s in meta["steps"]] == ["impact", "triage", "probes", "repo map"]
    [comparison] = client.get(f"/api/runs/{run_id}/report").json()["comparisons"]
    assert (comparison["base"]["output"], comparison["head"]["output"]) == (-6, 6)
    assert client.get(f"/api/runs/{run_id}/repo_map").json()["modules"]
    assert "runs/" in (repo / ".behavior-review/.gitignore").read_text(encoding="utf-8").split()
    assert [run["id"] for run in client.get("/api/runs").json()] == [run_id]
    events = client.get(f"/api/runs/{run_id}/events").text
    assert '"step": "probes"' in events and "event: end" in events


def test_one_review_at_a_time(repo):
    release = threading.Event()

    def slow_review(root, base, head, full, folder, emit):
        release.wait(10)
        return {}

    client = _client(create_app(repo, TOKEN, RunStore(repo, slow_review)))
    first = client.post("/api/runs", json={"base": "base", "head": "head"})
    second = client.post("/api/runs", json={"base": "base", "head": "head"})
    release.set()

    assert (first.status_code, second.status_code) == (201, 409)


@pytest.mark.parametrize("ref", ["--output=/tmp/x", "no-such-branch", ""])
def test_a_review_takes_only_real_branches_or_commits(repo, ref):
    response = _client(create_app(repo, TOKEN)).post("/api/runs", json={"base": ref, "head": "head"})
    assert response.status_code == 422


def test_run_files_cant_leave_the_history(repo):
    client = _client(create_app(repo, TOKEN))
    assert client.get("/api/runs/..%2F..%2Fcalc.py/report").status_code == 404
    assert client.get("/api/runs/20260101-000000-abcd/report").status_code == 404


def test_a_decision_is_saved_as_proposed_in_the_ledger(repo):
    client = _client(create_app(repo, TOKEN))
    run_id = client.post("/api/runs", json={"base": "base", "head": "head"}).json()["id"]
    _wait(client, run_id)
    decision = {"run_id": run_id, "probe_id": "multiply_negative", "intent": "intended"}

    assert client.post("/api/decisions", json={**decision, "rationale": "short"}).status_code == 422
    saved = client.post("/api/decisions", json={**decision, "rationale": "Products are absolute from now on."})

    body = saved.json()
    assert saved.status_code == 201 and body["decision"]["status"] == "proposed"
    assert body["path"].startswith(".behavior-review/decisions/") and (repo / body["path"]).is_file()
    assert body["git"] == f"git add {body['path']}"
    assert [d["id"] for d in client.get("/api/decisions").json()["proposed"]] == [body["decision"]["id"]]
