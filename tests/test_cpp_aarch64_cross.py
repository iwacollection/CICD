from __future__ import annotations
import json

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
        self.assertEqual(item["compiler_version"], "13.5.0")
        self.assertEqual(item["sdk_version"], "debian-forky-20261108-gcc13-cross")
        matrix = build_publish_matrix(self.catalog, ROOT, include_planned=True)
        row = next(x for x in matrix["include"] if x["toolchain"] == "cpp-aarch64-gcc13-cross")
        self.assertEqual(row["verification_fixture"], "examples/cpp-aarch64-cross")

    def test_version_registry_accepts_exact_cross_compiler_identity(self) -> None:
        self.assertEqual([], validate_versions(self.versions, self.catalog))
        item = next(x for x in self.versions["combinations"] if x["id"] == "cpp-aarch64-gcc13-cross")
        self.assertEqual("13.5.0-1cross1", item["build_tool_version"])
        self.assertEqual("candidate", item["status"])

    def test_host_and_build_profiles_are_not_mixed(self) -> None:
        host = (self.fixture / "profiles" / "host").read_text(encoding="utf-8")
        build = (self.fixture / "profiles" / "build").read_text(encoding="utf-8")
        self.assertIn("arch=armv8", host)
        self.assertIn("aarch64-linux-gnu-g++-13", host)
        self.assertIn("arch=x86_64", build)
        self.assertNotIn("aarch64-linux-gnu", build)
        self.assertIn("compiler.version=13", build)
        self.assertIn("/usr/bin/gcc-13", build)
        self.assertIn("/usr/bin/g++-13", build)

    def test_target_identity_files_are_required_build_inputs(self) -> None:
        names = ("conan.lock", "profiles/host", "profiles/build", "ci/aarch64-toolchain.cmake", "ci/target-sdk.lock")
        readme = (self.fixture / "README.md").read_text(encoding="utf-8")
        self.assertTrue(all(name in readme for name in names))
        script = (self.fixture / "ci" / "verify.sh").read_text(encoding="utf-8")
        for value in ("target_triple", "sysroot_digest", "sdk_digest", "APT_SNAPSHOT", "host_compiler_version", "host_gcc_package", "host_gxx_package"):
            self.assertIn(value, script)

    def test_each_declared_cache_input_changes_the_real_manifest_fingerprint(self) -> None:
        manifest_path = self.fixture / "ci" / "cache-key-files.json"
        patterns = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertTrue({
            "conan.lock", "profiles/host", "profiles/build",
            "ci/aarch64-toolchain.cmake", "ci/target-sdk.lock",
            "conanfile.txt", "CMakeLists.txt", "ci/cache-key-files.json",
        }.issubset(set(patterns)))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = {
                "conanfile.txt": "[requires]\nfmt/11.0.2\n",
                "conan.lock": '{"requires":["fmt/11.0.2"]}\n',
                "profiles/host": "arch=armv8\ncompiler.cppstd=17\n",
                "profiles/build": "arch=x86_64\n",
                "ci/aarch64-toolchain.cmake": "set(CMAKE_SYSTEM_PROCESSOR aarch64)\n",
                "ci/target-sdk.lock": '{"sysroot":{"sha256":"a"},"sdk":{"sha256":"b"}}\n',
                "ci/cache-key-files.json": manifest_path.read_text(encoding="utf-8"),
                "CMakeLists.txt": "project(cache_identity LANGUAGES CXX)\n",
                "include/core.h": "#pragma once\n",
                "src/main.cpp": "int main() { return 0; }\n",
            }
            for name, content in inputs.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            baseline = fingerprint_files(root, patterns)
            for name in inputs:
                with self.subTest(input=name):
                    path = root / name
                    original = path.read_text(encoding="utf-8")
                    path.write_text(original + "# identity-change\n", encoding="utf-8")
                    self.assertNotEqual(baseline, fingerprint_files(root, patterns), name)
                    path.write_text(original, encoding="utf-8")

    def test_cache_directories_are_scoped_to_target_sdk_and_abi(self) -> None:
        script = (self.fixture / "ci" / "verify.sh").read_text(encoding="utf-8")
        self.assertIn('readonly CACHE_NAMESPACE=', script)
        self.assertIn('export CCACHE_DIR="$PWD/.ci-cache/ccache/$CACHE_NAMESPACE"', script)
        self.assertIn('export CONAN_HOME="$PWD/.ci-cache/conan/$CACHE_NAMESPACE"', script)
        self.assertIn('TARGET_CXX_ABI="libstdc++11"', script)
        self.assertIn('TARGET_CXX_STANDARD="17"', script)
        self.assertIn("ci/cache-key-files.json", script)
        self.assertIn("CMAKE_CXX_COMPILER_LAUNCHER=ccache", script)
        self.assertIn('"build_host_compiler": {', script)


    def test_verify_script_fails_closed_and_checks_elf_and_qemu(self) -> None:
        script = (self.fixture / "ci" / "verify.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ "$mode" != "--bootstrap" ]]', script)
        self.assertIn("required ci/target-sdk.lock is missing", script)
        self.assertIn("import json", script)
        self.assertIn('"build_host_compiler": {', script)
        self.assertIn("required Conan lockfile is missing", script)
        self.assertIn("aarch64-linux-gnu-readelf -h", script)
        self.assertIn("aarch64-linux-gnu-readelf --version-info", script)
        self.assertIn("aarch64-linux-gnu-readelf -A", script)
        self.assertIn("abi-compatibility.json", script)
        self.assertIn("Version (definition|needs) section", script)
        self.assertIn("RPATH|RUNPATH", script)
        self.assertIn("CMAKE_SKIP_RPATH=ON", script)
        self.assertIn("qemu-aarch64 -L", script)
        self.assertIn('grep -F "AArch64"', script)
        self.assertIn("build-evidence.json", script)
        self.assertIn("linux-libc-dev-arm64-cross", script)
        self.assertIn("debian-forky-20261108-gcc13-cross", script)
        self.assertIn("https://center2.conan.io", script)
        self.assertIn('test "$qemu_package" = "1:11.1.3+ds-1"', script)
        self.assertIn('grep -F "11.1.3" <<< "$qemu_version"', script)


if __name__ == "__main__":
    unittest.main()
