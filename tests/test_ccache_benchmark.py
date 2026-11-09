from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from ccache_benchmark import _copy_ignore, _inside  # noqa: E402


class CcacheBenchmarkTests(unittest.TestCase):
    def test_benchmark_source_file_must_exist_inside_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            source = root / "src" / "worker.cpp"
            source.write_text("int worker() { return 1; }\n", encoding="utf-8")

            self.assertEqual(_inside(root, Path("src/worker.cpp")), source)

            with self.assertRaisesRegex(ValueError, "does not exist"):
                _inside(root, Path("src/missing.cpp"))

    def test_benchmark_rejects_paths_outside_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / "outside.cpp"
            outside.write_text("int outside() { return 0; }\n", encoding="utf-8")
            try:
                with self.assertRaisesRegex(ValueError, "stay inside"):
                    _inside(root, Path("../outside.cpp"))
            finally:
                outside.unlink(missing_ok=True)

    def test_benchmark_copy_omits_build_and_cache_directories(self) -> None:
        ignored = _copy_ignore(
            "/workspace/project",
            [
                ".git",
                ".ci-cache",
                ".ci-telemetry",
                ".cache",
                "build",
                "src",
                "CMakeLists.txt",
                "cmake-build-debug",
            ],
        )
        self.assertEqual(
            ignored,
            {".git", ".ci-cache", ".ci-telemetry", ".cache", "build", "cmake-build-debug"},
        )


if __name__ == "__main__":
    unittest.main()
