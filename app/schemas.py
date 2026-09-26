"""Shared data contracts between lanes. Owner: A.

Any change here must be announced to the other lanes and the fixtures in
contracts/ updated in the same commit (tests/test_contracts.py validates them).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "0.1"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Revision(StrEnum):
    BASE = "base"
    HEAD = "head"


# --- Revision pair (snapshot) -------------------------------------------------


class Revisions(Model):
    base_ref: str
    head_ref: str
    base_sha: str
    head_sha: str
    changed_files: list[str] = Field(
        description="Repo-relative POSIX paths that differ between base and head."
    )


class RevisionPair(Model):
    """Runtime-only: holds local checkout paths, so only `repo` and `revisions` go into a report."""

    repo: str = Field(description="'owner/name' from the origin remote, else the folder name.")
    root: str
    base_path: str
    head_path: str
    revisions: Revisions


# --- Impact (AST + graph) -----------------------------------------------------


class ChangeTag(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    SIGNATURE_CHANGED = "signature_changed"
    BODY_CHANGED = "body_changed"
    DECORATORS_CHANGED = "decorators_changed"
    IMPORTS_CHANGED = "imports_changed"


class SymbolRef(Model):
    path: str = Field(description="Repo-relative POSIX path of the defining file.")
    symbol: str = Field(description="Qualified name inside the module, e.g. 'Cart.total'. '<module>' for module level.")

    @property
    def key(self) -> str:
        return f"{self.path}::{self.symbol}"


class ChangedSymbol(SymbolRef):
    tags: list[ChangeTag]
    base_line: int | None = None
    head_line: int | None = None


class Edge(Model):
    caller: SymbolRef
    callee: SymbolRef
    line: int = Field(description="Call-site line in the caller's file.")
    revisions: list[Revision] = Field(description="Revisions in which this edge exists.")


class Hop(SymbolRef):
    line: int = Field(description="Line of the call to the next hop (or the definition line for the changed symbol).")


class ImpactPath(Model):
    hops: list[Hop] = Field(description="Caller first, changed symbol last.")
    outside_diff: bool = Field(description="The first caller lives in a file not touched by the diff.")
    is_test: bool = Field(description="The first caller is test code.")

    def render(self) -> str:
        return " → ".join(h.symbol for h in self.hops)


class Unknown(Model):
    path: str
    line: int
    symbol: str = Field(description="Enclosing symbol of the unresolved reference.")
    expression: str
    reason: str
    may_reach: list[str] = Field(default_factory=list, description="Changed symbol keys this might reach.")


class ImpactResult(Model):
    changed_symbols: list[ChangedSymbol]
    edges: list[Edge]
    paths: list[ImpactPath]
    unknowns: list[Unknown]
    max_hops: int


# --- Execution (runner, owner B) ----------------------------------------------


class Outcome(StrEnum):
    SAME_ON_TESTED_CASES = "same_on_tested_cases"
    DELTA_OBSERVED = "delta_observed"
    PRE_EXISTING_FAILURE = "pre_existing_failure"
    INCONCLUSIVE = "inconclusive"


class RunStatus(StrEnum):
    OK = "ok"
    EXCEPTION = "exception"
    ERROR = "error"
    TIMEOUT = "timeout"


class Probe(Model):
    id: str
    target: SymbolRef
    input: dict[str, Any]
    hash: str = Field(description="sha256 of the probe file bytes; identical on both revisions.")
    authored_by: str = Field(default="bob", description="'bob' or 'human'.")


class ProbeBundle(Model):
    probes: list[Probe]


class Observation(Model):
    revision: Revision
    sha: str
    status: RunStatus
    output: Any = None
    exception: str | None = None
    duration_ms: int | None = None


class Comparison(Model):
    probe: Probe
    base: Observation
    head: Observation
    outcome: Outcome
    ran_at: datetime
    reruns: str | None = Field(default=None, description="probe_id of the earlier delta this rerun resolves.")


class SuiteRun(Model):
    revision: Revision
    sha: str
    status: RunStatus
    passed: int
    failed: int
    errors: int
    suite_hash: str = Field(description="Hash of the frozen base test suite run on both sides.")


# --- Decisions (owner D) ------------------------------------------------------


class Intent(StrEnum):
    UNINTENDED = "unintended"
    INTENDED = "intended"
    UNRESOLVED = "unresolved"


class DecisionStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    STALE = "stale"
    SUPERSEDED = "superseded"


class Decision(Model):
    id: str
    repo: str
    target: SymbolRef
    base_sha: str
    head_sha: str
    probe_hash: str
    before: Any
    after: Any
    intent: Intent
    rationale: str | None = None
    requirement_ref: str | None = None
    status: DecisionStatus = DecisionStatus.PROPOSED
    supersedes: str | None = None


# --- Report (the one object every door consumes) ------------------------------


class ReviewReport(Model):
    schema_version: str = SCHEMA_VERSION
    fixture: bool = Field(default=False, description="True for hand-written contract examples; never shown in the final demo.")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    repo: str = Field(description="'owner/name' from the origin remote, else the folder name. Matches Decision.repo.")
    revisions: Revisions
    impact: ImpactResult
    tests: list[SuiteRun] = Field(default_factory=list)
    comparisons: list[Comparison] = Field(default_factory=list)
    needs_bob_action: list[SymbolRef] = Field(
        default_factory=list, description="Impacted non-test callers with no committed probe."
    )
    decisions: list[Decision] = Field(default_factory=list)
    prior_decisions: list[Decision] = Field(default_factory=list, description="Approved/stale ledger records matching changed symbols.")
    limits: list[str] = Field(default_factory=list)
