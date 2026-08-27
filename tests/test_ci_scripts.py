from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from dependency_plan import build_levels  # noqa: E402
from discover_matrix import build_matrix  # noqa: E402
from impact_analysis import analyze_impact  # noqa: E402
from validate_config import load_catalog, validate_catalog  # noqa: E402


class CiPlatformTests(unittest.TestCase):
    def test_catalog_is_valid(self) -> None:
        data = load_catalog(ROOT / "ci" / "projects.json")
        self.assertEqual(validate_catalog(data), [])

    def test_disabled_soc_template_not_in_matrix(self) -> None:
        data = load_catalog(ROOT / "ci" / "projects.json")
        matrix = build_matrix(data)
        self.assertEqual([item["project"] for item in matrix["include"]], ["hello-cpp"])
        target = matrix["include"][0]
        self.assertEqual(json.loads(target["runner_labels"]), ["ubuntu-latest"])
        self.assertEqual(target["execution_mode"], "container")
        self.assertEqual(target["container_dockerfile"], "docker/toolchains/gcc-host/Dockerfile")
        self.assertEqual(target["container_image"], "")
        self.assertEqual(target["lane"], "full")

    def test_fast_lane_can_select_projects_and_fast_test(self) -> None:
        data = {
            "projects": [
                {
                    "name": "a",
                    "enabled": True,
                    "path": "services/a",
                    "targets": [
                        {
                            "enabled": True,
                            "soc": "generic",
                            "target_os": "linux",
                            "arch": "x86_64",
                            "toolchain": "gcc",
                            "runner_labels": ["ubuntu-latest"],
                            "build_command": "make",
                            "test_command": "make full-test",
                            "fast_test_command": "make smoke-test",
                            "artifact_paths": ["out/a"],
                        }
                    ],
                },
                {
                    "name": "b",
                    "enabled": True,
                    "path": "services/b",
                    "targets": [
                        {
                            "enabled": True,
                            "soc": "generic",
                            "target_os": "linux",
                            "arch": "x86_64",
                            "toolchain": "gcc",
                            "runner_labels": ["ubuntu-latest"],
                            "build_command": "make",
                            "test_command": "make full-test",
                            "artifact_paths": ["out/b"],
                        }
                    ],
                },
            ]
        }
        matrix = build_matrix(data, project_names={"a"}, lane="fast")
        self.assertEqual(len(matrix["include"]), 1)
        self.assertEqual(matrix["include"][0]["project"], "a")
        self.assertEqual(matrix["include"][0]["test_command"], "make smoke-test")
        self.assertEqual(matrix["include"][0]["lane"], "fast")

    def test_container_mode_requires_image_or_dockerfile(self) -> None:
        data = load_catalog(ROOT / "ci" / "projects.json")
        target = data["projects"][0]["targets"][0]
        target["container_image"] = ""
        target["container_dockerfile"] = ""
        errors = validate_catalog(data)
        self.assertTrue(any("container mode requires" in error for error in errors))

    def test_container_mode_rejects_two_image_sources(self) -> None:
        data = load_catalog(ROOT / "ci" / "projects.json")
        target = data["projects"][0]["targets"][0]
        target["container_image"] = "registry.example.com/ci/toolchain@sha256:deadbeef"
        target["container_dockerfile"] = "docker/toolchains/gcc-host/Dockerfile"
        errors = validate_catalog(data)
        self.assertTrue(any("choose container_image or container_dockerfile" in error for error in errors))

    def test_dependency_levels_allow_parallel_roots(self) -> None:
        data = {
            "projects": [
                {"name": "lib-a", "enabled": True, "depends_on": []},
                {"name": "lib-b", "enabled": True, "depends_on": []},
                {"name": "app", "enabled": True, "depends_on": ["lib-a", "lib-b"]},
            ]
        }
        self.assertEqual(build_levels(data), [["lib-a", "lib-b"], ["app"]])

    def test_dependency_cycle_is_rejected(self) -> None:
        data = {
            "projects": [
                {"name": "a", "enabled": True, "depends_on": ["b"]},
                {"name": "b", "enabled": True, "depends_on": ["a"]},
            ]
        }
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            build_levels(data)

    def test_project_change_uses_fast_lane(self) -> None:
        data = {
            "projects": [
                {"name": "app-a", "enabled": True, "path": "apps/a", "depends_on": [], "targets": []},
                {"name": "app-b", "enabled": True, "path": "apps/b", "depends_on": [], "targets": []},
            ]
        }
        result = analyze_impact(data, ["apps/a/src/main.cpp"])
        self.assertEqual(result["lane"], "fast")
        self.assertEqual(result["projects"], ["app-a"])

    def test_dependency_change_expands_to_dependents(self) -> None:
        data = {
            "projects": [
                {"name": "lib", "enabled": True, "path": "libs/core", "depends_on": [], "targets": []},
                {"name": "service", "enabled": True, "path": "services/api", "depends_on": ["lib"], "targets": []},
                {"name": "ui", "enabled": True, "path": "apps/ui", "depends_on": ["service"], "targets": []},
            ]
        }
        result = analyze_impact(data, ["libs/core/include/core.h"])
        self.assertEqual(result["lane"], "fast")
        self.assertEqual(result["projects"], ["lib", "service", "ui"])

    def test_impact_paths_can_claim_shared_files(self) -> None:
        data = {
            "projects": [
                {
                    "name": "service",
                    "enabled": True,
                    "path": "services/api",
                    "impact_paths": ["shared/protos/**"],
                    "depends_on": [],
                    "targets": [],
                }
            ]
        }
        result = analyze_impact(data, ["shared/protos/order.proto"])
        self.assertEqual(result["lane"], "fast")
        self.assertEqual(result["projects"], ["service"])

    def test_global_ci_change_forces_full_lane(self) -> None:
        data = {
            "projects": [
                {"name": "a", "enabled": True, "path": "apps/a", "depends_on": [], "targets": []},
                {"name": "b", "enabled": True, "path": "apps/b", "depends_on": [], "targets": []},
            ]
        }
        result = analyze_impact(data, ["scripts/ci/run_build.py"])
        self.assertEqual(result["lane"], "full")
        self.assertEqual(result["projects"], ["a", "b"])

    def test_unknown_build_path_fails_safe_to_full_lane(self) -> None:
        data = {
            "projects": [
                {"name": "a", "enabled": True, "path": "apps/a", "depends_on": [], "targets": []},
                {"name": "b", "enabled": True, "path": "apps/b", "depends_on": [], "targets": []},
            ]
        }
        result = analyze_impact(data, ["shared/new-library/source.cc"])
        self.assertEqual(result["lane"], "full")
        self.assertEqual(result["projects"], ["a", "b"])

    def test_docs_only_change_skips_build(self) -> None:
        data = {
            "projects": [
                {"name": "a", "enabled": True, "path": "apps/a", "depends_on": [], "targets": []},
            ]
        }
        result = analyze_impact(data, ["docs/architecture.md"])
        self.assertEqual(result["lane"], "none")
        self.assertEqual(result["projects"], [])


if __name__ == "__main__":
    unittest.main()
