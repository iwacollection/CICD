from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from reproducibility_check import compare_iterations  # noqa: E402
from run_build import _container_env_args  # noqa: E402


class ReproducibilityGateTests(unittest.TestCase):
    def _iteration(self, artifact_digest: str, bundle_digest: str) -> dict:
        return {
            "artifacts": {
                "build/libhello-lib.a": {
                    "sha256": artifact_digest,
                    "size_bytes": 123,
                }
            },
            "bundle": {
                "file": "hello-lib.tar.gz",
                "sha256": bundle_digest,
            },
        }

    def test_identical_artifact_and_bundle_bytes_are_reproducible(self) -> None:
        first = self._iteration("a" * 64, "b" * 64)
        second = self._iteration("a" * 64, "b" * 64)
        self.assertEqual(compare_iterations(first, second), [])

    def test_artifact_byte_drift_is_rejected(self) -> None:
        first = self._iteration("a" * 64, "b" * 64)
        second = self._iteration("c" * 64, "b" * 64)
        mismatches = compare_iterations(first, second)
        self.assertTrue(any(item["kind"] == "artifact-bytes" for item in mismatches))

    def test_bundle_byte_drift_is_rejected(self) -> None:
        first = self._iteration("a" * 64, "b" * 64)
        second = self._iteration("a" * 64, "c" * 64)
        mismatches = compare_iterations(first, second)
        self.assertTrue(any(item["kind"] == "artifact-v2-bundle" for item in mismatches))

    def test_missing_artifact_is_rejected(self) -> None:
        first = self._iteration("a" * 64, "b" * 64)
        second = self._iteration("a" * 64, "b" * 64)
        second["artifacts"] = {}
        mismatches = compare_iterations(first, second)
        self.assertTrue(any(item["kind"] == "artifact-set" for item in mismatches))

    @patch.dict(
        os.environ,
        {
            "SOURCE_DATE_EPOCH": "1700000000",
            "CCACHE_DISABLE": "1",
            "SECRET_SHOULD_NOT_LEAK": "top-secret",
        },
        clear=True,
    )
    def test_container_only_receives_reproducibility_allowlist(self) -> None:
        args = _container_env_args(ROOT)
        joined = " ".join(args)
        self.assertIn("SOURCE_DATE_EPOCH=1700000000", joined)
        self.assertIn("CCACHE_DISABLE=1", joined)
        self.assertNotIn("SECRET_SHOULD_NOT_LEAK", joined)
        self.assertNotIn("top-secret", joined)

    def test_required_platform_validate_enforces_double_build_gate(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
        self.assertIn("- name: Reproducibility gate", workflow)
        self.assertIn("scripts/ci/reproducibility_check.py", workflow)
        self.assertIn("--project hello-lib", workflow)
        self.assertIn("--soc generic", workflow)
        self.assertIn("--target-os linux", workflow)
        self.assertIn("--arch x86_64", workflow)
        self.assertIn("- name: Enforce reproducibility result", workflow)
        self.assertIn('REPRO_OUTCOME: ${{ steps.reproducibility.outcome }}', workflow)
        self.assertIn("reproducibility.json", workflow)
        self.assertIn("reproducibility.md", workflow)
        self.assertIn("retention-days: 30", workflow)

    def test_checker_is_hosted_container_only_and_disables_ccache(self) -> None:
        checker = (ROOT / "scripts" / "ci" / "reproducibility_check.py").read_text(encoding="utf-8")
        self.assertIn('runner_labels != ["ubuntu-latest"]', checker)
        self.assertIn('execution_mode") != "container"', checker)
        self.assertIn("immutable_reference(toolchain)", checker)
        self.assertIn("CCACHE_DISABLE=1", checker)
        self.assertIn("SOURCE_DATE_EPOCH", checker)
        self.assertIn("TemporaryDirectory", checker)


if __name__ == "__main__":
    unittest.main()
