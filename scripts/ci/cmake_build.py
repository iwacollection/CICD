#!/usr/bin/env python3
"""Run an instrumented CMake/Ninja build and capture incremental-build evidence."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _run(argv: list[str], cwd: Path, *, timeout: int = 120) -> tuple[int, str, float]:
    started = time.monotonic_ns()
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        output.append(line)
        sys.stdout.write(line)
        sys.stdout.flush()
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        code = 124
        message = f"command timed out after {timeout}s: {argv[0]}\n"
        output.append(message)
        sys.stderr.write(message)
    elapsed_ms = round((time.monotonic_ns() - started) / 1_000_000, 2)
    return code, "".join(output), elapsed_ms


def _ninja_log_entries(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [
        line for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line and not line.startswith("#")
    ]


def _ninja_introspection(build_dir: Path, source_dir: Path, baseline_entries: int) -> dict[str, Any]:
    ninja = shutil_which("ninja")
    if not ninja or not (build_dir / "build.ninja").is_file():
        return {"available": False}

    result: dict[str, Any] = {"available": True}
    graph = subprocess.run(
        [ninja, "-C", str(build_dir), "-t", "graph", "all"],
        cwd=source_dir, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=30,
    )
    if graph.returncode == 0:
        graph_text = graph.stdout
        result["graph_nodes"] = len(re.findall(r"^\s*\"?(?:0x)?[0-9a-fA-F]+\"?\s+\[", graph_text, re.M))
        result["graph_edges"] = graph_text.count(" -> ")
        result["graph_bytes"] = len(graph_text.encode("utf-8"))
    else:
        result["graph_error"] = graph.stderr.strip()[:500]

    targets = subprocess.run(
        [ninja, "-C", str(build_dir), "-t", "targets", "all"],
        cwd=source_dir, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=30,
    )
    if targets.returncode == 0:
        result["target_count"] = sum(1 for line in targets.stdout.splitlines() if line.strip())

    commands = subprocess.run(
        [ninja, "-C", str(build_dir), "-t", "commands", "all"],
        cwd=source_dir, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=30,
    )
    if commands.returncode == 0:
        command_lines = [line for line in commands.stdout.splitlines() if line.strip()]
        result["command_count"] = len(command_lines)
        result["unique_command_count"] = len(set(command_lines))
        result["repeated_command_lines"] = max(0, len(command_lines) - len(set(command_lines)))

    log_entries = _ninja_log_entries(build_dir / ".ninja_log")
    recent = log_entries[baseline_entries:] if len(log_entries) >= baseline_entries else log_entries
    outputs = [
        fields[3]
        for line in recent
        if len(fields := line.split("\t")) >= 5
    ]
    object_outputs = [item for item in outputs if item.endswith((".o", ".obj"))]
    result["executed_edges"] = len(recent)
    result["object_compile_edges"] = len(object_outputs)
    result["duplicate_output_edges"] = max(0, len(object_outputs) - len(set(object_outputs)))

    dry_run = subprocess.run(
        [ninja, "-C", str(build_dir), "-n"],
        cwd=source_dir, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, timeout=30,
    )
    if dry_run.returncode == 0:
        result["pending_actions_after_build"] = len(
            re.findall(r"^\s*\[\d+/\d+\]", dry_run.stdout, re.M)
        )
        result["incremental_noop"] = result["pending_actions_after_build"] == 0
    return result


def shutil_which(binary: str) -> str | None:
    import shutil
    return shutil.which(binary)


def _profile_event_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    events = value.get("traceEvents", []) if isinstance(value, dict) else value
    return len(events) if isinstance(events, list) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", default=".")
    parser.add_argument("--build-dir", default="build")
    parser.add_argument("--generator", default="Ninja")
    parser.add_argument("--build-jobs", required=True)
    parser.add_argument("--define", action="append", default=[])
    args = parser.parse_args()

    source_dir = Path(args.source_dir).resolve()
    build_dir = Path(args.build_dir)
    if not build_dir.is_absolute():
        build_dir = (source_dir / build_dir).resolve()
    telemetry_dir = source_dir / ".ci-telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)
    profile_path = telemetry_dir / "cmake-configure-trace.json"
    output_path = telemetry_dir / "cmake-build.json"
    baseline_entries = len(_ninja_log_entries(build_dir / ".ninja_log"))

    configure_command = [
        "cmake", "-S", str(source_dir), "-B", str(build_dir),
        "-G", args.generator,
        "--profiling-format=google-trace",
        f"--profiling-output={profile_path}",
    ]
    for definition in args.define:
        configure_command.append(f"-D{definition}")

    total_started = time.monotonic_ns()
    configure_rc, configure_output, configure_ms = _run(configure_command, source_dir)
    compile_rc = 0
    compile_output = ""
    compile_ms = 0.0
    if configure_rc == 0:
        build_command = [
            "cmake", "--build", str(build_dir), "--parallel", args.build_jobs,
        ]
        if args.generator.lower() == "ninja":
            # Ninja's load guard prevents launching more jobs while the runner is
            # already saturated, in addition to the explicit CPU/memory job cap.
            build_command.extend(["--", "-d", "stats", "-l", args.build_jobs])
        compile_rc, compile_output, compile_ms = _run(build_command, source_dir, timeout=1800)

    ninja = _ninja_introspection(build_dir, source_dir, baseline_entries)
    telemetry = {
        "schema_version": 1,
        "generator": args.generator,
        "configure_generate_ms": configure_ms,
        "compile_link_ms": compile_ms,
        "total_ms": round((time.monotonic_ns() - total_started) / 1_000_000, 2),
        "configure_exit_code": configure_rc,
        "compile_exit_code": compile_rc,
        "cmake_profile_event_count": _profile_event_count(profile_path),
        "ninja": ninja,
    }
    output_path.write_text(json.dumps(telemetry, indent=2) + "\n", encoding="utf-8")
    print(
        "cmake_telemetry "
        + json.dumps(
            {key: value for key, value in telemetry.items() if key != "ninja"},
            sort_keys=True,
        )
    )
    if ninja.get("available"):
        print("ninja_graph_telemetry " + json.dumps(ninja, sort_keys=True))
    return configure_rc or compile_rc


if __name__ == "__main__":
    raise SystemExit(main())
