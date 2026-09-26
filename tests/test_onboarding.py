"""`behavior-review init`, `run` and `doctor` (LANE_F_PLAN F-D §7.2–§7.4) and the templates init writes."""

import json
from pathlib import Path

from app.cli import main
from app.onboarding import TEMPLATES
from app.runs import RUNS_DIR

ROOT = Path(__file__).resolve().parents[1]
CALC = "def multiply(a, b):\n    return a * b\n"
TEST = "from calc import multiply\n\n\ndef test_multiply():\n    assert multiply(2, 3) == 6\n"
PROJECT = {"calc.py": CALC, "tests/test_calc.py": TEST}
README = {"README.md": "# calc\n"}  # make_repo needs one change on head


def _init(repo, *extra):
    return main(["init", "--repo", str(repo), "--yes", "--base-branch", "base", *extra])


def test_init_writes_the_folder_the_action_and_the_bob_mode(make_repo, capsys):
    repo = make_repo(PROJECT, {"calc.py": CALC.replace("a * b", "abs(a * b)")})

    assert _init(repo) == 0

    config = json.loads((repo / ".behavior-review/config.json").read_text(encoding="utf-8"))
    assert config == {"language": "python", "base_branch": "base", "tests_dir": "tests"}
    for name in ("probes/README.md", "decisions/.gitkeep"):
        assert (repo / ".behavior-review" / name).is_file()
    assert (repo / ".behavior-review/.gitignore").read_text(encoding="utf-8") == "runs/\n"
    workflow = (repo / ".github/workflows/behavior-review.yml").read_text(encoding="utf-8")
    assert "__BEHAVIOR_REVIEW_REF__" not in workflow and "pocbobbin@" in workflow
    assert "slug: behavior-review" in (repo / ".bob/custom_modes.yaml").read_text(encoding="utf-8")
    assert "Next: commit .behavior-review/" in capsys.readouterr().out


def test_init_never_overwrites_and_merges_the_bob_mode(make_repo):
    other_mode = "customModes:\n  - slug: reviewer\n    name: Reviewer\n"
    repo = make_repo({**PROJECT, ".github/workflows/behavior-review.yml": "mine\n", ".bob/custom_modes.yaml": other_mode}, README)

    assert _init(repo, "--no-action") == 0
    assert _init(repo) == 0  # a second run changes nothing

    assert (repo / ".github/workflows/behavior-review.yml").read_text(encoding="utf-8") == "mine\n"
    modes = (repo / ".bob/custom_modes.yaml").read_text(encoding="utf-8")
    assert modes.startswith(other_mode) and modes.count("slug: behavior-review") == 1


def test_init_carries_a_legacy_setup_over(make_repo):
    legacy = {"behavior.json": json.dumps({"language": "python", "tests_dir": "tests", "max_hops": 3}), "probes/p.json": "{}"}
    repo = make_repo({**PROJECT, **legacy}, README)

    assert _init(repo, "--no-action", "--no-bob") == 0

    config = json.loads((repo / ".behavior-review/config.json").read_text(encoding="utf-8"))
    assert config["max_hops"] == 3 and config["base_branch"] == "base"
    assert not (repo / ".behavior-review/probes").exists(), "legacy probes/ stays the probe folder"


def test_init_asks_when_a_person_is_at_the_terminal(make_repo, monkeypatch):
    repo = make_repo(PROJECT, README)
    answers = iter(["base", "none", "", "n", "n"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert main(["init", "--repo", str(repo)]) == 0

    config = json.loads((repo / ".behavior-review/config.json").read_text(encoding="utf-8"))
    assert config == {"language": "python", "base_branch": "base"}
    assert not (repo / ".github").exists() and not (repo / ".bob").exists()


def test_run_reviews_into_the_history_the_ui_shows(make_repo, capsys):
    repo = make_repo(PROJECT, {"calc.py": CALC.replace("a * b", "abs(a * b)")})
    (repo / "calc.py").write_text(CALC, encoding="utf-8")  # an uncommitted edit: reviewed anyway, HEAD is what counts

    assert main(["run", "--repo", str(repo), "--yes", "--base", "base"]) == 0

    [run] = (repo / RUNS_DIR).iterdir()
    assert json.loads((run / "meta.json").read_text(encoding="utf-8"))["status"] == "done"
    out = capsys.readouterr().out
    assert "  triage: code_change" in out and "behavior-review ui" in out


def test_run_compares_with_the_configured_base_branch(make_repo, capsys):
    repo = make_repo({**PROJECT, ".behavior-review/config.json": '{"base_branch": "base"}'},
                     {"calc.py": CALC.replace("a * b", "abs(a * b)")})

    assert main(["run", "--repo", str(repo), "--yes"]) == 0

    [run] = (repo / RUNS_DIR).iterdir()
    assert json.loads((run / "meta.json").read_text(encoding="utf-8"))["base"] == "base"


def test_doctor_explains_and_fails_only_on_blockers(make_repo, tmp_path, capsys):
    repo = make_repo(PROJECT, README)
    assert main(["doctor", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "✔ tests: tests/" in out and "! probes: none yet" in out

    assert main(["doctor", "--repo", str(tmp_path / "nowhere")]) == 1
    assert "✖ not a git repository" in capsys.readouterr().out


def test_the_templates_match_this_repositorys_own_files():
    """One source of truth: the Bob mode init writes is ours, and the Action's comment step is ours."""
    assert (TEMPLATES / "custom_modes.yaml").read_bytes().replace(b"\r\n", b"\n") == (ROOT / ".bob/custom_modes.yaml").read_bytes().replace(b"\r\n", b"\n")
    ours = (ROOT / ".github/workflows/behavior-review.yml").read_text(encoding="utf-8").replace("\r\n", "\n")
    template = (TEMPLATES / "behavior-review.yml").read_text(encoding="utf-8").replace("\r\n", "\n")
    marker = "      - name: Upload Review Artifact"
    assert template[template.index(marker):] == ours[ours.index(marker):]
