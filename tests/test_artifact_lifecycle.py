from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from artifact_archive import release_tag  # noqa: E402
from deployment_pointer import create_pointer, inspect_pointer  # noqa: E402


class ArtifactLifecycleTests(unittest.TestCase):
    def test_release_tag_is_deterministic_and_content_addressed_by_identity(self) -> None:
        first = release_tag("app-generic-linux-x86_64-gcc-a-source1")
        second = release_tag("app-generic-linux-x86_64-gcc-a-source1")
        other = release_tag("app-generic-linux-x86_64-gcc-b-source1")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertRegex(first, r"^artifact-v2-[0-9a-f]{64}$")

    @patch("deployment_pointer._request")
    def test_create_pointer_records_digest_and_success_status(self, request) -> None:
        request.side_effect = [{"id": 321}, {"id": 654}]
        result = create_pointer(
            api_url="https://api.github.com",
            repository="example/repo",
            token="token",
            environment="production",
            artifact_name="artifact-a",
            bundle_sha256="a" * 64,
            source_sha="b" * 40,
            source_run_id="123",
            release_tag="artifact-v2-" + "c" * 64,
            reason="promotion",
        )
        self.assertEqual(result["deployment_id"], "321")
        self.assertEqual(result["bundle_sha256"], "a" * 64)
        create_payload = request.call_args_list[0].kwargs["payload"]
        self.assertEqual(create_payload["environment"], "production")
        self.assertEqual(create_payload["payload"]["artifact_name"], "artifact-a")
        self.assertEqual(create_payload["payload"]["bundle_sha256"], "a" * 64)
        status_payload = request.call_args_list[1].kwargs["payload"]
        self.assertEqual(status_payload["state"], "success")
        self.assertTrue(status_payload["auto_inactive"])

    @patch("deployment_pointer._request")
    def test_inspect_pointer_rejects_non_v2_payload(self, request) -> None:
        request.return_value = {
            "id": 42,
            "environment": "production",
            "payload": {"schema_version": 1, "artifact_contract": 1},
        }
        with self.assertRaisesRegex(ValueError, "Artifact Contract v2"):
            inspect_pointer(
                api_url="https://api.github.com",
                repository="example/repo",
                token="token",
                deployment_id="42",
            )

    def test_archive_workflow_only_accepts_successful_main_push_builds(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "archive-artifacts.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("github.event.workflow_run.conclusion == 'success'", workflow)
        self.assertIn("github.event.workflow_run.event == 'push'", workflow)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", workflow)
        self.assertIn("verify_artifact.py", workflow)
        self.assertIn("gh attestation verify", workflow)
        self.assertIn("artifact_archive.py archive", workflow)

    def test_promotion_uses_long_term_archive_not_actions_artifact(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "promote.yml").read_text(encoding="utf-8")
        self.assertIn("artifact_archive.py download", workflow)
        self.assertIn("deployment_pointer.py create", workflow)
        self.assertIn("deployments: write", workflow)
        self.assertNotIn("actions/download-artifact", workflow)
        self.assertNotIn("retention-days: 90", workflow)

    def test_rollback_restores_historical_pointer_without_rebuild(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "rollback.yml").read_text(encoding="utf-8")
        self.assertIn("deployment_pointer.py inspect", workflow)
        self.assertIn("Require same-environment rollback source", workflow)
        self.assertIn("artifact_archive.py download", workflow)
        self.assertIn("--reason rollback", workflow)
        self.assertIn("--restored-from-deployment-id", workflow)
        self.assertNotIn("package_artifact.py", workflow)
        self.assertNotIn("run_build.py", workflow)


if __name__ == "__main__":
    unittest.main()
