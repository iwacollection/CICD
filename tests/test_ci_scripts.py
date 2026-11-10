from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from cache_fingerprint import fingerprint_files  # noqa: E402
from dag_plan import render_dag  # noqa: E402
from dependency_plan import build_levels  # noqa: E402
from discover_matrix import build_matrix  # noqa: E402
from impact_analysis import analyze_impact  # noqa: E402
from resolve_upstream_artifacts import resolve as resolve_upstream_artifacts  # noqa: E402
from toolchain_catalog import (  # noqa: E402
    build_context_sha256,
    candidate_build_definition_sha256,
    build_publish_matrix,
    immutable_reference,
    load_toolchain_catalog,
    validate_toolchain_catalog,
)
from validate_config import load_catalog, validate_catalog  # noqa: E402
from validate_promotion_source import (  # noqa: E402
    validate_dispatch_inputs,
    validate_run_metadata,
)
from verify_artifact import validate_manifest_identity  # noqa: E402
from upstream_artifact_pattern import build_pattern as build_upstream_artifact_pattern  # noqa: E402


class CiPlatformTests(unittest.TestCase):
    def setUp(self) -> None:
        self.projects = load_catalog(ROOT / "ci" / "projects.json")
        self.toolchains = load_toolchain_catalog(ROOT / "ci" / "toolchains.json")

    def test_catalogs_are_valid(self) -> None:
        self.assertEqual(validate_toolchain_catalog(self.toolchains), [])
        self.assertEqual(validate_catalog(self.projects, self.toolchains), [])

    def test_disabled_soc_template_not_in_matrix(self) -> None:
        matrix = build_matrix(self.projects, self.toolchains)
        self.assertEqual(
            [item["project"] for item in matrix["include"]],
            ["hello-lib", "hello-cpp", "hello-service", "hello-release", "conan-format-lib", "conan-compression-lib", "conan-demo-app"],
        )
        targets = {item["project"]: item for item in matrix["include"]}
        target = targets["hello-cpp"]
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
        self.assertEqual(json.loads(target["cache_key_files"]), ["CMakeLists.txt", "src/main.cpp"])
        self.assertEqual(json.loads(target["depends_on"]), ["hello-lib"])

    def test_cache_fingerprint_changes_with_declared_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "deps.lock").write_text("v1\n", encoding="utf-8")
            before = fingerprint_files(root, ["deps.lock"])
            (root / "deps.lock").write_text("v2\n", encoding="utf-8")
            after = fingerprint_files(root, ["deps.lock"])
            self.assertNotEqual(before, after)

    def test_cache_fingerprint_fails_closed_on_missing_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "matched no files"):
                fingerprint_files(Path(directory), ["missing.lock"])

    def test_cache_paths_require_declared_key_files(self) -> None:
        data = deepcopy(self.projects)
        target = data["projects"][0]["targets"][0]
        target["cache_key_files"] = []
        errors = validate_catalog(data, self.toolchains)
        self.assertTrue(any("cache_key_files is required" in error for error in errors))

    def test_project_scoped_cache_paths_are_inside_project_checkout(self) -> None:
        for project in self.projects["projects"]:
            project_path = project["path"].rstrip("/") + "/"
            for target in project.get("targets", []):
                if target.get("cache_scope", "project") != "project":
                    continue
                for cache_path in target.get("cache_paths", []):
                    with self.subTest(project=project["name"], cache_path=cache_path):
                        self.assertTrue(
                            cache_path.startswith(project_path),
                            f"{project['name']} project cache must live under {project['path']}: {cache_path}",
                        )

    def test_dag_cache_lifecycle_uses_stable_key_and_trusted_main_saves(self) -> None:
        node = (ROOT / ".github" / "workflows" / "dag-node.yml").read_text(encoding="utf-8")
        key_lines = [line for line in node.splitlines() if "key: dag-ci-v4-" in line]
        self.assertEqual(len(key_lines), 2)
        identity_fields = (
            "inputs.cache_scope",
            "inputs.project",
            "inputs.soc",
            "inputs.target_os",
            "inputs.arch",
            "inputs.toolchain_identity",
        )
        for line in key_lines:
            self.assertNotIn("github.run_id", line)
            self.assertNotIn("github.run_attempt", line)
            for field in identity_fields:
                self.assertIn(field, line)

        restore_block = node.split("restore-keys:", 1)[1].split(
            "- name: Finish cache restore timer", 1
        )[0]
        for field in identity_fields:
            self.assertIn(field, restore_block)
        self.assertNotIn("steps.cache_key.outputs.fingerprint", restore_block)

        self.assertIn("uses: actions/cache/restore@", node)
        self.assertIn("uses: actions/cache/save@", node)
        save_block = node.split("- name: Save build cache", 1)[1]
        self.assertIn("github.event_name == 'push'", save_block)
        self.assertIn("github.ref == 'refs/heads/main'", save_block)
        self.assertIn("steps.cache_restore.outputs.cache-hit != 'true'", save_block)
        self.assertIn("steps.artifact_verify.outcome == 'success'", save_block)
        self.assertIn("steps.artifact_upload.outcome == 'success'", save_block)


    def test_promotion_source_requires_successful_main_push_build(self) -> None:
        run_id = "12345"
        repository = "iwacollection/CICD"
        run = {
            "id": int(run_id),
            "status": "completed",
            "conclusion": "success",
            "event": "push",
            "head_branch": "main",
            "head_sha": "a" * 40,
            "path": ".github/workflows/ci.yml",
            "repository": {"full_name": repository},
        }
        self.assertEqual(validate_run_metadata(run, repository, run_id), [])
        run["event"] = "workflow_dispatch"
        self.assertTrue(any("push event" in error for error in validate_run_metadata(run, repository, run_id)))

    def test_promotion_inputs_reject_shell_payloads(self) -> None:
        errors = validate_dispatch_inputs(
            "123", 'artifact"; touch /tmp/pwned; #', "a" * 64
        )
        self.assertTrue(any("artifact_name" in error for error in errors))

    def test_manifest_identity_is_bound_to_source_run(self) -> None:
        manifest = {
            "source_sha": "a" * 40,
            "workflow_run_id": "123",
            "source_repository": "iwacollection/CICD",
        }
        self.assertEqual(
            validate_manifest_identity(
                manifest,
                source_sha="a" * 40,
                run_id="123",
                repository="iwacollection/CICD",
            ),
            [],
        )
        self.assertTrue(validate_manifest_identity(manifest, run_id="999"))

    def test_external_actions_are_pinned_to_full_commit(self) -> None:
        for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for line in workflow.read_text(encoding="utf-8").splitlines():
                match = re.search(r"\buses:\s+([^\s#]+)", line)
                if not match or match.group(1).startswith("./"):
                    continue
                reference = match.group(1).rsplit("@", 1)[-1]
                self.assertRegex(reference, r"^[0-9a-f]{40}$", msg=f"{workflow}: {line}")

    def test_pr_builds_are_forced_to_hosted_runner(self) -> None:
        central = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        node = (ROOT / ".github" / "workflows" / "dag-node.yml").read_text(encoding="utf-8")
        self.assertIn("dag-node.yml", central)
        self.assertIn("name: Attest trusted DAG artifacts", central)
        self.assertIn(
            "github.event_name == 'pull_request' && 'ubuntu-latest' || fromJSON(inputs.runner_labels_json)",
            node,
        )
        self.assertIn("name: Hosted hardware PR validation", node)
        self.assertNotIn("id-token: write", node)
        self.assertNotIn("attestations: write", node)

        reusable = (
            ROOT / ".github" / "workflows" / "reusable-build.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("name: Hosted PR validation lane", reusable)
        self.assertIn('"self-hosted" in labels', reusable)
        self.assertIn("if: steps.lane.outputs.hardware_pr != 'true'", reusable)
        self.assertIn("pr_validation_command", reusable)

    def test_promotion_verifies_source_run_and_provenance(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "promote.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("validate_promotion_source.py", workflow)
        self.assertIn("--expected-source-sha", workflow)
        self.assertIn("gh attestation verify", workflow)

    def test_toolchain_publish_smokes_exact_digest(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "toolchain-images.yml"
        ).read_text(encoding="utf-8")
        smoke = workflow.index("name: Smoke test exact published digest")
        attest = workflow.index("name: Attest published image provenance")
        self.assertLess(smoke, attest)
        self.assertIn('immutable_image="${IMAGE}@${DIGEST}"', workflow[smoke:attest])
        self.assertIn('docker pull "$immutable_image"', workflow[smoke:attest])
        self.assertEqual(workflow.count("scanners: vuln,misconfig,secret,license"), 2)
        self.assertIn("--sbom toolchain-sbom.cdx.json", workflow)
        self.assertIn("--sbom published-sbom.cdx.json", workflow)
        self.assertIn("name: Generate published digest SBOM", workflow)

    def test_fast_lane_can_select_projects_and_fast_test(self) -> None:
        data = {
            "schema_version": 1,
            "projects": [
                {
                    "name": "a",
                    "enabled": True,
                    "path": "services/a",
                    "depends_on": [],
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
                    "depends_on": [],
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
        self.assertEqual(matrix["include"][0]["depends_on"], "[]")

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

    def test_only_unpublished_candidate_toolchains_are_built_and_published(self) -> None:
        pending = deepcopy(self.toolchains)
        pending_java17 = next(
            item for item in pending["toolchains"]
            if item["id"] == "java17-maven"
        )
        pending_java17["digest"] = ""
        pending_ids = [
            item["toolchain"]
            for item in build_publish_matrix(pending)["include"]
        ]
        self.assertIn("java17-maven", pending_ids)
        self.assertNotIn("gcc-host-container-v1", pending_ids)
        self.assertNotIn("rk-sdk-2026.08", pending_ids)

        published_ids = [
            item["toolchain"]
            for item in build_publish_matrix(self.toolchains)["include"]
        ]
        self.assertIn("java17-maven", published_ids)
        self.assertIn("go126", published_ids)

    def test_published_candidate_context_mismatch_fails_closed(self) -> None:
        published = deepcopy(self.toolchains)
        java17 = next(
            item for item in published["toolchains"]
            if item["id"] == "java17-maven"
        )
        java17["digest"] = "sha256:" + "a" * 64
        java17["build_context_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "build context changed.*clear digest"):
            build_publish_matrix(published)

    def test_published_candidate_build_definition_mismatch_fails_closed(self) -> None:
        published = deepcopy(self.toolchains)
        java17 = next(
            item for item in published["toolchains"]
            if item["id"] == "java17-maven"
        )
        java17["digest"] = "sha256:" + "a" * 64
        context_digest = build_context_sha256(ROOT, java17["context"])
        java17["build_context_sha256"] = context_digest
        java17["build_definition_sha256"] = candidate_build_definition_sha256(java17, context_digest)
        java17["smoke_command"] = "bash /usr/local/bin/skip-smoke"
        with self.assertRaisesRegex(ValueError, "build definition changed.*clear digest"):
            build_publish_matrix(published)

    def test_build_context_hash_covers_paths_and_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = root / "context"
            context.mkdir()
            source = context / "Dockerfile"
            source.write_text("FROM scratch\\n", encoding="utf-8")
            first = build_context_sha256(root, "context")
            source.write_text("FROM busybox\\n", encoding="utf-8")
            second = build_context_sha256(root, "context")
            self.assertNotEqual(first, second)

    def test_java17_toolchain_is_published_candidate_but_not_enabled_for_projects(self) -> None:
        java17 = next(
            item for item in self.toolchains["toolchains"]
            if item["id"] == "java17-maven"
        )
        self.assertEqual(java17["status"], "candidate")
        self.assertEqual(java17["execution_mode"], "container")
        self.assertEqual(java17["jdk_version"], "17.0.21+9")
        self.assertEqual(java17["maven_version"], "3.10.0")
        self.assertEqual(java17["guava_version"], "33.7.2-jre")
        self.assertEqual(
            java17["digest"],
            "",
        )
        self.assertEqual(
            java17["build_context_sha256"],
            "",
        )
        self.assertEqual(
            java17["build_definition_sha256"],
            "",
        )
        self.assertEqual(java17["apt_snapshot"], "20261105T000000Z")
        self.assertEqual(
            java17["image"],
            "ghcr.io/iwacollection/cicd-toolchain-java17-maven",
        )
        self.assertEqual(
            java17["smoke_command"],
            "bash /usr/local/bin/java-toolchain-smoke",
        )
        referenced_by_projects = [
            target.get("toolchain")
            for project in self.projects["projects"]
            for target in project.get("targets", [])
        ]
        self.assertNotIn("java17-maven", referenced_by_projects)
        self.assertEqual(validate_toolchain_catalog(self.toolchains), [])
        self.assertEqual(validate_catalog(self.projects, self.toolchains), [])

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

    def test_downstream_change_includes_transitive_prerequisites(self) -> None:
        data = {
            "projects": [
                {"name": "lib", "enabled": True, "path": "libs/core", "depends_on": [], "targets": []},
                {"name": "service", "enabled": True, "path": "services/api", "depends_on": ["lib"], "targets": []},
                {"name": "ui", "enabled": True, "path": "apps/ui", "depends_on": ["service"], "targets": []},
            ]
        }
        result = analyze_impact(data, ["apps/ui/src/app.ts"])
        self.assertEqual(result["lane"], "fast")
        self.assertEqual(result["projects"], ["lib", "service", "ui"])

    def test_real_catalog_renders_two_executable_dag_levels(self) -> None:
        rendered = render_dag(
            self.projects,
            self.toolchains,
            {"hello-lib", "hello-cpp"},
            "full",
        )
        self.assertEqual(rendered["level_count"], 2)
        self.assertEqual(rendered["levels"][0]["projects"], ["hello-lib"])
        self.assertEqual(rendered["levels"][1]["projects"], ["hello-cpp"])
        self.assertEqual(rendered["levels"][0]["count"], 1)
        self.assertEqual(rendered["levels"][1]["count"], 1)
        app = rendered["levels"][1]["matrix"]["include"][0]
        self.assertEqual(json.loads(app["depends_on"]), ["hello-lib"])

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


    def test_cross_project_rename_impacts_both_source_and_destination_projects(self) -> None:
        data = {
            "projects": [
                {"name": "app-a", "enabled": True, "path": "apps/a", "depends_on": [], "targets": []},
                {"name": "app-b", "enabled": True, "path": "apps/b", "depends_on": [], "targets": []},
            ]
        }
        # With git diff --no-renames, a move is represented as the old path
        # being deleted and the new path being added. Both projects must be
        # selected so neither side of the move is silently omitted.
        result = analyze_impact(
            data,
            ["apps/a/src/moved.cpp", "apps/b/src/moved.cpp"],
        )
        self.assertEqual(result["lane"], "fast")
        self.assertEqual(result["direct_projects"], ["app-a", "app-b"])
        self.assertEqual(result["projects"], ["app-a", "app-b"])

    def test_git_diff_change_detection_disables_rename_collapse(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("git diff --name-only --no-renames"), 2)


    def test_deleted_source_file_selects_owner_and_transitive_consumers_with_reasons(self) -> None:
        data = {
            "projects": [
                {"name": "lib", "enabled": True, "path": "libs/core", "depends_on": [], "targets": []},
                {"name": "service", "enabled": True, "path": "services/api", "depends_on": ["lib"], "targets": []},
                {"name": "ui", "enabled": True, "path": "apps/ui", "depends_on": ["service"], "targets": []},
                {"name": "other", "enabled": True, "path": "apps/other", "depends_on": [], "targets": []},
            ]
        }
        result = analyze_impact(data, ["libs/core/src/removed.cpp"])
        self.assertEqual(result["projects"], ["lib", "service", "ui"])
        self.assertEqual(result["direct_projects"], ["lib"])
        self.assertEqual(result["dependent_closure"], ["service", "ui"])
        self.assertEqual(result["dependency_closure"], [])
        self.assertIn(
            "direct path match: libs/core/src/removed.cpp",
            result["project_reasons"]["lib"]["reasons"],
        )
        self.assertIn(
            "downstream consumer of lib",
            result["project_reasons"]["service"]["reasons"],
        )
        self.assertFalse(result["project_reasons"]["other"]["included"])
        self.assertTrue(result["project_reasons"]["other"]["reasons"])

    def test_shared_public_header_selects_all_declared_consumers(self) -> None:
        data = {
            "projects": [
                {"name": "format-lib", "enabled": True, "path": "libs/format", "impact_paths": ["shared/public/**"], "depends_on": [], "targets": []},
                {"name": "codec-lib", "enabled": True, "path": "libs/codec", "impact_paths": ["shared/public/**"], "depends_on": [], "targets": []},
                {"name": "format-app", "enabled": True, "path": "apps/format", "depends_on": ["format-lib"], "targets": []},
                {"name": "codec-app", "enabled": True, "path": "apps/codec", "depends_on": ["codec-lib"], "targets": []},
                {"name": "integration", "enabled": True, "path": "tests/integration", "depends_on": ["format-app", "codec-app"], "targets": []},
            ]
        }
        result = analyze_impact(data, ["shared/public/api.h"])
        self.assertEqual(result["direct_projects"], ["format-lib", "codec-lib"])
        self.assertEqual(
            result["projects"],
            ["format-lib", "codec-lib", "format-app", "codec-app", "integration"],
        )
        self.assertIn(
            "direct path match: shared/public/api.h",
            result["project_reasons"]["codec-lib"]["reasons"],
        )

    def test_go_module_manifest_change_is_project_scoped(self) -> None:
        data = {
            "projects": [
                {"name": "go-api", "enabled": True, "path": "services/go-api", "depends_on": [], "targets": []},
                {"name": "go-consumer", "enabled": True, "path": "services/go-consumer", "depends_on": ["go-api"], "targets": []},
                {"name": "cpp-app", "enabled": True, "path": "apps/cpp", "depends_on": [], "targets": []},
            ]
        }
        result = analyze_impact(
            data,
            ["services/go-api/go.mod", "services/go-api/go.sum"],
        )
        self.assertEqual(result["lane"], "fast")
        self.assertEqual(result["direct_projects"], ["go-api"])
        self.assertEqual(result["projects"], ["go-api", "go-consumer"])
        self.assertNotIn("cpp-app", result["projects"])

    def test_toolchain_registry_project_catalog_and_shared_build_scripts_force_full(self) -> None:
        data = {
            "projects": [
                {"name": "a", "enabled": True, "path": "apps/a", "depends_on": [], "targets": []},
                {"name": "b", "enabled": True, "path": "apps/b", "depends_on": [], "targets": []},
            ]
        }
        for path in (
            "ci/toolchains.json",
            "ci/projects.json",
            "scripts/ci/run_build.py",
            "docker/toolchains/go/go126/Dockerfile",
        ):
            with self.subTest(path=path):
                result = analyze_impact(data, [path])
                self.assertEqual(result["lane"], "full")
                self.assertEqual(result["projects"], ["a", "b"])
                self.assertTrue(
                    all(
                        result["project_reasons"][name]["included"]
                        and result["project_reasons"][name]["reasons"]
                        for name in ("a", "b")
                    )
                )

    def test_workflow_change_selects_smoke_projects_and_prerequisites_not_dependents(self) -> None:
        data = {
            "projects": [
                {"name": "base", "enabled": True, "path": "libs/base", "depends_on": [], "targets": []},
                {"name": "smoke-app", "enabled": True, "path": "apps/smoke", "depends_on": ["base"], "ci_smoke": True, "targets": []},
                {"name": "release", "enabled": True, "path": "apps/release", "depends_on": ["smoke-app"], "targets": []},
            ]
        }
        result = analyze_impact(data, [".github/workflows/ci.yml"])
        self.assertEqual(result["lane"], "smoke")
        self.assertEqual(result["projects"], ["base", "smoke-app"])
        self.assertEqual(result["dependency_closure"], ["base"])
        self.assertEqual(result["dependent_closure"], [])
        self.assertFalse(result["project_reasons"]["release"]["included"])

    def test_workflow_change_without_declared_smoke_project_fails_full(self) -> None:
        data = {
            "projects": [
                {"name": "a", "enabled": True, "path": "apps/a", "depends_on": [], "targets": []},
                {"name": "b", "enabled": True, "path": "apps/b", "depends_on": [], "targets": []},
            ]
        }
        result = analyze_impact(data, [".github/workflows/ci.yml"])
        self.assertEqual(result["lane"], "full")
        self.assertEqual(result["projects"], ["a", "b"])

    def test_docs_plus_build_policy_change_never_selects_none(self) -> None:
        data = {
            "projects": [
                {"name": "a", "enabled": True, "path": "apps/a", "depends_on": [], "targets": []},
                {"name": "b", "enabled": True, "path": "apps/b", "depends_on": [], "targets": []},
            ]
        }
        result = analyze_impact(data, ["docs/architecture.md", "ci/projects.json"])
        self.assertEqual(result["lane"], "full")
        self.assertEqual(result["projects"], ["a", "b"])

    def test_project_reasons_cover_included_excluded_and_disabled_projects(self) -> None:
        data = {
            "projects": [
                {"name": "selected", "enabled": True, "path": "apps/selected", "depends_on": [], "targets": []},
                {"name": "unrelated", "enabled": True, "path": "apps/unrelated", "depends_on": [], "targets": []},
                {"name": "disabled", "enabled": False, "path": "apps/disabled", "depends_on": [], "targets": []},
            ]
        }
        result = analyze_impact(data, ["apps/selected/src/main.go"])
        self.assertEqual(set(result["project_reasons"]), {"selected", "unrelated", "disabled"})
        self.assertTrue(result["project_reasons"]["selected"]["included"])
        self.assertFalse(result["project_reasons"]["unrelated"]["included"])
        self.assertIn("excluded: project is disabled in ci/projects.json", result["project_reasons"]["disabled"]["reasons"])
        self.assertEqual(result["excluded_projects"], ["unrelated", "disabled"])

    def test_impact_plan_is_deterministic_and_incremental_selection_is_subset_of_full_oracle(self) -> None:
        data = {
            "projects": [
                {"name": "lib-a", "enabled": True, "path": "libs/a", "depends_on": [], "targets": []},
                {"name": "lib-b", "enabled": True, "path": "libs/b", "depends_on": [], "targets": []},
                {"name": "service", "enabled": True, "path": "services/api", "depends_on": ["lib-a", "lib-b"], "targets": []},
                {"name": "ui", "enabled": True, "path": "apps/ui", "depends_on": ["service"], "targets": []},
            ]
        }
        changed = ["services/api/go.mod", "services/api/go.sum"]
        first = analyze_impact(data, changed)
        second = analyze_impact(data, changed)
        full = analyze_impact(data, changed, force_full=True)
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True),
            json.dumps(second, ensure_ascii=False, sort_keys=True),
        )
        self.assertTrue(set(first["projects"]).issubset(set(full["projects"])))
        self.assertTrue({"lib-a", "lib-b", "service", "ui"}.issubset(set(first["projects"])))



    def test_discover_uploads_the_explainable_impact_plan(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn('export CI_IMPACT_PLAN_FILE="$GITHUB_WORKSPACE/ci-impact-plan.json"', workflow)
        self.assertIn("ci-impact-plan.json", workflow)
        self.assertIn("project_reasons_json", (ROOT / "scripts" / "ci" / "impact_analysis.py").read_text(encoding="utf-8"))


    def _create_upstream_artifact_fixture(
        self,
        root: Path,
        *,
        bundle_content: bytes = b"trusted upstream output",
        manifest_content: bytes | None = None,
    ) -> tuple[Path, Path]:
        artifact_dir = root / "dag-lib"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        bundle = artifact_dir / "lib-artifact.tar.gz"
        with tarfile.open(bundle, "w:gz") as archive:
            info = tarfile.TarInfo("dist/lib.txt")
            info.size = len(bundle_content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(bundle_content))

        recorded_content = bundle_content if manifest_content is None else manifest_content
        manifest = {
            "schema_version": 2,
            "artifact_name": "lib-artifact",
            "project": "lib",
            "source_sha": "a" * 40,
            "source_repository": "iwacollection/CICD",
            "source": {
                "repository": "iwacollection/CICD",
                "commit_sha": "a" * 40,
                "workflow_run_id": "123",
                "workflow_run_attempt": "1",
            },
            "workflow_run_id": "123",
            "workflow_run_attempt": "1",
            "target": {
                "soc": "generic",
                "target_os": "linux",
                "arch": "x86_64",
                "toolchain": "gcc-host-v1",
            },
            "toolchain": {
                "id": "gcc-host-v1",
                "identity": "host:gcc-host-v1",
                "execution_mode": "host",
                "container_image": "",
            },
            "runner": {"labels": ["ubuntu-latest"]},
            "compiler_versions": {},
            "dependencies": {"locks": [], "upstream_artifacts": []},
            "bundle": {
                "file": bundle.name,
                "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                "size_bytes": bundle.stat().st_size,
                "format": "tar.gz",
                "reproducible": True,
                "source_date_epoch": 0,
            },
            "files": [
                {
                    "path": "dist/lib.txt",
                    "sha256": hashlib.sha256(recorded_content).hexdigest(),
                    "size_bytes": len(recorded_content),
                }
            ],
        }
        manifest_path = artifact_dir / "lib.manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return bundle, manifest_path

    def _resolve_upstream_fixture(
        self,
        directory: Path,
        destination: Path,
        *,
        expected_source_sha: str = "a" * 40,
        expected_repository: str = "iwacollection/CICD",
        target_os: str = "linux",
    ) -> dict:
        return resolve_upstream_artifacts(
            directory,
            ["lib"],
            soc="generic",
            target_os=target_os,
            arch="x86_64",
            destination=destination,
            expected_source_sha=expected_source_sha,
            expected_repository=expected_repository,
        )

    def test_upstream_artifact_pattern_selects_only_declared_direct_dependencies(self) -> None:
        self.assertEqual(
            build_upstream_artifact_pattern(["hello-service"]),
            "dag-hello-service-*",
        )
        self.assertEqual(
            build_upstream_artifact_pattern(["conan-format-lib", "conan-compression-lib"]),
            "dag-{conan-compression-lib,conan-format-lib}-*",
        )
        with self.assertRaisesRegex(ValueError, "non-empty"):
            build_upstream_artifact_pattern([])
        with self.assertRaisesRegex(ValueError, "duplicates"):
            build_upstream_artifact_pattern(["hello-service", "hello-service"])
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            build_upstream_artifact_pattern(["service api", "service-api"])

        node = (ROOT / ".github" / "workflows" / "dag-node.yml").read_text(encoding="utf-8")
        self.assertIn("upstream_artifact_pattern.py", node)
        self.assertIn("pattern: ${{ steps.upstream_pattern.outputs.pattern }}", node)
        self.assertNotIn("pattern: dag-*", node)

    def test_upstream_resolver_fails_closed_when_artifact_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "no compatible upstream artifact"):
                self._resolve_upstream_fixture(root, root / "resolved")

    def test_upstream_resolver_fails_closed_on_source_sha_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_upstream_artifact_fixture(root)
            with self.assertRaisesRegex(ValueError, "manifest source_sha mismatch"):
                self._resolve_upstream_fixture(
                    root,
                    root / "resolved",
                    expected_source_sha="b" * 40,
                )

    def test_upstream_resolver_fails_closed_on_repository_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_upstream_artifact_fixture(root)
            with self.assertRaisesRegex(ValueError, "manifest source_repository mismatch"):
                self._resolve_upstream_fixture(
                    root,
                    root / "resolved",
                    expected_repository="untrusted/fork",
                )

    def test_upstream_resolver_rejects_non_v2_artifact_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, manifest_path = self._create_upstream_artifact_fixture(root)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["schema_version"] = 1
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "DAG handoff requires Artifact Contract v2"):
                self._resolve_upstream_fixture(root, root / "resolved")

    def test_upstream_resolver_fails_closed_on_target_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_upstream_artifact_fixture(root)
            with self.assertRaisesRegex(ValueError, "no compatible upstream artifact"):
                self._resolve_upstream_fixture(
                    root,
                    root / "resolved",
                    target_os="windows",
                )

    def test_upstream_resolver_fails_closed_on_corrupted_bundle_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, _ = self._create_upstream_artifact_fixture(root)
            bundle.write_bytes(bundle.read_bytes() + b"corrupted")
            with self.assertRaisesRegex(ValueError, "upstream bundle digest mismatch"):
                self._resolve_upstream_fixture(root, root / "resolved")

    def test_upstream_resolver_fails_closed_on_manifest_member_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_upstream_artifact_fixture(
                root,
                bundle_content=b"tampered payload",
                manifest_content=b"expected payload",
            )
            with self.assertRaisesRegex(ValueError, "bundle member digest mismatch"):
                self._resolve_upstream_fixture(root, root / "resolved")

    def test_upstream_resolver_accepts_verified_contract_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_upstream_artifact_fixture(root)
            result = self._resolve_upstream_fixture(root, root / "resolved")
            self.assertEqual(result["count"], 1)
            self.assertTrue((root / "resolved" / "lib" / "dist" / "lib.txt").is_file())
            self.assertTrue(Path(result["index"]).is_file())


if __name__ == "__main__":
    unittest.main()
