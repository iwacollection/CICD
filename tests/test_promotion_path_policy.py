from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from deployment_pointer import create_pointer, inspect_pointer, current_pointer  # noqa: E402
from promotion_policy import (  # noqa: E402
    find_successful_prerequisite,
    load_policy,
    normalize_identity,
    pointer_matches,
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

    def test_new_deployment_trace_fields_must_match_when_present(self) -> None:
        identity = {
            **self.identity,
            "toolchain_identity": "sha256:" + "d" * 64,
            "container_image": "ghcr.io/example/toolchain@sha256:" + "d" * 64,
            "manifest_sha256": "e" * 64,
            "application_image": "ghcr.io/example/app@sha256:" + "f" * 64,
        }
        self.assertTrue(pointer_matches(identity, identity))
        self.assertFalse(pointer_matches({**identity, "toolchain_identity": "sha256:" + "f" * 64}, identity))
        legacy = {key: value for key, value in self.identity.items() if key in {
            "artifact_name", "bundle_sha256", "source_sha", "source_run_id", "release_tag"
        }}
        self.assertFalse(pointer_matches(legacy, identity))
        self.assertTrue(pointer_matches(self.identity, self.identity))
        app_identity = normalize_identity(
            artifact_name="app-a", bundle_sha256="a" * 64, source_sha="b" * 40,
            source_run_id="123", release_tag="artifact-v2-" + "c" * 64,
            application_image="ghcr.io/example/app@sha256:" + "f" * 64,
        )
        self.assertTrue(pointer_matches(app_identity, app_identity))
        self.assertFalse(pointer_matches({**app_identity, "application_image": "ghcr.io/example/app@sha256:" + "d" * 64}, app_identity))
        self.assertFalse(pointer_matches({key: value for key, value in app_identity.items() if key != "application_image"}, app_identity))

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
    @patch("promotion_policy._list_all_pages")
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
    @patch("promotion_policy._list_all_pages")
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
    @patch("promotion_policy._list_all_pages")
    def test_failed_prerequisite_deployment_is_not_accepted(self, request, inspect_pointer) -> None:
        request.side_effect = [[{"id": 77}]]
        inspect_pointer.side_effect = ValueError("deployment 77 has no historical success")
        with self.assertRaisesRegex(ValueError, "no successful deployment"):
            find_successful_prerequisite(
                api_url="https://api.github.com",
                repository="example/repo",
                token="token",
                prerequisite_environment="staging",
                identity=self.identity,
            )
        inspect_pointer.assert_called_once()

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


    @patch("deployment_pointer._request")
    def test_inspect_requires_historical_success_even_when_latest_status_is_inactive(self, request) -> None:
        request.side_effect = [
            {"id": 901, "environment": "production", "payload": {
                "schema_version": 1, "artifact_contract": 2,
                "artifact_name": self.identity["artifact_name"],
                "bundle_sha256": self.identity["bundle_sha256"],
                "source_sha": self.identity["source_sha"],
                "source_run_id": self.identity["source_run_id"],
                "release_tag": self.identity["release_tag"],
            }},
            [{"state": "inactive"}, {"state": "success"}],
        ]
        pointer = inspect_pointer(
            api_url="https://api.github.com", repository="example/repo",
            token="token", deployment_id="901",
        )
        self.assertEqual(pointer["deployment_id"], "901")

    @patch("deployment_pointer._request")
    def test_inspect_rejects_deployment_without_historical_success(self, request) -> None:
        request.side_effect = [
            {"id": 902, "environment": "production", "payload": {
                "schema_version": 1, "artifact_contract": 2,
                "artifact_name": self.identity["artifact_name"],
                "bundle_sha256": self.identity["bundle_sha256"],
                "source_sha": self.identity["source_sha"],
                "source_run_id": self.identity["source_run_id"],
                "release_tag": self.identity["release_tag"],
            }},
            [{"state": "failure"}, {"state": "inactive"}],
        ]
        with self.assertRaisesRegex(ValueError, "no historical success"):
            inspect_pointer(
                api_url="https://api.github.com", repository="example/repo",
                token="token", deployment_id="902",
            )

    @patch("deployment_pointer._request")
    def test_current_pointer_reads_past_first_hundred_deployments(self, request) -> None:
        first_page = [{"id": i} for i in range(1000, 1100)]
        second_page = [{"id": 77}]
        payload = {
            "schema_version": 1, "artifact_contract": 2,
            "artifact_name": self.identity["artifact_name"],
            "bundle_sha256": self.identity["bundle_sha256"],
            "source_sha": self.identity["source_sha"],
            "source_run_id": self.identity["source_run_id"],
            "release_tag": self.identity["release_tag"],
        }
        request.side_effect = [
            first_page, second_page,
            {"id": 77, "environment": "dev", "payload": payload},
            [{"state": "success"}],
        ]
        result = current_pointer(
            api_url="https://api.github.com", repository="example/repo",
            token="token", environment="dev",
        )
        self.assertEqual(result["deployment_id"], "77")
        self.assertIn("page=2", request.call_args_list[1].args[0])

    def test_rollback_remains_same_environment_history_not_forward_promotion(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "rollback.yml").read_text(encoding="utf-8")
        self.assertIn("Require same-environment rollback source", workflow)
        self.assertIn("--restored-from-deployment-id", workflow)
        self.assertNotIn("promotion_policy.py", workflow)


if __name__ == "__main__":
    unittest.main()
