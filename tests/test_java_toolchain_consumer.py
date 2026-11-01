from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from resolve_toolchain import resolve_toolchain
from run_build import container_command
from toolchain_catalog import (
    build_consumer_matrix,
    build_context_sha256,
    candidate_build_definition_sha256,
)


class ResolveToolchainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.image = "ghcr.io/iwacollection/cicd-toolchain-java17-maven"
        self.digest = "sha256:" + "a" * 64
        self.catalog = {
            "schema_version": 1,
            "registry_policy": {
                "allowed_image_prefixes": ["ghcr.io/iwacollection/"],
                "require_digest_for_active_containers": True,
            },
            "toolchains": [
                {
                    "id": "java17-maven",
                    "status": "active",
                    "execution_mode": "container",
                    "image": self.image,
                    "digest": self.digest,
                }
            ],
        }

    def test_active_container_resolves_exact_digest(self) -> None:
        resolved = resolve_toolchain(
            self.catalog,
            "java17-maven",
            expected_mode="container",
            expected_image=f"{self.image}@{self.digest}",
        )
        self.assertEqual(resolved["status"], "active")
        self.assertEqual(resolved["image"], f"{self.image}@{self.digest}")
        self.assertEqual(resolved["identity"], self.digest)

    def test_project_may_resolve_image_from_toolchain_id_only(self) -> None:
        resolved = resolve_toolchain(
            self.catalog,
            "java17-maven",
            expected_mode="container",
        )
        self.assertEqual(
            resolved["image"],
            f"{self.image}@{self.digest}",
        )

    def test_candidate_is_never_consumable_by_general_build(self) -> None:
        candidate = json.loads(json.dumps(self.catalog))
        candidate["toolchains"][0]["status"] = "candidate"
        with self.assertRaisesRegex(ValueError, "only Active"):
            resolve_toolchain(
                candidate,
                "java17-maven",
                expected_mode="container",
                expected_image=f"{self.image}@{self.digest}",
            )

    def test_image_must_match_central_registry_exactly(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match Active"):
            resolve_toolchain(
                self.catalog,
                "java17-maven",
                expected_mode="container",
                expected_image=f"{self.image}@sha256:" + "b" * 64,
            )

    def test_host_toolchain_has_stable_identity(self) -> None:
        catalog = {
            "schema_version": 1,
            "registry_policy": {"allowed_image_prefixes": []},
            "toolchains": [
                {
                    "id": "gcc-host-v1",
                    "status": "active",
                    "execution_mode": "host",
                    "description": "test host compiler",
                }
            ],
        }
        resolved = resolve_toolchain(
            catalog,
            "gcc-host-v1",
            expected_mode="host",
        )
        self.assertEqual(resolved["identity"], "host:gcc-host-v1")
        self.assertEqual(resolved["image"], "")


class ContainerHomeTests(unittest.TestCase):
    def test_java_build_home_and_maven_cache_are_workspace_writable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            with patch("run_build.shutil.which", return_value="/usr/bin/docker"):
                command = container_command(
                    workdir,
                    "mvn --batch-mode clean verify",
                    "ghcr.io/iwacollection/cicd-toolchain-java17-maven@sha256:" + "a" * 64,
                )

            self.assertTrue((workdir / ".ci-home").is_dir())
            self.assertTrue((workdir / ".cache" / "maven" / "repository").is_dir())
            self.assertIn("HOME=/workspace/.ci-home", command)
            self.assertIn(
                "MAVEN_OPTS=-Dmaven.repo.local=/workspace/.cache/maven/repository",
                command,
            )
            with patch.dict("os.environ", {"MAVEN_ARGS": "-s /workspace/.ci-maven-settings.xml"}):
                with patch("run_build.shutil.which", return_value="/usr/bin/docker"):
                    command_with_settings = container_command(
                        workdir,
                        "mvn --batch-mode clean verify",
                        "ghcr.io/iwacollection/cicd-toolchain-java17-maven@sha256:" + "a" * 64,
                    )
            self.assertIn("MAVEN_ARGS=-s /workspace/.ci-maven-settings.xml", command_with_settings)


class CandidateConsumerMatrixTests(unittest.TestCase):
    def test_pinned_candidate_requires_a_matching_project_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = root / "docker" / "toolchains" / "java17"
            fixture = root / "examples" / "java17-maven-service"
            (context).mkdir(parents=True)
            (fixture / "ci").mkdir(parents=True)
            (context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (context / "smoke.sh").write_text("echo ok\n", encoding="utf-8")
            (fixture / "pom.xml").write_text("<project/>\n", encoding="utf-8")
            (fixture / "ci" / "toolchain.json").write_text(
                json.dumps({"schema_version": 1, "toolchain": "java17-maven"}),
                encoding="utf-8",
            )
            item = {
                "id": "java17-maven",
                "status": "candidate",
                "execution_mode": "container",
                "image": "ghcr.io/iwacollection/cicd-toolchain-java17-maven",
                "digest": "sha256:" + "a" * 64,
                "dockerfile": "docker/toolchains/java17/Dockerfile",
                "context": "docker/toolchains/java17",
                "platforms": ["linux/amd64"],
                "smoke_command": "bash smoke.sh",
                "consumer_fixture": "examples/java17-maven-service",
                "jdk_version": "17.0.21+9",
                "maven_version": "3.10.0",
            }
            context_digest = build_context_sha256(root, item["context"])
            item["build_context_sha256"] = context_digest
            item["build_definition_sha256"] = candidate_build_definition_sha256(
                item, context_digest
            )

            matrix = build_consumer_matrix({"toolchains": [item]}, root)
            self.assertEqual(len(matrix["include"]), 1)
            self.assertEqual(
                matrix["include"][0]["immutable_reference"],
                item["image"] + "@" + item["digest"],
            )

    def test_fixture_must_declare_the_same_toolchain_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = root / "docker" / "toolchains" / "java17"
            fixture = root / "examples" / "java17-maven-service"
            context.mkdir(parents=True)
            (fixture / "ci").mkdir(parents=True)
            (context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (fixture / "pom.xml").write_text("<project/>\n", encoding="utf-8")
            (fixture / "ci" / "toolchain.json").write_text(
                json.dumps({"schema_version": 1, "toolchain": "java11-maven"}),
                encoding="utf-8",
            )
            item = {
                "id": "java17-maven",
                "status": "candidate",
                "execution_mode": "container",
                "image": "ghcr.io/iwacollection/cicd-toolchain-java17-maven",
                "digest": "sha256:" + "a" * 64,
                "dockerfile": "docker/toolchains/java17/Dockerfile",
                "context": "docker/toolchains/java17",
                "platforms": ["linux/amd64"],
                "smoke_command": "bash smoke.sh",
                "consumer_fixture": "examples/java17-maven-service",
            }
            context_digest = build_context_sha256(root, item["context"])
            item["build_context_sha256"] = context_digest
            item["build_definition_sha256"] = candidate_build_definition_sha256(
                item, context_digest
            )
            with self.assertRaisesRegex(ValueError, "must declare toolchain"):
                build_consumer_matrix({"toolchains": [item]}, root)


if __name__ == "__main__":
    unittest.main()
