from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from promotion_policy import normalize_identity  # noqa: E402
from release_strategy import (  # noqa: E402
    abort_release,
    advance_release,
    load_release_policy,
    plan_release,
    project_release,
    render_documents,
    run_scenario,
    validate_release_policy,
)

BASELINE = "a" * 64
CANDIDATE = "b" * 64


def _identity(bundle: str = CANDIDATE) -> dict[str, str]:
    return normalize_identity(
        artifact_name="checkout-generic-linux-x86_64-gcc",
        bundle_sha256=bundle,
        source_sha="c" * 40,
        source_run_id="42",
        release_tag="artifact-v2-" + "d" * 64,
    )


class ProgressiveDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.strategies, self.clusters, self.promotion = load_release_policy(
            ROOT / "ci" / "release-strategies.json",
            ROOT / "ci" / "clusters.json",
            ROOT / "ci" / "promotion-policy.json",
        )

    def test_repository_policy_matches_method_decision(self) -> None:
        self.assertEqual(
            validate_release_policy(self.strategies, self.clusters, self.promotion),
            [],
        )
        self.assertEqual(self.strategies["strategies"]["canary"]["method"], "routing")
        self.assertEqual(self.strategies["strategies"]["blue_green"]["method"], "routing")
        self.assertEqual(self.strategies["strategies"]["multi_cluster"]["method"], "cluster_management")
        self.assertTrue(self.strategies["strategies"]["multi_cluster"]["pin_exact_cluster_ids"])

    def test_policy_rejects_loose_cluster_selection(self) -> None:
        broken = copy.deepcopy(self.strategies)
        broken["strategies"]["multi_cluster"]["pin_exact_cluster_ids"] = False
        errors = validate_release_policy(broken, self.clusters, self.promotion)
        self.assertTrue(any("pin_exact_cluster_ids" in item for item in errors))

    def test_canary_requires_ack_for_clusters_that_cannot_take_traffic(self) -> None:
        with self.assertRaisesRegex(ValueError, "prod-edge-offline"):
            self._plan("canary")

    def test_canary_steps_are_ordered_and_abort_returns_traffic_to_baseline(self) -> None:
        state = self._plan("canary", accept_excluded=True)
        self.assertEqual(state["status"], "planned")
        projected = project_release(state, self.clusters)
        self.assertEqual(projected["prod-cn-east-a"]["canary_weight"], 0)
        self.assertEqual(projected["prod-cn-east-a"]["serving_digests"], [BASELINE])
        self.assertFalse(projected["prod-edge-offline"]["targeted"])
        self.assertEqual(projected["dev-cn-east-a"]["serving_digests"], [BASELINE])

        bad = {"smoke": "pass", "error_rate": 0.5, "latency_p95_ms": 10, "requests": 100}
        untouched = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, "error_rate exceeds"):
            advance_release(state, bad, self.strategies)
        self.assertEqual(state, untouched)

        state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["step_name"], "1pct")
        self.assertEqual(state["waves"][0]["canary_weight"], 1)
        self.assertEqual(state["waves"][0]["stable_weight"], 99)
        self.assertEqual(state["waves"][0]["stable_digest"], BASELINE)
        self.assertEqual(state["waves"][0]["canary_digest"], CANDIDATE)
        routed = project_release(state, self.clusters)["prod-cn-north-a"]
        self.assertEqual(routed["serving_digests"], [BASELINE, CANDIDATE])
        self.assertEqual(projected["prod-edge-offline"]["serving_digests"], [BASELINE])

        aborted = abort_release(state)
        after = project_release(aborted, self.clusters)
        for cluster_id, view in after.items():
            self.assertEqual(view["serving_digests"], [BASELINE], cluster_id)
            self.assertEqual(view["canary_weight"], 0, cluster_id)
        with self.assertRaisesRegex(ValueError, "already aborted"):
            abort_release(aborted)

    def test_canary_completion_moves_stable_backend_without_leaving_canary_weight(self) -> None:
        final = run_scenario(
            strategies=self.strategies,
            clusters=self.clusters,
            promotion=self.promotion,
            strategy="canary",
            environment="production",
            service="checkout",
            identity=_identity(),
            baseline_digest=BASELINE,
            environment_pointer_digest=CANDIDATE,
            accept_excluded=True,
        )
        self.assertEqual(final["status"], "completed")
        self.assertEqual([item["step"] for item in final["history"] if item["action"] == "advance"], [
            "1pct",
            "5pct",
            "25pct",
            "50pct",
            "100pct",
        ])
        view = project_release(final, self.clusters)["prod-cn-east-canary"]
        self.assertEqual(view["serving_digests"], [CANDIDATE])
        self.assertEqual(view["canary_weight"], 0)
        self.assertEqual(view["stable_digest"], CANDIDATE)
        self.assertEqual(project_release(final, self.clusters)["prod-edge-offline"]["serving_digests"], [BASELINE])
        with self.assertRaisesRegex(ValueError, "environment rollback"):
            abort_release(final)

    def test_dev_canary_does_not_touch_other_environments(self) -> None:
        final = run_scenario(
            strategies=self.strategies,
            clusters=self.clusters,
            promotion=self.promotion,
            strategy="canary",
            environment="dev",
            service="checkout",
            identity=_identity(),
            baseline_digest=BASELINE,
            environment_pointer_digest=CANDIDATE,
        )
        projected = project_release(final, self.clusters)
        self.assertEqual(projected["dev-cn-east-a"]["serving_digests"], [CANDIDATE])
        self.assertEqual(projected["staging-cn-east-a"]["serving_digests"], [BASELINE])
        self.assertEqual(projected["prod-cn-east-a"]["serving_digests"], [BASELINE])
        self.assertFalse(projected["prod-cn-east-a"]["targeted"])

    def test_blue_green_preview_does_not_shift_production_and_cutover_is_atomic(self) -> None:
        state = self._plan("blue_green", accept_excluded=True)
        state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["step_name"], "deploy_inactive")
        held = project_release(state, self.clusters)["prod-cn-east-a"]
        self.assertEqual(held["serving_digests"], [BASELINE])
        self.assertEqual(held["active_slot"], "blue")
        self.assertEqual(state["waves"][0]["slot_digests"]["green"], CANDIDATE)

        state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["step_name"], "preview")
        preview = project_release(state, self.clusters)["prod-cn-east-a"]
        self.assertEqual(preview["serving_digests"], [BASELINE])
        self.assertEqual(preview["preview_digest"], CANDIDATE)
        documents = render_documents(state, self.strategies, "operator")
        preview_routes = [doc for _, doc in documents if doc["metadata"]["name"].endswith("-preview")]
        self.assertTrue(preview_routes)
        self.assertEqual(preview_routes[0]["spec"]["rules"][0]["backendRefs"][0]["weight"], 100)
        self.assertEqual(preview_routes[0]["spec"]["rules"][0]["matches"][0]["headers"][0]["name"], "x-release-preview")

        state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["step_name"], "cutover")
        cut = project_release(state, self.clusters)["prod-cn-east-b"]
        self.assertEqual(cut["serving_digests"], [CANDIDATE])
        self.assertEqual(cut["active_slot"], "green")
        self.assertEqual(cut["preview_digest"], "")
        routes = [
            doc
            for _, doc in render_documents(state, self.strategies, "operator")
            if doc["kind"] == "HTTPRoute" and doc["metadata"]["labels"]["cicd.platform/cluster-id"] == "prod-cn-east-b"
        ]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["spec"]["rules"][0]["backendRefs"], [
            {"name": "checkout-green", "port": 80, "weight": 100}
        ])

        aborted = abort_release(state)
        restored = project_release(aborted, self.clusters)["prod-cn-east-b"]
        self.assertEqual(restored["serving_digests"], [BASELINE])
        self.assertEqual(restored["active_slot"], "blue")
        self.assertEqual(aborted["waves"][0]["slot_digests"]["green"], "")

    def test_blue_green_confirm_keeps_previous_slot_for_rollback_window(self) -> None:
        final = run_scenario(
            strategies=self.strategies,
            clusters=self.clusters,
            promotion=self.promotion,
            strategy="blue_green",
            environment="production",
            service="checkout",
            identity=_identity(),
            baseline_digest=BASELINE,
            environment_pointer_digest=CANDIDATE,
            accept_excluded=True,
            active_slot="blue",
        )
        wave = final["waves"][0]
        self.assertEqual(final["status"], "completed")
        self.assertEqual(wave["active_slot"], "green")
        self.assertEqual(wave["slot_digests"]["green"], CANDIDATE)
        self.assertEqual(wave["slot_digests"]["blue"], BASELINE)
        self.assertFalse(any(name.endswith("-preview.json") for name, _ in render_documents(final, self.strategies, "operator")))

    def test_multi_cluster_pins_exact_ids_and_leaves_unselected_clusters_unchanged(self) -> None:
        state = self._plan("multi_cluster")
        self.assertEqual(state["method"], "cluster_management")
        self.assertEqual(state["waves"][0]["cluster_ids"], ["prod-cn-east-canary"])
        self.assertEqual(state["waves"][1]["cluster_ids"], ["prod-cn-east-a", "prod-cn-east-b"])
        self.assertEqual(state["waves"][2]["cluster_ids"], ["prod-cn-north-a"])
        self.assertIn({"id": "prod-edge-offline", "reason": "not_in_wave"}, state["unselected"])
        self.assertIn({"id": "dev-cn-east-a", "reason": "not_in_wave"}, state["unselected"])

        initial = render_documents(state, self.strategies, "unverified")
        self._assert_exact_clusters(initial, {"prod-cn-east-canary"})
        pinned = self._pins(initial)
        self.assertEqual(pinned[0]["spec"]["clusterIds"], ["prod-cn-east-canary"])
        self.assertEqual(pinned[0]["spec"]["method"], "cluster_management")
        decisions = [doc for _, doc in initial if doc["kind"] == "PlacementDecision"]
        self.assertEqual(decisions[0]["status"]["decisions"], [{"clusterName": "prod-cn-east-canary"}])
        appsets = [doc for _, doc in initial if doc["kind"] == "ApplicationSet"]
        self.assertEqual(list(appsets[0]["spec"]["generators"][0]), ["list"])
        self.assertNotIn("clusters", appsets[0]["spec"]["generators"][0])
        element = appsets[0]["spec"]["generators"][0]["list"]["elements"][0]
        self.assertEqual(element["stableDigest"], BASELINE)
        self.assertEqual(element["canaryDigest"], CANDIDATE)
        self.assertEqual(element["canaryWeight"], "0")

        state = advance_release(state, self._pass(), self.strategies)
        stepped = render_documents(state, self.strategies, "operator")
        route = next(
            doc
            for _, doc in stepped
            if doc["kind"] == "HTTPRoute" and doc["metadata"]["labels"]["cicd.platform/cluster-id"] == "prod-cn-east-canary"
        )
        weights = [backend["weight"] for backend in route["spec"]["rules"][0]["backendRefs"]]
        self.assertEqual(weights, [99, 1])
        self.assertEqual(sum(weights), 100)
        self._assert_exact_clusters(stepped, {"prod-cn-east-canary"})
        self.assertNotIn(CANDIDATE, project_release(state, self.clusters)["prod-cn-north-a"]["serving_digests"])

        final = run_scenario(
            strategies=self.strategies,
            clusters=self.clusters,
            promotion=self.promotion,
            strategy="multi_cluster",
            environment="production",
            service="checkout",
            identity=_identity(),
            baseline_digest=BASELINE,
            environment_pointer_digest=CANDIDATE,
        )
        projected = project_release(final, self.clusters)
        for cluster_id in ("prod-cn-east-canary", "prod-cn-east-a", "prod-cn-east-b", "prod-cn-north-a"):
            self.assertEqual(projected[cluster_id]["serving_digests"], [CANDIDATE], cluster_id)
        for cluster_id in ("prod-edge-offline", "dev-cn-east-a", "staging-cn-east-a"):
            self.assertEqual(projected[cluster_id]["serving_digests"], [BASELINE], cluster_id)
            self.assertFalse(projected[cluster_id]["targeted"], cluster_id)
        rendered = render_documents(final, self.strategies, "synthetic")
        self._assert_exact_clusters(
            rendered,
            {"prod-cn-east-canary", "prod-cn-east-a", "prod-cn-east-b", "prod-cn-north-a"},
        )
        self.assertTrue(all(doc["metadata"]["annotations"]["cicd.platform/evidence"] == "synthetic" for _, doc in rendered))

    def test_allowlist_cannot_add_clusters_and_denylist_cannot_leave_an_empty_wave(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty wave"):
            self._plan("multi_cluster", allow=["prod-cn-north-a"])
        with self.assertRaisesRegex(ValueError, "not selected"):
            self._plan(
                "multi_cluster",
                allow=[
                    "prod-cn-east-canary",
                    "prod-cn-east-a",
                    "prod-cn-east-b",
                    "prod-cn-north-a",
                    "prod-edge-offline",
                ],
            )
        with self.assertRaisesRegex(ValueError, "empty wave"):
            self._plan("multi_cluster", deny=["prod-cn-east-canary"])
        with self.assertRaisesRegex(ValueError, "outside the target environment"):
            self._plan("canary", allow=["dev-cn-east-a"], accept_excluded=True)
        with self.assertRaisesRegex(ValueError, "environment pointer"):
            plan_release(
                strategies=self.strategies,
                clusters=self.clusters,
                promotion=self.promotion,
                strategy="multi_cluster",
                environment="production",
                service="checkout",
                identity=_identity(),
                baseline_digest=BASELINE,
                environment_pointer_digest=BASELINE,
            )
        with self.assertRaisesRegex(ValueError, "already the baseline"):
            plan_release(
                strategies=self.strategies,
                clusters=self.clusters,
                promotion=self.promotion,
                strategy="multi_cluster",
                environment="production",
                service="checkout",
                identity=_identity(BASELINE),
                baseline_digest=BASELINE,
                environment_pointer_digest=BASELINE,
            )

    def test_failed_wave_aborts_the_whole_release_back_to_baseline(self) -> None:
        state = self._plan("multi_cluster")
        for _ in range(5):
            state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["status"], "completed")
        self.assertEqual(project_release(state, self.clusters)["prod-cn-east-canary"]["serving_digests"], [CANDIDATE])
        aborted = abort_release(state)
        projected = project_release(aborted, self.clusters)
        for cluster_id, view in projected.items():
            self.assertEqual(view["serving_digests"], [BASELINE], cluster_id)

    def test_cli_validate_and_docs_describe_the_same_controls(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/ci/release_strategy.py", "validate"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("progressive release policy validated", result.stdout)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "progressive-delivery.md").read_text(encoding="utf-8")
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        validate = (ROOT / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        for text in (readme, guide):
            self.assertIn("HTTPRoute", text)
            self.assertIn("PlacementDecision", text)
            self.assertIn("ApplicationSet", text)
        self.assertIn("docs/progressive-delivery.md", readme)
        self.assertIn("progressive-delivery.md", index)
        self.assertIn("release_strategy.py validate", validate)
        self.assertIn("deployment_pointer.py current", workflow)
        self.assertIn("check-pointer", workflow)
        self.assertIn("synthetic analysis is not production evidence", workflow)
        self.assertNotIn("deployments: write", workflow)
        self.assertNotIn("run_build.py", workflow)
        self.assertLess(
            workflow.index("check-pointer"),
            workflow.index("release_strategy.py plan") if "release_strategy.py plan" in workflow else workflow.index("release_strategy.py"),
        )

    def _plan(self, strategy: str, **overrides: object) -> dict:
        arguments = dict(
            strategies=self.strategies,
            clusters=self.clusters,
            promotion=self.promotion,
            strategy=strategy,
            environment="production",
            service="checkout",
            identity=_identity(),
            baseline_digest=BASELINE,
            environment_pointer_digest=CANDIDATE,
        )
        arguments.update(overrides)
        return plan_release(**arguments)  # type: ignore[arg-type]

    def _pass(self) -> dict[str, object]:
        return {
            "smoke": "pass",
            "readiness": "pass",
            "error_rate": 0.0,
            "latency_p95_ms": 20,
            "requests": 80,
        }

    def _pins(self, documents: list[tuple[str, dict]]) -> list[dict]:
        return [doc for _, doc in documents if doc["kind"] == "ClusterPin"]

    def _assert_exact_clusters(self, documents: list[tuple[str, dict]], expected: set[str]) -> None:
        seen: set[str] = set()
        for _, doc in documents:
            if doc["kind"] == "ClusterPin":
                seen.update(doc["spec"]["clusterIds"])
            if doc["kind"] == "PlacementDecision":
                seen.update(item["clusterName"] for item in doc["status"]["decisions"])
            if doc["kind"] == "HTTPRoute":
                seen.add(doc["metadata"]["labels"]["cicd.platform/cluster-id"])
            blob = json.dumps(doc)
            self.assertNotIn("prod-edge-offline", blob)
            self.assertNotIn("dev-cn-east-a", blob)
            self.assertNotIn("staging-cn-east-a", blob)
        self.assertEqual(seen, expected)


if __name__ == "__main__":
    unittest.main()
