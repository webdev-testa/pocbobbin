"""Minimal probe runner (lane B contract slice).

Executes one probe JSON file against a checkout and prints the canonical JSON
output. Same script bytes are used on both revisions, so any difference in the
printed output is a real behavioral difference, not a runner artifact.

Standard library only: it runs under the reviewed project's interpreter, which
need not have behavior-review or its dependencies installed.

Usage:  python run_probe.py probes/price_total_boundary.json   (cwd = the checkout)
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


def load_target(spec: str):
    module_path, _, func_name = spec.partition(":")
    module = importlib.import_module(module_path)
    return getattr(module, func_name)


def main(argv: list[str]) -> int:
    probe_path = Path(argv[1])
    probe = json.loads(probe_path.read_text())
    # The checkout under test is the cwd, not wherever this script lives; a `src/` layout
    # package is importable from `src/`.
    root = Path.cwd()
    sys.path[:0] = [str(root)] + ([str(root / "src")] if (root / "src").is_dir() else [])
    fn = load_target(probe["target"])
    try:
        result = fn(*probe["args"])
        payload = {"probe": probe["id"], "outcome": "value", "value": result}
    except Exception as exc:  # noqa: BLE001 - the classification is the point
        payload = {
            "probe": probe["id"],
            "outcome": "exception",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
