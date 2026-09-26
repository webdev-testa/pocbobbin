"""Decision ledger management for Behavior Review.

Handles validation, saving, and historical lookup of behavior decisions.
Follows the Management of Change (MOC) principle:
- Propose during review on a PR branch (status: proposed).
- Approves automatically when merged into main (status: approved).
- Lookup surfaces prior decisions to provide context for subsequent reviews.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from app.schemas import Decision, DecisionStatus, Intent, SymbolRef


class DecisionMatch(BaseModel):
    """Result of looking up prior decisions for an impacted symbol."""

    symbol: str
    decision: Decision
    match_type: Literal["approved", "stale", "superseded"] = "approved"
    is_stale: bool = False
    reason: Optional[str] = None


def generate_decision_id(
    repo: str,
    symbol: str,
    base_sha: str,
    head_sha: str,
    probe_hash: str,
) -> str:
    """Generate a deterministic 12-char SHA-256 ID for a decision."""
    raw = f"{repo.strip()}:{symbol.strip()}:{base_sha.strip()}:{head_sha.strip()}:{probe_hash.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def current_decision(
    records: List[Decision],
    path: str,
    symbol: str,
    *,
    exclude: Optional[str] = None,
) -> Optional[Decision]:
    """The record for path + symbol that no other record supersedes: the one a new decision replaces.

    Ids are content hashes, so ledger (file) order says nothing about age; only the `supersedes`
    chain does. `exclude` drops a record being rewritten under the same id, so its predecessor is
    current again. A ledger with several unreplaced records falls back to the last by id.
    """
    same = [d for d in records if d.target.path == path and d.target.symbol == symbol and d.id != exclude]
    replaced = {d.supersedes for d in same if d.supersedes}
    return max((d for d in same if d.id not in replaced), key=lambda d: d.id, default=None)


def validate_and_save(
    delta: Union[Dict[str, Any], Any],
    disposition: Union[str, Intent],
    rationale: Optional[str] = None,
    *,
    repo_root: Optional[Union[Path, str]] = None,
    repo: str = "",
    base_sha: str = "",
    head_sha: str = "",
    requirement_ref: Optional[str] = None,
    supersedes: Optional[str] = None,
    status: Union[str, DecisionStatus] = DecisionStatus.PROPOSED,
) -> Decision:
    """Validate a behavior decision and record it into the behavior_decisions/ ledger.

    Args:
        delta: Dictionary or object containing observed difference metadata
               (symbol, path, probe_hash, before/after behavior).
        disposition: One of 'unintended', 'intended', or 'unresolved' (or Intent enum).
        rationale: Human-written reason for change. Mandatory when disposition is 'intended'.
        repo_root: Root directory of repository. Defaults to cwd.
        repo: Repository identifier.
        base_sha: Base commit SHA.
        head_sha: Head commit SHA.
        requirement_ref: Optional requirement/issue reference.
        supersedes: Explicit ID of previous decision being superseded.
        status: DecisionStatus.PROPOSED (default) or DecisionStatus.APPROVED.

    Returns:
        The validated and persisted Decision instance from app.schemas.

    Raises:
        ValueError: If disposition is invalid, rationale is missing for intended changes,
                    or required symbol/path details are absent.
    """
    root_path = Path(repo_root) if repo_root else Path.cwd()
    ledger_dir = root_path / "behavior_decisions"
    ledger_dir.mkdir(parents=True, exist_ok=True)

    # Normalize disposition to Intent
    if isinstance(disposition, Intent):
        intent = disposition
    else:
        norm_disp = disposition.strip().lower()
        try:
            intent = Intent(norm_disp)
        except ValueError:
            raise ValueError(
                f"Invalid disposition '{disposition}'. Must be one of: 'unintended', 'intended', 'unresolved'."
            )

    # Validate rationale for intended changes
    clean_rationale = rationale.strip() if rationale else None
    if intent == Intent.INTENDED:
        if not clean_rationale or len(clean_rationale) < 10:
            raise ValueError(
                "A rationale of at least 10 characters is strictly required for intended behavior changes."
            )

    # Normalize status to DecisionStatus
    if isinstance(status, DecisionStatus):
        dec_status = status
    else:
        norm_status = status.strip().lower()
        try:
            dec_status = DecisionStatus(norm_status)
        except ValueError:
            dec_status = DecisionStatus.PROPOSED

    # Extract delta properties
    target_obj = None
    if isinstance(delta, dict):
        target_obj = delta.get("target")
        if isinstance(target_obj, dict):
            symbol = target_obj.get("symbol", "")
            path = target_obj.get("path", "")
        elif isinstance(target_obj, SymbolRef):
            symbol = target_obj.symbol
            path = target_obj.path
        else:
            symbol = delta.get("symbol") or delta.get("symbol_name") or ""
            path = delta.get("path") or delta.get("file_path") or ""

        probe_hash = delta.get("probe_hash") or ""
        before_behavior = delta.get("before") if "before" in delta else delta.get("before_behavior")
        after_behavior = delta.get("after") if "after" in delta else delta.get("after_behavior")
        if before_behavior is None and "base_output" in delta:
            before_behavior = delta.get("base_output")
        if after_behavior is None and "head_output" in delta:
            after_behavior = delta.get("head_output")
        repo = repo or delta.get("repo", "")
        base_sha = base_sha or delta.get("base_sha", "")
        head_sha = head_sha or delta.get("head_sha", "")
    else:
        target_obj = getattr(delta, "target", None)
        if isinstance(target_obj, SymbolRef):
            symbol = target_obj.symbol
            path = target_obj.path
        elif isinstance(target_obj, dict):
            symbol = target_obj.get("symbol", "")
            path = target_obj.get("path", "")
        else:
            symbol = getattr(delta, "symbol", getattr(delta, "symbol_name", ""))
            path = getattr(delta, "path", getattr(delta, "file_path", ""))

        probe_hash = getattr(delta, "probe_hash", "")
        before_behavior = getattr(delta, "before", getattr(delta, "before_behavior", getattr(delta, "base_output", None)))
        after_behavior = getattr(delta, "after", getattr(delta, "after_behavior", getattr(delta, "head_output", None)))
        repo = repo or getattr(delta, "repo", "")
        base_sha = base_sha or getattr(delta, "base_sha", "")
        head_sha = head_sha or getattr(delta, "head_sha", "")

    if not symbol:
        raise ValueError("Cannot record decision without an affected symbol.")
    if not path:
        raise ValueError("Cannot record decision without an affected file path.")

    decision_id = generate_decision_id(repo, symbol, base_sha, head_sha, probe_hash)
    auto_supersedes = supersedes
    if auto_supersedes is None:
        current = current_decision(load_all_decisions(ledger_dir), path, symbol, exclude=decision_id)
        auto_supersedes = current.id if current else None

    target = SymbolRef(path=path, symbol=symbol)

    decision = Decision(
        id=decision_id,
        repo=repo,
        target=target,
        base_sha=base_sha,
        head_sha=head_sha,
        probe_hash=probe_hash,
        before=before_behavior,
        after=after_behavior,
        intent=intent,
        rationale=clean_rationale,
        requirement_ref=requirement_ref,
        status=dec_status,
        supersedes=auto_supersedes,
    )

    # Persist decision JSON
    target_file = ledger_dir / f"{decision_id}.json"
    target_file.write_text(decision.model_dump_json(indent=2) + "\n", encoding="utf-8")

    return decision


def load_all_decisions(directory: Union[Path, str]) -> List[Decision]:
    """Read all decision records from the ledger directory."""
    ledger_path = Path(directory)
    if not ledger_path.exists() or not ledger_path.is_dir():
        return []

    decisions: List[Decision] = []
    for file in sorted(ledger_path.glob("*.json")):
        try:
            content = file.read_text(encoding="utf-8")
            data = json.loads(content)
            decisions.append(Decision.model_validate(data))
        except Exception:
            continue
    return decisions


def load_decision_from_file(file_path: Union[Path, str]) -> Decision:
    """Load a specific decision JSON file."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Decision file not found: {path}")
    content = path.read_text(encoding="utf-8")
    return Decision.model_validate(json.loads(content))


def load_branch_decisions_via_git(
    repo_root: Union[Path, str],
    branch: str = "main",
) -> List[Decision]:
    """Read decisions from behavior_decisions/ on a git branch without checking it out."""
    root = Path(repo_root)
    decisions: List[Decision] = []

    try:
        cmd_ls = ["git", "ls-tree", "-r", "--name-only", branch, "behavior_decisions/"]
        res_ls = subprocess.run(
            cmd_ls, cwd=root, capture_output=True, text=True, check=True
        )
        files = [line.strip() for line in res_ls.stdout.splitlines() if line.strip().endswith(".json")]

        for rel_file in files:
            cmd_show = ["git", "show", f"{branch}:{rel_file}"]
            res_show = subprocess.run(
                cmd_show, cwd=root, capture_output=True, text=True, check=True
            )
            data = json.loads(res_show.stdout)
            decisions.append(Decision.model_validate(data))
    except Exception:
        pass

    return decisions


def lookup(
    symbols: List[str],
    *,
    approved_records: Optional[List[Decision]] = None,
    repo_root: Optional[Union[Path, str]] = None,
    branch: str = "main",
) -> List[DecisionMatch]:
    """Look up approved historical decisions for the given symbols.

    Returns matches indicating whether a previous decision exists,
    and whether it remains active or has been superseded/marked stale.

    Args:
        symbols: List of symbol names to search.
        approved_records: Pre-loaded list of approved Decision objects.
        repo_root: Repository root path (used to locate ledger or run git queries).
        branch: Target branch to read approved decisions from (default 'main').

    Returns:
        List of DecisionMatch records.
    """
    root_path = Path(repo_root) if repo_root else Path.cwd()

    records = approved_records
    # Records read off the target branch are approved *by having been merged there*, which is
    # the plan's trust rule (FINAL_PLAN.md section 7). A stored status cannot know that, so
    # approval is derived from where the record came from, not only from its `status` field.
    read_from_branch = approved_records is None
    if records is None:
        records = load_branch_decisions_via_git(root_path, branch)
        if not records:
            # Local ledger only, and only records that are explicitly approved. A record that
            # is merely present was proposed on a feature branch and never reviewed.
            ledger_dir = root_path / "behavior_decisions"
            records = [
                d for d in load_all_decisions(ledger_dir)
                if d.status == DecisionStatus.APPROVED
            ]

    superseded_ids = {d.supersedes for d in records if d.supersedes}

    matches: List[DecisionMatch] = []
    symbol_set = set(symbols)

    for record in records:
        sym_name = record.target.symbol
        if sym_name in symbol_set:
            is_superseded = record.id in superseded_ids
            if is_superseded:
                matches.append(
                    DecisionMatch(
                        symbol=sym_name,
                        decision=record,
                        match_type="superseded",
                        is_stale=True,
                        reason=f"Decision {record.id} was superseded by a later decision.",
                    )
                )
            else:
                is_approved = read_from_branch or record.status == DecisionStatus.APPROVED
                matches.append(
                    DecisionMatch(
                        symbol=sym_name,
                        decision=record,
                        match_type="approved" if is_approved else "stale",
                        is_stale=not is_approved,
                        reason=None if is_approved else "Decision is proposed but not yet approved on main.",
                    )
                )

    return matches


def approve_decision(
    decision_id: str,
    repo_root: Optional[Union[Path, str]] = None,
) -> Decision:
    """Mark a decision as approved (e.g. following PR merge)."""
    root_path = Path(repo_root) if repo_root else Path.cwd()
    file_path = root_path / "behavior_decisions" / f"{decision_id}.json"
    decision = load_decision_from_file(file_path)
    updated = decision.model_copy(update={"status": DecisionStatus.APPROVED})
    file_path.write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return updated
