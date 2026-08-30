from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from platform_health import evaluate_runs, load_policy, render_markdown, validate_policy  # noqa: E402


class PlatformHealthTests(unittest.TestCase):
    def _policy(self, *, minimum: int = 2) -> dict:
        return {
            "schema_version": 1,
            "window": {
                "branch": "main",
                "event": "push",
                "max_runs": 20,
                "min_completed_runs_per_workflow": minimum,
            },
            "workflows": ["Build Matrix"],
            "thresholds": {
                "success_rate_min": 0.95,
                "queue_p95_seconds_max": 300,
                "duration_p95_seconds_max": 1800,
                "rerun_rate_max": 0.10,
            },
        }

    def _run(
        self,
        *,
        conclusion: str = "success",
        queue_seconds: int = 10,
        duration_seconds: int = 60,
        attempt: int = 1,
    ) -> dict:
        created = datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc)
        started = created + timedelta(seconds=queue_seconds)
        updated = started + timedelta(seconds=duration_seconds)
        return {
            "name": "Build Matrix",
            "status": "completed",
            "conclusion": conclusion,
            "created_at": created.isoformat().replace("+00:00", "Z"),
            "run_started_at": started.isoformat().replace("+00:00", "Z"),
            "updated_at": updated.isoformat().replace("+00:00", "Z"),
            "run_attempt": attempt,
        }

    def test_repository_policy_is_valid(self) -> None:
        policy = load_policy(ROOT / "ci" / "platform-slo.json")
        self.assertEqual(validate_policy(policy), [])
        self.assertEqual(
            policy["workflows"],
            ["Platform Validate", "Build Matrix", "Toolchain Supply Chain"],
        )

    def test_healthy_sample_reports_queue_duration_success_and_reruns(self) -> None:
        report = evaluate_runs(
            [
                self._run(queue_seconds=5, duration_seconds=50),
                self._run(queue_seconds=10, duration_seconds=70),
            ],
            self._policy(),
        )
        metrics = report["workflows"]["Build Matrix"]
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(metrics["status"], "healthy")
        self.assertEqual(metrics["completed_runs"], 2)
        self.assertEqual(metrics["success_rate"], 1.0)
        self.assertEqual(metrics["queue_p95_seconds"], 10.0)
        self.assertEqual(metrics["duration_p95_seconds"], 70.0)
        self.assertEqual(metrics["rerun_rate"], 0.0)
        self.assertEqual(report["breaches"], [])

    def test_slo_breach_is_fail_closed_in_report(self) -> None:
        report = evaluate_runs(
            [
                self._run(conclusion="failure", queue_seconds=301, attempt=2),
                self._run(conclusion="success", queue_seconds=10),
            ],
            self._policy(),
        )
        metrics = report["workflows"]["Build Matrix"]
        self.assertEqual(report["status"], "breached")
        self.assertEqual(metrics["status"], "breached")
        breached_metrics = {item["metric"] for item in report["breaches"]}
        self.assertIn("success_rate", breached_metrics)
        self.assertIn("queue_p95_seconds", breached_metrics)
        self.assertIn("rerun_rate", breached_metrics)

    def test_insufficient_sample_does_not_create_false_breach(self) -> None:
        report = evaluate_runs([self._run(conclusion="failure", queue_seconds=999)], self._policy(minimum=2))
        self.assertEqual(report["status"], "insufficient-data")
        self.assertEqual(report["workflows"]["Build Matrix"]["status"], "insufficient-data")
        self.assertEqual(report["breaches"], [])

    def test_malformed_timestamp_is_dropped_not_counted_as_failure(self) -> None:
        malformed = self._run()
        malformed["run_started_at"] = "not-a-time"
        report = evaluate_runs([malformed, self._run()], self._policy(minimum=1))
        self.assertEqual(report["dropped_run_count"], 1)
        self.assertEqual(report["workflows"]["Build Matrix"]["completed_runs"], 1)
        self.assertEqual(report["status"], "healthy")

    def test_markdown_contains_operational_metrics(self) -> None:
        report = evaluate_runs([self._run(), self._run()], self._policy())
        markdown = render_markdown(report)
        self.assertIn("Queue P95", markdown)
        self.assertIn("Duration P95", markdown)
        self.assertIn("Rerun rate", markdown)
        self.assertIn("Build Matrix", markdown)

    def test_invalid_policy_rejects_bad_thresholds_and_duplicates(self) -> None:
        policy = self._policy()
        policy["workflows"] = ["Build Matrix", "Build Matrix"]
        policy["thresholds"]["success_rate_min"] = 2
        errors = validate_policy(policy)
        self.assertTrue(any("workflow names must be unique" in item for item in errors))
        self.assertTrue(any("success_rate_min" in item for item in errors))

    def test_input_shape_can_be_serialized_for_offline_replay(self) -> None:
        payload = {"workflow_runs": [self._run(), self._run()]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            decoded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(decoded["workflow_runs"]), 2)

    def test_health_workflow_is_read_only_and_retains_evidence(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "platform-health.yml").read_text(encoding="utf-8")
        self.assertIn("actions: read", workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertIn("platform_health.py", workflow)
        self.assertIn("--fail-on-breach", workflow)
        self.assertIn("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", workflow)
        self.assertIn("retention-days: 30", workflow)
        self.assertIn('cron: "17 */6 * * *"', workflow)


if __name__ == "__main__":
    unittest.main()
