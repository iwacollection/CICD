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
        self.assertIn('APT::Snapshot "20260828T000000Z"', text)


    def test_debian_snapshot_is_accepted_for_apt_based_toolchain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dockerfile = Path(directory) / "Dockerfile"
            dockerfile.write_text(
                "FROM golang:1.26.10-trixie@sha256:" + "a" * 64 + "\n"
                "RUN echo 'deb [check-valid-until=no] "
                "https://snapshot.debian.org/archive/debian/20261106T000000Z trixie main' "
                "> /etc/apt/sources.list && apt-get update\n",
                encoding="utf-8",
            )
            self.assertEqual(validate_dockerfile(dockerfile, self.policy), [])

    def test_gcc_toolchain_does_not_disable_apt_tls_verification(self) -> None:
        dockerfile = ROOT / "docker" / "toolchains" / "gcc" / "gcc12" / "Dockerfile"
        self.assertNotIn("Verify-Peer=false", dockerfile.read_text(encoding="utf-8"))

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

    def test_license_policy_blocks_named_denials_not_all_copyleft(self) -> None:
        report = {
            "Results": [
                {
                    "Target": "OS Packages",
                    "Licenses": [
                        {"Name": "GPL-3.0-only", "Severity": "HIGH"},
                        {"Name": "AGPL-3.0-only", "Severity": "HIGH"},
                    ],
                }
            ]
        }
        errors, summary = validate_trivy_report(report, self.policy)
        self.assertEqual(summary["licenses"], 2)
        self.assertEqual(summary["denied_licenses"], 1)
        self.assertFalse(any("GPL-3.0-only" in error and "AGPL" not in error for error in errors))
        self.assertTrue(any("AGPL-3.0-only" in error for error in errors))

    def test_cyclonedx_sbom_is_required_shape(self) -> None:
        valid = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "metadata": {},
            "components": [],
        }
        self.assertEqual(validate_sbom(valid), [])
        self.assertTrue(validate_sbom({"bomFormat": "SPDX"}))

    def test_capability_container_pins_security_fixed_ubuntu_packages(self) -> None:
        dockerfile_path = ROOT / "examples" / "capability-service" / "Dockerfile"
        dockerfile = dockerfile_path.read_text(encoding="utf-8")
        self.assertEqual(validate_dockerfile(dockerfile_path, self.policy), [])
        self.assertIn('APT::Snapshot "20261105T000000Z"', dockerfile)
        self.assertIn("apt=2.8.4ubuntu0.1", dockerfile)
        self.assertIn("libssl3t64=3.0.13-0ubuntu3.16", dockerfile)
        self.assertIn("Acquire::Retries=0", dockerfile)

    def test_required_workflows_enforce_supply_chain(self) -> None:
        central = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        dag_node = (ROOT / ".github" / "workflows" / "dag-node.yml").read_text(encoding="utf-8")
        dag_security = (ROOT / ".github" / "workflows" / "dag-security.yml").read_text(encoding="utf-8")
        reusable = (ROOT / ".github" / "workflows" / "reusable-build.yml").read_text(encoding="utf-8")
        reusable_container = (ROOT / ".github" / "workflows" / "reusable-container.yml").read_text(encoding="utf-8")
        toolchain = (ROOT / ".github" / "workflows" / "toolchain-images.yml").read_text(encoding="utf-8")
        archive = (ROOT / ".github" / "workflows" / "archive-artifacts.yml").read_text(encoding="utf-8")
        promote = (ROOT / ".github" / "workflows" / "promote.yml").read_text(encoding="utf-8")
        toolchain_promote = (ROOT / ".github" / "workflows" / "promote-toolchain.yml").read_text(encoding="utf-8")
        rollback = (ROOT / ".github" / "workflows" / "rollback.yml").read_text(encoding="utf-8")

        for workflow in (reusable, reusable_container, toolchain):
            self.assertIn("aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25", workflow)
            self.assertIn("v0.70.0", workflow)
            self.assertIn("supply_chain_policy.py", workflow)
            self.assertIn("ignore-unfixed: false", workflow)

        self.assertIn("aquasecurity/setup-trivy@3fb12ec12f41e471780db15c232d5dd185dcb514", dag_security)
        self.assertIn("actions/cache@27d5ce7f107fe9357f9df03efb73ab90386fccae", dag_security)
        self.assertIn("v0.70.0", dag_security)
        self.assertIn("parallel_trivy.py", dag_security)
        self.assertIn("--scanners", (ROOT / "scripts" / "ci" / "parallel_trivy.py").read_text(encoding="utf-8"))
        self.assertIn("--severity", (ROOT / "scripts" / "ci" / "parallel_trivy.py").read_text(encoding="utf-8"))
        self.assertIn("--trivy-stage-prestarted", (ROOT / "scripts" / "ci" / "parallel_trivy.py").read_text(encoding="utf-8"))
        self.assertIn("supply_chain_policy.py", dag_security)
        self.assertIn("security-scan.json", dag_security)
        self.assertIn("security-sbom.cdx.json", dag_security)

        self.assertIn("dag-node.yml", central)
        self.assertIn("dag-security.yml", central)
        self.assertIn("security_complete", central)
        self.assertIn("supply_chain_policy.py", central)
        self.assertNotIn("aquasecurity/trivy-action", dag_node)
        self.assertNotIn("supply_chain_policy.py", dag_node)
        self.assertIn("security-scan.json", dag_security)
        self.assertIn("security-sbom.cdx.json", dag_security)
        self.assertIn("security-scan.json", reusable)
        self.assertIn("security-sbom.cdx.json", reusable)

        self.assertIn("sigstore/cosign-installer@6f9f17788090df1f26f669e9d70d6ae9567deba6", toolchain)
        self.assertIn("cosign verify", toolchain)
        self.assertIn("cosign attest --yes --type cyclonedx", toolchain)
        self.assertIn("cosign attest --yes --type vuln", toolchain)
        self.assertIn("gh attestation verify", toolchain)
        self.assertIn("--bundle-from-oci", toolchain)
        self.assertIn("toolchain_registry.py register", toolchain)
        self.assertIn("Build and test Java consumer fixture against exact published digest", toolchain)
        self.assertIn("ignore-unfixed: false", toolchain)
        self.assertIn("cosign verify-attestation --type cyclonedx", toolchain_promote)
        self.assertIn("cosign verify-attestation --type vuln", toolchain_promote)
        self.assertIn("gh attestation verify", toolchain_promote)
        self.assertIn("--bundle-from-oci", toolchain_promote)
        self.assertIn("environment: toolchain-production", toolchain_promote)
        self.assertIn("toolchain_registry.py approve", toolchain_promote)
        self.assertIn('["gh", "pr", "create"', toolchain_promote)
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
