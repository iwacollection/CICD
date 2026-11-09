from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from toolchain_catalog import load_toolchain_catalog, build_publish_matrix  # noqa: E402
from toolchain_versions import load_versions, validate_versions  # noqa: E402


class ConanToolchainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_toolchain_catalog(ROOT / "ci" / "toolchains.json")
        cls.versions = load_versions(ROOT / "ci" / "toolchain-versions.json")
        cls.fixture = ROOT / "examples" / "cpp-conan-multimodule"

    def test_candidate_is_not_active_and_has_exact_conan_version(self) -> None:
        item = next(x for x in self.catalog["toolchains"] if x["id"] == "cpp-gcc13-conan234")
        self.assertEqual(item["status"], "candidate")
        self.assertEqual(item["conan_version"], "2.34.0")
        self.assertIn("sha256", (ROOT / item["dockerfile"]).read_text(encoding="utf-8"))
        matrix = build_publish_matrix(self.catalog, ROOT, include_planned=True)
        candidate = next(x for x in matrix["include"] if x["toolchain"] == "cpp-gcc13-conan234")
        self.assertEqual(candidate["verification_fixture"], "examples/cpp-conan-multimodule")

    def test_version_registry_binds_candidate_to_exact_tool(self) -> None:
        self.assertEqual([], validate_versions(self.versions, self.catalog))
        item = next(x for x in self.versions["combinations"] if x["id"] == "cpp-gcc13-conan234")
        self.assertEqual(("cpp", "13", "conan", "2.34.0", "candidate"),
                         (item["language"], item["language_version"], item["build_tool"],
                          item["build_tool_version"], item["status"]))

    def test_fixture_contains_two_ordered_internal_libraries_and_fmt(self) -> None:
        root = (self.fixture / "CMakeLists.txt").read_text(encoding="utf-8")
        core = (self.fixture / "libs" / "core" / "CMakeLists.txt").read_text(encoding="utf-8")
        greeting = (self.fixture / "libs" / "greeting" / "CMakeLists.txt").read_text(encoding="utf-8")
        recipe = (self.fixture / "conanfile.py").read_text(encoding="utf-8")
        self.assertIn("add_subdirectory(libs/core)", root)
        self.assertIn("add_subdirectory(libs/greeting)", root)
        self.assertIn("project::core", greeting)
        self.assertIn("fmt::fmt", greeting)
        self.assertIn('requires = "fmt/11.0.2"', recipe)
        self.assertIn("add_library(core STATIC", core)

    def test_strict_script_rejects_missing_lockfile_without_bootstrap(self) -> None:
        script = (self.fixture / "ci" / "verify.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ "$mode" != "--bootstrap" ]]', script)
        self.assertIn("required committed conan.lock is missing", script)
        self.assertIn('--lockfile="$lockfile"', script)

    def test_profile_pins_compiler_abi_and_build_type(self) -> None:
        profile = (self.fixture / "ci" / "profile").read_text(encoding="utf-8")
        for value in ("compiler=gcc", "compiler.version=13", "compiler.libcxx=libstdc++11",
                      "compiler.cppstd=17", "build_type=Release"):
            self.assertIn(value, profile)


if __name__ == "__main__":
    unittest.main()
