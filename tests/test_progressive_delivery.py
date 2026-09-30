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
    check_pointer,
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
        multi = self.strategies["strategies"]["multi_cluster_canary"]
        self.assertEqual(multi["method"], "cluster_management")
        self.assertEqual(multi["traffic_strategy"], "canary")
        self.assertEqual(multi["region_order"], ["cn-east", "cn-north"])
        self.assertTrue(multi["pin_exact_cluster_ids"])
        self.assertEqual(
            [wave["strategy"] for wave in multi["waves"]],
            ["canary", "canary"],
        )
        self.assertEqual(
            [wave["selector"]["region"] for wave in multi["waves"]],
            ["cn-east", "cn-north"],
        )
        multi_blue = self.strategies["strategies"]["multi_cluster_blue_green"]
        self.assertEqual(multi_blue["method"], "cluster_management")
        self.assertEqual(multi_blue["traffic_strategy"], "blue_green")
        self.assertEqual(multi_blue["abort"], "restore_baseline_slot")
        self.assertEqual(multi_blue["region_order"], ["cn-east", "cn-north"])
        self.assertTrue(multi_blue["pin_exact_cluster_ids"])
        self.assertEqual(
            [wave["strategy"] for wave in multi_blue["waves"]],
            ["blue_green", "blue_green"],
        )
        self.assertNotIn("multi_cluster", self.strategies["strategies"])
        weights = [step["canary_weight"] for step in self.strategies["strategies"]["canary"]["steps"]]
        self.assertEqual(weights, [1, 5, 25, 50, 100])

    def test_policy_rejects_loose_cluster_selection(self) -> None:
        broken = copy.deepcopy(self.strategies)
        broken["strategies"]["multi_cluster_canary"]["pin_exact_cluster_ids"] = False
        errors = validate_release_policy(broken, self.clusters, self.promotion)
        self.assertTrue(any("pin_exact_cluster_ids" in item for item in errors))

        broken_blue = copy.deepcopy(self.strategies)
        broken_blue["strategies"]["multi_cluster_blue_green"]["pin_exact_cluster_ids"] = False
        errors = validate_release_policy(broken_blue, self.clusters, self.promotion)
        self.assertTrue(any("multi_cluster_blue_green" in item and "pin_exact_cluster_ids" in item for item in errors))

    def test_policy_rejects_blue_green_regions_and_a_single_region(self) -> None:
        blue_region = copy.deepcopy(self.strategies)
        blue_region["strategies"]["multi_cluster_canary"]["waves"][1]["strategy"] = "blue_green"
        errors = validate_release_policy(blue_region, self.clusters, self.promotion)
        self.assertTrue(any("must be canary" in item for item in errors))

        one_region = copy.deepcopy(self.strategies)
        body = one_region["strategies"]["multi_cluster_canary"]
        body["region_order"] = ["cn-east"]
        body["waves"] = [body["waves"][0]]
        errors = validate_release_policy(one_region, self.clusters, self.promotion)
        self.assertTrue(any("more than one region" in item or "at least two regions" in item for item in errors))

        canary_region = copy.deepcopy(self.strategies)
        canary_region["strategies"]["multi_cluster_blue_green"]["waves"][1]["strategy"] = "canary"
        errors = validate_release_policy(canary_region, self.clusters, self.promotion)
        self.assertTrue(any("must be blue_green" in item for item in errors))

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
        self._assert_istio_matches_http_route(documents)
        preview_services = [doc for name, doc in documents if name.endswith("-preview-vs.json")]
        self.assertEqual(len(preview_services), len(preview_routes))
        preview_match = preview_services[0]["spec"]["http"][0]
        self.assertEqual(preview_match["match"][0]["headers"]["x-release-preview"]["exact"], "true")
        self.assertEqual(preview_match["route"][0]["weight"], 100)
        self.assertEqual(preview_match["route"][0]["destination"]["host"], "checkout-green")
        production_service = next(
            doc
            for name, doc in documents
            if name.endswith("-vs.json") and not name.endswith("-preview-vs.json") and doc["metadata"]["annotations"]["cicd.platform/cluster"] == "prod-cn-east-a"
        )
        self.assertNotIn("match", production_service["spec"]["http"][0])
        self.assertEqual(production_service["spec"]["http"][0]["route"][0]["weight"], 100)
        self.assertEqual(production_service["spec"]["http"][0]["route"][0]["destination"]["host"], "checkout-blue")

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
        cut_documents = render_documents(state, self.strategies, "operator")
        virtual = next(
            doc
            for _, doc in cut_documents
            if doc["kind"] == "VirtualService" and doc["metadata"]["annotations"]["cicd.platform/cluster"] == "prod-cn-east-b"
        )
        self.assertEqual(virtual["spec"]["http"], [
            {"route": [{"destination": {"host": "checkout-green", "port": {"number": 80}}, "weight": 100}]}
        ])
        self.assertEqual(virtual["metadata"]["annotations"]["cicd.platform/cluster"], "prod-cn-east-b")
        self.assertIn("prod-cn-east-b", virtual["metadata"]["annotations"]["cicd.platform/clusters"].split(","))

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

    def test_environment_canary_moves_every_region_to_the_same_weight(self) -> None:
        state = self._plan("canary", accept_excluded=True)
        state = advance_release(state, self._pass(), self.strategies)
        projected = project_release(state, self.clusters)
        self.assertEqual(projected["prod-cn-east-a"]["canary_weight"], 1)
        self.assertEqual(projected["prod-cn-north-a"]["canary_weight"], 1)
        self.assertEqual(state["region_order"], [])
        documents = render_documents(state, self.strategies, "operator")
        self._assert_istio_matches_http_route(documents)
        self._assert_route_clusters_match_pin(documents)
        for cluster_id in ("prod-cn-east-a", "prod-cn-north-a"):
            route = next(
                doc
                for _, doc in documents
                if doc["kind"] == "HTTPRoute" and doc["metadata"]["labels"]["cicd.platform/cluster-id"] == cluster_id
            )
            service = next(
                doc
                for _, doc in documents
                if doc["kind"] == "VirtualService" and doc["metadata"]["annotations"]["cicd.platform/cluster"] == cluster_id
            )
            self.assertEqual(
                [backend["weight"] for backend in route["spec"]["rules"][0]["backendRefs"]],
                [99, 1],
            )
            self.assertEqual(
                [item["weight"] for item in service["spec"]["http"][0]["route"]],
                [99, 1],
            )

    def test_cross_region_canary_shares_weight_inside_a_region_and_holds_the_next_region(self) -> None:
        east = ["prod-cn-east-a", "prod-cn-east-b", "prod-cn-east-canary"]
        state = self._plan("multi_cluster_canary")
        self.assertEqual(state["strategy"], "multi_cluster_canary")
        self.assertEqual(state["method"], "cluster_management")
        self.assertEqual(state["region_order"], ["cn-east", "cn-north"])
        self.assertEqual(state["waves"][0]["strategy"], "canary")
        self.assertEqual(state["waves"][0]["region"], "cn-east")
        self.assertEqual(state["waves"][0]["cluster_ids"], east)
        self.assertEqual(state["waves"][1]["strategy"], "canary")
        self.assertEqual(state["waves"][1]["region"], "cn-north")
        self.assertEqual(state["waves"][1]["cluster_ids"], ["prod-cn-north-a"])
        self.assertFalse(state["waves"][1]["started"])
        self.assertIn({"id": "prod-edge-offline", "reason": "not_in_wave"}, state["unselected"])
        self.assertIn({"id": "dev-cn-east-a", "reason": "not_in_wave"}, state["unselected"])
        self.assertIn({"id": "staging-cn-east-a", "reason": "not_in_wave"}, state["unselected"])

        opened = project_release(state, self.clusters)
        for cluster_id in east:
            self.assertEqual(opened[cluster_id]["canary_weight"], 0, cluster_id)
            self.assertEqual(opened[cluster_id]["serving_digests"], [BASELINE], cluster_id)
            self.assertTrue(opened[cluster_id]["targeted"], cluster_id)
        self.assertFalse(opened["prod-cn-north-a"]["targeted"])
        self.assertEqual(opened["prod-cn-north-a"]["canary_weight"], 0)
        self.assertEqual(opened["prod-cn-north-a"]["serving_digests"], [BASELINE])
        self.assertEqual(opened["prod-edge-offline"]["serving_digests"], [BASELINE])
        self.assertFalse(opened["prod-edge-offline"]["targeted"])

        initial = render_documents(state, self.strategies, "unverified")
        self._assert_exact_clusters(initial, set(east))
        self._assert_route_clusters_match_pin(initial)
        pinned = self._pins(initial)
        self.assertEqual(pinned[0]["spec"]["clusterIds"], east)
        self.assertEqual(pinned[0]["spec"]["region"], "cn-east")
        self.assertEqual(pinned[0]["spec"]["method"], "cluster_management")
        self.assertEqual(pinned[0]["spec"]["trafficStrategy"], "canary")
        decisions = [doc for _, doc in initial if doc["kind"] == "PlacementDecision"]
        self.assertEqual(
            decisions[0]["status"]["decisions"],
            [{"clusterName": cluster_id} for cluster_id in east],
        )
        appsets = [doc for _, doc in initial if doc["kind"] == "ApplicationSet"]
        self.assertEqual(list(appsets[0]["spec"]["generators"][0]), ["list"])
        self.assertNotIn("clusters", appsets[0]["spec"]["generators"][0])
        elements = appsets[0]["spec"]["generators"][0]["list"]["elements"]
        self.assertEqual([item["cluster"] for item in elements], east)
        self.assertTrue(all(item["stableDigest"] == BASELINE for item in elements))
        self.assertTrue(all(item["canaryDigest"] == CANDIDATE for item in elements))
        self.assertTrue(all(item["canaryWeight"] == "0" for item in elements))
        self.assertNotIn("prod-cn-north-a", json.dumps([doc for _, doc in initial]))

        untouched = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, "error_rate exceeds"):
            advance_release(state, {"smoke": "pass", "error_rate": 0.5, "latency_p95_ms": 10, "requests": 100}, self.strategies)
        self.assertEqual(state, untouched)

        state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["step_name"], "1pct")
        self.assertEqual(state["waves"][0]["canary_weight"], 1)
        self.assertEqual(state["waves"][1]["canary_weight"], 0)
        self.assertFalse(state["waves"][1]["started"])
        at_one = project_release(state, self.clusters)
        self.assertEqual({at_one[cluster_id]["canary_weight"] for cluster_id in east}, {1})
        self.assertEqual({at_one[cluster_id]["stable_digest"] for cluster_id in east}, {BASELINE})
        self.assertEqual({at_one[cluster_id]["canary_digest"] for cluster_id in east}, {CANDIDATE})
        self.assertEqual(at_one["prod-cn-north-a"]["canary_weight"], 0)
        self.assertEqual(at_one["prod-cn-north-a"]["serving_digests"], [BASELINE])
        stepped = render_documents(state, self.strategies, "operator")
        self._assert_exact_clusters(stepped, set(east))
        self._assert_route_clusters_match_pin(stepped)
        east_weights = []
        istio_weights = []
        for _, doc in stepped:
            if doc["kind"] == "HTTPRoute":
                weights = [backend["weight"] for backend in doc["spec"]["rules"][0]["backendRefs"]]
                east_weights.append(weights)
                self.assertEqual(sum(weights), 100)
            if doc["kind"] == "VirtualService":
                weights = [item["weight"] for item in doc["spec"]["http"][0]["route"]]
                istio_weights.append(weights)
                self.assertEqual(sum(weights), 100)
                self.assertEqual(doc["metadata"]["annotations"]["cicd.platform/adapter"], "istio")
                cluster_id = doc["metadata"]["annotations"]["cicd.platform/cluster"]
                self.assertIn(cluster_id, east)
                self.assertNotIn("prod-cn-north-a", doc["metadata"]["annotations"]["cicd.platform/clusters"])
        self.assertEqual(east_weights, [[99, 1], [99, 1], [99, 1]])
        self.assertEqual(istio_weights, east_weights)

        for _ in range(2):
            state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["step_name"], "25pct")
        at_twenty_five = project_release(state, self.clusters)
        self.assertEqual({at_twenty_five[cluster_id]["canary_weight"] for cluster_id in east}, {25})
        self.assertEqual(at_twenty_five["prod-cn-north-a"]["canary_weight"], 0)
        self.assertNotIn(
            "prod-cn-north-a",
            json.dumps([doc for _, doc in render_documents(state, self.strategies, "operator")]),
        )

        for _ in range(2):
            state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["status"], "completed")
        self.assertEqual(state["waves"][0]["stable_digest"], CANDIDATE)
        self.assertEqual(state["waves"][0]["canary_weight"], 0)
        self.assertTrue(state["waves"][1]["started"])
        self.assertEqual(state["waves"][1]["strategy"], "canary")
        self.assertEqual(state["waves"][1]["canary_weight"], 0)
        self.assertEqual(state["waves"][1]["canary_digest"], CANDIDATE)
        self.assertNotEqual(state["waves"][1]["strategy"], "blue_green")
        opened_north = project_release(state, self.clusters)
        self.assertEqual(opened_north["prod-cn-east-a"]["serving_digests"], [CANDIDATE])
        self.assertEqual(opened_north["prod-cn-north-a"]["canary_weight"], 0)
        self.assertEqual(opened_north["prod-cn-north-a"]["stable_digest"], BASELINE)
        self.assertTrue(opened_north["prod-cn-north-a"]["targeted"])

        state = advance_release(state, self._pass(), self.strategies)
        state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][1]["step_name"], "5pct")
        split = project_release(state, self.clusters)
        self.assertTrue(all(split[cluster_id]["serving_digests"] == [CANDIDATE] for cluster_id in east))
        self.assertEqual(split["prod-cn-east-b"]["canary_weight"], 0)
        self.assertEqual(split["prod-cn-north-a"]["canary_weight"], 5)
        self.assertEqual(split["prod-cn-north-a"]["stable_digest"], BASELINE)
        self.assertEqual(split["prod-cn-north-a"]["canary_digest"], CANDIDATE)
        both = render_documents(state, self.strategies, "operator")
        self._assert_exact_clusters(both, set(east + ["prod-cn-north-a"]))
        self._assert_route_clusters_match_pin(both)
        north_route = next(
            doc
            for _, doc in both
            if doc["kind"] == "HTTPRoute" and doc["metadata"]["labels"]["cicd.platform/cluster-id"] == "prod-cn-north-a"
        )
        self.assertEqual(
            [backend["weight"] for backend in north_route["spec"]["rules"][0]["backendRefs"]],
            [95, 5],
        )
        north_service = next(
            doc
            for _, doc in both
            if doc["kind"] == "VirtualService" and doc["metadata"]["annotations"]["cicd.platform/cluster"] == "prod-cn-north-a"
        )
        self.assertEqual(
            [item["weight"] for item in north_service["spec"]["http"][0]["route"]],
            [95, 5],
        )
        self.assertEqual(sum(item["weight"] for item in north_service["spec"]["http"][0]["route"]), 100)
        self.assertIn("prod-cn-north-a", north_service["metadata"]["annotations"]["cicd.platform/clusters"].split(","))
        east_service = next(
            doc
            for _, doc in both
            if doc["kind"] == "VirtualService" and doc["metadata"]["annotations"]["cicd.platform/cluster"] == "prod-cn-east-a"
        )
        self.assertEqual(
            [item["weight"] for item in east_service["spec"]["http"][0]["route"]],
            [100],
        )
        self.assertNotIn("prod-cn-east-a", north_service["metadata"]["annotations"]["cicd.platform/clusters"])
        self.assertEqual(split["prod-edge-offline"]["serving_digests"], [BASELINE])
        self.assertEqual(split["dev-cn-east-a"]["serving_digests"], [BASELINE])

        held = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, "error_rate exceeds"):
            advance_release(
                state,
                {"error_rate": 0.2, "latency_p95_ms": 10, "requests": 100},
                self.strategies,
            )
        self.assertEqual(state["waves"][1]["canary_weight"], held["waves"][1]["canary_weight"])
        self.assertEqual(state, held)

        final = run_scenario(
            strategies=self.strategies,
            clusters=self.clusters,
            promotion=self.promotion,
            strategy="multi_cluster_canary",
            environment="production",
            service="checkout",
            identity=_identity(),
            baseline_digest=BASELINE,
            environment_pointer_digest=CANDIDATE,
        )
        self.assertEqual(
            [item["step"] for item in final["history"] if item["action"] == "advance"],
            ["1pct", "5pct", "25pct", "50pct", "100pct", "1pct", "5pct", "25pct", "50pct", "100pct"],
        )
        self.assertIn({"action": "open_wave", "wave": "cn-north"}, final["history"])
        projected = project_release(final, self.clusters)
        for cluster_id in east + ["prod-cn-north-a"]:
            self.assertEqual(projected[cluster_id]["serving_digests"], [CANDIDATE], cluster_id)
            self.assertEqual(projected[cluster_id]["canary_weight"], 0, cluster_id)
        for cluster_id in ("prod-edge-offline", "dev-cn-east-a", "staging-cn-east-a"):
            self.assertEqual(projected[cluster_id]["serving_digests"], [BASELINE], cluster_id)
            self.assertFalse(projected[cluster_id]["targeted"], cluster_id)
        rendered = render_documents(final, self.strategies, "synthetic")
        self._assert_exact_clusters(rendered, set(east + ["prod-cn-north-a"]))
        self.assertTrue(all(doc["metadata"]["annotations"]["cicd.platform/evidence"] == "synthetic" for _, doc in rendered))

    def test_allowlist_cannot_add_clusters_and_denylist_cannot_leave_an_empty_wave(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty wave"):
            self._plan("multi_cluster_canary", allow=["prod-cn-north-a"])
        with self.assertRaisesRegex(ValueError, "lack gateway"):
            self._plan("multi_cluster_canary", allow=["prod-edge-offline"])
        with self.assertRaisesRegex(ValueError, "lack gateway"):
            self._plan(
                "multi_cluster_canary",
                allow=[
                    "prod-cn-east-canary",
                    "prod-cn-east-a",
                    "prod-cn-east-b",
                    "prod-cn-north-a",
                    "prod-edge-offline",
                ],
            )
        with self.assertRaisesRegex(ValueError, "empty wave"):
            self._plan(
                "multi_cluster_canary",
                deny=["prod-cn-east-canary", "prod-cn-east-a", "prod-cn-east-b"],
            )
        with self.assertRaisesRegex(ValueError, "outside the target environment"):
            self._plan("canary", allow=["dev-cn-east-a"], accept_excluded=True)
        with self.assertRaisesRegex(ValueError, "environment pointer"):
            plan_release(
                strategies=self.strategies,
                clusters=self.clusters,
                promotion=self.promotion,
                strategy="multi_cluster_canary",
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
                strategy="multi_cluster_canary",
                environment="production",
                service="checkout",
                identity=_identity(BASELINE),
                baseline_digest=BASELINE,
                environment_pointer_digest=BASELINE,
            )

    def test_abort_returns_every_started_region_to_baseline(self) -> None:
        east = ["prod-cn-east-a", "prod-cn-east-b", "prod-cn-east-canary"]
        state = self._plan("multi_cluster_canary")
        for _ in range(3):
            state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["canary_weight"], 25)
        self.assertFalse(state["waves"][1]["started"])
        aborted = abort_release(state)
        projected = project_release(aborted, self.clusters)
        for cluster_id, view in projected.items():
            self.assertEqual(view["serving_digests"], [BASELINE], cluster_id)
            self.assertEqual(view["canary_weight"], 0, cluster_id)
        self.assertNotIn("prod-cn-north-a", json.dumps([doc for _, doc in render_documents(aborted, self.strategies, "operator")]))
        self.assertNotIn("prod-edge-offline", json.dumps([doc for _, doc in render_documents(aborted, self.strategies, "operator")]))

        state = self._plan("multi_cluster_canary")
        for _ in range(7):
            state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["status"], "completed")
        self.assertEqual(state["waves"][0]["stable_digest"], CANDIDATE)
        self.assertEqual(state["waves"][1]["step_name"], "5pct")
        self.assertEqual(project_release(state, self.clusters)["prod-cn-north-a"]["canary_weight"], 5)
        aborted = abort_release(state)
        projected = project_release(aborted, self.clusters)
        for cluster_id in east + ["prod-cn-north-a", "prod-edge-offline", "dev-cn-east-a"]:
            self.assertEqual(projected[cluster_id]["serving_digests"], [BASELINE], cluster_id)
            self.assertEqual(projected[cluster_id]["canary_weight"], 0, cluster_id)
        for wave in aborted["waves"]:
            self.assertEqual(wave["stable_digest"], BASELINE)
            self.assertEqual(wave["canary_digest"], "")
            self.assertEqual(wave["status"], "aborted")

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
        jenkins = (ROOT / "ops" / "Jenkinsfile").read_text(encoding="utf-8")
        for text in (readme, guide):
            self.assertIn("HTTPRoute", text)
            self.assertIn("VirtualService", text)
            self.assertIn("PlacementDecision", text)
            self.assertIn("ApplicationSet", text)
            self.assertIn("multi_cluster_canary", text)
            self.assertIn("multi_cluster_blue_green", text)
            self.assertIn("cn-east", text)
            self.assertIn("cn-north", text)
            self.assertIn("ops/Jenkinsfile", text)
            self.assertIn("不是第二份策略", text)
        self.assertNotIn("kubectl", jenkins)
        self.assertNotIn("kubeconfig", jenkins.lower())
        self.assertNotIn("prod-cn-", jenkins)
        self.assertNotIn("canary_weight", jenkins)
        for command in ("validate", "plan", "advance", "render"):
            self.assertIn(command, jenkins)
        self.assertIn("PLATFORM_SHA", jenkins)
        self.assertIn("scripts/ci/release_strategy.py", jenkins)
        self.assertIn("advance", workflow)
        self.assertIn("abort", workflow)
        self.assertIn("release_strategy.py advance", workflow)
        self.assertIn("release_strategy.py abort", workflow)
        self.assertIn("VirtualService", workflow)
        self.assertIn("check-pointer", workflow)
        self.assertIn("inputs.analysis", workflow)
        self.assertIn("inputs.state", workflow)
        self.assertIn("smoke", workflow)
        self.assertIn("readiness", workflow)
        self.assertIn("error_rate", workflow)
        self.assertIn("latency_p95_ms", workflow)
        self.assertIn("requests", workflow)
        self.assertNotIn("kubectl", workflow)
        self.assertNotIn("kubectl apply", workflow)
        self.assertNotIn("kubeconfig", workflow.lower())
        self.assertNotIn('"smoke": "pass"', workflow)
        self.assertLess(workflow.index("check-pointer"), workflow.index("release_strategy.py advance"))
        self.assertLess(workflow.index("check-pointer"), workflow.index("release_strategy.py abort"))
        self.assertLess(workflow.index("release_strategy.py advance"), workflow.index("VirtualService"))
        self.assertIn("后开区域仍然按 canary 权重推进，不会改成蓝绿", guide)
        self.assertIn("预览 header 不改变生产权重", guide)
        self.assertIn("--strategy multi_cluster_canary", guide)
        self.assertIn("--strategy multi_cluster_blue_green", guide)
        strategy_options = workflow.split("strategy:", 1)[1].split("environment:", 1)[0]
        for name in ("canary", "blue_green", "multi_cluster_canary", "multi_cluster_blue_green"):
            self.assertIn(f"- {name}", strategy_options)
        self.assertNotIn("canary-clusters", guide)
        self.assertNotIn("production-gateway", guide)
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

    def test_environment_blue_green_plan_renders_baseline_and_switches_every_region_together(self) -> None:
        state = self._plan("blue_green", accept_excluded=True)
        self.assertEqual(state["region_order"], [])
        self.assertEqual(len(state["waves"]), 1)
        self.assertTrue(state["waves"][0]["started"])
        self.assertEqual(state["waves"][0]["active_slot"], "blue")
        self.assertEqual(state["waves"][0]["slot_digests"]["green"], "")
        self.assertIn("prod-cn-north-a", state["waves"][0]["cluster_ids"])
        self.assertIn("prod-cn-east-a", state["waves"][0]["cluster_ids"])
        documents = render_documents(state, self.strategies, "unverified")
        self.assertTrue(documents)
        self.assertTrue(all(doc["metadata"]["annotations"]["cicd.platform/evidence"] == "unverified" for _, doc in documents))
        self.assertFalse(any(name.endswith("-preview.json") for name, _ in documents))
        self.assertIn("prod-cn-north-a", json.dumps([doc for _, doc in documents]))
        for _, doc in documents:
            if doc["kind"] == "HTTPRoute":
                self.assertEqual(
                    doc["spec"]["rules"][0]["backendRefs"],
                    [{"name": "checkout-blue", "port": 80, "weight": 100}],
                )
            if doc["kind"] == "VirtualService":
                self.assertEqual(
                    doc["spec"]["http"][0]["route"],
                    [{"destination": {"host": "checkout-blue", "port": {"number": 80}}, "weight": 100}],
                )
                self.assertNotIn("match", doc["spec"]["http"][0])
                cluster_id = doc["metadata"]["annotations"]["cicd.platform/cluster"]
                self.assertIn(cluster_id, state["waves"][0]["cluster_ids"])
                self.assertIn("prod-cn-north-a", doc["metadata"]["annotations"]["cicd.platform/clusters"].split(","))
                self.assertIn("prod-cn-east-a", doc["metadata"]["annotations"]["cicd.platform/clusters"].split(","))

        for _ in range(3):
            state = advance_release(state, self._pass(), self.strategies)
        projected = project_release(state, self.clusters)
        self.assertEqual(projected["prod-cn-east-a"]["active_slot"], "green")
        self.assertEqual(projected["prod-cn-north-a"]["active_slot"], "green")
        self.assertEqual(projected["prod-cn-east-a"]["serving_digests"], [CANDIDATE])
        self.assertEqual(projected["prod-cn-north-a"]["serving_digests"], [CANDIDATE])
        self.assertEqual(projected["prod-cn-east-a"]["canary_weight"], 0)
        self.assertEqual(projected["prod-edge-offline"]["serving_digests"], [BASELINE])

    def test_cross_region_blue_green_shares_a_slot_and_holds_the_next_region(self) -> None:
        east = ["prod-cn-east-a", "prod-cn-east-b", "prod-cn-east-canary"]
        state = self._plan("multi_cluster_blue_green")
        self.assertEqual(state["strategy"], "multi_cluster_blue_green")
        self.assertEqual(state["method"], "cluster_management")
        self.assertEqual(state["region_order"], ["cn-east", "cn-north"])
        self.assertEqual(state["waves"][0]["strategy"], "blue_green")
        self.assertEqual(state["waves"][0]["region"], "cn-east")
        self.assertEqual(state["waves"][0]["cluster_ids"], east)
        self.assertTrue(state["waves"][0]["started"])
        self.assertEqual(state["waves"][0]["active_slot"], "blue")
        self.assertEqual(state["waves"][0]["slot_digests"]["blue"], BASELINE)
        self.assertEqual(state["waves"][0]["slot_digests"]["green"], "")
        self.assertEqual(state["waves"][0]["canary_weight"], 0)
        self.assertEqual(state["waves"][1]["strategy"], "blue_green")
        self.assertEqual(state["waves"][1]["region"], "cn-north")
        self.assertEqual(state["waves"][1]["cluster_ids"], ["prod-cn-north-a"])
        self.assertFalse(state["waves"][1]["started"])
        self.assertIn({"id": "prod-edge-offline", "reason": "not_in_wave"}, state["unselected"])

        opened = project_release(state, self.clusters)
        for cluster_id in east:
            self.assertTrue(opened[cluster_id]["targeted"], cluster_id)
            self.assertEqual(opened[cluster_id]["serving_digests"], [BASELINE], cluster_id)
            self.assertEqual(opened[cluster_id]["active_slot"], "blue", cluster_id)
            self.assertEqual(opened[cluster_id]["preview_digest"], "", cluster_id)
            self.assertEqual(opened[cluster_id]["canary_weight"], 0, cluster_id)
        self.assertFalse(opened["prod-cn-north-a"]["targeted"])
        self.assertEqual(opened["prod-cn-north-a"]["serving_digests"], [BASELINE])
        self.assertEqual(opened["prod-edge-offline"]["serving_digests"], [BASELINE])

        initial = render_documents(state, self.strategies, "unverified")
        self._assert_exact_clusters(initial, set(east))
        self._assert_route_clusters_match_pin(initial)
        self.assertTrue(all(doc["metadata"]["annotations"]["cicd.platform/evidence"] == "unverified" for _, doc in initial))
        pinned = self._pins(initial)
        self.assertEqual(pinned[0]["spec"]["clusterIds"], east)
        self.assertEqual(pinned[0]["spec"]["region"], "cn-east")
        self.assertEqual(pinned[0]["spec"]["trafficStrategy"], "blue_green")
        self.assertEqual(pinned[0]["spec"]["method"], "cluster_management")
        elements = [doc for _, doc in initial if doc["kind"] == "ApplicationSet"][0]["spec"]["generators"][0]["list"]["elements"]
        self.assertEqual([item["cluster"] for item in elements], east)
        self.assertTrue(all(item["activeSlot"] == "blue" for item in elements))
        self.assertTrue(all(item["blueDigest"] == BASELINE for item in elements))
        self.assertTrue(all(item["greenDigest"] == "" for item in elements))
        self.assertTrue(all(item["canaryWeight"] == "0" for item in elements))
        self.assertTrue(all(item["previewDigest"] == "" for item in elements))
        self.assertFalse(any(name.endswith("-preview.json") for name, _ in initial))
        self._assert_single_slot_routes(initial, "checkout-blue")
        self.assertNotIn("prod-cn-north-a", json.dumps([doc for _, doc in initial]))

        untouched = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, "readiness must be pass"):
            advance_release(state, {"readiness": "fail"}, self.strategies)
        self.assertEqual(state, untouched)

        state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["step_name"], "deploy_inactive")
        self.assertEqual(state["waves"][0]["slot_digests"]["green"], CANDIDATE)
        self.assertEqual(state["waves"][0]["active_slot"], "blue")
        self.assertFalse(state["waves"][1]["started"])
        held = project_release(state, self.clusters)
        self.assertTrue(all(held[cluster_id]["serving_digests"] == [BASELINE] for cluster_id in east))
        self.assertEqual(held["prod-cn-north-a"]["serving_digests"], [BASELINE])
        self.assertFalse(held["prod-cn-north-a"]["targeted"])

        state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["step_name"], "preview")
        preview = project_release(state, self.clusters)
        self.assertTrue(all(preview[cluster_id]["serving_digests"] == [BASELINE] for cluster_id in east))
        self.assertEqual({preview[cluster_id]["preview_digest"] for cluster_id in east}, {CANDIDATE})
        self.assertEqual({preview[cluster_id]["active_slot"] for cluster_id in east}, {"blue"})
        self.assertEqual({preview[cluster_id]["canary_weight"] for cluster_id in east}, {0})
        self.assertEqual(preview["prod-cn-north-a"]["serving_digests"], [BASELINE])
        self.assertFalse(preview["prod-cn-north-a"]["targeted"])
        preview_docs = render_documents(state, self.strategies, "operator")
        self._assert_exact_clusters(preview_docs, set(east))
        self._assert_route_clusters_match_pin(preview_docs)
        self._assert_single_slot_routes(preview_docs, "checkout-blue")
        preview_routes = [doc for name, doc in preview_docs if name.endswith("-preview.json")]
        self.assertEqual(len(preview_routes), len(east))
        for route in preview_routes:
            rule = route["spec"]["rules"][0]
            self.assertEqual(rule["matches"][0]["headers"][0]["name"], "x-release-preview")
            self.assertEqual(rule["matches"][0]["headers"][0]["value"], "true")
            self.assertEqual(rule["backendRefs"], [{"name": "checkout-green", "port": 80, "weight": 100}])
            self.assertNotIn("matches", [doc for _, doc in preview_docs if doc["kind"] == "HTTPRoute" and not doc["metadata"]["name"].endswith("-preview")][0]["spec"]["rules"][0])
        preview_services = [doc for name, doc in preview_docs if name.endswith("-preview-vs.json")]
        self.assertEqual(len(preview_services), len(east))
        for service in preview_services:
            http = service["spec"]["http"]
            self.assertEqual(len(http), 1)
            self.assertEqual(http[0]["match"][0]["headers"]["x-release-preview"]["exact"], "true")
            self.assertEqual(http[0]["route"], [
                {"destination": {"host": "checkout-green", "port": {"number": 80}}, "weight": 100}
            ])
            cluster_id = service["metadata"]["annotations"]["cicd.platform/cluster"]
            self.assertIn(cluster_id, east)
            self.assertNotIn("prod-cn-north-a", service["metadata"]["annotations"]["cicd.platform/clusters"])
        for name, doc in preview_docs:
            if doc["kind"] != "VirtualService" or name.endswith("-preview-vs.json"):
                continue
            self.assertNotIn("match", doc["spec"]["http"][0])
            self.assertEqual(doc["spec"]["http"][0]["route"][0]["destination"]["host"], "checkout-blue")
            self.assertEqual(doc["spec"]["http"][0]["route"][0]["weight"], 100)
        self.assertNotIn("prod-cn-north-a", json.dumps([doc for _, doc in preview_docs]))

        early = copy.deepcopy(state)
        aborted_early = abort_release(early)
        restored = project_release(aborted_early, self.clusters)
        for cluster_id in east:
            self.assertEqual(restored[cluster_id]["serving_digests"], [BASELINE], cluster_id)
            self.assertEqual(restored[cluster_id]["active_slot"], "blue", cluster_id)
            self.assertEqual(restored[cluster_id]["preview_digest"], "", cluster_id)
        self.assertEqual(aborted_early["waves"][0]["slot_digests"]["green"], "")
        self.assertFalse(aborted_early["waves"][1]["started"])
        self.assertNotIn(
            "prod-cn-north-a",
            json.dumps([doc for _, doc in render_documents(aborted_early, self.strategies, "operator")]),
        )
        self.assertFalse(
            any(name.endswith("-preview.json") for name, _ in render_documents(aborted_early, self.strategies, "operator"))
        )

        held_cut = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, "latency_p95 exceeds"):
            advance_release(
                state,
                {"error_rate": 0.0, "latency_p95_ms": 5000, "requests": 80},
                self.strategies,
            )
        self.assertEqual(state, held_cut)
        with self.assertRaisesRegex(ValueError, "requests below min_requests"):
            advance_release(state, {"error_rate": 0.0, "latency_p95_ms": 20, "requests": True}, self.strategies)
        self.assertEqual(state, held_cut)

        state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["step_name"], "cutover")
        cut = project_release(state, self.clusters)
        self.assertTrue(all(cut[cluster_id]["serving_digests"] == [CANDIDATE] for cluster_id in east))
        self.assertEqual({cut[cluster_id]["active_slot"] for cluster_id in east}, {"green"})
        self.assertEqual({cut[cluster_id]["preview_digest"] for cluster_id in east}, {""})
        self.assertEqual({cut[cluster_id]["canary_weight"] for cluster_id in east}, {0})
        self.assertFalse(cut["prod-cn-north-a"]["targeted"])
        self.assertEqual(cut["prod-cn-north-a"]["serving_digests"], [BASELINE])
        cut_docs = render_documents(state, self.strategies, "operator")
        self._assert_single_slot_routes(cut_docs, "checkout-green")
        self.assertFalse(any(name.endswith("-preview.json") for name, _ in cut_docs))
        self.assertNotIn("prod-cn-north-a", json.dumps([doc for _, doc in cut_docs]))

        state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["status"], "completed")
        self.assertEqual(state["waves"][0]["slot_digests"]["blue"], BASELINE)
        self.assertEqual(state["waves"][0]["slot_digests"]["green"], CANDIDATE)
        self.assertTrue(state["waves"][1]["started"])
        self.assertEqual(state["waves"][1]["strategy"], "blue_green")
        self.assertNotEqual(state["waves"][1]["strategy"], "canary")
        self.assertEqual(state["waves"][1]["active_slot"], "blue")
        self.assertEqual(state["waves"][1]["slot_digests"]["green"], "")
        self.assertEqual(state["waves"][1]["canary_weight"], 0)
        self.assertEqual(state["status"], "in_progress")
        opened_north = project_release(state, self.clusters)
        self.assertEqual(opened_north["prod-cn-east-a"]["serving_digests"], [CANDIDATE])
        self.assertEqual(opened_north["prod-cn-north-a"]["serving_digests"], [BASELINE])
        self.assertEqual(opened_north["prod-cn-north-a"]["active_slot"], "blue")
        self.assertTrue(opened_north["prod-cn-north-a"]["targeted"])

        state = advance_release(state, self._pass(), self.strategies)
        state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][1]["step_name"], "preview")
        split = project_release(state, self.clusters)
        self.assertTrue(all(split[cluster_id]["serving_digests"] == [CANDIDATE] for cluster_id in east))
        self.assertEqual(split["prod-cn-east-a"]["preview_digest"], "")
        self.assertEqual(split["prod-cn-north-a"]["serving_digests"], [BASELINE])
        self.assertEqual(split["prod-cn-north-a"]["preview_digest"], CANDIDATE)
        self.assertEqual(split["prod-cn-north-a"]["canary_weight"], 0)
        both = render_documents(state, self.strategies, "operator")
        self._assert_exact_clusters(both, set(east + ["prod-cn-north-a"]))
        self._assert_route_clusters_match_pin(both)
        north_routes = [
            doc
            for _, doc in both
            if doc["kind"] == "HTTPRoute" and doc["metadata"]["labels"]["cicd.platform/cluster-id"] == "prod-cn-north-a"
        ]
        self.assertEqual(len(north_routes), 2)
        production = next(doc for doc in north_routes if not doc["metadata"]["name"].endswith("-preview"))
        header = next(doc for doc in north_routes if doc["metadata"]["name"].endswith("-preview"))
        self.assertEqual(production["spec"]["rules"][0]["backendRefs"], [{"name": "checkout-blue", "port": 80, "weight": 100}])
        self.assertNotIn("matches", production["spec"]["rules"][0])
        self.assertEqual(header["spec"]["rules"][0]["backendRefs"], [{"name": "checkout-green", "port": 80, "weight": 100}])
        north_services = [
            doc
            for name, doc in both
            if doc["kind"] == "VirtualService" and doc["metadata"]["annotations"]["cicd.platform/cluster"] == "prod-cn-north-a"
        ]
        self.assertEqual(len(north_services), 2)
        production_service = next(doc for name, doc in both if name.endswith("prod-cn-north-a-vs.json"))
        preview_service = next(doc for name, doc in both if name.endswith("prod-cn-north-a-preview-vs.json"))
        self.assertEqual(production_service["spec"]["http"][0]["route"], [
            {"destination": {"host": "checkout-blue", "port": {"number": 80}}, "weight": 100}
        ])
        self.assertNotIn("match", production_service["spec"]["http"][0])
        self.assertEqual(
            preview_service["spec"]["http"][0]["match"][0]["headers"]["x-release-preview"]["exact"],
            "true",
        )
        self.assertEqual(preview_service["spec"]["http"][0]["route"][0]["destination"]["host"], "checkout-green")
        self.assertEqual(preview_service["spec"]["http"][0]["route"][0]["weight"], 100)
        self.assertEqual(production_service["metadata"]["annotations"]["cicd.platform/cluster"], "prod-cn-north-a")
        east_still = next(doc for name, doc in both if name.endswith("prod-cn-east-a-vs.json"))
        self.assertEqual(east_still["spec"]["http"][0]["route"][0]["destination"]["host"], "checkout-green")
        self.assertEqual(east_still["spec"]["http"][0]["route"][0]["weight"], 100)
        self.assertNotIn("prod-cn-east-a", production_service["metadata"]["annotations"]["cicd.platform/clusters"])
        self.assertEqual(split["prod-edge-offline"]["serving_digests"], [BASELINE])
        self.assertEqual(split["dev-cn-east-a"]["serving_digests"], [BASELINE])

        frozen = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, "error_rate exceeds"):
            advance_release(state, {"error_rate": 0.2, "latency_p95_ms": 10, "requests": 100}, self.strategies)
        self.assertEqual(state, frozen)

        aborted = abort_release(state)
        projected = project_release(aborted, self.clusters)
        for cluster_id in east + ["prod-cn-north-a", "prod-edge-offline", "dev-cn-east-a"]:
            self.assertEqual(projected[cluster_id]["serving_digests"], [BASELINE], cluster_id)
            self.assertEqual(projected[cluster_id]["canary_weight"], 0, cluster_id)
            self.assertEqual(projected[cluster_id]["preview_digest"], "", cluster_id)
        for wave in aborted["waves"]:
            self.assertEqual(wave["active_slot"], "blue")
            self.assertEqual(wave["slot_digests"]["blue"], BASELINE)
            self.assertEqual(wave["slot_digests"]["green"], "")
            self.assertEqual(wave["status"], "aborted")
        self.assertNotIn("prod-edge-offline", json.dumps([doc for _, doc in render_documents(aborted, self.strategies, "operator")]))
        with self.assertRaisesRegex(ValueError, "already aborted"):
            abort_release(aborted)

        final = run_scenario(
            strategies=self.strategies,
            clusters=self.clusters,
            promotion=self.promotion,
            strategy="multi_cluster_blue_green",
            environment="production",
            service="checkout",
            identity=_identity(),
            baseline_digest=BASELINE,
            environment_pointer_digest=CANDIDATE,
        )
        self.assertEqual(
            [item["step"] for item in final["history"] if item["action"] == "advance"],
            ["deploy_inactive", "preview", "cutover", "confirm", "deploy_inactive", "preview", "cutover", "confirm"],
        )
        self.assertNotIn("1pct", [item.get("step") for item in final["history"]])
        self.assertIn({"action": "open_wave", "wave": "cn-north"}, final["history"])
        self.assertEqual(final["status"], "completed")
        done = project_release(final, self.clusters)
        for cluster_id in east + ["prod-cn-north-a"]:
            self.assertEqual(done[cluster_id]["serving_digests"], [CANDIDATE], cluster_id)
            self.assertEqual(done[cluster_id]["active_slot"], "green", cluster_id)
            self.assertEqual(done[cluster_id]["canary_weight"], 0, cluster_id)
            self.assertEqual(done[cluster_id]["preview_digest"], "", cluster_id)
        self.assertEqual(final["waves"][0]["slot_digests"]["blue"], BASELINE)
        self.assertEqual(final["waves"][1]["slot_digests"]["blue"], BASELINE)
        for cluster_id in ("prod-edge-offline", "dev-cn-east-a", "staging-cn-east-a"):
            self.assertEqual(done[cluster_id]["serving_digests"], [BASELINE], cluster_id)
            self.assertFalse(done[cluster_id]["targeted"], cluster_id)
        rendered = render_documents(final, self.strategies, "synthetic")
        self._assert_exact_clusters(rendered, set(east + ["prod-cn-north-a"]))
        self._assert_single_slot_routes(rendered, "checkout-green")
        self.assertTrue(all(doc["metadata"]["annotations"]["cicd.platform/evidence"] == "synthetic" for _, doc in rendered))
        self.assertFalse(any(name.endswith("-preview.json") for name, _ in rendered))
        with self.assertRaisesRegex(ValueError, "environment rollback"):
            abort_release(final)

    def test_cross_region_blue_green_respects_the_baseline_slot(self) -> None:
        state = self._plan("multi_cluster_blue_green", active_slot="green")
        self.assertEqual(state["waves"][0]["baseline_slot"], "green")
        self.assertEqual(state["waves"][0]["slot_digests"]["green"], BASELINE)
        self.assertEqual(state["waves"][0]["slot_digests"]["blue"], "")
        state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["slot_digests"]["blue"], CANDIDATE)
        self.assertEqual(project_release(state, self.clusters)["prod-cn-east-a"]["serving_digests"], [BASELINE])
        state = advance_release(state, self._pass(), self.strategies)
        documents = render_documents(state, self.strategies, "operator")
        self._assert_single_slot_routes(documents, "checkout-green")
        preview_routes = [doc for name, doc in documents if name.endswith("-preview.json")]
        self.assertTrue(preview_routes)
        self.assertEqual(preview_routes[0]["spec"]["rules"][0]["backendRefs"][0]["name"], "checkout-blue")
        preview_services = [doc for name, doc in documents if name.endswith("-preview-vs.json")]
        self.assertTrue(preview_services)
        self.assertEqual(preview_services[0]["spec"]["http"][0]["route"][0]["destination"]["host"], "checkout-blue")
        self.assertEqual(preview_services[0]["spec"]["http"][0]["route"][0]["weight"], 100)
        self.assertEqual(
            preview_services[0]["spec"]["http"][0]["match"][0]["headers"]["x-release-preview"]["exact"],
            "true",
        )
        self.assertEqual(
            preview_services[0]["metadata"]["annotations"]["cicd.platform/cluster"],
            preview_services[0]["metadata"]["labels"]["cicd.platform/cluster-id"],
        )
        self.assertEqual(project_release(state, self.clusters)["prod-cn-east-b"]["serving_digests"], [BASELINE])
        state = advance_release(state, self._pass(), self.strategies)
        self.assertEqual(state["waves"][0]["active_slot"], "blue")
        self.assertEqual(project_release(state, self.clusters)["prod-cn-east-canary"]["serving_digests"], [CANDIDATE])
        self.assertFalse(project_release(state, self.clusters)["prod-cn-north-a"]["targeted"])
        self._assert_single_slot_routes(render_documents(state, self.strategies, "operator"), "checkout-blue")

    def test_cross_region_blue_green_fail_closed_selection_and_pointer(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty wave"):
            self._plan("multi_cluster_blue_green", allow=["prod-cn-north-a"])
        with self.assertRaisesRegex(ValueError, "lack gateway"):
            self._plan("multi_cluster_blue_green", allow=["prod-edge-offline"])
        with self.assertRaisesRegex(ValueError, "release environment is dev"):
            self._plan("multi_cluster_blue_green", environment="dev")
        with self.assertRaisesRegex(ValueError, "environment pointer"):
            plan_release(
                strategies=self.strategies,
                clusters=self.clusters,
                promotion=self.promotion,
                strategy="multi_cluster_blue_green",
                environment="production",
                service="checkout",
                identity=_identity(),
                baseline_digest=BASELINE,
                environment_pointer_digest="c" * 64,
            )
        with self.assertRaisesRegex(ValueError, "evidence_mode"):
            render_documents(self._plan("multi_cluster_blue_green"), self.strategies, "production")
        check_pointer(
            {"environment": "production", "bundle_sha256": CANDIDATE},
            bundle_sha256=CANDIDATE,
            environment="production",
        )
        with self.assertRaisesRegex(ValueError, "pointer bundle_sha256"):
            check_pointer(
                {"environment": "production", "bundle_sha256": "not-a-digest"},
                bundle_sha256=CANDIDATE,
                environment="production",
            )
        with self.assertRaisesRegex(ValueError, "does not match the release environment"):
            check_pointer(
                {"environment": "staging", "bundle_sha256": CANDIDATE},
                bundle_sha256=CANDIDATE,
                environment="production",
            )
        with self.assertRaisesRegex(ValueError, "does not match release candidate"):
            check_pointer(
                {"environment": "production", "bundle_sha256": BASELINE},
                bundle_sha256=CANDIDATE,
                environment="production",
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

    def _assert_route_clusters_match_pin(self, documents: list[tuple[str, dict]]) -> None:
        pins = [doc["spec"]["clusterIds"] for _, doc in documents if doc["kind"] == "ClusterPin"]
        self.assertTrue(pins)
        allowed = {cluster_id for pin in pins for cluster_id in pin}
        for _, doc in documents:
            if doc["kind"] == "PlacementDecision":
                decided = [item["clusterName"] for item in doc["status"]["decisions"]]
                self.assertIn(decided, pins)
            if doc["kind"] == "HTTPRoute":
                cluster_id = doc["metadata"]["labels"]["cicd.platform/cluster-id"]
                self.assertIn(cluster_id, allowed)
                self.assertEqual(doc["metadata"]["annotations"]["cicd.platform/cluster"], cluster_id)
            if doc["kind"] == "VirtualService":
                cluster_id = doc["metadata"]["annotations"]["cicd.platform/cluster"]
                self.assertEqual(doc["metadata"]["labels"]["cicd.platform/cluster-id"], cluster_id)
                self.assertIn(cluster_id, allowed)
                self.assertIn(cluster_id, doc["metadata"]["annotations"]["cicd.platform/clusters"].split(","))
            if doc["kind"] == "ApplicationSet":
                clusters = [item["cluster"] for item in doc["spec"]["generators"][0]["list"]["elements"]]
                self.assertIn(clusters, pins)
        self._assert_istio_matches_http_route(documents)

    def _assert_istio_matches_http_route(self, documents: list[tuple[str, dict]]) -> None:
        routes = {
            doc["metadata"]["annotations"]["cicd.platform/cluster"]: doc
            for name, doc in documents
            if doc["kind"] == "HTTPRoute" and not name.endswith("-preview.json")
        }
        services = {
            doc["metadata"]["annotations"]["cicd.platform/cluster"]: doc
            for name, doc in documents
            if doc["kind"] == "VirtualService" and not name.endswith("-preview-vs.json")
        }
        self.assertEqual(set(services), set(routes))
        allowed = {
            cluster_id
            for _, doc in documents
            if doc["kind"] == "ClusterPin"
            for cluster_id in doc["spec"]["clusterIds"]
        }
        for cluster_id, route in routes.items():
            self.assertIn(cluster_id, allowed)
            service = services[cluster_id]
            self.assertEqual(service["apiVersion"], "networking.istio.io/v1")
            self.assertEqual(service["metadata"]["annotations"]["cicd.platform/adapter"], "istio")
            self.assertEqual(service["metadata"]["annotations"]["cicd.platform/cluster"], cluster_id)
            http_weights = [backend["weight"] for backend in route["spec"]["rules"][0]["backendRefs"]]
            production = service["spec"]["http"]
            self.assertEqual(len(production), 1)
            self.assertNotIn("match", production[0])
            istio_weights = [item["weight"] for item in production[0]["route"]]
            self.assertEqual(istio_weights, http_weights)
            self.assertEqual(sum(istio_weights), 100)
            self.assertEqual(
                [item["destination"]["host"] for item in production[0]["route"]],
                [backend["name"] for backend in route["spec"]["rules"][0]["backendRefs"]],
            )

    def _assert_single_slot_routes(self, documents: list[tuple[str, dict]], backend: str) -> None:
        production = [
            doc
            for _, doc in documents
            if doc["kind"] == "HTTPRoute" and not doc["metadata"]["name"].endswith("-preview")
        ]
        self.assertTrue(production)
        for doc in production:
            backends = doc["spec"]["rules"][0]["backendRefs"]
            self.assertEqual(backends, [{"name": backend, "port": 80, "weight": 100}])
            self.assertNotIn("canary", backends[0]["name"])
        virtual = [
            doc
            for name, doc in documents
            if doc["kind"] == "VirtualService" and not name.endswith("-preview-vs.json")
        ]
        self.assertEqual(len(virtual), len(production))
        for doc in virtual:
            http = doc["spec"]["http"]
            self.assertEqual(len(http), 1)
            self.assertNotIn("match", http[0])
            self.assertEqual(
                http[0]["route"],
                [{"destination": {"host": backend, "port": {"number": 80}}, "weight": 100}],
            )
            self.assertEqual(
                doc["metadata"]["annotations"]["cicd.platform/cluster"],
                doc["metadata"]["labels"]["cicd.platform/cluster-id"],
            )

    def _assert_exact_clusters(self, documents: list[tuple[str, dict]], expected: set[str]) -> None:
        seen: set[str] = set()
        for _, doc in documents:
            if doc["kind"] == "ClusterPin":
                seen.update(doc["spec"]["clusterIds"])
            if doc["kind"] == "PlacementDecision":
                seen.update(item["clusterName"] for item in doc["status"]["decisions"])
            if doc["kind"] == "HTTPRoute":
                seen.add(doc["metadata"]["labels"]["cicd.platform/cluster-id"])
            if doc["kind"] == "VirtualService":
                seen.add(doc["metadata"]["annotations"]["cicd.platform/cluster"])
            blob = json.dumps(doc)
            self.assertNotIn("prod-edge-offline", blob)
            self.assertNotIn("dev-cn-east-a", blob)
            self.assertNotIn("staging-cn-east-a", blob)
        self.assertEqual(seen, expected)


if __name__ == "__main__":
    unittest.main()
