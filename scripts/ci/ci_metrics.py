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
    for report in reports:
        for name, stage in report.get("stages", {}).items():
            duration = stage.get("duration_ms") if isinstance(stage, dict) else None
            if isinstance(duration, (int, float)):
                stage_values.setdefault(name, []).append(float(duration))

    stage_summary = {
        name: {
            "count": len(values),
            "median_ms": round(statistics.median(values), 2),
            "p95_ms": _percentile(values, 0.95),
            "max_ms": round(max(values), 2),
        }
        for name, values in sorted(stage_values.items())
    }
    return {
        "schema_version": 1,
        "generated_at": _utc_now(),
        "job_count": len(reports),
        "jobs": reports,
        "stage_summary": stage_summary,
    }


def format_aggregate_summary(value: dict[str, Any]) -> str:
    lines = [
        "# CI performance report",
        "",
        f"- Metric reports collected: {value.get('job_count', 0)}",
        "- Durations are measured inside individual jobs; this report does not add parallel job durations together.",
        "",
        "## Stage distribution",
        "",
        "| Stage | Samples | Median | P95 | Max |",
        "|---|---:|---:|---:|---:|",
    ]
    summary = value.get("stage_summary", {})
    if not summary:
        lines.append("| No stage data | 0 | n/a | n/a | n/a |")
    else:
        for name, item in summary.items():
            lines.append(
                f"| {name} | {item['count']} | {item['median_ms'] / 1000:.2f}s | "
                f"{item['p95_ms'] / 1000:.2f}s | {item['max_ms'] / 1000:.2f}s |"
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
