"""Focused checks for the dependency-light report presentation layer."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.report import render_markdown, to_web_data


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "report_scenario1.json"


class ReportRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_is_marked_and_all_evidence_states_are_preserved(self) -> None:
        self.assertIs(self.report["fixture"], True)
        web_data = to_web_data(self.report)
        self.assertIs(web_data["fixture"], True)
        self.assertEqual(web_data["run"]["base_sha"], "abc1234")
        self.assertEqual(web_data["impact"]["unknowns"][0]["reason"], "dynamic dispatch was not resolved")
        outcomes = [observation["outcome"] for observation in web_data["observations"]]
        self.assertEqual(
            outcomes,
            ["delta_observed", "same_on_tested_cases", "inconclusive"],
        )
        self.assertEqual(web_data["decisions"][0]["disposition"], "unresolved")
        self.assertEqual(web_data["decisions"][1]["rationale"], "The refactor keeps the ordinary discount case unchanged.")
        self.assertEqual(len(web_data["limits"]), 3)
        self.assertIn("action_run", web_data["links"])

    def test_markdown_is_deterministic_and_evidence_first(self) -> None:
        first = render_markdown(self.report)
        second = render_markdown(self.report)
        self.assertEqual(first, second)
        self.assertIn("Fixture data", first)
        self.assertIn("pricing.invoice.price_total", first)
        self.assertIn("pricing.discount.apply_discount", first)
        self.assertIn("Unknown impact edges", first)
        self.assertIn("delta_observed", first)
        self.assertIn("same_on_tested_cases", first)
        self.assertIn("inconclusive", first)
        self.assertIn("unresolved", first)
        self.assertIn("intended", first)
        self.assertIn("https://github.com/example/behavior-review/actions/runs/123456", first)
        # A paired test result is not presented as a human verdict.
        self.assertIn("Existing tests (separate from behavior outcomes)", first)

    def test_action_and_artifact_urls_survive_query_secret_redaction(self) -> None:
        action_url = (
            "https://github.com/example/behavior-review/actions/runs/123456"
            "?run_attempt=2&token=action-secret"
        )
        artifact_url = (
            "https://github.com/example/behavior-review/actions/runs/123456/artifacts/789"
            "?X-Amz-Signature=artifact-secret&download=1"
        )
        report = {"links": {"action_run": action_url, "artifact": artifact_url}}

        web_data = to_web_data(report)
        safe_action_url = (
            "https://github.com/example/behavior-review/actions/runs/123456"
            "?run_attempt=2&token=[redacted]"
        )
        safe_artifact_url = (
            "https://github.com/example/behavior-review/actions/runs/123456/artifacts/789"
            "?X-Amz-Signature=[redacted]&download=1"
        )
        self.assertEqual(web_data["links"]["action_run"], safe_action_url)
        self.assertEqual(web_data["links"]["artifact"], safe_artifact_url)

        markdown = render_markdown(report)
        self.assertIn(f"[GitHub Action run](<{safe_action_url}>)", markdown)
        self.assertIn(f"[Report artifact](<{safe_artifact_url}>)", markdown)
        self.assertNotIn("action-secret", markdown)
        self.assertNotIn("artifact-secret", markdown)

    def test_canonical_revision_containers_and_commit_metadata_are_preserved(self) -> None:
        report = {
            "repo": "example/behavior-review",
            "revisions": {
                "base_sha": "base-sha-123",
                "head_sha": "head-sha-456",
            },
            "comparisons": [
                {
                    "probe": {"id": "probe-1", "input": {"value": 1}, "hash": "sha256:probe"},
                    "base": {
                        "revision": "base",
                        "sha": "base-sha-123",
                        "status": "ok",
                        "output": {"value": "before"},
                        "duration_ms": 11,
                    },
                    "head": {
                        "revision": "head",
                        "sha": "head-sha-456",
                        "status": "ok",
                        "output": {"value": "after"},
                        "duration_ms": 12,
                    },
                    "outcome": "delta_observed",
                }
            ],
        }

        web_data = to_web_data(report)
        observation = web_data["observations"][0]
        self.assertEqual(observation["base"]["sha"], "base-sha-123")
        self.assertEqual(observation["head"]["sha"], "head-sha-456")

        markdown = render_markdown(report)
        self.assertIn("Base commit", markdown)
        self.assertIn("base-sha-123", markdown)
        self.assertIn("Head commit", markdown)
        self.assertIn("head-sha-456", markdown)
        self.assertIn("Base output / exception", markdown)
        self.assertIn('"duration_ms":11', markdown)
        self.assertIn('"duration_ms":12', markdown)
        self.assertIn('"status":"ok"', markdown)
        self.assertIn('"value":"before"', markdown)
        self.assertIn('"value":"after"', markdown)

    def test_every_analyzed_language_and_tier_is_named_at_the_top(self) -> None:
        report = {
            "schema_version": "0.1",
            "fixture": True,
            "analysis": {
                "language": "python", "adapter": "python-ast", "tier": "full", "config_source": "detected",
                "languages": [
                    {"language": "python", "adapter": "python-ast", "tier": "full"},
                    {"language": "typescript", "adapter": "tree-sitter", "tier": "static_probe"},
                ],
            },
        }
        markdown = render_markdown(report)
        self.assertIn("**Analyzed as:** `python` (tier `full`), `typescript` (tier `static_probe`)", markdown)
        self.assertLess(markdown.index("Analyzed as"), markdown.index("### Run"))
        self.assertNotIn("Additional report fields", markdown)

    def test_missing_optional_sections_do_not_crash_or_create_a_verdict(self) -> None:
        report = {"schema_version": "0.1", "fixture": True}
        web_data = to_web_data(report)
        self.assertEqual(web_data["run"], {})
        self.assertEqual(web_data["changed_symbols"], [])
        self.assertEqual(web_data["impact"], {})
        self.assertEqual(web_data["observations"], [])
        self.assertEqual(web_data["decisions"], [])
        self.assertEqual(web_data["limits"], [])
        self.assertEqual(web_data["links"], {})
        markdown = render_markdown(report)
        self.assertIn("No behavior observations", markdown)
        self.assertNotIn("delta_observed", markdown)
        self.assertNotIn("same_on_tested_cases", markdown)
        self.assertNotIn("inconclusive", markdown)
        self.assertNotIn("intended", markdown)
        self.assertNotIn("unintended", markdown)

    def test_paths_and_secrets_are_redacted_in_web_data_and_markdown(self) -> None:
        report = {
            "fixture": True,
            "run": {
                "repo": "example/repo",
                "workspace": r"C:\Users\reviewer\private repo\app.py",
                "secretKey": "camel-case-secret",
            },
            "observations": [
                {
                    "probe_name": "secret-check",
                    "input": {
                        "password": "correct-horse-battery-staple",
                        "safe_value": "visible evidence",
                    },
                    "detail": "Authorization: Basic basic-secret; Cookie: session=cookie-secret",
                    "path": r"C:\Users\reviewer\private repo\base.py",
                    "base_output": r"/home/reviewer/private-repo/output.json",
                    "outcome": "inconclusive",
                }
            ],
            "links": {
                "action_run": "https://example.test/run?token=link-secret&visible=1",
            },
        }

        web_data = to_web_data(report)
        serialized = json.dumps(web_data, sort_keys=True)
        markdown = render_markdown(report)
        for secret in (
            "correct-horse-battery-staple",
            "basic-secret",
            "cookie-secret",
            "camel-case-secret",
            "link-secret",
            r"C:\Users\reviewer\private repo\app.py",
            r"C:\Users\reviewer\private repo\base.py",
            "/home/reviewer/private-repo/output.json",
        ):
            self.assertNotIn(secret, serialized)
            self.assertNotIn(secret, markdown)
        self.assertEqual(web_data["observations"][0]["input"]["password"], "[redacted]")
        self.assertEqual(web_data["observations"][0]["input"]["safe_value"], "visible evidence")
        self.assertIn("[local path redacted]", serialized)
        self.assertIn("[redacted]", serialized)
        self.assertIn("inconclusive", markdown)

    def test_legacy_aliases_and_singular_sections_are_normalized(self) -> None:
        report = {
            "repo": "example/repo",
            "base": "base-sha",
            "head": "head-sha",
            "timestamp": "2026-09-26T12:00:00Z",
            "comparisons": {
                "name": "legacy-probe",
                "status": "inconclusive",
            },
            "decision": {"intent": "unresolved"},
            "limitations": "bounded analysis",
            "link": "https://example.test/report",
        }
        web_data = to_web_data(report)
        self.assertEqual(web_data["run"]["base"], "base-sha")
        self.assertEqual(web_data["run"]["head"], "head-sha")
        self.assertEqual(web_data["run"]["timestamp"], "2026-09-26T12:00:00Z")
        self.assertIsInstance(web_data["observations"], list)
        self.assertIsInstance(web_data["decisions"], list)
        self.assertIsInstance(web_data["limits"], list)
        self.assertEqual(web_data["links"]["link"], "https://example.test/report")
        markdown = render_markdown(report)
        self.assertIn("inconclusive", markdown)
        self.assertIn("unresolved", markdown)

    def test_markdown_link_destinations_are_escaped(self) -> None:
        markdown = render_markdown(
            {
                "links": {
                    "action_run": "https://example.test/run>](javascript:alert(1)\n",
                }
            }
        )
        self.assertIn("%3E", markdown)
        self.assertNotIn("] (javascript", markdown)
        self.assertNotIn("\njavascript", markdown)

    def test_machine_decision_status_is_not_rendered_as_human_disposition(self) -> None:
        report = {
            "decisions": [
                {
                    "id": "decision-1",
                    "observation_id": "observation-1",
                    "status": "pending_review",
                    "verdict": "machine_generated_value",
                    "disposition": "intended",
                },
                {"status": "pending_review"},
            ]
        }
        markdown = render_markdown(report)
        self.assertIn("Decision ID", markdown)
        self.assertIn("observation-1", markdown)
        self.assertIn("pending_review", markdown)
        self.assertIn("Recorded verdict field", markdown)
        self.assertIn("Disposition:** `intended`", markdown)
        self.assertIn("Disposition:** `not recorded`", markdown)
        self.assertNotIn("Disposition:** `pending_review`", markdown)
        self.assertIn("required for intended", markdown)

    def test_intended_decisions_require_non_blank_rationale(self) -> None:
        markdown = render_markdown(
            {
                "decisions": [
                    {"disposition": "intended", "rationale": None},
                    {"disposition": "intended", "rationale": "  "},
                ]
            }
        )
        self.assertEqual(markdown.count("required for intended"), 2)
        self.assertNotIn("Rationale:** `None`", markdown)

    def test_explicit_flags_are_preserved_without_inference(self) -> None:
        report = {
            "observations": [
                {
                    "name": "only-explicit-outcome",
                    "same_on_tested_cases": False,
                    "delta_observed": True,
                    "inconclusive": False,
                }
            ]
        }
        markdown = render_markdown(report)
        self.assertIn("same_on_tested_cases", markdown)
        self.assertIn("delta_observed", markdown)
        self.assertIn("inconclusive", markdown)
        self.assertNotIn("bug", markdown.lower())
        self.assertNotIn("safe", markdown.lower())
        self.assertIn("does not decide intent", markdown)


if __name__ == "__main__":
    unittest.main()
