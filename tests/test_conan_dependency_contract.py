from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from cache_fingerprint import fingerprint_files  # noqa: E402
from dependency_plan import build_plan  # noqa: E402
from validate_config import load_catalog, validate_catalog  # noqa: E402


class ConanDependencyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(ROOT / "ci" / "projects.json")
        cls.toolchains = load_catalog(ROOT / "ci" / "toolchains.json")

    def test_conan_modules_are_two_parallel_libraries_then_consumer(self) -> None:
        selected = {"conan-format-lib", "conan-compression-lib", "conan-demo-app"}
        plan = build_plan(self.catalog, selected)
        self.assertEqual(plan["level_count"], 2)
        self.assertEqual(
            plan["levels"][:2],
            [["conan-compression-lib", "conan-format-lib"], ["conan-demo-app"]],
        )

    def test_every_declared_dependency_lock_is_a_cache_fingerprint_input(self) -> None:
        projects = json.loads(json.dumps(self.catalog))
        module = next(p for p in projects["projects"] if p["name"] == "conan-format-lib")
        target = module["targets"][0]
        target["cache_key_files"].remove("conan.lock")
        errors = validate_catalog(projects, self.toolchains)
        self.assertTrue(any("dependency_lock_files must also be listed in cache_key_files" in e for e in errors))

    def test_each_project_cache_fingerprint_pattern_matches_real_inputs(self) -> None:
        for name in ("conan-format-lib", "conan-compression-lib", "conan-demo-app"):
            project = next(p for p in self.catalog["projects"] if p["name"] == name)
            target = project["targets"][0]
            fingerprint = fingerprint_files(ROOT / project["path"], target["cache_key_files"])
            self.assertTrue(fingerprint, name)

    def test_missing_lockfile_fails_fingerprint_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "conanfile.txt").write_text("[requires]\nfmt/11.0.2\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "matched no files: conan.lock"):
                fingerprint_files(root, ["conanfile.txt", "conan.lock"])

    def test_conan_package_cache_and_ccache_are_separate_from_outputs(self) -> None:
        for name in ("conan-format-lib", "conan-compression-lib", "conan-demo-app"):
            project = next(p for p in self.catalog["projects"] if p["name"] == name)
            target = project["targets"][0]
            self.assertIn(".ci-cache/conan-home", target["cache_paths"])
            self.assertIn(".cache/ccache", target["cache_paths"])
            self.assertFalse(any(path.startswith("build/") for path in target["cache_paths"]))
            self.assertIn("conan.lock", target["dependency_lock_files"])
            self.assertIn("conan.lock", target["cache_key_files"])

    def test_conan_helpers_are_read_only_mounted_into_container_builds(self) -> None:
        runner = (ROOT / "scripts" / "ci" / "run_build.py").read_text(encoding="utf-8")
        self.assertIn('("CI_CONAN_BUILD_HELPER", "conan_cmake_build.sh")', runner)
        self.assertIn('("CI_CONAN_CTEST_HELPER", "conan_ctest.sh")', runner)
        self.assertIn("CI_CONAN_BUILD_HELPER=/ci-platform/conan_cmake_build.sh", runner)
        self.assertIn("CI_CONAN_CTEST_HELPER=/ci-platform/conan_ctest.sh", runner)
        self.assertIn('"cmake_build.sh", "conan_cmake_build.sh", "conan_ctest.sh"', runner)
        for name in ("conan-format-lib", "conan-compression-lib", "conan-demo-app"):
            project = next(p for p in self.catalog["projects"] if p["name"] == name)
            self.assertEqual(project["targets"][0]["build_command"], 'bash "$CI_CONAN_BUILD_HELPER"')
        app = next(p for p in self.catalog["projects"] if p["name"] == "conan-demo-app")
        self.assertEqual(app["targets"][0]["test_command"], 'bash "$CI_CONAN_CTEST_HELPER"')

    def test_conan_build_uses_pinned_version_lock_and_generated_toolchain(self) -> None:
        script = (ROOT / "scripts" / "ci" / "conan_cmake_build.sh").read_text(encoding="utf-8")
        self.assertIn('CONAN_VERSION="2.34.0"', script)
        self.assertIn('--lockfile="$LOCK_FILE"', script)
        self.assertIn("build/conan/conan_toolchain.cmake", script)
        self.assertIn("--build=missing", script)
        self.assertIn("ninja-build", script)
        self.assertIn("missing_packages+=(ccache)", script)
        self.assertIn("dependency_install_fetch_build_ms", script)
        self.assertIn("warm_cache_validation", script)
        self.assertIn('cmake --build build --target clean', script)
        self.assertIn("ccache_warm_hit_delta", script)


if __name__ == "__main__":
    unittest.main()
