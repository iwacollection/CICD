#!/usr/bin/env python3
"""Collect GitHub Actions health metrics and evaluate CI platform SLOs."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _round_metric(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 3)


def load_policy(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read platform SLO policy: {exc}") from exc
    errors = validate_policy(payload)
    if errors:
        raise ValueError("invalid platform SLO policy: " + "; ".join(errors))
    return payload


def validate_policy(policy: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(policy, dict):
        return ["policy root must be an object"]
    if policy.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

    window = policy.get("window")
    if not isinstance(window, dict):
        errors.append("window must be an object")
    else:
        for key in ("branch", "event"):
            value = window.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"window.{key} must be a non-empty string")
        max_runs = window.get("max_runs")
        if not isinstance(max_runs, int) or isinstance(max_runs, bool) or not 1 <= max_runs <= 1000:
            errors.append("window.max_runs must be an integer between 1 and 1000")
        minimum = window.get("min_completed_runs_per_workflow")
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
            errors.append("window.min_completed_runs_per_workflow must be a positive integer")
        if isinstance(max_runs, int) and isinstance(minimum, int) and minimum > max_runs:
            errors.append("window.min_completed_runs_per_workflow cannot exceed window.max_runs")

    workflows = policy.get("workflows")
    if not isinstance(workflows, list) or not workflows:
        errors.append("workflows must be a non-empty array")
    elif not all(isinstance(item, str) and item.strip() for item in workflows):
        errors.append("every workflow name must be a non-empty string")
    elif len(set(workflows)) != len(workflows):
        errors.append("workflow names must be unique")

    thresholds = policy.get("thresholds")
    if not isinstance(thresholds, dict):
        errors.append("thresholds must be an object")
    else:
        for key in ("success_rate_min", "rerun_rate_max"):
            value = thresholds.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
                errors.append(f"thresholds.{key} must be between 0 and 1")
        for key in ("queue_p95_seconds_max", "duration_p95_seconds_max"):
            value = thresholds.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                errors.append(f"thresholds.{key} must be greater than 0")
    return errors


def _github_request(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "enterprise-ci-platform-health/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"GitHub Actions API request failed: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub Actions API returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub Actions API response must be an object")
    return payload


def fetch_runs(
    *,
    repository: str,
    token: str,
    api_url: str,
    branch: str,
    event: str,
    max_runs: int,
) -> list[dict[str, Any]]:
    if repository.count("/") != 1:
        raise ValueError("repository must use owner/name form")
    base = api_url.rstrip("/")
    encoded_repository = urllib.parse.quote(repository, safe="/")
    collected: list[dict[str, Any]] = []
    page = 1
    while len(collected) < max_runs:
        per_page = min(100, max_runs - len(collected))
        query = urllib.parse.urlencode(
            {
                "branch": branch,
                "event": event,
                "status": "completed",
                "per_page": per_page,
                "page": page,
            }
        )
        payload = _github_request(f"{base}/repos/{encoded_repository}/actions/runs?{query}", token)
        page_runs = payload.get("workflow_runs")
        if not isinstance(page_runs, list):
            raise RuntimeError("GitHub Actions API response is missing workflow_runs array")
        valid_runs = [item for item in page_runs if isinstance(item, dict)]
        collected.extend(valid_runs)
        if len(page_runs) < per_page or not page_runs:
            break
        page += 1
    return collected[:max_runs]


def load_runs(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read workflow run input: {exc}") from exc
    if isinstance(payload, dict):
        payload = payload.get("workflow_runs")
    if not isinstance(payload, list):
        raise ValueError("workflow run input must be an array or an object containing workflow_runs")
    return [item for item in payload if isinstance(item, dict)]


def evaluate_runs(runs: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    workflows = list(policy["workflows"])
    thresholds = policy["thresholds"]
    minimum = policy["window"]["min_completed_runs_per_workflow"]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dropped_runs = 0

    for run in runs:
        workflow_name = run.get("name")
        if workflow_name not in workflows:
            continue
        if run.get("status") not in (None, "completed"):
            continue
        try:
            created = _parse_timestamp(run.get("created_at"))
            started = _parse_timestamp(run.get("run_started_at"))
            updated = _parse_timestamp(run.get("updated_at"))
        except (TypeError, ValueError):
            dropped_runs += 1
            continue
        queue_seconds = max(0.0, (started - created).total_seconds())
        duration_seconds = max(0.0, (updated - started).total_seconds())
        try:
            attempt = int(run.get("run_attempt", 1))
        except (TypeError, ValueError):
            attempt = 1
        grouped[workflow_name].append(
            {
                "conclusion": str(run.get("conclusion") or "unknown"),
                "queue_seconds": queue_seconds,
                "duration_seconds": duration_seconds,
                "rerun": attempt > 1,
            }
        )

    workflow_reports: dict[str, Any] = {}
    all_breaches: list[dict[str, Any]] = []
    for workflow_name in workflows:
        samples = grouped.get(workflow_name, [])
        sample_count = len(samples)
        success_count = sum(item["conclusion"] == "success" for item in samples)
        success_rate = success_count / sample_count if sample_count else 0.0
        rerun_count = sum(bool(item["rerun"]) for item in samples)
        rerun_rate = rerun_count / sample_count if sample_count else 0.0
        queue_p95 = _percentile([float(item["queue_seconds"]) for item in samples], 0.95)
        duration_p95 = _percentile([float(item["duration_seconds"]) for item in samples], 0.95)

        breaches: list[dict[str, Any]] = []
        status = "insufficient-data"
        if sample_count >= minimum:
            checks = (
                ("success_rate", success_rate, ">=", float(thresholds["success_rate_min"])),
                ("queue_p95_seconds", queue_p95 or 0.0, "<=", float(thresholds["queue_p95_seconds_max"])),
                (
                    "duration_p95_seconds",
                    duration_p95 or 0.0,
                    "<=",
                    float(thresholds["duration_p95_seconds_max"]),
                ),
                ("rerun_rate", rerun_rate, "<=", float(thresholds["rerun_rate_max"])),
            )
            for metric, actual, operator, limit in checks:
                violated = actual < limit if operator == ">=" else actual > limit
                if violated:
                    breach = {
                        "workflow": workflow_name,
                        "metric": metric,
                        "actual": _round_metric(actual),
                        "operator": operator,
                        "limit": limit,
                    }
                    breaches.append(breach)
                    all_breaches.append(breach)
            status = "breached" if breaches else "healthy"

        workflow_reports[workflow_name] = {
            "status": status,
            "completed_runs": sample_count,
            "success_count": success_count,
            "success_rate": _round_metric(success_rate),
            "rerun_count": rerun_count,
            "rerun_rate": _round_metric(rerun_rate),
            "queue_p50_seconds": _round_metric(
                _percentile([float(item["queue_seconds"]) for item in samples], 0.50)
            ),
            "queue_p95_seconds": _round_metric(queue_p95),
            "duration_p50_seconds": _round_metric(
                _percentile([float(item["duration_seconds"]) for item in samples], 0.50)
            ),
            "duration_p95_seconds": _round_metric(duration_p95),
            "breaches": breaches,
        }

    statuses = {item["status"] for item in workflow_reports.values()}
    if all_breaches:
        overall_status = "breached"
    elif statuses == {"healthy"}:
        overall_status = "healthy"
    elif "healthy" in statuses:
        overall_status = "partial-data"
    else:
        overall_status = "insufficient-data"

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": overall_status,
        "window": dict(policy["window"]),
        "thresholds": dict(thresholds),
        "source_run_count": len(runs),
        "dropped_run_count": dropped_runs,
        "workflows": workflow_reports,
        "breaches": all_breaches,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# CI Platform Health",
        "",
        f"Overall status: **{report['status']}**",
        "",
        "| Workflow | Status | Runs | Success | Queue P95 | Duration P95 | Rerun rate |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, metrics in report["workflows"].items():
        success = f"{float(metrics['success_rate']) * 100:.1f}%"
        rerun = f"{float(metrics['rerun_rate']) * 100:.1f}%"
        queue = "n/a" if metrics["queue_p95_seconds"] is None else f"{metrics['queue_p95_seconds']:.1f}s"
        duration = (
            "n/a" if metrics["duration_p95_seconds"] is None else f"{metrics['duration_p95_seconds']:.1f}s"
        )
        lines.append(
            f"| {name} | {metrics['status']} | {metrics['completed_runs']} | {success} | {queue} | {duration} | {rerun} |"
        )

    lines.extend(["", "## SLO breaches", ""])
    if report["breaches"]:
        for breach in report["breaches"]:
            lines.append(
                f"- `{breach['workflow']}`: `{breach['metric']}` = {breach['actual']} "
                f"(required {breach['operator']} {breach['limit']})"
            )
    else:
        lines.append("- None in the current sample window.")

    lines.extend(
        [
            "",
            f"Source runs inspected: {report['source_run_count']}",
            f"Malformed/incomplete runs dropped: {report['dropped_run_count']}",
            "",
        ]
    )
    return "\n".join(lines)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default="ci/platform-slo.json")
    parser.add_argument("--validate-policy", action="store_true")
    parser.add_argument("--input", default="", help="Optional local workflow-runs JSON fixture")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    parser.add_argument("--json-out", default="")
    parser.add_argument("--markdown-out", default="")
    parser.add_argument("--github-output", default="")
    parser.add_argument("--fail-on-breach", action="store_true")
    args = parser.parse_args()

    try:
        policy = load_policy(Path(args.policy))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.validate_policy:
        print(f"OK: platform SLO policy validated for {len(policy['workflows'])} workflows")
        return 0

    try:
        if args.input:
            runs = load_runs(Path(args.input))
        else:
            token = os.environ.get("GITHUB_TOKEN", "").strip()
            if not args.repository:
                raise ValueError("repository is required when --input is not supplied")
            if not token:
                raise ValueError("GITHUB_TOKEN is required when --input is not supplied")
            runs = fetch_runs(
                repository=args.repository,
                token=token,
                api_url=args.api_url,
                branch=policy["window"]["branch"],
                event=policy["window"]["event"],
                max_runs=policy["window"]["max_runs"],
            )
        report = evaluate_runs(runs, policy)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    markdown = render_markdown(report)
    if args.json_out:
        _write_json(Path(args.json_out), report)
    if args.markdown_out:
        output_path = Path(args.markdown_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"status={report['status']}\n")
            handle.write(f"breach_count={len(report['breaches'])}\n")

    print(json.dumps({"status": report["status"], "breaches": len(report["breaches"])}, separators=(",", ":")))
    if args.fail_on_breach and report["breaches"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
