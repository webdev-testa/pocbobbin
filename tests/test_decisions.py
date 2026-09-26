"""Unit tests for app.decisions (Lane D deliverables) integrated with Lane A schemas."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from app.decisions import (
    approve_decision,
    current_decision,
    generate_decision_id,
    load_all_decisions,
    load_decision_from_file,
    lookup,
    validate_and_save,
)
from app.schemas import Decision, DecisionStatus, Intent, SymbolRef


class TestDecisionsModule(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.repo_root = Path(self.test_dir)
        (self.repo_root / "behavior_decisions").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_generate_decision_id_deterministic(self):
        id1 = generate_decision_id("repo", "apply_discount", "sha1", "sha2", "probe123")
        id2 = generate_decision_id("repo", "apply_discount", "sha1", "sha2", "probe123")
        self.assertEqual(id1, id2)
        self.assertEqual(len(id1), 12)

    def test_validate_and_save_intended_with_valid_rationale(self):
        delta = {
            "symbol": "apply_discount",
            "path": "pricing/discount.py",
            "probe_hash": "probe_abc123",
            "before": 100.0,
            "after": 70.0,
        }
        rationale = "Policy update: cap maximum discount at 30% per Q3 pricing review."
        decision = validate_and_save(
            delta=delta,
            disposition="intended",
            rationale=rationale,
            repo_root=self.repo_root,
            repo="my-org/pocbobbin",
            base_sha="base111",
            head_sha="head222",
        )

        self.assertIsInstance(decision, Decision)
        self.assertEqual(decision.intent, Intent.INTENDED)
        self.assertEqual(decision.target.symbol, "apply_discount")
        self.assertEqual(decision.target.path, "pricing/discount.py")
        self.assertEqual(decision.rationale, rationale)
        self.assertEqual(decision.status, DecisionStatus.PROPOSED)

        # Verify file persisted on disk and matches schema
        saved_file = self.repo_root / "behavior_decisions" / f"{decision.id}.json"
        self.assertTrue(saved_file.exists())

        loaded = load_decision_from_file(saved_file)
        self.assertEqual(loaded.id, decision.id)
        self.assertEqual(loaded.before, 100.0)
        self.assertEqual(loaded.after, 70.0)

    def test_validate_and_save_with_target_dict_or_object(self):
        delta = {
            "target": {"symbol": "price_total", "path": "pricing/invoice.py"},
            "probe_hash": "probe_xyz",
            "before": 100.0,
            "after": 99.99,
        }
        dec = validate_and_save(
            delta=delta,
            disposition="unintended",
            repo_root=self.repo_root,
        )
        self.assertEqual(dec.target.symbol, "price_total")
        self.assertEqual(dec.target.path, "pricing/invoice.py")
        self.assertEqual(dec.intent, Intent.UNINTENDED)

    def test_validate_and_save_intended_missing_rationale_raises_error(self):
        delta = {
            "symbol": "apply_discount",
            "path": "pricing/discount.py",
            "before": 100.0,
            "after": 70.0,
        }
        with self.assertRaises(ValueError) as ctx:
            validate_and_save(
                delta=delta,
                disposition="intended",
                rationale="too short",  # < 10 chars
                repo_root=self.repo_root,
            )
        self.assertIn("at least 10 characters", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx2:
            validate_and_save(
                delta=delta,
                disposition="intended",
                rationale=None,
                repo_root=self.repo_root,
            )
        self.assertIn("at least 10 characters", str(ctx2.exception))

    def test_validate_and_save_unintended_and_unresolved(self):
        delta = {
            "symbol": "price_total",
            "path": "pricing/invoice.py",
            "before": 100.00,
            "after": 99.99,
        }
        decision_unintended = validate_and_save(
            delta=delta,
            disposition="unintended",
            repo_root=self.repo_root,
        )
        self.assertEqual(decision_unintended.intent, Intent.UNINTENDED)
        self.assertEqual(decision_unintended.status, DecisionStatus.PROPOSED)

        decision_unresolved = validate_and_save(
            delta=delta,
            disposition="unresolved",
            repo_root=self.repo_root,
            base_sha="diff_sha",
        )
        self.assertEqual(decision_unresolved.intent, Intent.UNRESOLVED)

    def test_validate_and_save_invalid_disposition(self):
        delta = {"symbol": "foo", "path": "bar.py"}
        with self.assertRaises(ValueError) as ctx:
            validate_and_save(
                delta=delta,
                disposition="approved_by_ai",
                repo_root=self.repo_root,
            )
        self.assertIn("Invalid disposition", str(ctx.exception))

    def test_auto_supersedes_detection(self):
        delta1 = {
            "symbol": "apply_discount",
            "path": "pricing/discount.py",
            "before": 100.0,
            "after": 80.0,
        }
        dec1 = validate_and_save(
            delta=delta1,
            disposition="intended",
            rationale="Initial policy adjustment to 20%",
            repo_root=self.repo_root,
            base_sha="sha_A",
            head_sha="sha_B",
        )

        delta2 = {
            "symbol": "apply_discount",
            "path": "pricing/discount.py",
            "before": 80.0,
            "after": 70.0,
        }
        dec2 = validate_and_save(
            delta=delta2,
            disposition="intended",
            rationale="Subsequent policy adjustment to 30%",
            repo_root=self.repo_root,
            base_sha="sha_B",
            head_sha="sha_C",
        )

        self.assertEqual(dec2.supersedes, dec1.id)

    def _ledger_record(self, id_, supersedes=None, symbol="apply_discount"):
        record = Decision(
            id=id_, repo="acme/repo", target=SymbolRef(path="pricing/discount.py", symbol=symbol),
            base_sha="b", head_sha="h", probe_hash="p", before=1, after=2, intent=Intent.INTENDED,
            rationale="an earlier policy change", status=DecisionStatus.APPROVED, supersedes=supersedes,
        )
        (self.repo_root / "behavior_decisions" / f"{id_}.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return record

    def test_current_decision_follows_the_supersedes_chain_not_the_file_order(self):
        # Written oldest first, but the newest id sorts first: file order says nothing about age.
        oldest = self._ledger_record("ffff00000000")
        newest = self._ledger_record("000000000000", supersedes=oldest.id)
        other = self._ledger_record("888800000000", symbol="price_total")

        records = load_all_decisions(self.repo_root / "behavior_decisions")
        self.assertEqual(current_decision(records, "pricing/discount.py", "apply_discount"), newest)
        self.assertEqual(current_decision(records, "pricing/discount.py", "apply_discount", exclude=newest.id), oldest)
        self.assertEqual(current_decision(records, "pricing/discount.py", "price_total"), other)
        self.assertIsNone(current_decision(records, "pricing/invoice.py", "apply_discount"))

    def test_third_decision_supersedes_the_current_one(self):
        oldest = self._ledger_record("ffff00000000")
        newest = self._ledger_record("000000000000", supersedes=oldest.id)

        third = validate_and_save(
            delta={"symbol": "apply_discount", "path": "pricing/discount.py", "probe_hash": "p3", "before": 2, "after": 3},
            disposition="intended",
            rationale="A third policy change on the same function",
            repo_root=self.repo_root,
            repo="acme/repo",
            base_sha="sha_C",
            head_sha="sha_D",
        )

        self.assertEqual(third.supersedes, newest.id)
        records = load_all_decisions(self.repo_root / "behavior_decisions")
        self.assertEqual(current_decision(records, "pricing/discount.py", "apply_discount"), third)

    def test_approve_decision(self):
        delta = {
            "symbol": "apply_discount",
            "path": "pricing/discount.py",
            "before": 100.0,
            "after": 70.0,
        }
        dec = validate_and_save(
            delta=delta,
            disposition="intended",
            rationale="Approved business change for holiday pricing",
            repo_root=self.repo_root,
        )
        self.assertEqual(dec.status, DecisionStatus.PROPOSED)

        approved = approve_decision(dec.id, repo_root=self.repo_root)
        self.assertEqual(approved.status, DecisionStatus.APPROVED)

        reloaded = load_decision_from_file(self.repo_root / "behavior_decisions" / f"{dec.id}.json")
        self.assertEqual(reloaded.status, DecisionStatus.APPROVED)

    def test_lookup_approved_and_superseded_records(self):
        delta1 = {
            "symbol": "apply_discount",
            "path": "pricing/discount.py",
            "before": 100.0,
            "after": 80.0,
        }
        dec1 = validate_and_save(
            delta=delta1,
            disposition="intended",
            rationale="Initial policy adjustment to 20%",
            repo_root=self.repo_root,
            base_sha="sha_A",
            head_sha="sha_B",
            status=DecisionStatus.APPROVED,
        )

        matches = lookup(
            symbols=["apply_discount"],
            repo_root=self.repo_root,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].match_type, "approved")
        self.assertFalse(matches[0].is_stale)
        self.assertEqual(matches[0].decision.id, dec1.id)

        # Now add a second decision superseding dec1 and approve it
        delta2 = {
            "symbol": "apply_discount",
            "path": "pricing/discount.py",
            "before": 80.0,
            "after": 70.0,
        }
        dec2 = validate_and_save(
            delta=delta2,
            disposition="intended",
            rationale="Subsequent policy adjustment to 30%",
            repo_root=self.repo_root,
            base_sha="sha_B",
            head_sha="sha_C",
            status=DecisionStatus.APPROVED,
        )

        matches_after = lookup(
            symbols=["apply_discount"],
            repo_root=self.repo_root,
        )
        self.assertEqual(len(matches_after), 2)
        match_dec1 = next(m for m in matches_after if m.decision.id == dec1.id)
        match_dec2 = next(m for m in matches_after if m.decision.id == dec2.id)

        self.assertTrue(match_dec1.is_stale)
        self.assertEqual(match_dec1.match_type, "superseded")
        self.assertIn("superseded", match_dec1.reason)

        self.assertFalse(match_dec2.is_stale)
        self.assertEqual(match_dec2.match_type, "approved")

    def test_lookup_unrelated_symbol(self):
        matches = lookup(
            symbols=["unrelated_function"],
            repo_root=self.repo_root,
        )
        self.assertEqual(len(matches), 0)

    def test_merged_record_is_approved_even_when_stored_status_is_proposed(self):
        """Plan section 7: a decision is approved when it is MERGED into the target branch.

        The stored `status` field cannot know that. A decision written on a PR branch keeps
        `status: proposed`, and merging does not rewrite it, so reading a record off the target
        branch is approval in itself. Before this fix that normal flow was reported as stale,
        which broke the plan's Scenario 5 (a later change must be able to cite the decision).
        """
        import subprocess

        git_env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": self.test_dir,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        }

        def git(*args):
            subprocess.run(["git", "-C", self.test_dir, *args], check=True,
                           capture_output=True, text=True, env=git_env)

        git("init", "-q", "-b", "main")
        (self.repo_root / "README.md").write_text("x")
        git("add", "-A")
        git("commit", "-qm", "init")

        decision = validate_and_save(
            delta={"symbol": "apply_discount", "path": "pricing/discount.py",
                   "probe_hash": "probe_merged", "before": 100.0, "after": 99.99},
            disposition="intended",
            rationale="Finance approved truncating discounts; policy ref OPS-441.",
            repo_root=self.repo_root,
            repo="acme/repo",
            base_sha="sha_base",
            head_sha="sha_head",
        )
        # written on a PR branch, so it is only ever proposed in the file itself
        self.assertEqual(decision.status, DecisionStatus.PROPOSED)

        git("add", "-A")
        git("commit", "-qm", "merge the PR: the decision is now on main")

        matches = lookup(symbols=["apply_discount"], repo_root=self.repo_root, branch="main")
        self.assertEqual(len(matches), 1, "the merged decision should be found")
        self.assertEqual(
            matches[0].match_type, "approved",
            "a decision merged into the target branch is approved by the plan's rule, "
            "regardless of the status stored in the file",
        )
        self.assertFalse(matches[0].is_stale)


if __name__ == "__main__":
    unittest.main()
