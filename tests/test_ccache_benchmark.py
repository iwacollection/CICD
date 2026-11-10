from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from ccache_benchmark import (  # noqa: E402
    DEFAULT_CHANGED_CXX_FLAGS,
    REQUIRED_TOOLS,
    _cmake_cache_value,
    _copy_ignore,
    _effective_changed_cxx_flags,
    _inside,
    _ninja_phase_metrics,
)


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

    def test_selected_compiler_is_discovered_by_cmake_not_a_hard_coded_cxx_binary(self) -> None:
        self.assertEqual(REQUIRED_TOOLS, ("cmake", "ninja", "ccache"))
        self.assertNotIn("c++", REQUIRED_TOOLS)

    def test_changed_flag_affects_generated_code_and_can_be_overridden(self) -> None:
        self.assertEqual(DEFAULT_CHANGED_CXX_FLAGS, "-fno-omit-frame-pointer")
        self.assertNotIn("CCACHE_BENCHMARK_COMPILER_VARIANT", DEFAULT_CHANGED_CXX_FLAGS)
        self.assertEqual(_effective_changed_cxx_flags("-fno-omit-frame-pointer -fno-inline"), "-fno-omit-frame-pointer -fno-inline")
        self.assertEqual(_effective_changed_cxx_flags(""), DEFAULT_CHANGED_CXX_FLAGS)

    def test_cmake_cache_compiler_identity_is_read_from_configured_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            (build / "CMakeCache.txt").write_text(
                "CMAKE_CXX_COMPILER:FILEPATH=/usr/bin/clang++\\n"
                "CMAKE_CXX_COMPILER_ID:STRING=Clang\\n"
                "CMAKE_CXX_COMPILER_VERSION:STRING=19.1.0\\n",
                encoding="utf-8",
            )
            self.assertEqual(_cmake_cache_value(build, "CMAKE_CXX_COMPILER"), "/usr/bin/clang++")
            self.assertEqual(_cmake_cache_value(build, "CMAKE_CXX_COMPILER_ID"), "Clang")
            self.assertEqual(_cmake_cache_value(build, "CMAKE_CXX_COMPILER_VERSION"), "19.1.0")

    def test_ninja_log_edges_are_separated_into_compile_and_link_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            (build / "build.ninja").write_text(
                "build CMakeFiles/app.dir/main.cpp.o: CXX_COMPILER__app_Release\\n"
                "build app: CXX_EXECUTABLE_LINKER__app_Release\\n",
                encoding="utf-8",
            )
            before = set()
            after = {
                (10, 35, "CMakeFiles/app.dir/main.cpp.o", "abc"),
                (36, 56, "app", "def"),
            }
            metrics = _ninja_phase_metrics(before, after, build)
            self.assertEqual(metrics["total_edges"], 2)
            self.assertEqual(metrics["compile_edges"], 1)
            self.assertEqual(metrics["link_edges"], 1)
            self.assertEqual(metrics["compile_edge_work_ms"], 25)
            self.assertEqual(metrics["link_edge_work_ms"], 20)
            self.assertEqual(metrics["compile_window_ms"], 25)
            self.assertEqual(metrics["link_window_ms"], 20)

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
