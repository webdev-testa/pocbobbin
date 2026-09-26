"""What `uv tool install git+…` gets: the wheel must carry the probe runners and the built web page."""

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_wheel_ships_the_runners_and_the_web_page(tmp_path):
    subprocess.run([sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-q", "-w", str(tmp_path)],
                   check=True, capture_output=True)
    [wheel] = tmp_path.glob("*.whl")

    names = set(zipfile.ZipFile(wheel).namelist())

    assert {"app/harness/run_probe.py", "app/harness/run_command_probe.py", "app/harness/run_probe.ts"} <= names
    assert "app/web_dist/index.html" in names
    assert any(name.startswith("app/web_dist/assets/") for name in names)
    assert not any(name.startswith("app/web_dist/data/") for name in names), "the demo data stays on the static site"
