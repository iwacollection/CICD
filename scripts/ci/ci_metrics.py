#!/usr/bin/env python3
"""Collect per-job CI stage timings and aggregate them into a workflow report."""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metadata() -> dict[str, str]:
    keys = {
        "run_id": "GITHUB_RUN_ID",
        "run_attempt": "GITHUB_RUN_ATTEMPT",
        "job": "GITHUB_JOB",
        "workflow": "GITHUB_WORKFLOW",
        "sha": "GITHUB_SHA",
        "project": "CI_PROJECT",
        "artifact_name": "CI_ARTIFACT_NAME",
        "target": "CI_TARGET",
        "kind": "CI_METRICS_KIND",
        "lane": "CI_LANE",
        "runner": "RUNNER_NAME",
        "runner_os": "RUNNER_OS",
        "runner_arch": "RUNNER_ARCH",
    }
    return {name: os.environ.get(env_name, "") for name, env_name in keys.items()}


def _load(path: Path) -> dict[str, Any]:
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError(f"unsupported metrics file: {path}")
        return value
    now_ns = time.monotonic_ns()
    return {
        "schema_version": 1,
        "metadata": _metadata(),
        "started_at": _utc_now(),
        "started_monotonic_ns": now_ns,
        "stages": {},
    }


def _save(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def start_stage(path: Path, stage: str) -> None:
    value = _load(path)
    stages = value.setdefault("stages", {})
    current = stages.get(stage)
    if isinstance(current, dict) and current.get("started_monotonic_ns") is not None:
        raise ValueError(f"stage is already running: {stage}")
    stages[stage] = {
        "started_at": _utc_now(),
        "started_monotonic_ns": time.monotonic_ns(),
        "status": "running",
    }
    _save(path, value)


def finish_stage(path: Path, stage: str, exit_code: int = 0) -> None:
    value = _load(path)
    stages = value.setdefault("stages", {})
    current = stages.get(stage)
    if not isinstance(current, dict) or current.get("started_monotonic_ns") is None:
        raise ValueError(f"stage has no active start marker: {stage}")
    now_ns = time.monotonic_ns()
    started_ns = int(current.pop("started_monotonic_ns"))
    current["finished_at"] = _utc_now()
    current["duration_ms"] = max(0, round((now_ns - started_ns) / 1_000_000, 2))
    current["exit_code"] = int(exit_code)
    current["status"] = "success" if exit_code == 0 else "failure"
    _save(path, value)


def record_stage(path: Path, stage: str, started_ns: int) -> None:
    """Record a stage that began before the metrics helper was available (checkout)."""
    now_ns = time.monotonic_ns()
    if started_ns <= 0 or started_ns > now_ns:
        raise ValueError("stage start timestamp must be a positive monotonic timestamp")
    value = _load(path)
    stages = value.setdefault("stages", {})
    if stage in stages:
        raise ValueError(f"stage already recorded: {stage}")
    if value.get("started_monotonic_ns") is None or started_ns < int(value["started_monotonic_ns"]):
        elapsed_seconds = (now_ns - started_ns) / 1_000_000_000
        value["started_monotonic_ns"] = started_ns
        value["started_at"] = (
            datetime.fromtimestamp(time.time() - elapsed_seconds, timezone.utc).isoformat()
        )
    stages[stage] = {
        "started_at": datetime.fromtimestamp(
            time.time() - (now_ns - started_ns) / 1_000_000_000, timezone.utc
        ).isoformat(),
        "finished_at": _utc_now(),
        "duration_ms": max(0, round((now_ns - started_ns) / 1_000_000, 2)),
        "exit_code": 0,
        "status": "success",
    }
    _save(path, value)


def finalize_metrics(path: Path, output_path: Path, summary_path: Path | None = None) -> dict[str, Any]:
    value = _load(path)
    now_ns = time.monotonic_ns()
    for stage_name, stage in value.setdefault("stages", {}).items():
        if isinstance(stage, dict) and stage.get("started_monotonic_ns") is not None:
            started_ns = int(stage.pop("started_monotonic_ns"))
            stage["finished_at"] = _utc_now()
            stage["duration_ms"] = max(0, round((now_ns - started_ns) / 1_000_000, 2))
            stage["status"] = "interrupted"
    started_ns = int(value.get("started_monotonic_ns", now_ns))
    value["completed_at"] = _utc_now()
    value["instrumented_duration_ms"] = max(0, round((now_ns - started_ns) / 1_000_000, 2))
    value["metadata"] = {**value.get("metadata", {}), **_metadata()}
    value.pop("started_monotonic_ns", None)
    _save(output_path, value)
    if summary_path:
        append_summary(summary_path, format_job_summary(value))
    return value


def format_job_summary(value: dict[str, Any]) -> str:
    metadata = value.get("metadata", {})
    title = metadata.get("project") or metadata.get("job") or metadata.get("kind") or "CI job"
    lines = [
        f"### CI stage timings: {title}",
        "",
        "| Stage | Duration | Result |",
        "|---|---:|---|",
    ]
    stages = value.get("stages", {})
    for name, stage in sorted(stages.items()):
        duration = stage.get("duration_ms")
        rendered = f"{duration / 1000:.2f}s" if isinstance(duration, (int, float)) else "n/a"
        lines.append(f"| {name} | {rendered} | {stage.get('status', 'unknown')} |")
    lines.extend(
        [
            "",
            f"- Instrumented job duration: {value.get('instrumented_duration_ms', 0) / 1000:.2f}s",
            f"- Runner: {metadata.get('runner', '')} ({metadata.get('runner_os', '')}/{metadata.get('runner_arch', '')})",
            "",
        ]
    )
    return "\n".join(lines)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 2)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed



def attach_build_telemetry(path: Path, telemetry_path: Path) -> bool:
    """Attach one or more phase-specific build telemetry records to a job."""
    if telemetry_path.is_dir():
        telemetry_files = sorted(telemetry_path.glob("build-telemetry-*.json"))
    elif telemetry_path.is_file():
        telemetry_files = [telemetry_path]
    else:
        return False

    records: list[dict[str, Any]] = []
    for telemetry_file in telemetry_files:
        telemetry = json.loads(telemetry_file.read_text(encoding="utf-8"))
        if not isinstance(telemetry, dict) or telemetry.get("schema_version") != 1:
            raise ValueError(f"unsupported build telemetry file: {telemetry_file}")
        records.append(telemetry)
    if not records:
        return False

    value = _load(path)
    value["build_telemetry"] = records
    _save(path, value)
    return True


def _build_telemetry_summary(reports: list[dict[str, Any]]) -> dict[str, Any]:
    build_reports: list[dict[str, Any]] = []
    for report in reports:
        raw = report.get("build_telemetry", [])
        if isinstance(raw, dict):
            raw = [raw]
        if isinstance(raw, list):
            build_reports.extend(item for item in raw if isinstance(item, dict))

    # Only the build phase performs C/C++ compilation. Test invocations are
    # retained for resource diagnostics but do not dilute compiler hit rate.
    compile_reports = [item for item in build_reports if item.get("phase", "build") == "build"]
    cache_reports = [
        report.get("ccache", {}) for report in compile_reports
        if isinstance(report.get("ccache"), dict) and report.get("ccache", {}).get("available")
    ]
    hits = sum(int(item.get("hits") or 0) for item in cache_reports)
    misses = sum(int(item.get("misses") or 0) for item in cache_reports)
    total = hits + misses

    phase_values: dict[str, list[float]] = {}
    conan_phase_values: dict[str, list[float]] = {}
    conan_phase_fields = (
        "tool_preflight_ms",
        "conan_venv_setup_ms",
        "dependency_resolution_ms",
        "dependency_package_list_ms",
        "dependency_download_ms",
        "dependency_local_install_generate_ms",
        "dependency_warm_cache_reuse_ms",
        "clean_configure_compile_total_ms",
        "warm_clean_rebuild_total_ms",
        "test_ms",
    )
    ninja_values: dict[str, list[float]] = {}
    effective_jobs_values: list[float] = []
    cpu_throttled_usec_total = 0
    oom_kill_events_total = 0
    memory_peak_values: list[int] = []
    io_read_bytes_total = 0
    io_write_bytes_total = 0
    io_pressure_some_usec_total = 0
    no_op_successes = 0
    availability_counts = {
        name: {"available": 0, "unavailable": 0, "unreported": 0}
        for name in ("ccache", "cmake_ninja", "conan")
    }
    for item in build_reports:
        availability = item.get("telemetry_availability", {})
        if not isinstance(availability, dict):
            availability = {}
        for name, counts in availability_counts.items():
            record = availability.get(name)
            status = record.get("status") if isinstance(record, dict) else None
            if status in {"available", "unavailable"}:
                counts[status] += 1
            else:
                counts["unreported"] += 1

        resources = item.get("resources", {})
        if isinstance(resources, dict):
            effective_jobs = resources.get("effective_jobs")
            if isinstance(effective_jobs, (int, float)):
                effective_jobs_values.append(float(effective_jobs))
            delta = resources.get("delta", {})
            if isinstance(delta, dict):
                cpu_delta = delta.get("cpu", {})
                mem_delta = delta.get("memory_events", {})
                io_delta = delta.get("io", {})
                if isinstance(cpu_delta, dict):
                    cpu_throttled_usec_total += int(cpu_delta.get("throttled_usec") or 0)
                if isinstance(mem_delta, dict):
                    oom_kill_events_total += int(mem_delta.get("oom_kill") or 0)
                if isinstance(io_delta, dict):
                    io_read_bytes_total += int(io_delta.get("rbytes") or 0)
                    io_write_bytes_total += int(io_delta.get("wbytes") or 0)
                pressure = delta.get("pressure_total_usec", {})
                if isinstance(pressure, dict):
                    io_pressure = pressure.get("io", {})
                    if isinstance(io_pressure, dict):
                        io_pressure_some_usec_total += int(io_pressure.get("some") or 0)
            after = resources.get("after", {})
            memory_peak = after.get("memory_peak_bytes") if isinstance(after, dict) else None
            if isinstance(memory_peak, (int, float)):
                memory_peak_values.append(int(memory_peak))

        conan = item.get("conan", {})
        if isinstance(conan, dict):
            phase_name = item.get("phase", "build")
            fields = ("test_ms",) if phase_name == "test" else tuple(
                field for field in conan_phase_fields if field != "test_ms"
            )
            for field in fields:
                duration = conan.get(field)
                if isinstance(duration, (int, float)):
                    conan_phase_values.setdefault(field, []).append(float(duration))

        cmake_ninja = item.get("cmake_ninja", {})
        if not isinstance(cmake_ninja, dict):
            continue
        for field in ("configure_generate_ms", "compile_link_ms", "total_ms"):
            duration = cmake_ninja.get(field)
            if isinstance(duration, (int, float)):
                phase_values.setdefault(field, []).append(float(duration))
        ninja = cmake_ninja.get("ninja", {})
        if isinstance(ninja, dict) and ninja.get("available"):
            for field in ("graph_nodes", "graph_edges", "target_count", "executed_edges", "object_compile_edges", "pending_actions_after_build"):
                number = ninja.get(field)
                if isinstance(number, (int, float)):
                    ninja_values.setdefault(field, []).append(float(number))
            if ninja.get("incremental_noop") is True:
                no_op_successes += 1

    return {
        "build_telemetry_invocations": len(build_reports),
        "compile_invocations": len(compile_reports),
        "ccache": {
            "samples": len(cache_reports),
            "hits": hits if cache_reports else None,
            "misses": misses if cache_reports else None,
            "hit_rate_percent": round(hits / total * 100.0, 2) if total else None,
            "cleanup_events_total": (
                sum(int(item["cleanup_events"]) for item in cache_reports
                    if item.get("cleanup_events_scope") == "build" and isinstance(item.get("cleanup_events"), (int, float)))
                if any(item.get("cleanup_events_scope") == "build" and isinstance(item.get("cleanup_events"), (int, float)) for item in cache_reports)
                else None
            ),
            "global_cleanup_snapshot_max": (
                max(int(item["cleanup_events"]) for item in cache_reports
                    if item.get("cleanup_events_scope") == "global_snapshot" and isinstance(item.get("cleanup_events"), (int, float)))
                if any(item.get("cleanup_events_scope") == "global_snapshot" and isinstance(item.get("cleanup_events"), (int, float)) for item in cache_reports)
                else None
            ),
            "max_cache_size_gib": (
                max(float(item["cache_size_gib"]) for item in cache_reports if isinstance(item.get("cache_size_gib"), (int, float)))
                if any(isinstance(item.get("cache_size_gib"), (int, float)) for item in cache_reports)
                else None
            ),
            "max_files_in_cache": (
                max(int(item["files_in_cache"]) for item in cache_reports if isinstance(item.get("files_in_cache"), (int, float)))
                if any(isinstance(item.get("files_in_cache"), (int, float)) for item in cache_reports)
                else None
            ),
        },
        "telemetry_availability": availability_counts,
        "cmake_phases": {
            name: {
                "count": len(values),
                "median_ms": round(statistics.median(values), 2),
                "p95_ms": _percentile(values, 0.95),
                "max_ms": round(max(values), 2),
            }
            for name, values in sorted(phase_values.items())
        },
        "conan_phases": {
            name: {
                "count": len(values),
                "median_ms": round(statistics.median(values), 2),
                "p95_ms": _percentile(values, 0.95),
                "max_ms": round(max(values), 2),
            }
            for name, values in sorted(conan_phase_values.items())
        },
        "ninja": {
            name: {
                "count": len(values),
                "median": round(statistics.median(values), 2),
                "max": round(max(values), 2),
            }
            for name, values in sorted(ninja_values.items())
        },
        "resources": {
            "samples": len(effective_jobs_values),
            "effective_jobs_median": round(statistics.median(effective_jobs_values), 2) if effective_jobs_values else None,
            "effective_jobs_max": max(effective_jobs_values) if effective_jobs_values else None,
            "cpu_throttled_usec_total": cpu_throttled_usec_total,
            "memory_peak_bytes_max": max(memory_peak_values) if memory_peak_values else None,
            "oom_kill_events_total": oom_kill_events_total,
            "io_read_bytes_total": io_read_bytes_total,
            "io_write_bytes_total": io_write_bytes_total,
            "io_pressure_some_usec_total": io_pressure_some_usec_total,
        },
        "incremental_noop_successes": no_op_successes,
    }

def aggregate_metrics(input_dir: Path) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for path in sorted(input_dir.glob("**/ci-stage-metrics.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(report, dict) and report.get("schema_version") == 1:
            reports.append(report)

    stage_values: dict[str, list[float]] = {}
    stage_statuses: dict[str, dict[str, int]] = {}
    kind_durations: dict[str, list[float]] = {}
    job_starts: list[datetime] = []
    job_completions: list[datetime] = []
    for report in reports:
        metadata = report.get("metadata", {})
        kind = str(metadata.get("kind") or metadata.get("job") or "unknown")
        job_duration = report.get("instrumented_duration_ms")
        if isinstance(job_duration, (int, float)):
            kind_durations.setdefault(kind, []).append(float(job_duration))

        started_at = _parse_timestamp(report.get("started_at"))
        completed_at = _parse_timestamp(report.get("completed_at"))
        if started_at:
            job_starts.append(started_at)
        if completed_at:
            job_completions.append(completed_at)

        stages = report.get("stages", {})
        if not isinstance(stages, dict):
            continue
        for name, stage in stages.items():
            if not isinstance(stage, dict):
                continue
            duration = stage.get("duration_ms")
            if isinstance(duration, (int, float)):
                stage_values.setdefault(name, []).append(float(duration))
            status = str(stage.get("status") or "unknown")
            counts = stage_statuses.setdefault(
                name,
                {"success": 0, "failure": 0, "interrupted": 0, "unknown": 0},
            )
            counts[status if status in counts else "unknown"] += 1

    stage_summary = {
        name: {
            "count": len(values),
            "success_count": stage_statuses.get(name, {}).get("success", 0),
            "failure_count": stage_statuses.get(name, {}).get("failure", 0),
            "interrupted_count": stage_statuses.get(name, {}).get("interrupted", 0),
            "median_ms": round(statistics.median(values), 2),
            "p95_ms": _percentile(values, 0.95),
            "max_ms": round(max(values), 2),
        }
        for name, values in sorted(stage_values.items())
    }
    job_kind_summary = {
        kind: {
            "count": len(values),
            "median_ms": round(statistics.median(values), 2),
            "p95_ms": _percentile(values, 0.95),
            "max_ms": round(max(values), 2),
        }
        for kind, values in sorted(kind_durations.items())
    }

    instrumented_span_ms = None
    earliest_started_at = None
    latest_completed_at = None
    if job_starts and job_completions:
        earliest = min(job_starts)
        latest = max(job_completions)
        earliest_started_at = earliest.isoformat()
        latest_completed_at = latest.isoformat()
        instrumented_span_ms = max(0, round((latest - earliest).total_seconds() * 1000, 2))

    return {
        "schema_version": 1,
        "generated_at": _utc_now(),
        "job_count": len(reports),
        "instrumented_workflow_span_ms": instrumented_span_ms,
        "earliest_job_started_at": earliest_started_at,
        "latest_job_completed_at": latest_completed_at,
        "job_kind_summary": job_kind_summary,
        "jobs": reports,
        "stage_summary": stage_summary,
        "build_telemetry_summary": _build_telemetry_summary(reports),
    }


def format_aggregate_summary(value: dict[str, Any]) -> str:
    span = value.get("instrumented_workflow_span_ms")
    span_text = f"{span / 1000:.2f}s" if isinstance(span, (int, float)) else "n/a"
    lines = [
        "# CI performance report",
        "",
        f"- Metric reports collected: {value.get('job_count', 0)}",
        f"- Instrumented workflow span: {span_text}",
        "- Workflow span starts at the first recorded job marker and ends at the last recorded job completion. It excludes queue time before the first marker and post-job cleanup.",
        "- Job and stage percentiles are distributions, not sums of parallel work.",
        "",
        "## Job duration by type",
        "",
        "| Job type | Samples | Median | P95 | Max |",
        "|---|---:|---:|---:|---:|",
    ]
    kinds = value.get("job_kind_summary", {})
    if not kinds:
        lines.append("| No job data | 0 | n/a | n/a | n/a |")
    else:
        for name, item in kinds.items():
            lines.append(
                f"| {name} | {item['count']} | {item['median_ms'] / 1000:.2f}s | "
                f"{item['p95_ms'] / 1000:.2f}s | {item['max_ms'] / 1000:.2f}s |"
            )

    lines.extend(
        [
            "",
            "## Stage distribution",
            "",
            "| Stage | Samples | Success | Failure | Interrupted | Median | P95 | Max |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    summary = value.get("stage_summary", {})
    if not summary:
        lines.append("| No stage data | 0 | 0 | 0 | 0 | n/a | n/a | n/a |")
    else:
        for name, item in summary.items():
            lines.append(
                f"| {name} | {item['count']} | {item['success_count']} | "
                f"{item['failure_count']} | {item['interrupted_count']} | "
                f"{item['median_ms'] / 1000:.2f}s | {item['p95_ms'] / 1000:.2f}s | "
                f"{item['max_ms'] / 1000:.2f}s |"
            )

    build_telemetry = value.get("build_telemetry_summary", {})
    ccache = build_telemetry.get("ccache", {})
    lines.extend(["", "## Compiler cache / CMake / Ninja telemetry", ""])
    if ccache.get("samples", 0):
        rate = ccache.get("hit_rate_percent")
        rate_text = f"{rate:.1f}%" if isinstance(rate, (int, float)) else "n/a"
        cleanup = ccache.get("cleanup_events_total")
        cleanup_text = str(cleanup) if isinstance(cleanup, (int, float)) else "n/a"
        global_cleanup = ccache.get("global_cleanup_snapshot_max")
        global_cleanup_text = str(global_cleanup) if isinstance(global_cleanup, (int, float)) else "n/a"
        size = ccache.get("max_cache_size_gib")
        size_text = f"{size:.2f} GiB" if isinstance(size, (int, float)) else "n/a"
        files = ccache.get("max_files_in_cache")
        files_text = str(files) if isinstance(files, (int, float)) else "n/a"
        lines.append(
            f"- ccache: {ccache.get('hits')} hits / {ccache.get('misses')} misses "
            f"({rate_text}); per-build cleanup events: {cleanup_text}; "
            f"global cleanup snapshot max: {global_cleanup_text}; "
            f"largest observed cache: {size_text} / {files_text} files"
        )
    else:
        lines.append("- ccache: unavailable (no per-build hit/miss counters)")
    for name, label in (("ccache", "ccache"), ("cmake_ninja", "CMake/Ninja"), ("conan", "Conan phase")):
        counts = build_telemetry.get("telemetry_availability", {}).get(name, {})
        if counts:
            lines.append(
                f"- {label} telemetry availability: {counts.get('available', 0)} available / "
                f"{counts.get('unavailable', 0)} unavailable / {counts.get('unreported', 0)} unreported"
            )
    cmake_phases = build_telemetry.get("cmake_phases", {})
    if not cmake_phases:
        lines.append("- CMake/Ninja phase metrics: unavailable (no numeric samples)")
    for phase, item in cmake_phases.items():
        lines.append(
            f"- CMake {phase}: {item['count']} samples; median {item['median_ms'] / 1000:.2f}s; "
            f"P95 {item['p95_ms'] / 1000:.2f}s; max {item['max_ms'] / 1000:.2f}s"
        )
    conan_phases = build_telemetry.get("conan_phases", {})
    if not conan_phases:
        lines.append("- Conan phase metrics: unavailable (no numeric samples)")
    for phase, item in conan_phases.items():
        lines.append(
            f"- Conan {phase}: {item['count']} samples; median {item['median_ms'] / 1000:.2f}s; "
            f"P95 {item['p95_ms'] / 1000:.2f}s; max {item['max_ms'] / 1000:.2f}s"
        )
    resources = build_telemetry.get("resources", {})
    if resources.get("samples", 0):
        lines.append(
            f"- Resource-aware jobs: median {resources.get('effective_jobs_median')} / "
            f"max {resources.get('effective_jobs_max')}; CPU throttled {resources.get('cpu_throttled_usec_total', 0)} us; "
            f"memory peak {resources.get('memory_peak_bytes_max') or 'n/a'} bytes; "
            f"OOM kills {resources.get('oom_kill_events_total', 0)}"
        )
        lines.append(
            f"- Block I/O: read {resources.get('io_read_bytes_total', 0)} bytes / "
            f"write {resources.get('io_write_bytes_total', 0)} bytes; "
            f"I/O PSI some {resources.get('io_pressure_some_usec_total', 0)} us"
        )
    ninja = build_telemetry.get("ninja", {})
    if ninja:
        for field in ("graph_nodes", "graph_edges", "target_count", "executed_edges", "object_compile_edges", "pending_actions_after_build"):
            item = ninja.get(field)
            if item:
                lines.append(f"- Ninja {field}: median {item['median']:.0f}, max {item['max']:.0f}")
        lines.append(
            f"- Incremental dry-run confirmed no pending work: "
            f"{build_telemetry.get('incremental_noop_successes', 0)} build(s)"
        )
    lines.extend(["", "## Per-job breakdown", ""])
    lines.append("| Kind | Project | Target | Instrumented duration | Run/job |")
    lines.append("|---|---|---|---:|---|")
    for report in value.get("jobs", []):
        metadata = report.get("metadata", {})
        lines.append(
            f"| {metadata.get('kind', '')} | {metadata.get('project', '')} | "
            f"{metadata.get('target', '')} | "
            f"{report.get('instrumented_duration_ms', 0) / 1000:.2f}s | "
            f"{metadata.get('run_id', '')}/{metadata.get('job', '')} |"
        )
    lines.append("")
    return "\n".join(lines)


def append_summary(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content.rstrip() + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start")
    start.add_argument("--file", required=True)
    start.add_argument("--stage", required=True)

    finish = sub.add_parser("finish")
    finish.add_argument("--file", required=True)
    finish.add_argument("--stage", required=True)
    finish.add_argument("--exit-code", type=int, default=0)

    record = sub.add_parser("record")
    record.add_argument("--file", required=True)
    record.add_argument("--stage", required=True)
    record.add_argument("--start-ns", required=True, type=int)

    finalize = sub.add_parser("finalize")
    finalize.add_argument("--file", required=True)
    finalize.add_argument("--output-json", required=True)
    finalize.add_argument("--summary-file", default="")

    attach = sub.add_parser("attach-build-telemetry")
    attach.add_argument("--file", required=True)
    attach.add_argument("--telemetry-file", required=True)

    aggregate = sub.add_parser("aggregate")
    aggregate.add_argument("--input-dir", required=True)
    aggregate.add_argument("--output-json", required=True)
    aggregate.add_argument("--output-markdown", required=True)

    args = parser.parse_args()
    try:
        if args.command == "start":
            start_stage(Path(args.file), args.stage)
        elif args.command == "finish":
            finish_stage(Path(args.file), args.stage, args.exit_code)
        elif args.command == "record":
            record_stage(Path(args.file), args.stage, args.start_ns)
        elif args.command == "finalize":
            summary_path = Path(args.summary_file) if args.summary_file else None
            finalize_metrics(Path(args.file), Path(args.output_json), summary_path)
        elif args.command == "attach-build-telemetry":
            attach_build_telemetry(Path(args.file), Path(args.telemetry_file))
        elif args.command == "aggregate":
            report = aggregate_metrics(Path(args.input_dir))
            output_json = Path(args.output_json)
            output_json.parent.mkdir(parents=True, exist_ok=True)
            output_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            output_md = Path(args.output_markdown)
            output_md.parent.mkdir(parents=True, exist_ok=True)
            output_md.write_text(format_aggregate_summary(report) + "\n", encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
