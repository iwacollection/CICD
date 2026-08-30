from __future__ import annotations

import os
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from hardware_catalog import build_matrix as build_hardware_matrix  # noqa: E402
from hardware_catalog import validate_hardware_catalog, validate_rollout_policy  # noqa: E402
from hardware_execute import _trusted_main_execution  # noqa: E402
from resource_broker import BrokerError, acquire  # noqa: E402
from toolchain_catalog import validate_toolchain_catalog  # noqa: E402
from validate_config import load_catalog, validate_catalog  # noqa: E402


class HardwareIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.projects = load_catalog(ROOT / "ci" / "projects.json")
        self.toolchains = load_catalog(ROOT / "ci" / "toolchains.json")
        self.hardware = load_catalog(ROOT / "ci" / "hardware-profiles.json")
        self.rollout = load_catalog(ROOT / "ci" / "hardware-rollout.json")

    def test_real_catalogs_cross_validate(self) -> None:
        self.assertEqual(validate_toolchain_catalog(self.toolchains), [])
        self.assertEqual(validate_hardware_catalog(self.hardware), [])
        self.assertEqual(validate_rollout_policy(self.hardware, self.rollout), [])
        self.assertEqual(validate_catalog(self.projects, self.toolchains, self.hardware), [])

    def test_no_vendor_profile_is_accidentally_active(self) -> None:
        self.assertEqual(build_hardware_matrix(self.hardware, "active"), {"include": []})
        statuses = {item["id"]: item["status"] for item in self.hardware["profiles"]}
        self.assertEqual(statuses["rk-linux-arm64-lab"], "planned")
        self.assertEqual(statuses["qcom-android-arm64-lab"], "planned")
        self.assertEqual(statuses["mtk-android-arm64-lab"], "planned")

    def test_rk_first_rollout_blocks_qcom_or_mtk_activation(self) -> None:
        for soc in ("qualcomm", "mediatek"):
            data = deepcopy(self.hardware)
            profile = next(item for item in data["profiles"] if item["soc"] == soc)
            profile["status"] = "active"
            profile["sdk"]["expected_sha256"] = "sha256:" + "a" * 64
            errors = validate_rollout_policy(data, self.rollout)
            self.assertTrue(
                any("outside allowed_active_socs" in error for error in errors),
                msg=f"{soc} activation must be blocked during RK-first rollout",
            )

    def test_rk_first_rollout_allows_only_one_active_profile(self) -> None:
        data = deepcopy(self.hardware)
        rk = next(item for item in data["profiles"] if item["soc"] == "rk")
        rk["status"] = "active"
        rk["sdk"]["expected_sha256"] = "sha256:" + "a" * 64
        duplicate = deepcopy(rk)
        duplicate["id"] = "rk-linux-arm64-lab-2"
        data["profiles"].append(duplicate)
        errors = validate_rollout_policy(data, self.rollout)
        self.assertTrue(any("at most 1 active profile" in error for error in errors))

    def test_vendor_targets_are_independently_disabled_before_activation(self) -> None:
        project = next(item for item in self.projects["projects"] if item["name"] == "embedded-firmware-template")
        self.assertTrue(project["enabled"])
        target_states = {target["soc"]: target["enabled"] for target in project["targets"]}
        self.assertEqual(target_states, {"rk": False, "qualcomm": False, "mediatek": False})

    def test_active_profile_requires_pinned_sdk_identity(self) -> None:
        data = deepcopy(self.hardware)
        data["profiles"][0]["status"] = "active"
        errors = validate_hardware_catalog(data)
        self.assertTrue(any("requires immutable sdk.expected_sha256" in error for error in errors))

    def test_active_hardware_toolchain_requires_host_identity(self) -> None:
        data = deepcopy(self.toolchains)
        toolchain = next(item for item in data["toolchains"] if item["id"] == "rk-sdk-2026.08")
        toolchain["status"] = "active"
        errors = validate_toolchain_catalog(data)
        self.assertTrue(any("requires immutable host_identity" in error for error in errors))

    def test_pr_or_non_main_hardware_execution_is_rejected(self) -> None:
        base = {"RUNNER_ENVIRONMENT": "self-hosted", "GITHUB_REF": "refs/heads/main"}
        with patch.dict(os.environ, {**base, "GITHUB_EVENT_NAME": "pull_request"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "main push/manual dispatch"):
                _trusted_main_execution()
        with patch.dict(
            os.environ,
            {"RUNNER_ENVIRONMENT": "self-hosted", "GITHUB_REF": "refs/heads/feature", "GITHUB_EVENT_NAME": "push"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "main push/manual dispatch"):
                _trusted_main_execution()

    def test_required_broker_credentials_fail_closed(self) -> None:
        resource = {
            "required": True,
            "pool": "test",
            "broker_url_env": "CI_TEST_BROKER_URL",
            "token_env": "CI_TEST_BROKER_TOKEN",
            "ttl_seconds": 60,
        }
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(BrokerError):
                acquire(resource, kind="license", holder="test", metadata={})

    def test_vendor_adapters_exist(self) -> None:
        for profile in self.hardware["profiles"]:
            for key in ("build_adapter", "test_adapter"):
                self.assertTrue((ROOT / profile[key]).is_file(), msg=f"missing {profile[key]}")

    def test_qcom_and_mtk_require_license_pool(self) -> None:
        profiles = {item["soc"]: item for item in self.hardware["profiles"]}
        self.assertTrue(profiles["qualcomm"]["license"]["required"])
        self.assertTrue(profiles["mediatek"]["license"]["required"])
        self.assertFalse(profiles["rk"]["license"]["required"])

    def test_rk_readiness_and_enrollment_are_rk_only(self) -> None:
        readiness = (ROOT / ".github" / "workflows" / "hardware-readiness.yml").read_text(encoding="utf-8")
        enrollment = (ROOT / ".github" / "workflows" / "rk-sdk-enrollment.yml").read_text(encoding="utf-8")
        self.assertIn("--status active --soc rk", readiness)
        self.assertIn("Require exactly one active RK profile", readiness)
        self.assertIn("--status planned --soc rk", enrollment)
        self.assertIn("Require exactly one planned RK profile", enrollment)
        self.assertNotIn("soc-qualcomm", readiness + enrollment)
        self.assertNotIn("soc-mediatek", readiness + enrollment)

    def test_reusable_rk_workflow_keeps_runner_and_toolchain_central(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "reusable-rk-build.yml").read_text(encoding="utf-8")
        input_section = workflow.split("permissions:", 1)[0]
        self.assertNotIn("runner_labels_json:", input_section)
        self.assertNotIn("toolchain:", input_section)
        self.assertNotIn("soc:", input_section)
        self.assertIn("soc: rk", workflow)
        self.assertIn("toolchain: rk-sdk-2026.08", workflow)
        self.assertIn("runner_labels_json: ${{ needs.resolve.outputs.runner_labels }}", workflow)
        self.assertIn("central rollout policy is not RK-first", workflow)
        self.assertIn("trusted RK build requires active RK profile and toolchain", workflow)

    def test_dag_node_keeps_hosted_pr_boundary_and_hardware_leases(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "dag-node.yml").read_text(encoding="utf-8")
        self.assertIn("name: Hosted hardware PR validation", workflow)
        self.assertIn("name: Hardware Runner and SDK preflight", workflow)
        self.assertIn("name: Vendor build with license lease", workflow)
        self.assertIn("name: HIL test with device lease", workflow)
        self.assertIn("hardware_binding.py", workflow)
        self.assertIn("github.event_name == 'pull_request' && 'ubuntu-latest'", workflow)


if __name__ == "__main__":
    unittest.main()
