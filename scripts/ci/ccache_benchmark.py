#!/usr/bin/env python3
"""Benchmark CMake/Ninja incremental builds and ccache in an isolated source copy.

This is an experiment harness, not a CI gate. It records raw measurements and
never edits the caller's source tree.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from compile_telemetry import parse_ccache_stats


REQUIRED_TOOLS = ("cmake", "ninja", "ccache")
DEFAULT_CHANGED_CXX_FLAGS = "-fno-omit-frame-pointer"


IGNORE_DIRS = {
    ".git", ".ci-cache", ".ci-telemetry", ".cache", "build", "dist",
    "out", "node_modules", "__pycache__",
}


def _inside(root: Path, candidate: Path) -> Path:
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"path must stay inside source directory: {candidate}") from exc
    if not resolved.is_file():
        raise ValueError(f"benchmark input file does not exist: {candidate}")
    return resolved


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in IGNORE_DIRS or name.startswith("cmake-build-")}


def _effective_changed_cxx_flags(value: str | None) -> str:
    return (value or DEFAULT_CHANGED_CXX_FLAGS).strip() or DEFAULT_CHANGED_CXX_FLAGS


def _cmake_cache_value(build_dir: Path, key: str) -> str | None:
    cache = build_dir / "CMakeCache.txt"
    if not cache.is_file():
        return None
    prefix = f"{key}:"
    for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(prefix) and "=" in line:
            return line.split("=", 1)[1].strip()
    return None


def _compiler_identity(build_dir: Path) -> dict[str, Any]:
    compiler = _cmake_cache_value(build_dir, "CMAKE_CXX_COMPILER")
    identity = {
        "path": compiler,
        "id": _cmake_cache_value(build_dir, "CMAKE_CXX_COMPILER_ID"),
        "version": _cmake_cache_value(build_dir, "CMAKE_CXX_COMPILER_VERSION"),
    }
    if compiler:
        version = subprocess.run(
            [compiler, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        identity["version_line"] = version.stdout.splitlines()[0] if version.stdout.splitlines() else None
    return identity


def _ninja_rule_map(build_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    build_file = build_dir / "build.ninja"
    if not build_file.is_file():
        return mapping
    for line in build_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("build "):
            continue
        left, separator, right = line.partition(":")
        if not separator:
            continue
        rule = right.strip().split(maxsplit=1)[0] if right.strip() else ""
        outputs = left[6:].split("|", 1)[0].split()
        for output in outputs:
            mapping[output] = rule
    return mapping


def _ninja_log_snapshot(build_dir: Path) -> set[tuple[int, int, str, str]]:
    path = build_dir / ".ninja_log"
    if not path.is_file():
        return set()
    entries: set[tuple[int, int, str, str]] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\\t")
        if len(fields) < 5:
            continue
        try:
            entries.add((int(fields[0]), int(fields[1]), fields[3], fields[4]))
        except ValueError:
            continue
    return entries


def _ninja_phase_metrics(
    before: set[tuple[int, int, str, str]],
    after: set[tuple[int, int, str, str]],
    build_dir: Path,
) -> dict[str, Any]:
    rules = _ninja_rule_map(build_dir)
    grouped: dict[str, list[tuple[int, int]]] = {"compile": [], "link": [], "other": []}
    for start_ms, end_ms, output, _command_hash in after - before:
        rule = rules.get(output, "").upper()
        suffix = Path(output).suffix.lower()
        if suffix in {".o", ".obj", ".pch", ".gch", ".pcm"} or "COMPILER" in rule:
            kind = "compile"
        elif "LINKER" in rule or suffix in {".a", ".so", ".dylib", ".dll", ".exe"}:
            kind = "link"
        else:
            kind = "other"
        grouped[kind].append((start_ms, end_ms))

    result: dict[str, Any] = {"total_edges": sum(len(items) for items in grouped.values())}
    for kind, items in grouped.items():
        result[f"{kind}_edges"] = len(items)
        result[f"{kind}_edge_work_ms"] = sum(max(0, end - start) for start, end in items)
        result[f"{kind}_window_ms"] = (max(end for _, end in items) - min(start for start, _ in items)) if items else 0
    result["window_semantics"] = "Per-kind edge windows may overlap; edge-work sums include parallel work and must not be added to wall-clock build time."
    return result


def _ctest_phase(build_dir: Path, env: dict[str, str], parallelism: int) -> dict[str, Any]:
    if shutil.which("ctest") is None:
        return {"name": "test", "exit_code": 0, "status": "skipped", "reason": "ctest is unavailable", "duration_ms": 0.0}
    listing = subprocess.run(
        ["ctest", "--test-dir", str(build_dir), "-N"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if listing.returncode != 0:
        return {"name": "test", "exit_code": listing.returncode, "status": "failed", "reason": "ctest -N failed", "output_tail": listing.stdout[-12000:]}
    match = re.search(r"Total Tests:\\s*(\\d+)", listing.stdout)
    count = int(match.group(1)) if match else 0
    if count == 0:
        return {"name": "test", "exit_code": 0, "status": "skipped", "test_count": 0, "reason": "no registered CTest tests", "duration_ms": 0.0}
    code, elapsed, output = _run(
        ["ctest", "--test-dir", str(build_dir), "--output-on-failure", "--parallel", str(parallelism)],
        cwd=build_dir,
        env=env,
    )
    return {
        "name": "test",
        "exit_code": code,
        "status": "success" if code == 0 else "failed",
        "test_count": count,
        "parallelism": parallelism,
        "duration_ms": elapsed,
        "output_tail": output,
    }


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> tuple[int, float, str]:
    started = time.monotonic()
    proc = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
    return proc.returncode, elapsed_ms, proc.stdout[-12000:]


def _ninja_log_count(build_dir: Path) -> int:
    path = build_dir / ".ninja_log"
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line and not line.startswith("#"))


def _pending_ninja_actions(build_dir: Path, env: dict[str, str]) -> int | None:
    proc = subprocess.run(
        ["ninja", "-C", str(build_dir), "-n"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return sum(1 for line in proc.stdout.splitlines() if re.match(r"^\\s*\\[\\d+/\\d+\\]", line))


def _cache_stats(cache_dir: Path, stats_log: Path, env: dict[str, str]) -> dict[str, Any]:
    phase_env = env.copy()
    phase_env["CCACHE_DIR"] = str(cache_dir)
    phase_env["CCACHE_STATSLOG"] = str(stats_log)
    log = subprocess.run(
        ["ccache", "--show-log-stats"],
        env=phase_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    global_stats = subprocess.run(
        ["ccache", "--show-stats", "--verbose"],
        env=phase_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return parse_ccache_stats(log.stdout if log.returncode == 0 else "", global_stats.stdout if global_stats.returncode == 0 else "")


def _configure(
    source_dir: Path,
    build_dir: Path,
    env: dict[str, str],
    *,
    compiler_flags: str = "",
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    command = [
        "cmake", "-S", str(source_dir), "-B", str(build_dir), "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_C_COMPILER_LAUNCHER=ccache",
        "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache",
    ]
    if compiler_flags:
        command.append(f"-DCMAKE_CXX_FLAGS={compiler_flags}")
    command.extend(extra_args or [])
    code, elapsed, output = _run(command, cwd=source_dir, env=env)
    result = {"exit_code": code, "duration_ms": elapsed, "output_tail": output}
    if code != 0:
        raise RuntimeError(f"CMake configure failed for {source_dir}:\n{output}")
    return result


def _build_phase(
    name: str,
    source_dir: Path,
    build_dir: Path,
    cache_dir: Path,
    base_env: dict[str, str],
    *,
    disable_cache: bool = False,
) -> dict[str, Any]:
    stats_log = build_dir.parent / f"{name}.ccache-stats.log"
    env = base_env.copy()
    env["CCACHE_DIR"] = str(cache_dir)
    env["CCACHE_STATSLOG"] = str(stats_log)
    env.pop("CCACHE_DISABLE", None)
    if disable_cache:
        env["CCACHE_DISABLE"] = "1"
    before = _ninja_log_snapshot(build_dir)
    code, elapsed, output = _run(
        ["cmake", "--build", str(build_dir), "--parallel", env["CI_BUILD_JOBS"]],
        cwd=source_dir,
        env=env,
    )
    after = _ninja_log_snapshot(build_dir)
    ninja_metrics = _ninja_phase_metrics(before, after, build_dir)
    stats = _cache_stats(cache_dir, stats_log, env) if not disable_cache else None
    return {
        "name": name,
        "exit_code": code,
        "build_duration_ms": elapsed,
        "ninja_log_entries_added": ninja_metrics["total_edges"],
        "ninja": ninja_metrics,
        "pending_ninja_actions_after": _pending_ninja_actions(build_dir, env),
        "ccache": stats,
        "output_tail": output,
    }


def _append_marker(path: Path, marker: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n// {marker}\n")


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    source_dir = Path(args.source_dir).resolve()
    if not (source_dir / "CMakeLists.txt").is_file():
        raise ValueError(f"source directory must contain CMakeLists.txt: {source_dir}")
    source_input = _inside(source_dir, Path(args.source_file))
    header_input = _inside(source_dir, Path(args.header_file))
    if source_input == header_input:
        raise ValueError("--source-file and --header-file must be different files")
    for tool in REQUIRED_TOOLS:
        if shutil.which(tool) is None:
            raise RuntimeError(f"required benchmark tool is missing: {tool}")

    phases: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ci-ccache-benchmark-") as temporary:
        root = Path(temporary)
        project = root / "project"
        shutil.copytree(source_dir, project, ignore=_copy_ignore, symlinks=True)
        relative_source = source_input.relative_to(source_dir)
        relative_header = header_input.relative_to(source_dir)
        source_file = project / relative_source
        header_file = project / relative_header
        cache_dir = root / "ccache"
        cache_dir.mkdir()
        jobs = str(args.build_jobs)
        base_env = os.environ.copy()
        base_env.update({
            "CCACHE_DIR": str(cache_dir),
            "CCACHE_MAXSIZE": args.cache_max_size,
            "CCACHE_BASEDIR": str(root),
            "CI_BUILD_JOBS": jobs,
            "CMAKE_BUILD_PARALLEL_LEVEL": jobs,
        })
        (root / "metadata.json").write_text(
            json.dumps({
                "source_dir": str(source_dir),
                "source_file": str(relative_source),
                "header_file": str(relative_header),
                "build_jobs": args.build_jobs,
                "cache_max_size": args.cache_max_size,
            }, indent=2) + "\n",
            encoding="utf-8",
        )

        primary_build = root / "build-primary"
        configure = _configure(project, primary_build, base_env, extra_args=args.cmake_arg)
        phases.append({"name": "configure_cold", **configure})
        compiler_identity = _compiler_identity(primary_build)
        phases.append(_build_phase("cold_build", project, primary_build, cache_dir, base_env))
        phases.append(_ctest_phase(primary_build, base_env, args.test_parallelism))
        warm_runs = []
        for index in range(1, args.warm_runs + 1):
            warm_runs.append(_build_phase(f"warm_noop_{index}", project, primary_build, cache_dir, base_env))
        phases.extend(warm_runs)

        original_source = source_file.read_bytes()
        _append_marker(source_file, "ccache benchmark source-only change")
        phases.append(_build_phase("source_incremental", project, primary_build, cache_dir, base_env))
        source_file.write_bytes(original_source)
        phases.append(_build_phase("source_cache_replay", project, primary_build, cache_dir, base_env))

        _append_marker(header_file, "ccache benchmark shared-header change")
        phases.append(_build_phase("header_incremental", project, primary_build, cache_dir, base_env))

        changed_flags = root / "build-changed-flags"
        changed_configure = _configure(
            project,
            changed_flags,
            base_env,
            compiler_flags=args.changed_cxx_flags,
            extra_args=args.cmake_arg,
        )
        phases.append({"name": "configure_changed_flags", **changed_configure})
        phases.append(_build_phase("compiler_flags_changed", project, changed_flags, cache_dir, base_env))
        flag_phase = phases[-1]
        flag_stats = flag_phase.get("ccache") or {}
        if flag_stats.get("available"):
            flag_phase["compiler_flag_cache_validation"] = "passed" if flag_stats.get("misses", 0) > 0 else "failed"
        else:
            flag_phase["compiler_flag_cache_validation"] = "unavailable"

        # Compare ccache-on and ccache-off from the exact same final source state.
        cold_control_cache = root / "ccache-cold-control"
        cold_control_cache.mkdir()
        cold_control_build = root / "build-cold-cache-control"
        cold_control_configure = _configure(project, cold_control_build, base_env, extra_args=args.cmake_arg)
        phases.append({"name": "configure_cold_cache_control", **cold_control_configure})
        cold_control_env = base_env.copy()
        cold_control_env["CCACHE_DIR"] = str(cold_control_cache)
        phases.append(_build_phase("cold_cache_control", project, cold_control_build, cold_control_cache, cold_control_env))

        no_cache_build = root / "build-cache-disabled"
        no_cache_env = base_env.copy()
        no_cache_env["CCACHE_DISABLE"] = "1"
        no_cache_configure = _configure(project, no_cache_build, no_cache_env, extra_args=args.cmake_arg)
        phases.append({"name": "configure_cache_disabled", **no_cache_configure})
        phases.append(_build_phase("cache_disabled_control", project, no_cache_build, cache_dir, no_cache_env, disable_cache=True))

        secondary = None
        if args.secondary_source_dir:
            secondary_source = Path(args.secondary_source_dir).resolve()
            if not (secondary_source / "CMakeLists.txt").is_file():
                raise ValueError(f"secondary source directory must contain CMakeLists.txt: {secondary_source}")
            secondary_copy = root / "secondary-project"
            shutil.copytree(secondary_source, secondary_copy, ignore=_copy_ignore, symlinks=True)
            secondary_build = root / "build-secondary"
            secondary_configure = _configure(secondary_copy, secondary_build, base_env, extra_args=args.cmake_arg)
            phases.append({"name": "configure_secondary_shared_cache", **secondary_configure})
            secondary = _build_phase("secondary_project_shared_cache", secondary_copy, secondary_build, cache_dir, base_env)
            phases.append(secondary)

        failed = [phase["name"] for phase in phases if phase.get("exit_code") != 0]
        if flag_phase.get("compiler_flag_cache_validation") == "failed":
            failed.append("compiler_flags_changed_cache_invalidation")
        warm_noop_valid = all(
            phase.get("exit_code") == 0
            and phase.get("ninja", {}).get("total_edges") == 0
            and phase.get("pending_ninja_actions_after") == 0
            for phase in warm_runs
        )
        if not warm_noop_valid:
            failed.append("warm_noop_validation")
        return {
            "schema_version": 1,
            "benchmark": "cmake-ninja-ccache",
            "source_project": str(source_dir),
            "source_file": str(relative_source),
            "shared_header_file": str(relative_header),
            "build_jobs": args.build_jobs,
            "cache_max_size": args.cache_max_size,
            "changed_cxx_flags": args.changed_cxx_flags,
            "warm_runs": args.warm_runs,
            "test_parallelism": args.test_parallelism,
            "compiler_identity": compiler_identity,
            "runner": {
                "os": os.environ.get("RUNNER_OS") or os.name,
                "arch": os.environ.get("RUNNER_ARCH") or None,
                "name": os.environ.get("RUNNER_NAME") or None,
            },
            "cache_directory_isolated": True,
            "source_tree_modified": False,
            "secondary_project": args.secondary_source_dir or None,
            "phases": phases,
            "failed_phases": failed,
            "status": "failed" if failed else "completed",
            "interpretation": {
                "cold_build": "First build with an empty isolated ccache directory.",
                "warm_noop": "Repeated no-op rebuilds; each must report zero new Ninja edges and zero pending actions.",
                "source_cache_replay": "Restores the original source bytes after an incremental change; ccache should reuse the original object when the compiler identity and flags match.",
                "test": "Serial or explicitly selected CTest run; test time is reported separately from build time.",
                "source_incremental": "One selected source file receives a comment-only content change.",
                "header_incremental": "One selected shared header receives a comment-only content change.",
                "compiler_flags_changed": "A separate build directory uses a code-generation-affecting C++ compiler flag; ccache must report at least one miss when stats are available.",
                "cold_cache_control": "Same final source state as the cache-disabled control, with a fresh empty ccache directory.",
                "cache_disabled_control": "Same final source state as the cold-cache control, with ccache disabled; compare timings, not just hit rate.",
                "secondary_project_shared_cache": "Optional second project uses the same isolated cache; observed hits, not assumptions, determine reuse.",
                "limits": "This is a single-run experiment. Repeat on the same runner/toolchain and use representative production sources before claiming performance gains.",
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, help="CMake project directory to copy into a temporary workspace")
    parser.add_argument("--source-file", required=True, help="One source file relative to --source-dir")
    parser.add_argument("--header-file", required=True, help="A shared header relative to --source-dir")
    parser.add_argument("--secondary-source-dir", default="", help="Optional second CMake project to test the same toolchain cache")
    parser.add_argument("--build-jobs", type=int, default=max(1, os.cpu_count() or 1))
    parser.add_argument("--cache-max-size", default="2G")
    parser.add_argument("--changed-cxx-flags", default=DEFAULT_CHANGED_CXX_FLAGS, help="C++ flags that change generated code in the compiler-flag isolation scenario")
    parser.add_argument("--warm-runs", type=int, default=3, help="Number of repeated warm no-op builds")
    parser.add_argument("--test-parallelism", type=int, default=1, help="CTest parallelism; default 1 records a serial baseline")
    parser.add_argument("--cmake-arg", action="append", default=[], help="Extra CMake configure argument; may be repeated")
    parser.add_argument("--output-json", default="ccache-benchmark-report.json")
    args = parser.parse_args()
    if args.build_jobs < 1:
        parser.error("--build-jobs must be positive")
    if args.warm_runs < 1:
        parser.error("--warm-runs must be positive")
    if args.test_parallelism < 1:
        parser.error("--test-parallelism must be positive")
    args.changed_cxx_flags = _effective_changed_cxx_flags(args.changed_cxx_flags)

    output = Path(args.output_json)
    try:
        report = run_benchmark(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report = {
            "schema_version": 1,
            "benchmark": "cmake-ninja-ccache",
            "status": "failed",
            "error": str(exc),
            "source_tree_modified": False,
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "output_json": str(output),
        "failed_phases": report.get("failed_phases", []),
        "phases": [
            {
                "name": item.get("name"),
                "build_duration_ms": item.get("build_duration_ms"),
                "ninja_log_entries_added": item.get("ninja_log_entries_added"),
                "compile_window_ms": (item.get("ninja") or {}).get("compile_window_ms"),
                "link_window_ms": (item.get("ninja") or {}).get("link_window_ms"),
                "test_duration_ms": item.get("duration_ms") if item.get("name") == "test" else None,
                "ccache_hits": (item.get("ccache") or {}).get("hits"),
                "ccache_misses": (item.get("ccache") or {}).get("misses"),
            }
            for item in report.get("phases", [])
        ],
    }, indent=2))
    return 0 if report.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
