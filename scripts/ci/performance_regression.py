#!/usr/bin/env python3
"""Compare mainline CI build/scan P95 and functional failure rate with recent history."""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FAILURE_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required"}
VALID_CONCLUSIONS = FAILURE_CONCLUSIONS | {"success"}
DEFAULT_API_URL = "https://api.github.com"


def percentile(values: list[float], fraction: float = 0.95) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 2)


def _report_lane(report: dict[str, Any]) -> str:
    lanes = {
        str(job.get("metadata", {}).get("lane", "")).strip()
        for job in report.get("jobs", [])
        if isinstance(job, dict) and job.get("metadata", {}).get("kind") == "build"
    }
    lanes.discard("")
    return next(iter(lanes)) if len(lanes) == 1 else ""


def _stage_p95(report: dict[str, Any], kind: str, lane: str, stage_name: str) -> float | None:
    values: list[float] = []
    for job in report.get("jobs", []):
        if not isinstance(job, dict):
            continue
        metadata = job.get("metadata", {})
        if metadata.get("kind") != kind or metadata.get("lane") != lane:
            continue
        stage = job.get("stages", {}).get(stage_name, {})
        duration = stage.get("duration_ms") if isinstance(stage, dict) else None
        if isinstance(duration, (int, float)) and duration >= 0:
            values.append(float(duration))
    return percentile(values)


def evaluate_regressions(
    current_report: dict[str, Any],
    historical_reports: list[dict[str, Any]],
    historical_conclusions: list[str],
    current_conclusion: str,
    *,
    history_limit: int = 20,
    minimum_baseline_samples: int = 5,
    minimum_failure_samples: int = 10,
    build_regression_percent: float = 15.0,
    scan_regression_percent: float = 20.0,
    failure_rate_percent: float = 10.0,
) -> dict[str, Any]:
    lane = _report_lane(current_report)
    current_build = _stage_p95(current_report, "build", lane, "build") if lane else None
    current_scan = _stage_p95(current_report, "security", lane, "trivy_scan") if lane else None

    def compare(current: float | None, stage: str, threshold: float) -> dict[str, Any]:
        kind = "build" if stage == "build" else "security"
        baseline_values = [
            value
            for report in historical_reports
            if _report_lane(report) == lane
            for value in [_stage_p95(report, kind, lane, stage)]
            if value is not None
        ]
        baseline = round(statistics.median(baseline_values), 2) if baseline_values else None
        regression = (
            round((current / baseline - 1.0) * 100.0, 2)
            if current is not None and baseline is not None and baseline > 0
            else None
        )
        evaluated = current is not None and len(baseline_values) >= minimum_baseline_samples
        breached = evaluated and regression is not None and regression > threshold
        return {
            "current_p95_ms": current,
            "baseline_median_of_run_p95_ms": baseline,
            "baseline_samples": len(baseline_values),
            "regression_percent": regression,
            "threshold_percent": threshold,
            "evaluated": evaluated,
            "breached": bool(breached),
            "status": "regression" if breached else ("healthy" if evaluated else "warming_up"),
        }

    build = compare(current_build, "build", build_regression_percent)
    scan = compare(current_scan, "trivy_scan", scan_regression_percent)
    conclusions = [current_conclusion.strip().lower(), *historical_conclusions[: max(0, history_limit - 1)]]
    valid = [value for value in conclusions if value in VALID_CONCLUSIONS]
    failures = sum(value in FAILURE_CONCLUSIONS for value in valid)
    rate = round(failures / len(valid) * 100.0, 2) if valid else None
    failure_evaluated = len(valid) >= minimum_failure_samples
    failure_breached = failure_evaluated and rate is not None and rate > failure_rate_percent
    failure_check = {
        "sample_count": len(valid),
        "failure_count": failures,
        "failure_rate_percent": rate,
        "threshold_percent": failure_rate_percent,
        "evaluated": failure_evaluated,
        "breached": bool(failure_breached),
        "status": "regression" if failure_breached else ("healthy" if failure_evaluated else "warming_up"),
    }

    alerts: list[str] = []
    if build["breached"]:
        alerts.append(
            f"Build-stage P95 increased {build['regression_percent']:.1f}% "
            f"(current {build['current_p95_ms'] / 1000:.2f}s; "
            f"baseline {build['baseline_median_of_run_p95_ms'] / 1000:.2f}s; "
            f"threshold {build_regression_percent:.1f}%)."
        )
    if scan["breached"]:
        alerts.append(
            f"Trivy scan P95 increased {scan['regression_percent']:.1f}% "
            f"(current {scan['current_p95_ms'] / 1000:.2f}s; "
            f"baseline {scan['baseline_median_of_run_p95_ms'] / 1000:.2f}s; "
            f"threshold {scan_regression_percent:.1f}%)."
        )
    if failure_breached:
        alerts.append(
            f"Mainline functional build failure rate is {rate:.1f}% "
            f"({failures}/{len(valid)}; threshold {failure_rate_percent:.1f}%)."
        )

    warming = not build["evaluated"] or not scan["evaluated"] or not failure_evaluated
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "regression" if alerts else ("warming_up" if warming else "healthy"),
        "branch": "main",
        "lane": lane or "unknown",
        "history_limit": history_limit,
        "checks": {
            "build_stage_p95": build,
            "trivy_scan_p95": scan,
            "functional_failure_rate": failure_check,
        },
        "alerts": alerts,
    }


def format_markdown(report: dict[str, Any]) -> str:
    checks = report.get("checks", {})
    build = checks.get("build_stage_p95", {})
    scan = checks.get("trivy_scan_p95", {})
    failure = checks.get("functional_failure_rate", {})

    def seconds(value: Any) -> str:
        return f"{value / 1000:.2f}s" if isinstance(value, (int, float)) else "n/a"

    def comparison(item: dict[str, Any]) -> str:
        current = seconds(item.get("current_p95_ms"))
        baseline = seconds(item.get("baseline_median_of_run_p95_ms"))
        regression = item.get("regression_percent")
        delta = f"{regression:+.1f}%" if isinstance(regression, (int, float)) else "n/a"
        return (
            f"{current} / baseline {baseline} / {delta} "
            f"({item.get('baseline_samples', 0)} baseline runs; "
            f"threshold {item.get('threshold_percent', 0):.1f}%; {item.get('status', 'unknown')})"
        )

    rate = failure.get("failure_rate_percent")
    rate_text = f"{rate:.1f}%" if isinstance(rate, (int, float)) else "n/a"
    lines = [
        "# Mainline CI performance regression monitor",
        "",
        f"- Status: **{report.get('status', 'unknown')}**",
        f"- Branch / lane: {report.get('branch', 'main')} / {report.get('lane', 'unknown')}",
        f"- Build-stage P95: {comparison(build)}",
        f"- Trivy scan P95: {comparison(scan)}",
        f"- Functional failure rate: {rate_text} "
        f"({failure.get('failure_count', 0)}/{failure.get('sample_count', 0)}; "
        f"threshold {failure.get('threshold_percent', 0):.1f}%; "
        f"{failure.get('status', 'unknown')})",
        "",
        "P95 baselines are the median of per-run P95 values from recent main-branch builds in the same Lane. "
        "Build/scan comparisons warm up after 5 baseline reports; failure rate warms up after 10 completed runs.",
        "",
    ]
    alerts = report.get("alerts", [])
    if alerts:
        lines.extend(["## Regression warnings", ""])
        lines.extend(f"- {alert}" for alert in alerts)
    elif report.get("status") == "warming_up":
        lines.extend(["## Baseline warm-up", "", "More comparable mainline samples are needed before thresholds can be evaluated."])
    elif report.get("status") == "monitor_unavailable":
        lines.extend(["## Monitoring unavailable", "", report.get("error", "Metrics or GitHub history could not be read.")])
    else:
        lines.extend(["All evaluated metrics are within their configured thresholds."])
    return "\n".join(lines) + "\n"


class GitHubApi:
    def __init__(self, token: str, api_url: str = DEFAULT_API_URL) -> None:
        self.token = token
        self.api_url = api_url.rstrip("/")

    def get_bytes(self, url: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "cicd-performance-regression-monitor",
            },
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.read()

    def get_json(self, url: str) -> dict[str, Any]:
        return json.loads(self.get_bytes(url).decode("utf-8"))

    def fetch_recent_runs(
        self, repo: str, workflow: str, branch: str, current_run_id: str, limit: int
    ) -> list[dict[str, Any]]:
        workflow_id = urllib.parse.quote(workflow, safe="")
        query = urllib.parse.urlencode({"branch": branch, "status": "completed", "per_page": 100})
        url = f"{self.api_url}/repos/{repo}/actions/workflows/{workflow_id}/runs?{query}"
        payload = self.get_json(url)
        runs = payload.get("workflow_runs", [])
        eligible = [
            run for run in runs
            if str(run.get("id")) != str(current_run_id)
            and run.get("head_branch") == branch
            and run.get("event") in {"push", "workflow_dispatch", "schedule"}
            and run.get("status") == "completed"
            and run.get("conclusion") in VALID_CONCLUSIONS
        ]
        eligible.sort(key=lambda run: run.get("created_at", ""), reverse=True)
        return eligible[: max(0, limit)]

    def fetch_run_report(self, repo: str, run_id: int) -> dict[str, Any] | None:
        url = f"{self.api_url}/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"
        payload = self.get_json(url)
        artifacts = payload.get("artifacts", [])
        matches = [
            artifact for artifact in artifacts
            if artifact.get("name") == "ci-performance-report" and not artifact.get("expired", False)
        ]
        if not matches:
            return None
        artifact_id = matches[0].get("id")
        if not artifact_id:
            return None
        archive_url = f"{self.api_url}/repos/{repo}/actions/artifacts/{artifact_id}/zip"
        archive = self.get_bytes(archive_url)
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            candidates = [
                name for name in bundle.namelist()
                if name.endswith("ci-performance-report.json") and not name.endswith("/")
            ]
            if not candidates:
                return None
            return json.loads(bundle.read(candidates[0]).decode("utf-8"))


def _write_outputs(report: dict[str, Any], output_json: Path, output_markdown: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown = format_markdown(report)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.write_text(markdown, encoding="utf-8")
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    for alert in report.get("alerts", []):
        safe = alert.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::warning title=CI performance regression::{safe}")
    if report.get("status") == "monitor_unavailable":
        print("::warning title=CI performance monitor unavailable::" + report.get("error", "unknown error"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--workflow", default="ci.yml")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--current-run-id", required=True)
    parser.add_argument("--current-conclusion", required=True)
    parser.add_argument("--current-report", required=True)
    parser.add_argument("--history-limit", type=int, default=20)
    parser.add_argument("--minimum-baseline-samples", type=int, default=5)
    parser.add_argument("--minimum-failure-samples", type=int, default=10)
    parser.add_argument("--build-regression-percent", type=float, default=15.0)
    parser.add_argument("--scan-regression-percent", type=float, default=20.0)
    parser.add_argument("--failure-rate-percent", type=float, default=10.0)
    parser.add_argument("--output-json", default="ci-performance-regression.json")
    parser.add_argument("--output-markdown", default="ci-performance-regression.md")
    args = parser.parse_args()

    try:
        current = json.loads(Path(args.current_report).read_text(encoding="utf-8"))
        token = os.environ.get("CI_GITHUB_TOKEN", "")
        if not token:
            raise RuntimeError("CI_GITHUB_TOKEN is unavailable for reading mainline run artifacts")
        api = GitHubApi(token, os.environ.get("GITHUB_API_URL", DEFAULT_API_URL))
        recent_runs = api.fetch_recent_runs(
            args.repo, args.workflow, args.branch, args.current_run_id, args.history_limit - 1
        )
        historical_reports: list[dict[str, Any]] = []
        historical_conclusions = [str(run.get("conclusion", "")) for run in recent_runs]
        for run in recent_runs:
            try:
                report = api.fetch_run_report(args.repo, int(run["id"]))
            except (OSError, ValueError, KeyError, urllib.error.HTTPError, zipfile.BadZipFile):
                report = None
            if report is not None:
                historical_reports.append(report)
        report = evaluate_regressions(
            current,
            historical_reports,
            historical_conclusions,
            args.current_conclusion,
            history_limit=args.history_limit,
            minimum_baseline_samples=args.minimum_baseline_samples,
            minimum_failure_samples=args.minimum_failure_samples,
            build_regression_percent=args.build_regression_percent,
            scan_regression_percent=args.scan_regression_percent,
            failure_rate_percent=args.failure_rate_percent,
        )
        report["current_run_id"] = args.current_run_id
        report["historical_runs_checked"] = len(recent_runs)
        report["historical_metric_reports_found"] = len(historical_reports)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        report = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "monitor_unavailable",
            "branch": args.branch,
            "lane": "unknown",
            "alerts": [],
            "error": str(exc),
        }
    _write_outputs(report, Path(args.output_json), Path(args.output_markdown))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
