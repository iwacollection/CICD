from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from supply_chain_policy import (  # noqa: E402
    validate_dockerfile,
    validate_policy,
    validate_sbom,
    validate_trivy_report,
)


class SupplyChainPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = json.loads(
            (ROOT / "ci" / "supply-chain-policy.json").read_text(encoding="utf-8")
        )

    def test_repository_policy_is_valid(self) -> None:
        self.assertEqual(validate_policy(self.policy), [])

    def test_toolchain_dockerfile_pins_base_and_apt_snapshot(self) -> None:
        dockerfile = ROOT / "docker" / "toolchains" / "gcc-host" / "Dockerfile"
        self.assertEqual(validate_dockerfile(dockerfile, self.policy), [])
        text = dockerfile.read_text(encoding="utf-8")
        self.assertRegex(text, r"FROM ubuntu:24\.04@sha256:[0-9a-f]{64}")
        self.assertIn('APT::Snapshot "20260810T000000Z"', text)

    def test_floating_container_base_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dockerfile = Path(directory) / "Dockerfile"
            dockerfile.write_text(
                "FROM ubuntu:24.04\nRUN apt-get update && apt-get install -y bash\n",
                encoding="utf-8",
            )
            errors = validate_dockerfile(dockerfile, self.policy)
        self.assertTrue(any("sha256 digest" in error for error in errors))
        self.assertTrue(any("Snapshot" in error for error in errors))

    def test_high_vulnerability_and_any_secret_are_blocked(self) -> None:
        report = {
            "Results": [
                {
                    "Target": "demo",
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-2099-0001",
                            "Severity": "HIGH",
                            "PkgName": "demo-lib",
                        }
                    ],
                    "Secrets": [{"RuleID": "private-key"}],
                }
            ]
        }
        errors, summary = validate_trivy_report(report, self.policy)
        self.assertEqual(summary["vulnerabilities"], 1)
        self.assertEqual(summary["secrets"], 1)
        self.assertTrue(any("vulnerability blocked" in error for error in errors))
        self.assertTrue(any("secret finding blocked" in error for error in errors))

    def test_cyclonedx_sbom_is_required_shape(self) -> None:
        valid = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "metadata": {},
            "components": [],
        }
        self.assertEqual(validate_sbom(valid), [])
        self.assertTrue(validate_sbom({"bomFormat": "SPDX"}))

    def test_required_workflows_enforce_supply_chain(self) -> None:
        central = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        reusable = (ROOT / ".github" / "workflows" / "reusable-build.yml").read_text(encoding="utf-8")
        toolchain = (ROOT / ".github" / "workflows" / "toolchain-images.yml").read_text(encoding="utf-8")
        archive = (ROOT / ".github" / "workflows" / "archive-artifacts.yml").read_text(encoding="utf-8")
        promote = (ROOT / ".github" / "workflows" / "promote.yml").read_text(encoding="utf-8")
        rollback = (ROOT / ".github" / "workflows" / "rollback.yml").read_text(encoding="utf-8")

        for workflow in (central, reusable, toolchain):
            self.assertIn("aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25", workflow)
            self.assertIn("v0.70.0", workflow)
            self.assertIn("supply_chain_policy.py", workflow)

        self.assertIn("security-scan.json", central)
        self.assertIn("security-sbom.cdx.json", central)
        self.assertIn("security-scan.json", reusable)
        self.assertIn("security-sbom.cdx.json", reusable)

        self.assertIn("sigstore/cosign-installer@6f9f17788090df1f26f669e9d70d6ae9567deba6", toolchain)
        self.assertIn("cosign verify", toolchain)
        self.assertIn("cosign sign-blob", archive)
        self.assertIn("id-token: write", archive)

        for workflow in (promote, rollback):
            self.assertIn("cosign verify-blob", workflow)
            self.assertIn("security-scan.json", workflow)
            self.assertIn("security-sbom.cdx.json", workflow)
            self.assertIn("supply_chain_policy.py", workflow)

        self.assertNotIn("package_artifact.py", rollback)
        self.assertNotIn("run_build.py", rollback)

    def test_archive_contract_requires_security_assets(self) -> None:
        archive_script = (ROOT / "scripts" / "ci" / "artifact_archive.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("security-scan.json", archive_script)
        self.assertIn("security-sbom.cdx.json", archive_script)
        self.assertIn("sigstore.json", archive_script)
        self.assertIn("assets_sha256", archive_script)


if __name__ == "__main__":
    unittest.main()
