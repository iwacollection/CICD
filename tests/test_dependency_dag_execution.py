from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from package_artifact import _write_reproducible_tar_gz, sha256  # noqa: E402
from resolve_upstream_artifacts import resolve  # noqa: E402


class DependencyDagExecutionTests(unittest.TestCase):
    def _write_upstream(
        self,
        root: Path,
        *,
        project: str = "hello-lib",
        content: bytes = b"library-v1\n",
        soc: str = "generic",
    ) -> tuple[Path, str]:
        workdir = root / "work"
        artifact_root = root / f"dag-{project}-artifact"
        dist = artifact_root / "dist"
        workdir.mkdir(parents=True)
        dist.mkdir(parents=True)
        output = workdir / "build" / "libhello-lib.a"
        output.parent.mkdir()
        output.write_bytes(content)

        artifact_name = f"{project}-{soc}-linux-x86_64-gcc-test-aaaaaaaaaaaa"
        bundle = dist / f"{artifact_name}.tar.gz"
        _write_reproducible_tar_gz(bundle, [output], workdir, 0)
        digest = sha256(bundle)
        manifest = {
            "schema_version": 2,
            "artifact_name": artifact_name,
            "project": project,
            "source_sha": "a" * 40,
            "source_repository": "example/repo",
            "workflow_run_id": "123",
            "workflow_run_attempt": "1",
            "source": {
                "repository": "example/repo",
                "commit_sha": "a" * 40,
                "workflow_run_id": "123",
                "workflow_run_attempt": "1",
                "workflow_ref": "",
            },
            "target": {
                "soc": soc,
                "target_os": "linux",
                "arch": "x86_64",
                "toolchain": "gcc-test",
            },
            "toolchain": {
                "id": "gcc-test",
                "identity": "host:gcc-test",
                "execution_mode": "host",
                "container_image": "",
            },
            "runner": {"labels": ["ubuntu-latest"]},
            "compiler_versions": {},
            "dependencies": {"locks": [], "upstream_artifacts": []},
            "bundle": {
                "file": bundle.name,
                "sha256": digest,
                "size_bytes": bundle.stat().st_size,
                "format": "tar.gz",
                "reproducible": True,
                "source_date_epoch": 0,
            },
            "files": [
                {
                    "path": "build/libhello-lib.a",
                    "sha256": sha256(output),
                    "size_bytes": output.stat().st_size,
                    "mode": "0644",
                }
            ],
        }
        manifest_path = dist / f"{artifact_name}.manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return artifact_root, digest

    def test_resolver_verifies_and_extracts_exact_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "incoming"
            incoming.mkdir()
            self._write_upstream(incoming)
            destination = root / "consumer" / ".ci-upstream"

            result = resolve(
                incoming,
                ["hello-lib"],
                soc="generic",
                target_os="linux",
                arch="x86_64",
                destination=destination,
                expected_source_sha="a" * 40,
                expected_repository="example/repo",
            )

            extracted = destination / "hello-lib" / "build" / "libhello-lib.a"
            self.assertEqual(extracted.read_bytes(), b"library-v1\n")
            self.assertEqual(result["count"], 1)
            self.assertRegex(result["fingerprint"], r"^[0-9a-f]{64}$")
            index = json.loads((destination / "upstream-index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["dependencies"][0]["project"], "hello-lib")
            self.assertEqual(index["upstream_fingerprint"], result["fingerprint"])

    def test_upstream_digest_change_invalidates_fingerprint(self) -> None:
        fingerprints: list[str] = []
        for payload in (b"library-v1\n", b"library-v2\n"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                incoming = root / "incoming"
                incoming.mkdir()
                self._write_upstream(incoming, content=payload)
                result = resolve(
                    incoming,
                    ["hello-lib"],
                    soc="generic",
                    target_os="linux",
                    arch="x86_64",
                    destination=root / "consumer" / ".ci-upstream",
                    expected_source_sha="a" * 40,
                    expected_repository="example/repo",
                )
                fingerprints.append(result["fingerprint"])
        self.assertNotEqual(fingerprints[0], fingerprints[1])

    def test_resolver_rejects_wrong_source_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "incoming"
            incoming.mkdir()
            self._write_upstream(incoming)
            with self.assertRaisesRegex(ValueError, "source_sha mismatch"):
                resolve(
                    incoming,
                    ["hello-lib"],
                    soc="generic",
                    target_os="linux",
                    arch="x86_64",
                    destination=root / "consumer" / ".ci-upstream",
                    expected_source_sha="b" * 40,
                    expected_repository="example/repo",
                )

    def test_real_app_requires_upstream_artifact_contract(self) -> None:
        cmake = (ROOT / "examples" / "cpp-app" / "CMakeLists.txt").read_text(encoding="utf-8")
        source = (ROOT / "examples" / "cpp-app" / "src" / "main.cpp").read_text(encoding="utf-8")
        catalog = json.loads((ROOT / "ci" / "projects.json").read_text(encoding="utf-8"))
        app = next(project for project in catalog["projects"] if project["name"] == "hello-cpp")
        self.assertEqual(app["depends_on"], ["hello-lib"])
        self.assertIn("CI_UPSTREAM_ROOT", cmake)
        self.assertIn("libhello-lib.a", cmake)
        self.assertIn('#include "hello_lib.h"', source)

    def test_central_workflow_has_real_level_barriers_and_handoff(self) -> None:
        central = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        node = (ROOT / ".github" / "workflows" / "dag-node.yml").read_text(encoding="utf-8")
        self.assertIn("name: Dependency DAG barrier", central)
        self.assertIn("level_0:", central)
        self.assertIn("level_1:", central)
        self.assertIn("needs: [discover, level_0]", central)
        self.assertIn("pattern: dag-*", node)
        self.assertIn("resolve_upstream_artifacts.py", node)
        self.assertIn("steps.upstream.outputs.fingerprint", node)
        self.assertIn("--upstream-index", node)
        self.assertIn("CI_UPSTREAM_ROOT", node)


if __name__ == "__main__":
    unittest.main()
