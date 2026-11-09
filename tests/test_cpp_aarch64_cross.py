from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from cache_fingerprint import fingerprint_files  # noqa: E402
from toolchain_catalog import build_publish_matrix, load_toolchain_catalog  # noqa: E402
from toolchain_versions import load_versions, validate_versions  # noqa: E402


class Aarch64CrossToolchainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_toolchain_catalog(ROOT / "ci" / "toolchains.json")
        cls.versions = load_versions(ROOT / "ci" / "toolchain-versions.json")
        cls.fixture = ROOT / "examples" / "cpp-aarch64-cross"

    def test_candidate_is_separate_and_exactly_pinned(self) -> None:
        item = next(x for x in self.catalog["toolchains"] if x["id"] == "cpp-aarch64-gcc13-cross")
        self.assertEqual(item["status"], "candidate")
        self.assertEqual(item["target_triple"], "aarch64-linux-gnu")
        self.assertEqual(item["compiler_version"], "13.4.0")
        self.assertEqual(item["sdk_version"], "ubuntu-resolute-20261107-gcc13-cross")
        matrix = build_publish_matrix(self.catalog, ROOT, include_planned=True)
        row = next(x for x in matrix["include"] if x["toolchain"] == "cpp-aarch64-gcc13-cross")
        self.assertEqual(row["verification_fixture"], "examples/cpp-aarch64-cross")

    def test_version_registry_accepts_exact_cross_compiler_identity(self) -> None:
        self.assertEqual([], validate_versions(self.versions, self.catalog))
        item = next(x for x in self.versions["combinations"] if x["id"] == "cpp-aarch64-gcc13-cross")
        self.assertEqual("13.4.0-10ubuntu1cross1", item["build_tool_version"])
        self.assertEqual("candidate", item["status"])

    def test_host_and_build_profiles_are_not_mixed(self) -> None:
        host = (self.fixture / "profiles" / "host").read_text(encoding="utf-8")
        build = (self.fixture / "profiles" / "build").read_text(encoding="utf-8")
        self.assertIn("arch=armv8", host)
        self.assertIn("aarch64-linux-gnu-g++-13", host)
        self.assertIn("arch=x86_64", build)
        self.assertNotIn("aarch64-linux-gnu", build)

    def test_target_identity_files_are_required_build_inputs(self) -> None:
        names = ("conan.lock", "profiles/host", "profiles/build", "ci/aarch64-toolchain.cmake", "ci/target-sdk.lock")
        readme = (self.fixture / "README.md").read_text(encoding="utf-8")
        self.assertTrue(all(name in readme for name in names))
        script = (self.fixture / "ci" / "verify.sh").read_text(encoding="utf-8")
        for value in ("target_triple", "sysroot_digest", "sdk_digest", "APT_SNAPSHOT"):
            self.assertIn(value, script)

    def test_each_target_identity_input_changes_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = {
                "conan.lock": '{"requires":["fmt/11.0.2"]}\n',
                "profiles/host": "arch=armv8\ncompiler.cppstd=17\n",
                "profiles/build": "arch=x86_64\n",
                "ci/aarch64-toolchain.cmake": "set(CMAKE_SYSTEM_PROCESSOR aarch64)\n",
                "ci/target-sdk.lock": '{"sysroot":{"sha256":"a"},"sdk":{"sha256":"b"}}\n',
                "src/main.cpp": "int main() { return 0; }\n",
            }
            for name, content in inputs.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            patterns = list(inputs)
            baseline = fingerprint_files(root, patterns)
            for name in inputs:
                with self.subTest(input=name):
                    path = root / name
                    original = path.read_text(encoding="utf-8")
                    path.write_text(original + "# identity-change\n", encoding="utf-8")
                    self.assertNotEqual(baseline, fingerprint_files(root, patterns), name)
                    path.write_text(original, encoding="utf-8")

    def test_verify_script_fails_closed_and_checks_elf_and_qemu(self) -> None:
        script = (self.fixture / "ci" / "verify.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ "$mode" != "--bootstrap" ]]', script)
        self.assertIn("required ci/target-sdk.lock is missing", script)
        self.assertIn("required Conan lockfile is missing", script)
        self.assertIn("aarch64-linux-gnu-readelf -h", script)
        self.assertIn("qemu-aarch64 -L", script)
        self.assertIn('grep -F "AArch64"', script)
        self.assertIn("build-evidence.json", script)


if __name__ == "__main__":
    unittest.main()
