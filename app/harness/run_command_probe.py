"""Run a repository-owned command probe for compiled and non-Python projects.

The probe JSON owns the command argv. The same frozen probe bytes are supplied
to both revisions by ``app.runner``. The command must print either a JSON
value or the canonical ``outcome`` object; no shell is involved.

Example probe:

    {
      "id": "discount",
      "command": ["go", "run", "./cmd/probe", "{input}"] ,
      "input": {"value": 10}
    }
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _payload(probe: dict, command: list[str]) -> dict:
    encoded_input = json.dumps(probe.get("input", {"args": probe.get("args", [])}), separators=(",", ":"))
    resolved = [
        encoded_input if token in {"{input}", "${INPUT}", "$PROBE_JSON"} else token
        for token in command
    ]
    try:
        completed = subprocess.run(
            resolved,
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"probe": probe["id"], "outcome": "error", "error_type": type(exc).__name__, "error": str(exc)}
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-400:]
        return {"probe": probe["id"], "outcome": "error", "error_type": "ProcessError", "error": detail}
    output = completed.stdout.strip()
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        value = output
    if isinstance(value, dict) and value.get("outcome") in {"value", "exception", "error", "inconclusive"}:
        return {"probe": probe["id"], **value}
    return {"probe": probe["id"], "outcome": "value", "value": value}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"outcome": "error", "error_type": "UsageError", "error": "expected one probe JSON path"}))
        return 0
    try:
        probe = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        command = probe.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            raise ValueError("probe must define a non-empty command string array")
        print(json.dumps(_payload(probe, command), sort_keys=True))
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(json.dumps({"outcome": "error", "error_type": type(exc).__name__, "error": str(exc)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
