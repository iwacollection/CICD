from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from repository_governance import build_report, evaluate_ruleset, load_policy, validate_policy  # noqa: E402


class RepositoryGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(ROOT / "ci" / "repository-governance-policy.json")
        self.ruleset = {
            "id": 21844229,
            "name": "main-production-governance",
            "target": "branch",
            "enforcement": "active",
            "conditions": {"ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}},
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {
                    "type": "pull_request",
                    "parameters": {
                        "required_approving_review_count": 1,
                        "dismiss_stale_reviews_on_push": True,
                        "require_code_owner_review": True,
                        "required_review_thread_resolution": True,
                        "require_extra_approval_for_unattributed_changes": True,
                    },
                },
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [
                            {"context": "Validate CI platform", "integration_id": 15368},
                            {"context": "Build gate", "integration_id": 15368},
                            {"context": "Toolchain gate", "integration_id": 15368},
                        ],
                    },
                },
            ],
            "bypass_actors": [],
        }

    def test_repository_policy_is_valid(self) -> None:
        self.assertEqual(validate_policy(self.policy), [])
        self.assertEqual(self.policy["ruleset_name"], "main-production-governance")
        self.assertFalse(self.policy["bypass_actors"]["allow_when_visible"])
        self.assertFalse(self.policy["bypass_actors"]["visibility_required"])

    def test_current_production_ruleset_shape_is_healthy(self) -> None:
        violations, warnings = evaluate_ruleset(self.ruleset, self.policy)
        self.assertEqual(violations, [])
        self.assertEqual(warnings, [])
        report = build_report(self.ruleset, self.policy)
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(report["violations"], [])
        self.assertEqual(report["warnings"], [])

    def test_stronger_approval_count_is_allowed(self) -> None:
        ruleset = deepcopy(self.ruleset)
        pull_request = next(rule for rule in ruleset["rules"] if rule["type"] == "pull_request")
        pull_request["parameters"]["required_approving_review_count"] = 2
        violations, warnings = evaluate_ruleset(ruleset, self.policy)
        self.assertEqual(violations, [])
        self.assertEqual(warnings, [])

    def test_approval_count_drift_is_rejected(self) -> None:
        ruleset = deepcopy(self.ruleset)
        pull_request = next(rule for rule in ruleset["rules"] if rule["type"] == "pull_request")
        pull_request["parameters"]["required_approving_review_count"] = 0
        violations, _ = evaluate_ruleset(ruleset, self.policy)
        self.assertTrue(any("approving review count" in item for item in violations))

    def test_missing_required_check_is_rejected(self) -> None:
        ruleset = deepcopy(self.ruleset)
        status = next(rule for rule in ruleset["rules"] if rule["type"] == "required_status_checks")
        status["parameters"]["required_status_checks"] = [
            check for check in status["parameters"]["required_status_checks"] if check["context"] != "Build gate"
        ]
        violations, _ = evaluate_ruleset(ruleset, self.policy)
        self.assertIn("required status check is missing: Build gate", violations)

    def test_force_push_protection_cannot_disappear(self) -> None:
        ruleset = deepcopy(self.ruleset)
        ruleset["rules"] = [rule for rule in ruleset["rules"] if rule["type"] != "non_fast_forward"]
        violations, _ = evaluate_ruleset(ruleset, self.policy)
        self.assertIn("required ruleset rule is missing: non_fast_forward", violations)

    def test_code_owner_and_thread_resolution_cannot_be_disabled(self) -> None:
        ruleset = deepcopy(self.ruleset)
        pull_request = next(rule for rule in ruleset["rules"] if rule["type"] == "pull_request")
        pull_request["parameters"]["require_code_owner_review"] = False
        pull_request["parameters"]["required_review_thread_resolution"] = False
        violations, _ = evaluate_ruleset(ruleset, self.policy)
        self.assertTrue(any("require_code_owner_review=true" in item for item in violations))
        self.assertTrue(any("required_review_thread_resolution=true" in item for item in violations))

    def test_visible_bypass_actor_is_rejected(self) -> None:
        ruleset = deepcopy(self.ruleset)
        ruleset["bypass_actors"] = [{"actor_type": "OrganizationAdmin", "bypass_mode": "always"}]
        violations, warnings = evaluate_ruleset(ruleset, self.policy)
        self.assertIn("ruleset bypass actors are not allowed", violations)
        self.assertEqual(warnings, [])

    def test_missing_bypass_visibility_is_explicit_warning_not_false_drift(self) -> None:
        ruleset = deepcopy(self.ruleset)
        ruleset.pop("bypass_actors")
        violations, warnings = evaluate_ruleset(ruleset, self.policy)
        self.assertEqual(violations, [])
        self.assertTrue(any("not visible" in item for item in warnings))
        report = build_report(ruleset, self.policy)
        self.assertEqual(report["status"], "healthy-with-limited-visibility")

    def test_policy_can_require_bypass_visibility_for_privileged_audit_identity(self) -> None:
        ruleset = deepcopy(self.ruleset)
        ruleset.pop("bypass_actors")
        policy = deepcopy(self.policy)
        policy["bypass_actors"]["visibility_required"] = True
        violations, warnings = evaluate_ruleset(ruleset, policy)
        self.assertTrue(any("visibility is required" in item for item in violations))
        self.assertEqual(warnings, [])

    def test_governance_workflow_is_read_only_and_retains_evidence(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "repository-governance.yml").read_text(encoding="utf-8")
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("id-token: write", workflow)
        self.assertNotIn("deployments: write", workflow)
        self.assertIn("--fail-on-drift", workflow)
        self.assertIn("repository-governance.json", workflow)
        self.assertIn("retention-days: 30", workflow)
        self.assertIn('cron: "43 3 * * *"', workflow)


if __name__ == "__main__":
    unittest.main()
