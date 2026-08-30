from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from deployment_pointer import create_pointer  # noqa: E402
from promotion_policy import (  # noqa: E402
    find_successful_prerequisite,
    load_policy,
    normalize_identity,
    required_prerequisite,
    validate_policy,
    verify_promotion,
)


class PromotionPathPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(ROOT / "ci" / "promotion-policy.json")
        self.identity = normalize_identity(
            artifact_name="app-generic-linux-x86_64-gcc-source",
            bundle_sha256="a" * 64,
            source_sha="b" * 40,
            source_run_id="123",
            release_tag="artifact-v2-" + "c" * 64,
        )

    def test_repository_policy_requires_strict_dev_staging_production_order(self) -> None:
        self.assertEqual(validate_policy(self.policy), [])
        self.assertIsNone(required_prerequisite(self.policy, "dev"))
        self.assertEqual(required_prerequisite(self.policy, "staging"), "dev")
        self.assertEqual(required_prerequisite(self.policy, "production"), "staging")

    def test_policy_rejects_environment_skip(self) -> None:
        broken = {
            "schema_version": 1,
            "environments": ["dev", "staging", "production"],
            "prerequisites": {
                "dev": None,
                "staging": "dev",
                "production": "dev",
            },
            "require_exact_artifact_identity": True,
        }
        errors = validate_policy(broken)
        self.assertTrue(any("production must require immediately previous environment staging" in item for item in errors))

    @patch("promotion_policy.find_successful_prerequisite")
    def test_dev_is_the_only_promotion_root(self, find_prerequisite) -> None:
        result = verify_promotion(
            policy=self.policy,
            api_url="https://api.github.com",
            repository="example/repo",
            token="token",
            target_environment="dev",
            identity=self.identity,
        )
        self.assertEqual(result["status"], "allowed")
        self.assertEqual(result["prerequisite_environment"], "")
        self.assertEqual(result["prerequisite_deployment_id"], "")
        find_prerequisite.assert_not_called()

    @patch("promotion_policy.inspect_pointer")
    @patch("promotion_policy._request")
    def test_staging_accepts_exact_historical_successful_dev_deployment(self, request, inspect_pointer) -> None:
        request.side_effect = [
            [{"id": 77}, {"id": 66}],
            [{"state": "success"}],
        ]
        inspect_pointer.return_value = {
            "deployment_id": "77",
            "environment": "dev",
            **self.identity,
        }
        result = find_successful_prerequisite(
            api_url="https://api.github.com",
            repository="example/repo",
            token="token",
            prerequisite_environment="dev",
            identity=self.identity,
        )
        self.assertEqual(result["prerequisite_environment"], "dev")
        self.assertEqual(result["prerequisite_deployment_id"], "77")

    @patch("promotion_policy.inspect_pointer")
    @patch("promotion_policy._request")
    def test_exact_identity_prevents_promoting_different_digest(self, request, inspect_pointer) -> None:
        request.side_effect = [
            [{"id": 77}],
            [{"state": "success"}],
        ]
        inspect_pointer.return_value = {
            "deployment_id": "77",
            "environment": "dev",
            **{**self.identity, "bundle_sha256": "d" * 64},
        }
        with self.assertRaisesRegex(ValueError, "no successful deployment"):
            find_successful_prerequisite(
                api_url="https://api.github.com",
                repository="example/repo",
                token="token",
                prerequisite_environment="dev",
                identity=self.identity,
            )

    @patch("promotion_policy.inspect_pointer")
    @patch("promotion_policy._request")
    def test_failed_prerequisite_deployment_is_not_accepted(self, request, inspect_pointer) -> None:
        request.side_effect = [
            [{"id": 77}],
            [{"state": "failure"}],
        ]
        with self.assertRaisesRegex(ValueError, "no successful deployment"):
            find_successful_prerequisite(
                api_url="https://api.github.com",
                repository="example/repo",
                token="token",
                prerequisite_environment="staging",
                identity=self.identity,
            )
        inspect_pointer.assert_not_called()

    @patch("deployment_pointer._request")
    def test_promotion_pointer_records_verified_prerequisite_deployment(self, request) -> None:
        request.side_effect = [{"id": 321}, {"id": 654}]
        result = create_pointer(
            api_url="https://api.github.com",
            repository="example/repo",
            token="token",
            environment="staging",
            artifact_name=self.identity["artifact_name"],
            bundle_sha256=self.identity["bundle_sha256"],
            source_sha=self.identity["source_sha"],
            source_run_id=self.identity["source_run_id"],
            release_tag=self.identity["release_tag"],
            reason="promotion",
            promoted_from_deployment_id="77",
        )
        self.assertEqual(result["promoted_from_deployment_id"], "77")
        payload = request.call_args_list[0].kwargs["payload"]["payload"]
        self.assertEqual(payload["promoted_from_deployment_id"], "77")

    def test_promotion_workflow_enforces_path_before_moving_pointer(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "promote.yml").read_text(encoding="utf-8")
        policy_index = workflow.index("Enforce ordered promotion path")
        pointer_index = workflow.index("Move environment digest pointer")
        self.assertLess(policy_index, pointer_index)
        self.assertIn("promotion_policy.py", workflow)
        self.assertIn("ci/promotion-policy.json", workflow)
        self.assertIn("--promoted-from-deployment-id", workflow)
        self.assertIn("steps.promotion_path.outputs.prerequisite_deployment_id", workflow)

    def test_rollback_remains_same_environment_history_not_forward_promotion(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "rollback.yml").read_text(encoding="utf-8")
        self.assertIn("Require same-environment rollback source", workflow)
        self.assertIn("--restored-from-deployment-id", workflow)
        self.assertNotIn("promotion_policy.py", workflow)


if __name__ == "__main__":
    unittest.main()
