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

    def test_repository_default_is_fail_closed(self):
        self.assertEqual(validate_contract(self.contract), [])
        self.assertEqual(self.contract["defaults"]["mode"], "observe-only")
        self.assertEqual(self.contract["defaults"]["traffic_owner"], "none")
        self.assertFalse(self.contract["defaults"]["allow_production"])

    def test_rejects_two_traffic_owners(self):
        contract = json.loads(json.dumps(self.contract))
        contract["environments"] = [{
            "environment": "staging",
            "cluster_id": "cluster-a",
            "namespace": "demo",
            "mode": "non-production",
            "traffic_owner": "cicd-renderer",
            "rollouts_manages_traffic": True
        }]
        self.assertTrue(any("both" in item for item in validate_contract(contract)))

    def test_production_requires_human_approval(self):
        contract = json.loads(json.dumps(self.contract))
        contract["environments"] = [{
            "environment": "production",
            "cluster_id": "cluster-a",
            "namespace": "demo",
            "mode": "production",
            "traffic_owner": "argo-rollouts",
            "allow_production": True,
            "human_approval_required": False
        }]
        self.assertTrue(any("human_approval_required" in item for item in validate_contract(contract)))

    def test_rejects_unknown_traffic_owner(self):
        contract = json.loads(json.dumps(self.contract))
        contract["environments"] = [{
            "environment": "dev",
            "cluster_id": "cluster-a",
            "namespace": "demo",
            "mode": "local-lab",
            "traffic_owner": "both"
        }]
        self.assertTrue(any("traffic_owner is unsupported" in item for item in validate_contract(contract)))


if __name__ == "__main__":
    unittest.main()
