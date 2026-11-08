#!/usr/bin/env python3
"""Collect GitHub workflow-run and job timing data after a Build Matrix run completes."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_API_URL = "https://api.github.com"
SUMMARY_STAGES = (
    "source_checkout", "platform_checkout", "toolchain_image_pull",
    "upstream_artifact_download", "upstream_artifact_resolution",
    "build", "test", "artifact_package", "artifact_verify", "artifact_upload",
    "trivy_scan", "sbom_generation", "supply_chain_policy", "security_evidence_upload",
)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed_ms(start: Any, end: Any) -> float | None:
    start_dt, end_dt = _parse_timestamp(start), _parse_timestamp(end)
    if start_dt is None or end_dt is None:
        return None
    duration = (end_dt - start_dt).total_seconds() * 1000
    return round(duration, 2) if duration >= 0 else None


def _percentile(values: list[float], fraction: float = 0.95) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 2)


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median_ms": None, "p95_ms": None, "max_ms": None}
    return {
        "count": len(values),
        "median_ms": round(statistics.median(values), 2),
        "p95_ms": _percentile(values),
        "max_ms": round(max(values), 2),
    }


def _job_category(name: str) -> str:
    normalized = name.strip().lower()
    if normalized.startswith("discover") or "dependency dag" in normalized:
        return "discover"
    if normalized.startswith("dag "):
        return "build"
    if normalized.startswith("security "):
        return "security"
    if any(token in normalized for token in ("barrier", "gate", "aggregate", "attest", "performance")):
        return "orchestration"
    return "other"


def build_end_to_end_report(
    run: dict[str, Any],
    jobs: list[dict[str, Any]],
    performance_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a report from the completed workflow run, its jobs and CI stage metrics."""
    created_at = run.get("created_at")
    run_started_at = run.get("run_started_at")
    run_updated_at = run.get("updated_at")
    run_completed = run.get("status") == "completed"

    normalized_jobs: list[dict[str, Any]] = []
    queue_waits: list[float] = []
    runtimes: list[float] = []
    categories: dict[str, dict[str, Any]] = {}
    conclusions: dict[str, int] = {}

    for job in jobs:
        name = str(job.get("name") or "")
        queue_ms = _elapsed_ms(job.get("created_at"), job.get("started_at"))
        runtime_ms = _elapsed_ms(job.get("started_at"), job.get("completed_at"))
        conclusion = str(job.get("conclusion") or job.get("status") or "unknown")
        conclusions[conclusion] = conclusions.get(conclusion, 0) + 1
        if queue_ms is not None:
            queue_waits.append(queue_ms)
        if runtime_ms is not None:
            runtimes.append(runtime_ms)

        category = _job_category(name)
        item = categories.setdefault(category, {"job_count": 0, "queue_wait_ms": [], "runtime_ms": []})
        item["job_count"] += 1
        if queue_ms is not None:
            item["queue_wait_ms"].append(queue_ms)
        if runtime_ms is not None:
            item["runtime_ms"].append(runtime_ms)

        normalized_jobs.append(
            {
                "id": job.get("id"),
                "name": name,
                "category": category,
                "status": job.get("status"),
                "conclusion": job.get("conclusion"),
                "created_at": job.get("created_at"),
                "started_at": job.get("started_at"),
                "completed_at": job.get("completed_at"),
                "queue_wait_ms": queue_ms,
                "runtime_ms": runtime_ms,
                "runner_name": job.get("runner_name"),
                "runner_group_name": job.get("runner_group_name"),
                "labels": job.get("labels") if isinstance(job.get("labels"), list) else [],
            }
        )

    started_jobs = [job for job in normalized_jobs if job.get("started_at")]
    completed_jobs = [job for job in normalized_jobs if job.get("completed_at")]
    first_job_start = min(
        (job["started_at"] for job in started_jobs),
        key=lambda value: _parse_timestamp(value) or datetime.max,
        default=None,
    )
    last_job_completion = max(
        (job["completed_at"] for job in completed_jobs),
        key=lambda value: _parse_timestamp(value) or datetime.min,
        default=None,
    )

    source_report = performance_report if isinstance(performance_report, dict) else {}
    first_metric_marker = source_report.get("earliest_job_started_at")
    last_metric_completion = source_report.get("latest_job_completed_at")
    instrumented_span = source_report.get("instrumented_workflow_span_ms")
    stage_summary = source_report.get("stage_summary", {})
    selected_stages = {
        name: stage_summary[name]
        for name in SUMMARY_STAGES
        if isinstance(stage_summary, dict) and isinstance(stage_summary.get(name), dict)
    }

    category_summary = {
        category: {
            "job_count": values["job_count"],
            "queue_wait": _summary(values["queue_wait_ms"]),
            "runtime": _summary(values["runtime_ms"]),
        }
        for category, values in sorted(categories.items())
    }

    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(),
        "source": {
            "api": "GitHub Actions workflow_run and workflow_run jobs endpoint",
            "completion_timestamp": "workflow_run.updated_at, fetched after status=completed",
            "performance_report_available": bool(source_report),
        },
        "run": {
            "id": run.get("id"),
            "attempt": run.get("run_attempt"),
            "name": run.get("name"),
            "workflow_id": run.get("workflow_id"),
            "repository": (run.get("repository") or {}).get("full_name"),
            "branch": run.get("head_branch"),
            "head_sha": run.get("head_sha"),
            "event": run.get("event"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "html_url": run.get("html_url"),
            "created_at": created_at,
            "run_started_at": run_started_at,
            "updated_at": run_updated_at,
            "run_queue_ms": _elapsed_ms(created_at, run_started_at),
            "run_active_ms": _elapsed_ms(run_started_at, run_updated_at) if run_completed else None,
            "run_end_to_end_ms": _elapsed_ms(created_at, run_updated_at) if run_completed else None,
        },
        "workflow_window": {
            "first_job_started_at": first_job_start,
            "last_job_completed_at": last_job_completion,
            "first_job_start_after_run_started_ms": _elapsed_ms(run_started_at, first_job_start),
            "last_job_completion_to_run_updated_ms": (
                _elapsed_ms(last_job_completion, run_updated_at) if run_completed else None
            ),
            "jobs_execution_window_ms": _elapsed_ms(first_job_start, last_job_completion),
            "earliest_metric_marker_at": first_metric_marker,
            "latest_metric_completion_at": last_metric_completion,
            "first_job_start_to_first_metric_marker_ms": _elapsed_ms(first_job_start, first_metric_marker),
            "last_metric_completion_to_last_job_completion_ms": _elapsed_ms(
                last_metric_completion, last_job_completion
            ),
            "instrumented_workflow_span_ms": instrumented_span,
        },
        "job_summary": {
            "job_count": len(normalized_jobs),
            "started_job_count": len(started_jobs),
            "completed_job_count": len(completed_jobs),
            "conclusions": conclusions,
            "queue_wait": _summary(queue_waits),
            "runtime": _summary(runtimes),
            "by_category": category_summary,
        },
        "jobs": normalized_jobs,
        "ci_stage_summary": selected_stages,
    }


def _format_duration(value: Any) -> str:
    return f"{value / 1000:.2f}s" if isinstance(value, (int, float)) else "n/a"


def format_markdown(value: dict[str, Any]) -> str:
    run = value.get("run", {})
    window = value.get("workflow_window", {})
    summary = value.get("job_summary", {})
    lines = [
        "# CI end-to-end timing report",
        "",
        f"- Workflow: {run.get('name', 'unknown')}",
        f"- Run: [{run.get('id', 'unknown')}]({run.get('html_url', '')})",
        f"- Branch / event: {run.get('branch', 'unknown')} / {run.get('event', 'unknown')}",
        f"- Conclusion: {run.get('conclusion', 'unknown')}",
        f"- Head SHA: {run.get('head_sha', 'unknown')}",
        "",
        "## Workflow lifecycle",
        "",
        "| Metric | Duration |",
        "|---|---:|",
    ]
    metrics = (
        ("Run queue (created to run started)", run.get("run_queue_ms")),
        ("Run end-to-end (created to completed update)", run.get("run_end_to_end_ms")),
        ("Active run (run started to completed update)", run.get("run_active_ms")),
        ("First job start after run started", window.get("first_job_start_after_run_started_ms")),
        ("Last job completion to run update", window.get("last_job_completion_to_run_updated_ms")),
        ("Job execution window (first start to last completion)", window.get("jobs_execution_window_ms")),
        ("Instrumented workflow span", window.get("instrumented_workflow_span_ms")),
    )
    for label, duration in metrics:
        lines.append(f"| {label} | {_format_duration(duration)} |")

    queue = summary.get("queue_wait", {})
    runtime = summary.get("runtime", {})
    lines.extend(
        [
            "",
            "## Job timing distribution",
            "",
            "| Metric | Samples | Median | P95 | Max |",
            "|---|---:|---:|---:|---:|",
            f"| Job queue wait | {queue.get('count', 0)} | {_format_duration(queue.get('median_ms'))} | {_format_duration(queue.get('p95_ms'))} | {_format_duration(queue.get('max_ms'))} |",
            f"| Job runtime | {runtime.get('count', 0)} | {_format_duration(runtime.get('median_ms'))} | {_format_duration(runtime.get('p95_ms'))} | {_format_duration(runtime.get('max_ms'))} |",
            "",
            "## Job timing by category",
            "",
            "| Category | Jobs | Queue median | Queue P95 | Runtime median | Runtime P95 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for category, item in sorted(summary.get("by_category", {}).items()):
        q = item.get("queue_wait", {})
        r = item.get("runtime", {})
        lines.append(
            f"| {category} | {item.get('job_count', 0)} | "
            f"{_format_duration(q.get('median_ms'))} | {_format_duration(q.get('p95_ms'))} | "
            f"{_format_duration(r.get('median_ms'))} | {_format_duration(r.get('p95_ms'))} |"
        )

    stages = value.get("ci_stage_summary", {})
    if stages:
        lines.extend(
            [
                "",
                "## Instrumented CI stages",
                "",
                "| Stage | Samples | Median | P95 | Max |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for stage, item in sorted(stages.items()):
            lines.append(
                f"| {stage} | {item.get('count', 0)} | "
                f"{_format_duration(item.get('median_ms'))} | "
                f"{_format_duration(item.get('p95_ms'))} | "
                f"{_format_duration(item.get('max_ms'))} |"
            )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Run end-to-end time uses the completed workflow run's GitHub API updated_at timestamp because the workflow-run REST object does not expose a dedicated completed_at field.",
            "- Job queue wait is measured as job started_at minus created_at; it is a per-job runner wait, not the workflow's dependency wait.",
            "- Job execution window is the wall-clock interval from the first started job to the last completed job. It is not the sum of parallel job runtimes.",
            "- Instrumented stage timings explain work inside jobs; they do not include all GitHub queueing, runner provisioning or post-job cleanup.",
            "",
        ]
    )
    return "\n".join(lines)


def _api_get_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ci-end-to-end-observability",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub API request failed for {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"GitHub API returned a non-object payload for {url}")
    return payload


def fetch_workflow_run_data(
    repo: str,
    run_id: int,
    token: str,
    *,
    api_url: str = DEFAULT_API_URL,
    per_page: int = 100,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not 1 <= per_page <= 100:
        raise ValueError("per_page must be between 1 and 100")
    repo_path = urllib.parse.quote(repo, safe="/")
    base = f"{api_url.rstrip('/')}/repos/{repo_path}/actions/runs/{run_id}"
    run = _api_get_json(base, token)
    attempt = run.get("run_attempt")
    if not isinstance(attempt, int) or attempt < 1:
        raise RuntimeError(f"Workflow run {run_id} did not return a valid run_attempt")

    jobs: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = _api_get_json(
            f"{base}/attempts/{attempt}/jobs?per_page={per_page}&page={page}",
            token,
        )
        batch = payload.get("jobs", [])
        if not isinstance(batch, list):
            raise RuntimeError(f"GitHub API returned invalid jobs for workflow run {run_id}")
        jobs.extend(job for job in batch if isinstance(job, dict))
        if len(batch) < per_page:
            break
        page += 1
    return run, jobs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("CI_REPOSITORY"))
    parser.add_argument("--run-id", type=int, default=int(os.environ.get("CI_PARENT_RUN_ID", "0")))
    parser.add_argument("--token", default=os.environ.get("CI_GITHUB_TOKEN"))
    parser.add_argument("--report-json", default="parent-report/ci-performance-report.json")
    parser.add_argument("--output-json", default="ci-end-to-end-report.json")
    parser.add_argument("--output-markdown", default="ci-end-to-end-report.md")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    args = parser.parse_args()

    if not args.repo or "/" not in args.repo:
        parser.error("--repo must be owner/name or CI_REPOSITORY must be set")
    if args.run_id <= 0:
        parser.error("--run-id must be a positive integer or CI_PARENT_RUN_ID must be set")
    if not args.token:
        parser.error("--token or CI_GITHUB_TOKEN must be set")

    run, jobs = fetch_workflow_run_data(args.repo, args.run_id, args.token, api_url=args.api_url)
    if run.get("status") != "completed":
        raise SystemExit(f"Workflow run {args.run_id} is not completed: status={run.get('status')}")

    report_path = Path(args.report_json)
    performance_report = None
    if report_path.is_file():
        try:
            loaded = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                performance_report = loaded
        except (OSError, json.JSONDecodeError):
            performance_report = None

    result = build_end_to_end_report(run, jobs, performance_report)
    output_json, output_markdown = Path(args.output_json), Path(args.output_markdown)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_markdown.write_text(format_markdown(result), encoding="utf-8")
    print(format_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
