from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from toolchain_registry import validate_record, register_candidate, inspect_candidate  # noqa: E402


def valid_record() -> dict:
    return {
        "schema_version": 3,
        "kind": "toolchain-image-registry",
        "toolchain": "go126",
        "image": "ghcr.io/iwacollection/cicd-toolchain-go126",
        "digest": "sha256:" + "a" * 64,
        "immutable_reference": "ghcr.io/iwacollection/cicd-toolchain-go126@sha256:" + "a" * 64,
        "source_commit": "b" * 40,
        "workflow_run_id": "12345",
        "workflow_ref": "iwacollection/CICD/.github/workflows/toolchain-images.yml@refs/heads/main",
        "dockerfile_sha256": "c" * 64,
        "build_context_sha256": "d" * 64,
        "build_definition_sha256": "e" * 64,
        "sbom_sha256": "f" * 64,
        "scan_report_sha256": "1" * 64,
        "signature_verified": True,
        "sbom_attestation_verified": True,
        "scan_attestation_verified": True,
        "provenance_verified": True,
        "security_policy_passed": True,
    }


class ToolchainRegistryTests(unittest.TestCase):
    def test_candidate_record_requires_all_independent_evidence(self) -> None:
        record = valid_record()
        self.assertEqual(validate_record(record), [])
        record["provenance_verified"] = False
        self.assertTrue(any("provenance" in error for error in validate_record(record)))

    def test_candidate_registration_is_idempotent_for_same_digest_and_source(self) -> None:
        record = valid_record()
        with patch("toolchain_registry._list_all_pages", side_effect=[
            [{"id": 99, "payload": {
                "kind": "toolchain-image-registry",
                "digest": record["digest"],
                "source_commit": record["source_commit"],
            }}],
            [{"state": "success"}],
        ]), patch("toolchain_registry._create_deployment") as create:
            result = register_candidate(
                api_url="https://api.github.com", repository="iwacollection/CICD",
                token="token", record=record,
            )
        self.assertEqual(result["status"], "already-registered")
        self.assertEqual(result["deployment_id"], "99")
        create.assert_not_called()

    def test_candidate_inspection_requires_successful_registry_record(self) -> None:
        record = valid_record()
        with patch("toolchain_registry._list_all_pages", side_effect=[
            [{"id": 77, "payload": {
                "kind": "toolchain-image-registry", "toolchain": "go126",
                "digest": record["digest"], "state": "candidate",
            }}],
            [{"state": "success"}],
        ]):
            result = inspect_candidate(
                api_url="https://api.github.com", repository="iwacollection/CICD",
                token="token", toolchain="go126", digest=record["digest"],
            )
        self.assertEqual(result["deployment_id"], "77")


if __name__ == "__main__":
    unittest.main()
