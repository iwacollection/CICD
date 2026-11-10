from __future__ import annotations

import json
import os
import sys
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from ci_metrics import aggregate_metrics, attach_build_telemetry, finalize_metrics, finish_stage, start_stage  # noqa: E402
from discover_matrix import build_matrix  # noqa: E402
from dag_plan import _dag_cache_paths  # noqa: E402
from impact_analysis import analyze_impact  # noqa: E402
from package_artifact import build_artifact_name  # noqa: E402
from security_evidence import create_manifest, merge_security_evidence  # noqa: E402
from toolchain_catalog import load_toolchain_catalog, toolchain_identity  # noqa: E402
from validate_config import load_catalog  # noqa: E402
from resource_telemetry import calculate_resource_delta  # noqa: E402


class CiPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.projects = load_catalog(ROOT / "ci" / "projects.json")
        self.toolchains = load_toolchain_catalog(ROOT / "ci" / "toolchains.json")

    def test_workflow_only_change_uses_declared_smoke_roots(self) -> None:
        result = analyze_impact(self.projects, [".github/workflows/ci.yml"])
        self.assertEqual(result["lane"], "smoke")
        self.assertEqual(result["projects"], ["hello-lib"])
        self.assertEqual(result["direct_projects"], [])

    def test_project_change_still_rebuilds_dependents_and_prerequisites(self) -> None:
        result = analyze_impact(self.projects, ["examples/cpp-app/src/main.cpp"])
        self.assertEqual(result["lane"], "fast")
        self.assertEqual(
            result["projects"],
            ["hello-lib", "hello-cpp", "hello-service", "hello-release"],
        )

    def test_build_semantic_global_change_still_uses_full_lane(self) -> None:
        for path in ("ci/projects.json", "scripts/ci/run_build.py", "docker/toolchains/gcc-host/Dockerfile"):
            with self.subTest(path=path):
                result = analyze_impact(self.projects, [path])
                self.assertEqual(result["lane"], "full")
                self.assertEqual(
                    result["projects"],
                    ["hello-lib", "hello-cpp", "embedded-firmware-template", "hello-service", "hello-release", "conan-format-lib", "conan-compression-lib", "conan-demo-app"],
                )

    def test_smoke_lane_uses_fast_test_commands(self) -> None:
        matrix = build_matrix(
            self.projects,
            self.toolchains,
            project_names={"hello-lib"},
            lane="smoke",
        )
        self.assertEqual(len(matrix["include"]), 1)
        self.assertEqual(matrix["include"][0]["lane"], "smoke")
        self.assertEqual(
            matrix["include"][0]["test_command"],
            next(
                target["fast_test_command"]
                for project in self.projects["projects"]
                if project["name"] == "hello-lib"
                for target in project["targets"]
                if target.get("enabled", True)
            ),
        )

    def test_discovery_artifact_name_matches_artifact_contract_v2(self) -> None:
        source_sha = "a" * 40
        with patch.dict(os.environ, {"GITHUB_SHA": source_sha}):
            matrix = build_matrix(
                self.projects,
                self.toolchains,
                project_names={"hello-lib"},
                lane="full",
            )
        item = matrix["include"][0]
        expected = build_artifact_name(
            item["project"],
            item["soc"],
            item["target_os"],
            item["arch"],
            item["toolchain"],
            item["toolchain_identity"],
            source_sha,
        )
        self.assertEqual(item["artifact_name"], expected)
        self.assertEqual(item["build_parallelism"], "auto")
        self.assertEqual(item["cache_paths"], ".ci-cache/ccache")
        self.assertEqual(item["cache_scope"], "toolchain")
        self.assertTrue(item["build_command"].startswith("bash \"$CI_CMAKE_BUILD_HELPER\""))

    def test_stage_metrics_finalize_and_aggregate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working = root / "working.json"
            output = root / "build-job" / "ci-stage-metrics.json"
            start_stage(working, "compile")
            time.sleep(0.015)
            finish_stage(working, "compile")
            report = finalize_metrics(working, output)
            self.assertEqual(report["stages"]["compile"]["status"], "success")
            self.assertGreater(report["stages"]["compile"]["duration_ms"], 0)
            self.assertGreater(report["instrumented_duration_ms"], 0)

            aggregate = aggregate_metrics(root)
            self.assertEqual(aggregate["job_count"], 1)
            self.assertEqual(aggregate["stage_summary"]["compile"]["count"], 1)
            self.assertGreater(aggregate["stage_summary"]["compile"]["median_ms"], 0)



    def test_dag_plan_prefixes_cache_path_exactly_once(self) -> None:
        self.assertEqual(
            _dag_cache_paths("examples/cpp-lib/.cache/ccache"),
            "source/examples/cpp-lib/.cache/ccache",
        )

    def test_build_runner_aligns_ccache_and_cmake_parallelism(self) -> None:
        from run_build import _configure_build_environment, _container_env_args

        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            with patch.dict(os.environ, {"CI_BUILD_JOBS": "4"}, clear=False):
                jobs = _configure_build_environment(workdir, "4")
                args = _container_env_args(workdir)
                self.assertEqual(jobs, "4")
                self.assertEqual(os.environ["CI_BUILD_JOBS"], "4")
                self.assertEqual(os.environ["CMAKE_BUILD_PARALLEL_LEVEL"], "4")
                self.assertEqual(os.environ["CCACHE_DIR"], str(workdir / ".cache" / "ccache"))
                self.assertTrue((workdir / ".cache" / "ccache").is_dir())
                ccache_env_index = args.index("CCACHE_DIR=/workspace/.cache/ccache")
                self.assertGreater(ccache_env_index, 0)
                self.assertEqual(args[ccache_env_index - 1], "-e")
                self.assertIn("CMAKE_BUILD_PARALLEL_LEVEL=4", args)



    def test_build_matrix_uses_host_execution_for_packaging_only_nodes(self) -> None:
        matrix = build_matrix(
            self.projects,
            self.toolchains,
            project_names={"hello-service", "hello-release"},
            lane="full",
        )
        self.assertEqual(len(matrix["include"]), 2)
        for target in matrix["include"]:
            self.assertEqual(target["execution_mode"], "host")
            self.assertEqual(target["container_image"], "")
            self.assertEqual(target["cache_scope"], "none")
            self.assertEqual(target["cache_paths"], "")

    def test_toolchain_shared_cache_root_is_exact_and_scoped(self) -> None:
        self.assertEqual(
            _dag_cache_paths(".ci-cache/ccache"),
            "source/.ci-cache/ccache",
        )
        with self.assertRaisesRegex(ValueError, "unsafe DAG cache path"):
            _dag_cache_paths("../outside-cache")

    def test_resource_aware_parallelism_respects_cpu_and_memory_cgroups(self) -> None:
        from run_build import _detect_resource_limits, _resolve_build_jobs

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cpu.max").write_text("200000 100000\n", encoding="utf-8")
            (root / "cpuset.cpus.effective").write_text("0-7\n", encoding="utf-8")
            (root / "memory.max").write_text(str(8 * 1024 * 1024 * 1024), encoding="utf-8")
            limits = _detect_resource_limits(
                root,
                cpu_count=8,
                affinity_count=8,
                memory_per_job_mb=2048,
                memory_reserve_mb=1024,
            )

        self.assertEqual(limits["cpu_limit"], 2)
        self.assertEqual(limits["memory_job_limit"], 3)
        self.assertEqual(limits["effective_jobs"], 2)
        self.assertEqual(_resolve_build_jobs("auto", limits), "2")
        self.assertEqual(_resolve_build_jobs("4", limits), "4")

    def test_build_runner_uses_repo_shared_ccache_dir_and_per_build_stats_log(self) -> None:
        from run_build import _configure_build_environment, _container_env_args

        with tempfile.TemporaryDirectory() as directory:
            source_root = Path(directory) / "source"
            workdir = source_root / "examples" / "cpp-lib"
            workdir.mkdir(parents=True)
            with patch.dict(os.environ, {"CI_BUILD_JOBS": "auto", "CI_CCACHE_SCOPE": "toolchain"}, clear=False):
                jobs = _configure_build_environment(workdir, "auto")
                args = _container_env_args(workdir)
                self.assertTrue((source_root / ".ci-cache" / "ccache").is_dir())
                self.assertEqual(os.environ["CCACHE_DIR"], str(source_root / ".ci-cache" / "ccache"))
                self.assertTrue(os.environ["CCACHE_STATSLOG"].endswith(".ci-telemetry/ccache.stats.log"))
                self.assertIn("CCACHE_DIR=/ccache", args)
                self.assertIn("CCACHE_STATSLOG=/workspace/.ci-telemetry/ccache.stats.log", args)
                self.assertIn("CCACHE_MAXSIZE=2G", args)
                self.assertGreaterEqual(int(jobs), 1)

    def test_ccache_build_local_statistics_include_hits_misses_size_and_cleanups(self) -> None:
        from compile_telemetry import parse_ccache_stats

        build_stats = (
            "Cacheable calls: 10 / 10 (100.0%)\n"
            "  Hits: 8 / 10 (80.0%)\n"
            "    Direct: 6 / 8 (75.0%)\n"
            "    Preprocessed: 2 / 8 (25.0%)\n"
            "  Misses: 2 / 10 (20.0%)\n"
            "Uncacheable calls: 0 / 10 (0.0%)\n"
        )
        global_stats = (
            "Cache size (GB): 0.3 / 5.0 (6.0%)\n"
            "Files: 120\n"
            "Cleanups: 3\n"
        )
        result = parse_ccache_stats(build_stats, global_stats)
        self.assertEqual(result["hits"], 8)
        self.assertEqual(result["hits_direct"], 6)
        self.assertEqual(result["hits_preprocessed"], 2)
        self.assertEqual(result["misses"], 2)
        self.assertEqual(result["hit_rate_percent"], 80.0)
        self.assertEqual(result["cleanup_events"], 3)
        self.assertEqual(result["cleanup_events_scope"], "global_snapshot")
        self.assertEqual(result["files_in_cache"], 120)
        self.assertEqual(result["cache_size_gib"], 0.3)

    def test_resource_telemetry_calculates_cpu_memory_io_and_psi_deltas(self) -> None:
        before = {
            "cpu_stat": {"usage_usec": 1000, "throttled_usec": 200, "nr_throttled": 2},
            "memory_events": {"oom": 0, "oom_kill": 0},
            "io_stat": {"8:0": {"rbytes": 100, "wbytes": 200, "rios": 1, "wios": 2}},
            "pressure": {"io": {"some": {"total": 10000, "avg10": 0.1}}},
        }
        after = {
            "cpu_stat": {"usage_usec": 9000, "throttled_usec": 700, "nr_throttled": 5},
            "memory_events": {"oom": 0, "oom_kill": 1},
            "io_stat": {"8:0": {"rbytes": 1100, "wbytes": 2200, "rios": 11, "wios": 22}},
            "pressure": {"io": {"some": {"total": 15000, "avg10": 0.2}}},
            "memory_peak_bytes": 4096,
            "memory_current_bytes": 2048,
        }
        delta = calculate_resource_delta(before, after)
        self.assertEqual(delta["cpu"]["throttled_usec"], 500)
        self.assertEqual(delta["memory_events"]["oom_kill"], 1)
        self.assertEqual(delta["io"]["rbytes"], 1000)
        self.assertEqual(delta["io"]["wbytes"], 2000)
        self.assertEqual(delta["pressure_total_usec"]["io"]["some"], 5000)
        self.assertEqual(delta["memory_peak_bytes"], 4096)

    def test_cmake_bash_helper_has_valid_shell_syntax(self) -> None:
        helper = ROOT / "scripts" / "ci" / "cmake_build.sh"
        result = subprocess.run(
            ["bash", "-n", str(helper)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_ninja_introspection_reports_graph_commands_and_noop_incremental_state(self) -> None:
        from cmake_build import _ninja_introspection

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_dir = root / "build"
            bin_dir = root / "bin"
            build_dir.mkdir()
            bin_dir.mkdir()
            (build_dir / "build.ninja").write_text("# fake ninja\n", encoding="utf-8")
            (build_dir / ".ninja_log").write_text(
                "# ninja log v5\n1\t5\t0\tCMakeFiles/app.dir/main.cpp.o\tabc\n",
                encoding="utf-8",
            )
            fake_ninja = bin_dir / "ninja"
            fake_ninja.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "args=sys.argv[1:]\n"
                "if '-t' in args and 'graph' in args:\n"
                " print('0 [label=source];')\n"
                " print('1 [label=object];')\n"
                " print('0 -> 1;')\n"
                "elif '-t' in args and 'targets' in args:\n"
                " print('app: phony\\nmain.o: cxx')\n"
                "elif '-t' in args and 'commands' in args:\n"
                " print('c++ -c main.cpp -o main.o')\n"
                "elif '-n' in args:\n"
                " print('ninja: no work to do.')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )
            fake_ninja.chmod(0o755)
            with patch.dict(os.environ, {"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}):
                result = _ninja_introspection(build_dir, root, baseline_entries=0)

        self.assertTrue(result["available"])
        self.assertEqual(result["graph_edges"], 1)
        self.assertEqual(result["target_count"], 2)
        self.assertEqual(result["command_count"], 1)
        self.assertEqual(result["object_compile_edges"], 1)
        self.assertTrue(result["incremental_noop"])

    def test_build_telemetry_attaches_and_aggregates_cache_and_cmake_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working = root / "working.json"
            output = root / "job" / "ci-stage-metrics.json"
            telemetry_dir = root / ".ci-telemetry"
            telemetry_file = telemetry_dir / "build-telemetry-build.json"
            start_stage(working, "build")
            finish_stage(working, "build")
            telemetry_file.parent.mkdir(parents=True)
            telemetry_file.write_text(json.dumps({
                "schema_version": 1,
                "ccache": {
                    "available": True, "hits": 8, "misses": 2,
                    "hit_rate_percent": 80.0, "cleanup_events": 1,
                    "cache_size_gib": 0.3, "files_in_cache": 120,
                },
                "resources": {
                    "effective_jobs": 2,
                    "after": {"memory_peak_bytes": 4096},
                    "delta": {
                        "cpu": {"throttled_usec": 500},
                        "memory_events": {"oom_kill": 1},
                        "io": {"rbytes": 1000, "wbytes": 2000},
                        "pressure_total_usec": {"io": {"some": 2500}},
                    },
                },
                "cmake_ninja": {
                    "configure_generate_ms": 1200,
                    "compile_link_ms": 800,
                    "ninja": {"available": True, "graph_nodes": 12, "graph_edges": 8,
                              "target_count": 4, "executed_edges": 2,
                              "object_compile_edges": 1, "pending_actions_after_build": 0,
                              "incremental_noop": True},
                },
                "conan": {
                    "dependency_resolution_ms": 1000,
                    "dependency_package_list_ms": 200,
                    "dependency_download_ms": 5000,
                    "dependency_local_install_generate_ms": 7000,
                    "dependency_warm_cache_reuse_ms": 300,
                    "clean_configure_compile_total_ms": 1200,
                    "warm_clean_rebuild_total_ms": 1500,
                },
            }), encoding="utf-8")
            test_telemetry = telemetry_dir / "build-telemetry-test.json"
            test_telemetry.write_text(json.dumps({
                "schema_version": 1,
                "phase": "test",
                "ccache": {"available": True, "hits": 0, "misses": 0},
                "resources": {"effective_jobs": 2, "delta": {"cpu": {"throttled_usec": 10}}},
                "cmake_ninja": {},
                "conan": {"test_ms": 250},
            }), encoding="utf-8")
            self.assertTrue(attach_build_telemetry(working, telemetry_dir))
            finalize_metrics(working, output)
            aggregate = aggregate_metrics(root)
            summary = aggregate["build_telemetry_summary"]

        self.assertEqual(summary["ccache"]["hit_rate_percent"], 80.0)
        self.assertEqual(summary["compile_invocations"], 1)
        self.assertEqual(summary["build_telemetry_invocations"], 2)
        self.assertEqual(summary["ccache"]["cleanup_events_total"], 1)
        self.assertEqual(summary["cmake_phases"]["configure_generate_ms"]["median_ms"], 1200)
        self.assertEqual(summary["conan_phases"]["dependency_download_ms"]["median_ms"], 5000)
        self.assertEqual(summary["conan_phases"]["dependency_local_install_generate_ms"]["median_ms"], 7000)
        self.assertEqual(summary["conan_phases"]["test_ms"]["median_ms"], 250)
        self.assertEqual(summary["ninja"]["graph_nodes"]["median"], 12)
        self.assertEqual(summary["incremental_noop_successes"], 1)
        self.assertEqual(summary["resources"]["effective_jobs_median"], 2)
        self.assertEqual(summary["resources"]["cpu_throttled_usec_total"], 510)
        self.assertEqual(summary["resources"]["memory_peak_bytes_max"], 4096)
        self.assertEqual(summary["resources"]["io_read_bytes_total"], 1000)
        self.assertEqual(summary["resources"]["io_pressure_some_usec_total"], 2500)

    def test_build_telemetry_reads_conan_phase_timings(self) -> None:
        from compile_telemetry import collect_build_telemetry

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry_dir = root / ".ci-telemetry"
            telemetry_dir.mkdir()
            (telemetry_dir / "conan-build-timings.json").write_text(
                json.dumps({
                    "schema_version": 1,
                    "dependency_resolution_ms": 1234,
                    "dependency_download_ms": 5678,
                }),
                encoding="utf-8",
            )
            telemetry = collect_build_telemetry(
                root,
                build_exit_code=0,
                build_duration_ms=9000,
            )

        self.assertEqual(telemetry["conan"]["dependency_resolution_ms"], 1234)
        self.assertEqual(telemetry["conan"]["dependency_download_ms"], 5678)

    def test_parallel_trivy_generates_both_outputs_and_overlaps_stage_runtime(self) -> None:
        from parallel_trivy import run_parallel

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake_trivy = bin_dir / "trivy"
            fake_trivy.write_text(
                """#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
fmt = args[args.index('--format') + 1]
out = Path(args[args.index('--output') + 1])
events = Path(os.environ['FAKE_TRIVY_EVENTS'])
with events.open('a', encoding='utf-8') as handle: handle.write(f"start {fmt} {time.monotonic_ns()}\\n")
time.sleep(0.2)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({'format': fmt}), encoding='utf-8')
with events.open('a', encoding='utf-8') as handle: handle.write(f"end {fmt} {time.monotonic_ns()}\\n")
""",
                encoding="utf-8",
            )
            fake_trivy.chmod(0o755)
            metrics_file = root / "metrics.json"
            metrics_file.write_text(json.dumps({"schema_version": 1, "stages": {}}), encoding="utf-8")
            from ci_metrics import start_stage
            start_stage(metrics_file, "trivy_scan")
            events = root / "events.log"

            with patch.dict(
                os.environ,
                {
                    "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                    "FAKE_TRIVY_EVENTS": str(events),
                },
                clear=False,
            ):
                result = run_parallel(
                    scan_ref="source/example",
                    scan_output=root / "evidence" / "scan.json",
                    sbom_output=root / "evidence" / "sbom.json",
                    metrics_file=metrics_file,
                    scan_cache_dir=root / "cache" / "scan",
                    sbom_cache_dir=root / "cache" / "sbom",
                    trivy_stage_prestarted=True,
                )

            self.assertTrue(result["success"])
            self.assertEqual(json.loads((root / "evidence" / "scan.json").read_text())["format"], "json")
            self.assertEqual(json.loads((root / "evidence" / "sbom.json").read_text())["format"], "cyclonedx")
            metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
            self.assertEqual(metrics["stages"]["trivy_scan"]["status"], "success")
            self.assertEqual(metrics["stages"]["sbom_generation"]["status"], "success")
            self.assertGreater(metrics["stages"]["trivy_scan"]["duration_ms"], 0)
            self.assertGreater(metrics["stages"]["sbom_generation"]["duration_ms"], 0)

            events_by_type = {}
            for line in events.read_text(encoding="utf-8").splitlines():
                event, fmt, timestamp = line.split()
                events_by_type.setdefault(event, []).append(int(timestamp))
            self.assertEqual(len(events_by_type["start"]), 2)
            self.assertEqual(len(events_by_type["end"]), 2)
            self.assertLess(max(events_by_type["start"]), min(events_by_type["end"]))

    def test_parallel_trivy_failure_is_recorded_and_propagated(self) -> None:
        from parallel_trivy import run_parallel

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake_trivy = bin_dir / "trivy"
            fake_trivy.write_text(
                """#!/usr/bin/env python3
import json, sys
from pathlib import Path
args = sys.argv[1:]
fmt = args[args.index('--format') + 1]
out = Path(args[args.index('--output') + 1])
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({'format': fmt}), encoding='utf-8')
sys.exit(23 if fmt == 'json' else 0)
""",
                encoding="utf-8",
            )
            fake_trivy.chmod(0o755)
            metrics_file = root / "metrics.json"
            metrics_file.write_text(json.dumps({"schema_version": 1, "stages": {}}), encoding="utf-8")

            with patch.dict(
                os.environ,
                {"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")},
                clear=False,
            ):
                result = run_parallel(
                    scan_ref="source/example",
                    scan_output=root / "evidence" / "scan.json",
                    sbom_output=root / "evidence" / "sbom.json",
                    metrics_file=metrics_file,
                    scan_cache_dir=root / "cache" / "scan",
                    sbom_cache_dir=root / "cache" / "sbom",
                )

            self.assertFalse(result["success"])
            metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
            self.assertEqual(metrics["stages"]["trivy_scan"]["status"], "failure")
            self.assertEqual(metrics["stages"]["trivy_scan"]["exit_code"], 23)
            self.assertEqual(metrics["stages"]["sbom_generation"]["status"], "success")

    def test_run_end_to_end_metrics_separates_run_queue_and_job_queue(self) -> None:
        from end_to_end_metrics import build_end_to_end_report, format_markdown

        run = {
            "id": 12,
            "run_attempt": 1,
            "name": "Build Matrix",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-11-08T12:00:00Z",
            "run_started_at": "2026-11-08T12:00:05Z",
            "updated_at": "2026-11-08T12:01:05Z",
            "head_branch": "main",
            "head_sha": "abc",
            "event": "push",
            "html_url": "https://github.com/iwacollection/CICD/actions/runs/12",
            "repository": {"full_name": "iwacollection/CICD"},
        }
        jobs = [
            {
                "id": 1,
                "name": "Discover impacted dependency DAG",
                "created_at": "2026-11-08T12:00:05Z",
                "started_at": "2026-11-08T12:00:07Z",
                "completed_at": "2026-11-08T12:00:20Z",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 2,
                "name": "DAG L0 / hello-lib",
                "created_at": "2026-11-08T12:00:20Z",
                "started_at": "2026-11-08T12:00:22Z",
                "completed_at": "2026-11-08T12:01:00Z",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 3,
                "name": "Build gate",
                "created_at": "2026-11-08T12:01:00Z",
                "started_at": None,
                "completed_at": None,
                "status": "completed",
                "conclusion": "skipped",
            },
        ]
        performance = {
            "earliest_job_started_at": "2026-11-08T12:00:08Z",
            "latest_job_completed_at": "2026-11-08T12:00:59Z",
            "instrumented_workflow_span_ms": 51000,
            "stage_summary": {
                "toolchain_image_pull": {
                    "count": 2,
                    "median_ms": 1000,
                    "p95_ms": 1500,
                    "max_ms": 1500,
                }
            },
        }

        result = build_end_to_end_report(run, jobs, performance)
        self.assertEqual(result["run"]["run_queue_ms"], 5000)
        self.assertEqual(result["run"]["run_end_to_end_ms"], 65000)
        self.assertEqual(result["run"]["run_active_ms"], 60000)
        self.assertEqual(result["workflow_window"]["first_job_start_after_run_started_ms"], 2000)
        self.assertEqual(result["workflow_window"]["last_job_completion_to_run_updated_ms"], 5000)
        self.assertEqual(result["workflow_window"]["jobs_execution_window_ms"], 53000)
        self.assertEqual(result["workflow_window"]["first_job_start_to_first_metric_marker_ms"], 1000)
        self.assertEqual(result["workflow_window"]["last_metric_completion_to_last_job_completion_ms"], 1000)
        self.assertEqual(result["job_summary"]["job_count"], 3)
        self.assertEqual(result["job_summary"]["started_job_count"], 2)
        self.assertEqual(result["job_summary"]["completed_job_count"], 2)
        self.assertEqual(result["job_summary"]["queue_wait"]["median_ms"], 2000)
        self.assertEqual(result["job_summary"]["runtime"]["median_ms"], 25500)
        self.assertEqual(result["ci_stage_summary"]["toolchain_image_pull"]["count"], 2)
        self.assertIn("Run end-to-end (created to completed update)", format_markdown(result))

    def test_run_end_to_end_report_handles_missing_performance_report(self) -> None:
        from end_to_end_metrics import build_end_to_end_report

        run = {
            "id": 13,
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-11-08T12:00:00Z",
            "run_started_at": "2026-11-08T12:00:03Z",
            "updated_at": "2026-11-08T12:00:20Z",
        }
        jobs = [
            {
                "id": 1,
                "name": "Discover impacted dependency DAG",
                "created_at": "2026-11-08T12:00:03Z",
                "started_at": "2026-11-08T12:00:05Z",
                "completed_at": "2026-11-08T12:00:15Z",
                "status": "completed",
                "conclusion": "success",
            }
        ]

        result = build_end_to_end_report(run, jobs)
        self.assertFalse(result["source"]["performance_report_available"])
        self.assertEqual(result["run"]["run_end_to_end_ms"], 20000)
        self.assertEqual(result["job_summary"]["runtime"]["median_ms"], 10000)
        self.assertEqual(result["ci_stage_summary"], {})

    def test_end_to_end_api_fetch_uses_current_attempt_and_paginates_jobs(self) -> None:
        from end_to_end_metrics import fetch_workflow_run_data

        first_job = {"id": 1, "name": "first"}
        second_job = {"id": 2, "name": "second"}
        third_job = {"id": 3, "name": "third"}
        run = {"id": 42, "run_attempt": 3, "status": "completed"}
        with patch(
            "end_to_end_metrics._api_get_json",
            side_effect=[
                run,
                {"jobs": [first_job, second_job]},
                {"jobs": [third_job]},
            ],
        ) as api_get:
            fetched_run, jobs = fetch_workflow_run_data(
                "iwacollection/CICD",
                42,
                "test-token",
                api_url="https://api.example.test",
                per_page=2,
            )

        self.assertEqual(fetched_run, run)
        self.assertEqual(jobs, [first_job, second_job, third_job])
        requested_urls = [call.args[0] for call in api_get.call_args_list]
        self.assertEqual(
            requested_urls[1],
            "https://api.example.test/repos/iwacollection/CICD/actions/runs/42/attempts/3/jobs?per_page=2&page=1",
        )
        self.assertEqual(
            requested_urls[2],
            "https://api.example.test/repos/iwacollection/CICD/actions/runs/42/attempts/3/jobs?per_page=2&page=2",
        )


    def test_end_to_end_observability_runs_after_completed_main_build_matrix(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "ci-end-to-end.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("workflow_run:", workflow)
        self.assertIn('workflows: ["Build Matrix"]', workflow)
        self.assertIn("types: [completed]", workflow)
        self.assertIn("branches: [main]", workflow)
        self.assertIn("github.event.repository.default_branch", workflow)
        self.assertIn("actions/checkout@11d5960a326750d5838078e36cf38b85af677262", workflow)
        self.assertIn("actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093", workflow)
        self.assertIn("run-id: ${{ github.event.workflow_run.id }}", workflow)
        self.assertIn("github-token: ${{ secrets.GITHUB_TOKEN }}", workflow)
        self.assertIn("end_to_end_metrics.py", workflow)
        self.assertIn("retention-days: 90", workflow)
        self.assertNotIn("pull_request:", workflow)


    def test_performance_regression_monitor_is_lane_scoped_and_detects_p95_regressions(self) -> None:
        from performance_regression import evaluate_regressions

        def report(lane: str, build: list[float], scan: list[float]) -> dict:
            jobs = []
            for index, duration in enumerate(build):
                jobs.append({
                    "metadata": {"kind": "build", "lane": lane, "project": f"build-{index}"},
                    "stages": {"build": {"duration_ms": duration, "status": "success"}},
                })
            for index, duration in enumerate(scan):
                jobs.append({
                    "metadata": {"kind": "security", "lane": lane, "project": f"security-{index}"},
                    "stages": {"trivy_scan": {"duration_ms": duration, "status": "success"}},
                })
            return {"schema_version": 1, "jobs": jobs}

        history = [report("full", [100, 110], [100, 110]) for _ in range(5)]
        history.extend(report("smoke", [900, 950], [800, 850]) for _ in range(5))
        current = report("full", [160, 170], [150, 160])
        result = evaluate_regressions(
            current, history, ["success"] * 9, "success",
            minimum_baseline_samples=5, minimum_failure_samples=10,
        )
        self.assertEqual(result["lane"], "full")
        self.assertEqual(result["checks"]["build_stage_p95"]["baseline_samples"], 5)
        self.assertTrue(result["checks"]["build_stage_p95"]["breached"])
        self.assertTrue(result["checks"]["trivy_scan_p95"]["breached"])
        self.assertEqual(result["checks"]["run_failure_rate"]["failure_rate_percent"], 0)


    def test_performance_regression_monitor_handles_mixed_lanes_independently(self) -> None:
        from performance_regression import evaluate_regressions, format_markdown

        def report(lanes: dict[str, tuple[float, float]]) -> dict:
            jobs = []
            for lane, (build, scan) in lanes.items():
                jobs.extend([
                    {"metadata": {"kind": "build", "lane": lane}, "stages": {"build": {"duration_ms": build}}},
                    {"metadata": {"kind": "security", "lane": lane}, "stages": {"trivy_scan": {"duration_ms": scan}}},
                ])
            return {"jobs": jobs}

        history = [report({"full": (100, 100), "smoke": (1000, 1000)}) for _ in range(5)]
        current = report({"full": (160, 160), "smoke": (1000, 1000)})
        result = evaluate_regressions(
            current, history, ["success"] * 9, "success",
            minimum_baseline_samples=5, minimum_failure_samples=10,
        )
        self.assertEqual(result["lanes"], ["full", "smoke"])
        self.assertTrue(result["lane_checks"]["full"]["build_stage_p95"]["breached"])
        self.assertFalse(result["lane_checks"]["smoke"]["build_stage_p95"]["breached"])
        self.assertIn("Lane: full", format_markdown(result))
        self.assertIn("Lane: smoke", format_markdown(result))

    def test_performance_regression_failure_rate_includes_current_main_build(self) -> None:
        from performance_regression import evaluate_regressions

        report = {
            "jobs": [
                {"metadata": {"kind": "build", "lane": "fast"}, "stages": {"build": {"duration_ms": 100}}},
                {"metadata": {"kind": "security", "lane": "fast"}, "stages": {"trivy_scan": {"duration_ms": 100}}},
            ]
        }
        history = [report for _ in range(5)]
        result = evaluate_regressions(
            report, history,
            ["failure", "failure", "success", "success", "success", "success", "success", "success", "success"],
            "success",
            minimum_baseline_samples=5, minimum_failure_samples=10, history_limit=20,
        )
        failure = result["checks"]["run_failure_rate"]
        self.assertEqual(failure["sample_count"], 10)
        self.assertEqual(failure["failure_count"], 2)
        self.assertEqual(failure["failure_rate_percent"], 20)
        self.assertTrue(failure["breached"])

    def test_performance_regression_excludes_cancelled_and_non_failure_outcomes(self) -> None:
        from performance_regression import evaluate_regressions, format_markdown

        report = {
            "jobs": [
                {"metadata": {"kind": "build", "lane": "smoke"}, "stages": {"build": {"duration_ms": 100}}},
                {"metadata": {"kind": "security", "lane": "smoke"}, "stages": {"trivy_scan": {"duration_ms": 100}}},
            ]
        }
        history = ["success"] * 5 + ["failure"] + ["cancelled"] * 2 + ["timed_out"] + ["action_required"]
        result = evaluate_regressions(
            report, [report] * 5, history, "success",
            minimum_baseline_samples=5, minimum_failure_samples=7,
            failure_rate_percent=10,
        )

        metric = result["checks"]["run_failure_rate"]
        self.assertEqual(metric["sample_count"], 7)
        self.assertEqual(metric["success_count"], 6)
        self.assertEqual(metric["failure_count"], 1)
        self.assertEqual(metric["failure_rate_percent"], 14.29)
        self.assertEqual(metric["cancelled_count"], 2)
        self.assertEqual(metric["cancelled_rate_percent"], 18.18)
        self.assertEqual(metric["timed_out_count"], 1)
        self.assertEqual(metric["action_required_count"], 1)
        self.assertEqual(metric["excluded_outcome_count"], 4)
        self.assertEqual(metric["total_completed_runs"], 11)
        self.assertTrue(metric["breached"])
        markdown = format_markdown(result)
        self.assertIn("Run failure-conclusion rate", markdown)
        self.assertIn("cancelled=2", markdown)
        self.assertIn("does not reveal whether cancellation was caused", markdown)

    def test_performance_regression_cancellations_do_not_warm_up_failure_rate(self) -> None:
        from performance_regression import evaluate_regressions

        report = {"jobs": []}
        result = evaluate_regressions(
            report, [report] * 5, ["cancelled"] * 12, "cancelled",
            minimum_baseline_samples=5, minimum_failure_samples=10,
        )
        metric = result["checks"]["run_failure_rate"]
        self.assertEqual(metric["sample_count"], 0)
        self.assertEqual(metric["cancelled_count"], 13)
        self.assertFalse(metric["evaluated"])
        self.assertEqual(metric["status"], "warming_up")

    def test_performance_regression_warms_up_without_enough_history(self) -> None:
        from performance_regression import evaluate_regressions

        report = {
            "jobs": [
                {"metadata": {"kind": "build", "lane": "smoke"}, "stages": {"build": {"duration_ms": 100}}},
                {"metadata": {"kind": "security", "lane": "smoke"}, "stages": {"trivy_scan": {"duration_ms": 100}}},
            ]
        }
        result = evaluate_regressions(report, [report, report], ["success", "success"], "success")
        self.assertEqual(result["status"], "warming_up")
        self.assertFalse(result["checks"]["build_stage_p95"]["evaluated"])
        self.assertFalse(result["checks"]["run_failure_rate"]["evaluated"])

    def test_build_metadata_probes_tools_with_one_process(self) -> None:
        from types import SimpleNamespace

        from collect_build_metadata import collect_metadata

        completed = SimpleNamespace(
            returncode=0,
            stdout="cc\tGCC 14.2.0\ncmake\tcmake version 3.31.0\n",
        )
        with patch("collect_build_metadata.subprocess.run", return_value=completed) as run:
            metadata = collect_metadata(ROOT)

        self.assertEqual(run.call_count, 1)
        self.assertEqual(metadata["probe_invocations"], 1)
        self.assertEqual(metadata["tools"]["cc"], "GCC 14.2.0")
        self.assertEqual(metadata["tools"]["cmake"], "cmake version 3.31.0")

    def test_failed_and_interrupted_stages_are_reported_with_workflow_span(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working = root / "working.json"
            output = root / "job" / "ci-stage-metrics.json"
            with patch.dict(os.environ, {"CI_METRICS_KIND": "build", "CI_PROJECT": "fixture"}):
                start_stage(working, "compile_failure")
                finish_stage(working, "compile_failure", exit_code=17)
                start_stage(working, "cancelled_test")
                time.sleep(0.01)
                report = finalize_metrics(working, output)

            self.assertEqual(report["stages"]["compile_failure"]["status"], "failure")
            self.assertEqual(report["stages"]["compile_failure"]["exit_code"], 17)
            self.assertEqual(report["stages"]["cancelled_test"]["status"], "interrupted")

            aggregate = aggregate_metrics(root)
            self.assertGreater(aggregate["instrumented_workflow_span_ms"], 0)
            self.assertEqual(aggregate["job_kind_summary"]["build"]["count"], 1)
            self.assertEqual(aggregate["stage_summary"]["compile_failure"]["failure_count"], 1)
            self.assertEqual(aggregate["stage_summary"]["cancelled_test"]["interrupted_count"], 1)

    def test_metrics_report_labels_parallel_wall_span_and_failure_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working = root / "working.json"
            output = root / "job" / "ci-stage-metrics.json"
            start_stage(working, "cache_restore")
            finish_stage(working, "cache_restore")
            report = finalize_metrics(working, output)
            aggregate = aggregate_metrics(root)

            from ci_metrics import format_aggregate_summary

            summary = format_aggregate_summary(aggregate)
            self.assertIn("Instrumented workflow span", summary)
            self.assertIn("Failure", summary)
            self.assertIn("Interrupted", summary)

    def test_security_evidence_merges_only_for_matching_artifact_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_name = "hello-lib-generic-linux-x86_64-gcc-host-123456789abc-abcdef012345"
            build_dir = root / "incoming" / f"dag-{artifact_name}"
            evidence_root = root / "incoming"
            evidence_dir = evidence_root / f"security-{artifact_name}"
            build_dir.mkdir(parents=True)
            evidence_dir.mkdir(parents=True)

            build_manifest = {
                "schema_version": 2,
                "artifact_name": artifact_name,
                "project": "hello-lib",
                "source_sha": "a" * 40,
                "source_repository": "iwacollection/CICD",
                "workflow_run_id": "12345",
                "workflow_run_attempt": "2",
                "target": {"soc": "generic", "target_os": "linux", "arch": "x86_64"},
                "toolchain": {"id": "gcc-host", "identity": "sha256:" + "1" * 64},
            }
            (build_dir / f"{artifact_name}.manifest.json").write_text(
                json.dumps(build_manifest), encoding="utf-8"
            )
            create_manifest(
                evidence_dir / "security-evidence.json",
                artifact_name=artifact_name,
                project="hello-lib",
                soc="generic",
                target_os="linux",
                arch="x86_64",
                toolchain="gcc-host",
                toolchain_identity="sha256:" + "1" * 64,
                source_sha="a" * 40,
                source_repository="iwacollection/CICD",
                workflow_run_id="12345",
                workflow_run_attempt="2",
            )
            (evidence_dir / "security-scan.json").write_text('{"Results": []}\n', encoding="utf-8")
            (evidence_dir / "security-sbom.cdx.json").write_text(
                '{"bomFormat": "CycloneDX"}\n', encoding="utf-8"
            )

            merged = merge_security_evidence(
                build_dir,
                evidence_root,
                expected_source_sha="a" * 40,
                expected_repository="iwacollection/CICD",
                expected_run_id="12345",
            )
            self.assertEqual(merged["artifact_name"], artifact_name)
            self.assertTrue((build_dir / "security-scan.json").is_file())
            self.assertTrue((build_dir / "security-sbom.cdx.json").is_file())

            evidence_manifest = evidence_dir / "security-evidence.json"
            tampered = json.loads(evidence_manifest.read_text(encoding="utf-8"))
            tampered["target"]["arch"] = "arm64"
            evidence_manifest.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                merge_security_evidence(
                    build_dir,
                    evidence_root,
                    expected_source_sha="a" * 40,
                    expected_repository="iwacollection/CICD",
                    expected_run_id="12345",
                )

    def test_build_security_and_archive_workflows_remain_separated_and_gated(self) -> None:
        build = (ROOT / ".github" / "workflows" / "dag-node.yml").read_text(encoding="utf-8")
        security = (ROOT / ".github" / "workflows" / "dag-security.yml").read_text(encoding="utf-8")
        central = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        archive = (ROOT / ".github" / "workflows" / "archive-artifacts.yml").read_text(encoding="utf-8")

        self.assertNotIn("aquasecurity/trivy-action", build)
        self.assertNotIn("aquasecurity/trivy-action", security)
        self.assertIn("aquasecurity/setup-trivy@3fb12ec12f41e471780db15c232d5dd185dcb514", security)
        self.assertIn("actions/cache@27d5ce7f107fe9357f9df03efb73ab90386fccae", security)
        self.assertIn("Generate Trivy scan report and SBOM concurrently", security)
        self.assertIn("parallel_trivy.py", security)
        self.assertIn("Start Trivy scan timer", security)
        self.assertIn("--trivy-stage-prestarted", security)
        self.assertIn("--report security-evidence/security-scan.json", security)
        self.assertIn("--sbom security-evidence/security-sbom.cdx.json", security)
        self.assertIn("if: always()", security)
        self.assertIn("if: always()", security)
        self.assertIn("hardware_build", build)
        self.assertIn("hardware_test", build)
        self.assertEqual(build.count("CI_BUILD_JOBS: ${{ inputs.build_parallelism }}"), 4)
        self.assertIn("CI_BUILD_PHASE: build", build)
        self.assertIn("CI_BUILD_PHASE: test", build)
        self.assertIn('--telemetry-file "source/$CI_WORKING_DIRECTORY/.ci-telemetry"', build)
        self.assertIn("Finish artifact verify timer", build)
        self.assertIn("name: Detached security evidence barrier", central)
        self.assertIn('SECURITY_RESULT: ${{ needs.security_complete.result }}', central)
        self.assertIn("security_evidence.py merge", archive)
        self.assertIn("name: Aggregate CI stage timings", central)
        self.assertIn("name: Mainline CI performance regression monitor", central)
        self.assertIn("name: Attach compiler cache and CMake/Ninja telemetry", build)
        self.assertIn("CI_CCACHE_SCOPE: ${{ inputs.cache_scope }}", build)
        self.assertEqual(central.split("max-parallel: 4").__len__(), 9)
        security_calls = central.split("uses: ./.github/workflows/dag-security.yml")
        self.assertEqual(len(security_calls) - 1, 8)
        for block in security_calls[1:]:
            self.assertIn("lane: ${{ matrix.lane }}", block.split("\n\n", 1)[0])
        self.assertIn("CI_LANE: ${{ inputs.lane }}", build)
        self.assertIn("CI_LANE: ${{ inputs.lane }}", security)

    def test_external_github_actions_remain_pinned(self) -> None:
        for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for line in workflow.read_text(encoding="utf-8").splitlines():
                if "uses:" not in line or "./" in line:
                    continue
                reference = line.split("uses:", 1)[1].strip().split()[0].rsplit("@", 1)[-1]
                self.assertRegex(reference, r"^[0-9a-f]{40}$", msg=f"{workflow}: {line}")


if __name__ == "__main__":
    unittest.main()
