from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from dependency_plan import build_levels  # noqa: E402
from discover_matrix import build_matrix  # noqa: E402
from impact_analysis import analyze_impact  # noqa: E402
from toolchain_catalog import (  # noqa: E402
    build_publish_matrix,
    immutable_reference,
    load_toolchain_catalog,
    validate_toolchain_catalog,
)
from validate_config import load_catalog, validate_catalog  # noqa: E402


class CiPlatformTests(unittest.TestCase):
    def setUp(self) -> None:
        self.projects = load_catalog(ROOT / "ci" / "projects.json")
        self.toolchains = load_toolchain_catalog(ROOT / "ci" / "toolchains.json")

    def test_catalogs_are_valid(self) -> None:
        self.assertEqual(validate_toolchain_catalog(self.toolchains), [])
        self.assertEqual(validate_catalog(self.projects, self.toolchains), [])

    def test_disabled_soc_template_not_in_matrix(self) -> None:
        matrix = build_matrix(self.projects, self.toolchains)
        self.assertEqual([item["project"] for item in matrix["include"]], ["hello-cpp"])
        target = matrix["include"][0]
        self.assertEqual(json.loads(target["runner_labels"]), ["ubuntu-latest"])
        self.assertEqual(target["execution_mode"], "container")
        self.assertEqual(target["toolchain"], "gcc-host-container-v1")
        self.assertEqual(target["toolchain_status"], "active")
        self.assertRegex(target["toolchain_identity"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(
            target["container_image"],
            "ghcr.io/iwacollection/cicd-toolchain-gcc-host@" + target["toolchain_identity"],
        )
        self.assertEqual(target["lane"], "full")

    def test_fast_lane_can_select_projects_and_fast_test(self) -> None:
        data = {
            "schema_version": 1,
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
            ],
        }
        toolchains = {
            "schema_version": 1,
            "registry_policy": {"allowed_image_prefixes": [], "require_digest_for_active_containers": True},
            "toolchains": [{"id": "gcc", "status": "active", "execution_mode": "host"}],
        }
        matrix = build_matrix(data, toolchains, project_names={"a"}, lane="fast")
        self.assertEqual(len(matrix["include"]), 1)
        self.assertEqual(matrix["include"][0]["project"], "a")
        self.assertEqual(matrix["include"][0]["test_command"], "make smoke-test")
        self.assertEqual(matrix["include"][0]["lane"], "fast")

    def test_project_cannot_own_container_image_or_dockerfile(self) -> None:
        data = deepcopy(self.projects)
        target = data["projects"][0]["targets"][0]
        target["execution_mode"] = "container"
        target["container_image"] = "ghcr.io/example/unsafe:latest"
        target["container_dockerfile"] = "Dockerfile"
        errors = validate_catalog(data, self.toolchains)
        self.assertTrue(any("toolchain execution and image ownership belong to ci/toolchains.json" in error for error in errors))

    def test_enabled_target_requires_active_toolchain(self) -> None:
        toolchains = deepcopy(self.toolchains)
        container = next(item for item in toolchains["toolchains"] if item["id"] == "gcc-host-container-v1")
        container["status"] = "candidate"
        errors = validate_catalog(self.projects, toolchains)
        self.assertTrue(any("must be active" in error for error in errors))

    def test_enabled_target_rejects_unknown_toolchain(self) -> None:
        data = deepcopy(self.projects)
        data["projects"][0]["targets"][0]["toolchain"] = "does-not-exist"
        errors = validate_catalog(data, self.toolchains)
        self.assertTrue(any("unknown toolchain" in error for error in errors))

    def test_active_container_requires_full_sha256_digest(self) -> None:
        data = deepcopy(self.toolchains)
        container = next(item for item in data["toolchains"] if item["id"] == "gcc-host-container-v1")
        container["status"] = "active"
        container["digest"] = "sha256:deadbeef"
        errors = validate_toolchain_catalog(data)
        self.assertTrue(any("requires immutable sha256 digest" in error for error in errors))

    def test_active_container_resolves_immutable_reference(self) -> None:
        item = {
            "id": "gcc-container",
            "status": "active",
            "execution_mode": "container",
            "image": "ghcr.io/iwacollection/gcc-container",
            "digest": "sha256:" + "a" * 64,
        }
        self.assertEqual(
            immutable_reference(item),
            "ghcr.io/iwacollection/gcc-container@sha256:" + "a" * 64,
        )

    def test_candidate_or_active_toolchain_is_buildable_but_planned_is_not(self) -> None:
        matrix = build_publish_matrix(self.toolchains)
        ids = [item["toolchain"] for item in matrix["include"]]
        self.assertIn("gcc-host-container-v1", ids)
        self.assertNotIn("rk-sdk-2026.08", ids)

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
