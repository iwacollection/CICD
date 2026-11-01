import copy
import json
import unittest
from pathlib import Path

from scripts.ci.argo_rollouts_contract import validate_contract


class ArgoRolloutsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(
            Path("ci/argo-rollouts-integration.json").read_text(encoding="utf-8")
        )

    def valid_environment(self, **overrides):
        environment = {
            "environment": "staging",
            "cluster_id": "cluster-a",
            "namespace": "demo",
            "mode": "non-production",
            "traffic_owner": "none",
        }
        environment.update(overrides)
        return environment

    def test_repository_default_is_fail_closed(self):
        self.assertEqual(validate_contract(self.contract), [])
        self.assertEqual(self.contract["defaults"]["mode"], "observe-only")
        self.assertEqual(self.contract["defaults"]["traffic_owner"], "none")
        self.assertFalse(self.contract["defaults"]["allow_production"])

    def test_rejects_two_traffic_writers_even_when_owner_is_none(self):
        contract = copy.deepcopy(self.contract)
        contract["environments"] = [self.valid_environment(
            cicd_renderer_manages_traffic=True,
            rollouts_manages_traffic=True,
        )]
        self.assertTrue(any(
            "both CICD renderer and Rollouts" in item
            for item in validate_contract(contract)
        ))

    def test_rejects_writer_when_traffic_owner_is_none(self):
        contract = copy.deepcopy(self.contract)
        contract["environments"] = [self.valid_environment(
            cicd_renderer_manages_traffic=True,
        )]
        self.assertTrue(any(
            "traffic_owner is none" in item
            for item in validate_contract(contract)
        ))

    def test_rejects_traffic_writer_conflicting_with_selected_owner(self):
        contract = copy.deepcopy(self.contract)
        contract["environments"] = [self.valid_environment(
            traffic_owner="cicd-renderer",
            rollouts_manages_traffic=True,
        )]
        self.assertTrue(any(
            "both CICD renderer and Rollouts" in item
            for item in validate_contract(contract)
        ))

    def test_observe_only_rejects_promote_and_abort(self):
        for action in ("allow_promote", "allow_abort"):
            with self.subTest(action=action):
                contract = copy.deepcopy(self.contract)
                contract["environments"] = [self.valid_environment(
                    mode="observe-only",
                    **{action: True},
                )]
                self.assertTrue(any(
                    f"{action} must be false in observe-only mode" in item
                    for item in validate_contract(contract)
                ))

    def test_observe_only_rejects_production_enablement(self):
        contract = copy.deepcopy(self.contract)
        contract["environments"] = [self.valid_environment(
            mode="observe-only",
            allow_production=True,
        )]
        self.assertTrue(any(
            "allow_production must be false in observe-only mode" in item
            for item in validate_contract(contract)
        ))

    def test_production_requires_human_approval(self):
        contract = copy.deepcopy(self.contract)
        contract["environments"] = [self.valid_environment(
            environment="production",
            mode="production",
            traffic_owner="argo-rollouts",
            allow_production=True,
            human_approval_required=False,
        )]
        self.assertTrue(any(
            "human_approval_required" in item for item in validate_contract(contract)
        ))

    def test_rejects_unknown_traffic_owner(self):
        contract = copy.deepcopy(self.contract)
        contract["environments"] = [self.valid_environment(traffic_owner="both")]
        self.assertTrue(any(
            "traffic_owner is unsupported" in item for item in validate_contract(contract)
        ))

    def test_rejects_unsafe_credential_policy(self):
        contract = copy.deepcopy(self.contract)
        contract["credential_policy"]["observer_has_write_access"] = True
        self.assertTrue(any(
            "credential_policy.observer_has_write_access" in item
            for item in validate_contract(contract)
        ))

    def test_rejects_ci_build_cluster_write_permission(self):
        contract = copy.deepcopy(self.contract)
        contract["credential_policy"]["ci_build_has_cluster_write"] = True
        self.assertTrue(any(
            "credential_policy.ci_build_has_cluster_write" in item
            for item in validate_contract(contract)
        ))

    def test_rejects_disabled_artifact_digest_verification(self):
        contract = copy.deepcopy(self.contract)
        contract["promotion_policy"]["require_image_digest_match"] = False
        self.assertTrue(any(
            "promotion_policy.require_image_digest_match" in item
            for item in validate_contract(contract)
        ))

    def test_rejects_disabled_bundle_digest_verification(self):
        contract = copy.deepcopy(self.contract)
        contract["promotion_policy"]["require_verified_bundle_sha256"] = False
        self.assertTrue(any(
            "promotion_policy.require_verified_bundle_sha256" in item
            for item in validate_contract(contract)
        ))


if __name__ == "__main__":
    unittest.main()
