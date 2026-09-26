"""AST symbol diff + bounded reverse-caller impact graph. Owner: A.

Static only: a reference we cannot resolve is reported as an Unknown when it
could reach a changed symbol — never silently treated as "no impact".
"""

from __future__ import annotations

import ast
import os
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from importlib.util import resolve_name
from pathlib import Path, PurePosixPath

from app.schemas import (
    ChangedSymbol,
    ChangeTag,
    Edge,
    Hop,
    ImpactPath,
    ImpactResult,
    Revision,
    RevisionPair,
    SymbolRef,
    Unknown,
)

MODULE = "<module>"
SKIP_DIRS = {"__pycache__", "node_modules", "venv", "build", "dist", "site-packages"}
MAX_REEXPORT_DEPTH = 3

# (qualname, enclosing class, nodes whose loads belong to that symbol)
Scope = tuple[str, str | None, list[ast.AST]]


@dataclass(frozen=True)
class SymbolDef:
    line: int
    signature: str
    body: str
    decorators: str = ""
    imports: str = ""


@dataclass
class Module:
    path: str
    name: str
    is_package: bool
    error: str | None = None
    symbols: dict[str, SymbolDef] = field(default_factory=dict)
    toplevel: SymbolDef | None = None
    scopes: list[Scope] = field(default_factory=list)
    # local name -> (absolute module, imported attribute or None for `import x`)
    bindings: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    star_imports: list[str] = field(default_factory=list)


# --- Parsing -----------------------------------------------------------------


def _iter_py_files(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def _dump(nodes: Iterable[ast.AST]) -> str:
    return ast.dump(ast.Module(body=list(nodes), type_ignores=[]))


def _body(node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    return node.body[1:] if ast.get_docstring(node, clean=False) is not None else node.body


def _assigned_name(stmt: ast.stmt) -> str | None:
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
        return stmt.targets[0].id
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
        return stmt.target.id
    return None


def _definitions(body: list[ast.stmt], prefix: str, cls: str | None, rest: list[ast.AST]) -> Iterator[tuple[str, str | None, ast.stmt]]:
    """Functions, methods and assigned names: the unit of both the diff and the caller graph.

    Everything else (imports, bare statements, class bases/decorators) is appended to `rest`.
    """
    for stmt in body:
        if isinstance(stmt, ast.ClassDef):
            qual = prefix + stmt.name
            rest.extend([*stmt.bases, *stmt.decorator_list])
            yield from _definitions(_body(stmt), f"{qual}.", qual, rest)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield prefix + stmt.name, cls, stmt
        elif name := _assigned_name(stmt):
            yield prefix + name, cls, stmt
        else:
            rest.append(stmt)


def _symbol_def(stmt: ast.stmt) -> SymbolDef:
    if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return SymbolDef(stmt.lineno, "", _dump([stmt]))
    signature = ast.unparse(stmt.args)
    if stmt.returns:
        signature += " -> " + ast.unparse(stmt.returns)
    if isinstance(stmt, ast.AsyncFunctionDef):
        signature = "async " + signature
    decorators = "\n".join(ast.unparse(d) for d in stmt.decorator_list)
    return SymbolDef(stmt.lineno, signature, _dump(_body(stmt)), decorators)


def _toplevel(rest: list[ast.AST]) -> SymbolDef | None:
    if not rest:
        return None
    is_import = lambda n: isinstance(n, (ast.Import, ast.ImportFrom))  # noqa: E731
    imports = "\n".join(sorted(ast.unparse(n) for n in rest if is_import(n)))
    code = [n for n in rest if not is_import(n)]
    return SymbolDef(min(n.lineno for n in rest), "", _dump(code), imports=imports)


def _import_source(node: ast.ImportFrom, module: Module) -> str | None:
    if node.level == 0:
        return node.module
    package = module.name if module.is_package else module.name.rpartition(".")[0]
    try:
        return resolve_name("." * node.level + (node.module or ""), package)
    except ImportError:
        return None  # relative import beyond the top-level package


def _collect_bindings(module: Module, tree: ast.Module) -> None:
    # Function-level imports are treated as module-wide: bounded, and errs toward more edges.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                module.bindings[local] = (alias.name if alias.asname else local, None)
        elif isinstance(node, ast.ImportFrom) and (source := _import_source(node, module)):
            for alias in node.names:
                if alias.name == "*":
                    module.star_imports.append(source)
                else:
                    module.bindings[alias.asname or alias.name] = (source, alias.name)


def _load_module(file: Path, rel: PurePosixPath, name: str) -> Module:
    module = Module(path=rel.as_posix(), name=name, is_package=rel.name == "__init__.py")
    try:
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=module.path)
    except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
        module.error = f"{type(exc).__name__}: {exc}"
        return module
    rest: list[ast.AST] = []
    for qual, cls, stmt in _definitions(_body(tree), "", None, rest):
        module.symbols[qual] = _symbol_def(stmt)
        module.scopes.append((qual, cls, [stmt]))
    module.toplevel = _toplevel(rest)
    module.scopes.append((MODULE, None, rest))
    _collect_bindings(module, tree)
    return module


def _import_names(rel: PurePosixPath, package_dirs: set[PurePosixPath]) -> list[str]:
    """How code imports this file (from its package root), then its repo-root dotted path.

    e.g. sample_project/pricing/discount.py → ["pricing.discount", "sample_project.pricing.discount"]
    """
    parts = rel.with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    start = len(rel.parent.parts)
    while start > 0 and PurePosixPath(*rel.parts[:start]) in package_dirs:
        start -= 1
    names = [".".join(parts[start:]), ".".join(parts)]
    return [n for n in dict.fromkeys(names) if n]


# --- Reference helpers --------------------------------------------------------


def _walk(nodes: list[ast.AST]) -> Iterator[ast.AST]:
    for node in nodes:
        yield from ast.walk(node)


def _terminal_name(expr: ast.Name | ast.Attribute) -> str:
    return expr.id if isinstance(expr, ast.Name) else expr.attr


def _getattr_literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
        arg = node.args[1]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


# --- Codebase (one revision) ------------------------------------------------


class Codebase:
    def __init__(self, root: Path):
        files = sorted(_iter_py_files(root))
        rels = [PurePosixPath(f.relative_to(root).as_posix()) for f in files]
        package_dirs = {r.parent for r in rels if r.name == "__init__.py"}
        self.modules: list[Module] = []
        # None marks an ambiguous name: references through it stay unresolved and surface as Unknowns.
        self._by_name: dict[str, Module | None] = {}
        for file, rel in zip(files, rels):
            names = _import_names(rel, package_dirs)
            module = _load_module(file, rel, names[0] if names else "")
            self.modules.append(module)
            for name in names:
                self._by_name[name] = None if name in self._by_name else module
        self.by_path = {m.path: m for m in self.modules}

    def _lookup(self, module: Module, name: str, depth: int = 0) -> SymbolRef | None:
        if depth > MAX_REEXPORT_DEPTH:
            return None
        if name in module.symbols:
            return SymbolRef(path=module.path, symbol=name)
        if name in module.bindings:
            source, attr = module.bindings[name]
            target = self._by_name.get(source)
            return self._lookup(target, attr, depth + 1) if attr and target else None
        for source in module.star_imports:
            if (target := self._by_name.get(source)) and (ref := self._lookup(target, name, depth + 1)):
                return ref
        return None

    def _module_of(self, module: Module, expr: ast.expr) -> Module | None:
        """The in-repo module `expr` denotes (e.g. `pricing.discount`), if any."""
        attrs: list[str] = []
        while isinstance(expr, ast.Attribute):
            attrs.append(expr.attr)
            expr = expr.value
        if not isinstance(expr, ast.Name) or expr.id not in module.bindings:
            return None
        source, attr = module.bindings[expr.id]
        return self._by_name.get(".".join([source, *([attr] if attr else []), *reversed(attrs)]))

    def resolve(self, module: Module, expr: ast.Name | ast.Attribute, cls: str | None) -> SymbolRef | None:
        if isinstance(expr, ast.Name):
            return self._lookup(module, expr.id)
        if cls and isinstance(expr.value, ast.Name) and expr.value.id in ("self", "cls"):
            qual = f"{cls}.{expr.attr}"
            return SymbolRef(path=module.path, symbol=qual) if qual in module.symbols else None
        target = self._module_of(module, expr.value)
        return self._lookup(target, expr.attr) if target else None

    def _references(self, module: Module, cls: str | None, nodes: list[ast.AST]) -> Iterator[tuple[ast.AST, SymbolRef | None, str, str]]:
        """(node, resolved target or None, referenced name, reason if unresolved) for every symbol-like load."""
        for node in _walk(nodes):
            if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
                yield node, self.resolve(module, node, cls), _terminal_name(node), "unresolved reference"
            elif name := _getattr_literal(node):
                yield node, None, name, "dynamic attribute access"

    def scan(self, revision: Revision, changed_by_name: dict[str, list[str]]) -> tuple[dict[tuple[str, str], Edge], list[Unknown]]:
        edges: dict[tuple[str, str], Edge] = {}
        unknowns: list[Unknown] = []
        for module in self.modules:
            if module.error:
                reason = f"could not parse on {revision}: {module.error}"
                unknowns.append(Unknown(path=module.path, line=1, symbol=MODULE, expression="", reason=reason))
                continue
            for qual, cls, nodes in module.scopes:
                caller = SymbolRef(path=module.path, symbol=qual)
                for node, callee, name, reason in self._references(module, cls, nodes):
                    if callee is None and name in changed_by_name:
                        unknowns.append(Unknown(
                            path=module.path, line=node.lineno, symbol=qual, expression=ast.unparse(node)[:120],
                            reason=reason, may_reach=changed_by_name[name],
                        ))
                    elif callee is not None and callee.key != caller.key:
                        key = (caller.key, callee.key)
                        if key not in edges or node.lineno < edges[key].line:
                            edges[key] = Edge(caller=caller, callee=callee, line=node.lineno, revisions=[revision])
        return edges, unknowns


# --- Diff, graph, paths -------------------------------------------------------


def _tags(before: SymbolDef | None, after: SymbolDef | None) -> list[ChangeTag]:
    if before is None:
        return [ChangeTag.ADDED]
    if after is None:
        return [ChangeTag.REMOVED]
    checks = [
        (before.signature != after.signature, ChangeTag.SIGNATURE_CHANGED),
        (before.body != after.body, ChangeTag.BODY_CHANGED),
        (before.decorators != after.decorators, ChangeTag.DECORATORS_CHANGED),
        (before.imports != after.imports, ChangeTag.IMPORTS_CHANGED),
    ]
    return [tag for changed, tag in checks if changed]


def _diffable(module: Module | None) -> dict[str, SymbolDef]:
    if module is None:
        return {}
    return {**module.symbols, MODULE: module.toplevel} if module.toplevel else module.symbols


def _diff(base: Codebase, head: Codebase, changed_files: list[str]) -> list[ChangedSymbol]:
    changed: list[ChangedSymbol] = []
    for path in changed_files:
        before, after = base.by_path.get(path), head.by_path.get(path)
        if not path.endswith(".py") or (before and before.error) or (after and after.error):
            continue  # unparseable files surface as Unknowns instead of fake add/remove churn
        old, new = _diffable(before), _diffable(after)
        for qual in sorted(old.keys() | new.keys()):
            if tags := _tags(old.get(qual), new.get(qual)):
                changed.append(ChangedSymbol(
                    path=path, symbol=qual, tags=tags,
                    base_line=old[qual].line if qual in old else None,
                    head_line=new[qual].line if qual in new else None,
                ))
    return changed


def _changed_by_name(changed: list[ChangedSymbol]) -> dict[str, list[str]]:
    by_name: dict[str, list[str]] = defaultdict(list)
    for symbol in changed:
        if symbol.symbol != MODULE:
            by_name[symbol.symbol.rsplit(".", 1)[-1]].append(symbol.key)
    return dict(by_name)


def _merge_edges(base: dict[tuple[str, str], Edge], head: dict[tuple[str, str], Edge]) -> list[Edge]:
    merged = dict(base)
    for key, edge in head.items():
        if key in merged:
            merged[key].revisions.append(Revision.HEAD)
            merged[key].line = edge.line  # prefer the head line: that's the code the author is looking at
        else:
            merged[key] = edge
    return sorted(merged.values(), key=lambda e: (e.callee.key, e.caller.key))


def _is_test(ref: SymbolRef) -> bool:
    path = PurePosixPath(ref.path)
    return (
        bool({"tests", "test"} & set(path.parent.parts))
        or path.name.startswith("test_")
        or path.name.endswith("_test.py")
        or path.name == "conftest.py"
    )


def _impact_paths(changed: list[ChangedSymbol], edges: list[Edge], changed_files: list[str], max_hops: int) -> list[ImpactPath]:
    callers_of: dict[str, list[Edge]] = defaultdict(list)
    for edge in edges:
        callers_of[edge.callee.key].append(edge)
    touched = set(changed_files)
    paths: list[ImpactPath] = []
    for symbol in changed:
        stack = [[Hop(path=symbol.path, symbol=symbol.symbol, line=symbol.head_line or symbol.base_line or 0)]]
        while stack:
            chain = stack.pop()
            for edge in callers_of.get(chain[0].key, []):
                if any(hop.key == edge.caller.key for hop in chain):
                    continue
                hops = [Hop(path=edge.caller.path, symbol=edge.caller.symbol, line=edge.line), *chain]
                first = hops[0]
                paths.append(ImpactPath(hops=hops, outside_diff=first.path not in touched, is_test=_is_test(first)))
                if len(hops) - 1 < max_hops:
                    stack.append(hops)
    return sorted(paths, key=lambda p: (not p.outside_diff, p.is_test, len(p.hops), p.render()))


def _edges_on_paths(edges: list[Edge], paths: list[ImpactPath]) -> list[Edge]:
    used = {(a.key, b.key) for p in paths for a, b in zip(p.hops, p.hops[1:])}
    return [e for e in edges if (e.caller.key, e.callee.key) in used]


def _dedupe_unknowns(unknowns: list[Unknown]) -> list[Unknown]:
    seen: dict[tuple[str, int, str, str], Unknown] = {}
    for unknown in unknowns:
        seen.setdefault((unknown.path, unknown.line, unknown.expression, unknown.reason), unknown)
    return sorted(seen.values(), key=lambda u: (u.path, u.line))


def analyze(pair: RevisionPair, max_hops: int = 2) -> ImpactResult:
    base, head = Codebase(Path(pair.base_path)), Codebase(Path(pair.head_path))
    changed_files = pair.revisions.changed_files
    changed = _diff(base, head, changed_files)
    by_name = _changed_by_name(changed)
    base_edges, base_unknowns = base.scan(Revision.BASE, by_name)
    head_edges, head_unknowns = head.scan(Revision.HEAD, by_name)
    edges = _merge_edges(base_edges, head_edges)
    paths = _impact_paths(changed, edges, changed_files, max_hops)
    return ImpactResult(
        changed_symbols=changed,
        edges=_edges_on_paths(edges, paths),
        paths=paths,
        unknowns=_dedupe_unknowns(base_unknowns + head_unknowns),
        max_hops=max_hops,
    )
