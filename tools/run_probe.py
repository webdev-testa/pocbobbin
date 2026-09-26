"""Minimal probe runner (lane B contract slice).

Executes one probe JSON file against a checkout and prints the canonical JSON
output. Same script bytes are used on both revisions, so any difference in the
printed output is a real behavioral difference, not a runner artifact.

Usage:  python tools/run_probe.py probes/price_total_boundary.json

Probe `target` accepts either spelling:
  * "package.module:function"        -- concise, module importable from the checkout
  * {"path": "pkg/mod.py", "symbol": "function"} -- Bob IDE mode's documented object form;
    the file is imported from its path, so a module that is not on sys.path still resolves.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path


def _package_root_and_name(path: Path) -> tuple[Path, str] | None:
    """Split a source file into (import root, dotted module name) using its package layout.

    A monorepo keeps the Python package root somewhere below the repo root (e.g. ``backend/``),
    and the module's own internal imports are written against that root. Importing the file by
    path would run it in isolation and its real imports would fail, so resolve the package first.
    """
    resolved = path.resolve()
    parts: list[str] = [resolved.stem]
    parent = resolved.parent
    while (parent / "__init__.py").exists():
        parts.append(parent.name)
        parent = parent.parent
    if len(parts) == 1:
        return None  # not part of a package: nothing to resolve, import by path instead
    return parent, ".".join(reversed(parts))


def _import_from_path(path: Path):
    """Import a source file from the checkout under test, preferring its real package name."""
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    package = _package_root_and_name(resolved)
    if package is not None:
        root, dotted = package
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            return importlib.import_module(dotted)
        except Exception:  # noqa: BLE001 - fall back to a path import and let the error surface
            pass
    module_name = "_probe_" + resolved.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _resolve(module, symbol: str):
    """Resolve 'Class.method' as well as a bare function name.

    A method resolved off its class is unbound, so calling it would bind the first argument as
    `self`. Instantiate the class when it can be instantiated without arguments, so a probe can
    call a method the same way a caller does.
    """
    parts = symbol.split(".")
    owner = getattr(module, parts[0])
    if len(parts) == 1:
        return owner
    if isinstance(owner, type):
        try:
            owner = owner()
        except Exception:  # noqa: BLE001 - fall back to the unbound attribute and let it surface
            pass
    target = owner
    for part in parts[1:]:
        target = getattr(target, part)
    return target


def load_target(spec):
    """Accept the string form and the object form the Bob mode documents."""
    if isinstance(spec, dict):
        path = Path(str(spec["path"]))
        if not path.exists():
            raise FileNotFoundError(f"probe target path does not exist: {path}")
        return _resolve(_import_from_path(path), str(spec["symbol"]))
    module_path, _, func_name = str(spec).partition(":")
    if not module_path or not func_name:
        raise ValueError(f"probe target must be 'module:function' or {{'path', 'symbol'}}, got {spec!r}")
    return _resolve(importlib.import_module(module_path), func_name)


def main(argv: list[str]) -> int:
    probe_path = Path(argv[1])
    probe = json.loads(probe_path.read_text())
    # The checkout under test is the cwd, not wherever this script lives.
    sys.path.insert(0, str(Path.cwd()))
    try:
        fn = load_target(probe["target"])
        result = fn(*probe.get("args", []))
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
