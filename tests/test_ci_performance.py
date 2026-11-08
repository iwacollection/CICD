from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from ci_metrics import aggregate_metrics, finalize_metrics, finish_stage, start_stage  # noqa: E402
from discover_matrix import build_matrix  # noqa: E402
from impact_analysis import analyze_impact  # noqa: E402
from package_artifact import build_artifact_name  # noqa: E402
from security_evidence import create_manifest, merge_security_evidence  # noqa: E402
from toolchain_catalog import load_toolchain_catalog, toolchain_identity  # noqa: E402
from validate_config import load_catalog  # noqa: E402


class CiPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.projects = load_catalog(ROOT / "ci" / "projects.json")
        self.toolchains = load_toolchain_catalog(ROOT / "ci" / "toolchains.json")

    def test_workflow_only_change_uses_declared_smoke_roots(self) -> None:
        result = analyze_impact(self.projects, [".github/workflows/ci.yml"])
        self.assertEqual(result["lane"], "smoke")
        self.assertEqual(result["projects"], ["hello-lib"])
        self.assertEqual(result["direct_projects"], [])

    def test_project_change_still_rebuilds_dependents_and_prerequisites(self) -> None:
        result = analyze_impact(self.projects, ["examples/cpp-app/src/main.cpp"])
        self.assertEqual(result["lane"], "fast")
        self.assertEqual(
            result["projects"],
            ["hello-lib", "hello-cpp", "embedded-firmware-template", "hello-service", "hello-release"],
        )

    def test_build_semantic_global_change_still_uses_full_lane(self) -> None:
        for path in ("ci/projects.json", "scripts/ci/run_build.py", "docker/toolchains/gcc-host/Dockerfile"):
            with self.subTest(path=path):
                result = analyze_impact(self.projects, [path])
                self.assertEqual(result["lane"], "full")
                self.assertEqual(
                    result["projects"],
                    ["hello-lib", "hello-cpp", "embedded-firmware-template", "hello-service", "hello-release"],
                )

    def test_smoke_lane_uses_fast_test_commands(self) -> None:
        matrix = build_matrix(
            self.projects,
            self.toolchains,
            project_names={"hello-lib"},
            lane="smoke",
        )
        self.assertEqual(len(matrix["include"]), 1)
        self.assertEqual(matrix["include"][0]["lane"], "smoke")
        self.assertEqual(
            matrix["include"][0]["test_command"],
            next(
                target["fast_test_command"]
                for project in self.projects["projects"]
                if project["name"] == "hello-lib"
                for target in project["targets"]
                if target.get("enabled", True)
            ),
        )

    def test_discovery_artifact_name_matches_artifact_contract_v2(self) -> None:
        source_sha = "a" * 40
        with patch.dict(os.environ, {"GITHUB_SHA": source_sha}):
            matrix = build_matrix(
                self.projects,
                self.toolchains,
                project_names={"hello-lib"},
                lane="full",
            )
        item = matrix["include"][0]
        expected = build_artifact_name(
            item["project"],
            item["soc"],
            item["target_os"],
            item["arch"],
            item["toolchain"],
            item["toolchain_identity"],
            source_sha,
        )
        self.assertEqual(item["artifact_name"], expected)
        self.assertEqual(item["build_parallelism"], "auto")

    def test_stage_metrics_finalize_and_aggregate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working = root / "working.json"
            output = root / "build-job" / "ci-stage-metrics.json"
            start_stage(working, "compile")
            time.sleep(0.015)
            finish_stage(working, "compile")
            report = finalize_metrics(working, output)
            self.assertEqual(report["stages"]["compile"]["status"], "success")
            self.assertGreater(report["stages"]["compile"]["duration_ms"], 0)
            self.assertGreater(report["instrumented_duration_ms"], 0)

            aggregate = aggregate_metrics(root)
            self.assertEqual(aggregate["job_count"], 1)
            self.assertEqual(aggregate["stage_summary"]["compile"]["count"], 1)
            self.assertGreater(aggregate["stage_summary"]["compile"]["median_ms"], 0)

    def test_security_evidence_merges_only_for_matching_artifact_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_name = "hello-lib-generic-linux-x86_64-gcc-host-123456789abc-abcdef012345"
            build_dir = root / "incoming" / f"dag-{artifact_name}"
            evidence_root = root / "incoming"
            evidence_dir = evidence_root / f"security-{artifact_name}"
            build_dir.mkdir(parents=True)
            evidence_dir.mkdir(parents=True)

            build_manifest = {
                "schema_version": 2,
                "artifact_name": artifact_name,
                "project": "hello-lib",
                "source_sha": "a" * 40,
                "source_repository": "iwacollection/CICD",
                "workflow_run_id": "12345",
                "workflow_run_attempt": "2",
                "target": {"soc": "generic", "target_os": "linux", "arch": "x86_64"},
                "toolchain": {"id": "gcc-host", "identity": "sha256:" + "1" * 64},
            }
            (build_dir / f"{artifact_name}.manifest.json").write_text(
                json.dumps(build_manifest), encoding="utf-8"
            )
            create_manifest(
                evidence_dir / "security-evidence.json",
                artifact_name=artifact_name,
                project="hello-lib",
                soc="generic",
                target_os="linux",
                arch="x86_64",
                toolchain="gcc-host",
                toolchain_identity="sha256:" + "1" * 64,
                source_sha="a" * 40,
                source_repository="iwacollection/CICD",
                workflow_run_id="12345",
                workflow_run_attempt="2",
            )
            (evidence_dir / "security-scan.json").write_text('{"Results": []}\n', encoding="utf-8")
            (evidence_dir / "security-sbom.cdx.json").write_text(
                '{"bomFormat": "CycloneDX"}\n', encoding="utf-8"
            )

            merged = merge_security_evidence(
                build_dir,
                evidence_root,
                expected_source_sha="a" * 40,
                expected_repository="iwacollection/CICD",
                expected_run_id="12345",
            )
            self.assertEqual(merged["artifact_name"], artifact_name)
            self.assertTrue((build_dir / "security-scan.json").is_file())
            self.assertTrue((build_dir / "security-sbom.cdx.json").is_file())

            evidence_manifest = evidence_dir / "security-evidence.json"
            tampered = json.loads(evidence_manifest.read_text(encoding="utf-8"))
            tampered["target"]["arch"] = "arm64"
            evidence_manifest.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                merge_security_evidence(
                    build_dir,
                    evidence_root,
                    expected_source_sha="a" * 40,
                    expected_repository="iwacollection/CICD",
                    expected_run_id="12345",
                )

    def test_build_security_and_archive_workflows_remain_separated_and_gated(self) -> None:
        build = (ROOT / ".github" / "workflows" / "dag-node.yml").read_text(encoding="utf-8")
        security = (ROOT / ".github" / "workflows" / "dag-security.yml").read_text(encoding="utf-8")
        central = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        archive = (ROOT / ".github" / "workflows" / "archive-artifacts.yml").read_text(encoding="utf-8")

        self.assertNotIn("aquasecurity/trivy-action", build)
        self.assertIn("aquasecurity/trivy-action", security)
        self.assertIn("Generate CycloneDX SBOM", security)
        self.assertIn("name: Detached security evidence barrier", central)
        self.assertIn('SECURITY_RESULT: ${{ needs.security_complete.result }}', central)
        self.assertIn("security_evidence.py merge", archive)
        self.assertIn("name: Aggregate CI stage timings", central)

    def test_external_github_actions_remain_pinned(self) -> None:
        for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for line in workflow.read_text(encoding="utf-8").splitlines():
                if "uses:" not in line or "./" in line:
                    continue
                reference = line.split("uses:", 1)[1].strip().split()[0].rsplit("@", 1)[-1]
                self.assertRegex(reference, r"^[0-9a-f]{40}$", msg=f"{workflow}: {line}")


if __name__ == "__main__":
    unittest.main()
