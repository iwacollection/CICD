from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from discover_matrix import build_matrix  # noqa: E402


class RunnerTrustBoundaryTests(unittest.TestCase):
    def test_matrix_carries_hosted_pr_validation_command(self) -> None:
        projects = {
            "schema_version": 1,
            "projects": [
                {
                    "name": "firmware",
                    "enabled": True,
                    "path": "firmware/product-a",
                    "depends_on": [],
                    "targets": [
                        {
                            "enabled": True,
                            "soc": "rk",
                            "target_os": "linux",
                            "arch": "arm64",
                            "toolchain": "rk-sdk",
                            "runner_labels": ["self-hosted", "linux", "arm64", "soc-rk"],
                            "build_command": "./ci/build.sh rk linux arm64",
                            "test_command": "./ci/test-package.sh out/rk",
                            "pr_validation_command": "./ci/pr-validate.sh rk linux arm64",
                            "artifact_paths": ["out/rk/**/*.img"],
                        }
                    ],
                }
            ],
        }
        toolchains = {
            "schema_version": 1,
            "registry_policy": {
                "allowed_image_prefixes": [],
                "require_digest_for_active_containers": True,
            },
            "toolchains": [
                {
                    "id": "rk-sdk",
                    "status": "active",
                    "execution_mode": "host",
                }
            ],
        }

        matrix = build_matrix(projects, toolchains)
        self.assertEqual(
            matrix["include"][0]["pr_validation_command"],
            "./ci/pr-validate.sh rk linux arm64",
        )
        self.assertEqual(matrix["include"][0]["depends_on"], "[]")

    def test_central_dag_preserves_untrusted_hardware_boundary(self) -> None:
        central = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        node = (ROOT / ".github" / "workflows" / "dag-node.yml").read_text(encoding="utf-8")
        self.assertIn("name: Reject non-main manual dispatch", central)
        self.assertIn("github.ref != 'refs/heads/main'", central)
        self.assertIn("dag-node.yml", central)

        self.assertIn("github.event_name == 'pull_request' && 'ubuntu-latest'", node)
        self.assertIn("name: Hosted hardware PR validation", node)
        self.assertIn("inputs.pr_validation_command", node)
        self.assertIn("Self-hosted DAG target requires pr_validation_command", node)
        self.assertIn("if: steps.trust.outputs.hardware_pr != 'true'", node)
        self.assertNotIn("id-token: write", node)
        self.assertNotIn("attestations: write", node)

    def test_reusable_workflow_fails_closed_for_self_hosted_prs(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "reusable-build.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("contains(fromJSON(inputs.runner_labels_json), 'self-hosted')", workflow)
        self.assertIn("github.ref != 'refs/heads/main'", workflow)
        self.assertIn("name: Reject untrusted self-hosted event", workflow)
        self.assertIn("Self-hosted hardware PR validation requires pr_validation_command", workflow)
        self.assertIn("exit 1", workflow)
        self.assertNotIn("Validation: metadata only", workflow)


if __name__ == "__main__":
    unittest.main()
