from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from package_artifact import (  # noqa: E402
    _identity_suffix,
    _resolve_toolchain_identity,
    _write_reproducible_tar_gz,
    build_manifest,
    sha256,
)
from verify_artifact import (  # noqa: E402
    validate_manifest_contract,
    verify_bundle_contents,
)


class ArtifactContractV2Tests(unittest.TestCase):
    def test_reproducible_bundle_has_identical_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory) / "work"
            out = Path(directory) / "out"
            workdir.mkdir()
            out.mkdir()
            artifact = workdir / "bin" / "app"
            artifact.parent.mkdir()
            artifact.write_bytes(b"same-input\n")
            first = out / "first.tar.gz"
            second = out / "second.tar.gz"

            _write_reproducible_tar_gz(first, [artifact], workdir, 0)
            os.utime(artifact, (123456789, 123456789))
            _write_reproducible_tar_gz(second, [artifact], workdir, 0)

            self.assertEqual(sha256(first), sha256(second))

    def test_toolchain_identity_uses_container_digest(self) -> None:
        digest = "sha256:" + "a" * 64
        image = f"ghcr.io/example/toolchain@{digest}"
        self.assertEqual(_resolve_toolchain_identity("gcc-v1", "", image), digest)
        self.assertEqual(_identity_suffix(digest), "a" * 12)

    def test_v2_manifest_binds_toolchain_runner_locks_files_and_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory) / "work"
            out = Path(directory) / "dist"
            workdir.mkdir()
            out.mkdir()
            artifact = workdir / "build" / "app"
            artifact.parent.mkdir()
            artifact.write_bytes(b"binary\n")
            lock = workdir / "deps.lock"
            lock.write_text("dep=1\n", encoding="utf-8")
            base = "demo-generic-linux-x86_64-gcc-v1-bbbbbbbbbbbb-cccccccccccc"
            bundle = out / f"{base}.tar.gz"
            _write_reproducible_tar_gz(bundle, [artifact], workdir, 0)

            args = argparse.Namespace(
                project="demo",
                soc="generic",
                target_os="linux",
                arch="x86_64",
                toolchain="gcc-v1",
                execution_mode="container",
                container_image="ghcr.io/example/gcc@sha256:" + "b" * 64,
                runner_labels_json='["ubuntu-latest"]',
            )
            metadata = {
                "runner": {
                    "name": "runner-1",
                    "os": "Linux",
                    "arch": "X64",
                    "environment": "github-hosted",
                    "image_os": "ubuntu24",
                    "image_version": "20260830.1",
                },
                "tools": {"gcc": "gcc (Ubuntu) 14.2.0", "cmake": "cmake version 3.31.0"},
            }
            upstream = [
                {
                    "project": "hello-lib",
                    "artifact_name": "hello-lib-artifact",
                    "bundle_sha256": "d" * 64,
                    "source_sha": "c" * 40,
                    "source_repository": "example/repo",
                    "target": {
                        "soc": "generic",
                        "target_os": "linux",
                        "arch": "x86_64",
                        "toolchain": "gcc-v1",
                    },
                }
            ]
            env = {
                "GITHUB_SHA": "c" * 40,
                "GITHUB_REPOSITORY": "example/repo",
                "GITHUB_RUN_ID": "123",
                "GITHUB_RUN_ATTEMPT": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                manifest = build_manifest(
                    args=args,
                    workdir=workdir,
                    files=[artifact],
                    dependency_locks=[lock],
                    upstream_artifacts=upstream,
                    bundle=bundle,
                    bundle_digest=sha256(bundle),
                    base=base,
                    toolchain_identity="sha256:" + "b" * 64,
                    build_metadata=metadata,
                    source_date_epoch=0,
                )

            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(manifest["toolchain"]["identity"], "sha256:" + "b" * 64)
            self.assertEqual(manifest["runner"]["labels"], ["ubuntu-latest"])
            self.assertEqual(manifest["compiler_versions"]["gcc"], "gcc (Ubuntu) 14.2.0")
            self.assertEqual(manifest["dependencies"]["locks"][0]["path"], "deps.lock")
            self.assertEqual(
                manifest["dependencies"]["upstream_artifacts"][0]["bundle_sha256"],
                "d" * 64,
            )
            self.assertEqual(manifest["files"][0]["path"], "build/app")
            self.assertEqual(validate_manifest_contract(manifest), [])
            self.assertEqual(verify_bundle_contents(bundle, manifest), [])

    def test_v2_verifier_rejects_manifest_member_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory) / "work"
            workdir.mkdir()
            artifact = workdir / "app"
            artifact.write_bytes(b"binary\n")
            bundle = Path(directory) / "bundle.tar.gz"
            _write_reproducible_tar_gz(bundle, [artifact], workdir, 0)
            manifest = {
                "schema_version": 2,
                "artifact_name": "bundle",
                "project": "demo",
                "source_sha": "a" * 40,
                "source_repository": "example/repo",
                "workflow_run_id": "1",
                "source": {},
                "target": {"soc": "generic", "target_os": "linux", "arch": "x86_64", "toolchain": "gcc"},
                "toolchain": {"id": "gcc", "identity": "host:gcc", "execution_mode": "host", "container_image": ""},
                "runner": {"labels": ["ubuntu-latest"]},
                "compiler_versions": {},
                "dependencies": {"locks": [], "upstream_artifacts": []},
                "bundle": {
                    "file": "bundle.tar.gz",
                    "sha256": sha256(bundle),
                    "size_bytes": bundle.stat().st_size,
                    "format": "tar.gz",
                    "reproducible": True,
                    "source_date_epoch": 0,
                },
                "files": [{"path": "app", "sha256": "0" * 64, "size_bytes": 7, "mode": "0644"}],
            }
            errors = verify_bundle_contents(bundle, manifest)
            self.assertTrue(any("digest mismatch" in error for error in errors))

    def test_workflows_use_artifact_contract_v2(self) -> None:
        dag_node = (ROOT / ".github" / "workflows" / "dag-node.yml").read_text(encoding="utf-8")
        reusable = (ROOT / ".github" / "workflows" / "reusable-build.yml").read_text(encoding="utf-8")
        for workflow in (dag_node, reusable):
            self.assertIn("collect_build_metadata.py", workflow)
            self.assertIn("--dependency-locks-json", workflow)
            self.assertIn("--build-metadata-file build-environment.json", workflow)
            self.assertIn("Artifact contract: v2", workflow)
        self.assertIn("--upstream-index", dag_node)
        central = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("name: Attest artifact manifests", central)
        self.assertIn("dag-node.yml", central)


if __name__ == "__main__":
    unittest.main()
