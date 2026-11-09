from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "cpp-conan-consumer"
sys.path.insert(0, str(FIXTURE / "ci"))

from dependency_contract import fingerprint, validate_contract  # noqa: E402


class CppDependencyContractTests(unittest.TestCase):
    def test_contract_pins_conan_and_declares_lock_inputs(self) -> None:
        contract = json.loads((FIXTURE / "ci" / "dependencies.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["package_manager_version"], "2.34.0")
        self.assertIn("fmt/11.0.2", contract["direct_dependencies"])
        self.assertIn("conan.lock", contract["fingerprint_files"])
        self.assertIn("profiles/gcc13-linux-x86_64", contract["fingerprint_files"])

    def test_missing_lockfile_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(FIXTURE / "ci", root / "ci")
            shutil.copytree(FIXTURE / "libs", root / "libs")
            shutil.copytree(FIXTURE / "profiles", root / "profiles")
            shutil.copy2(FIXTURE / "conanfile.py", root / "conanfile.py")
            shutil.copy2(FIXTURE / "CMakeLists.txt", root / "CMakeLists.txt")
            errors = validate_contract(root, require_lock=True)
            self.assertTrue(any("conan.lock is required" in error for error in errors))

    def test_lockfile_content_changes_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(FIXTURE / "ci", root / "ci")
            shutil.copytree(FIXTURE / "libs", root / "libs")
            shutil.copytree(FIXTURE / "profiles", root / "profiles")
            shutil.copy2(FIXTURE / "conanfile.py", root / "conanfile.py")
            shutil.copy2(FIXTURE / "CMakeLists.txt", root / "CMakeLists.txt")
            (root / "conan.lock").write_text('{"version":"0.5","requires":["fmt/11.0.2#abc"]}\n', encoding="utf-8")
            before = fingerprint(root)
            (root / "conan.lock").write_text('{"version":"0.5","requires":["fmt/11.0.2#def"]}\n', encoding="utf-8")
            after = fingerprint(root)
            self.assertNotEqual(before, after)

    def test_lockfile_must_contain_locked_fmt_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(FIXTURE / "ci", root / "ci")
            shutil.copytree(FIXTURE / "libs", root / "libs")
            shutil.copytree(FIXTURE / "profiles", root / "profiles")
            shutil.copy2(FIXTURE / "conanfile.py", root / "conanfile.py")
            shutil.copy2(FIXTURE / "CMakeLists.txt", root / "CMakeLists.txt")
            (root / "conan.lock").write_text('{"version":"0.5","requires":["fmt/12.0.0#abc"]}\n', encoding="utf-8")
            errors = validate_contract(root, require_lock=True)
            self.assertTrue(any("does not lock fmt/11.0.2" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
