from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import argo_rollouts as ar  # noqa: E402
from promotion_policy import normalize_identity  # noqa: E402
from release_strategy import load_release_policy, plan_release  # noqa: E402

BASELINE = "a" * 64
CANDIDATE = "b" * 64
IMAGE = "ghcr.io/iwacollection/checkout@sha256:" + "e" * 64
GITOPS_URL = "https://git.example.com/platform/gitops.git"
EAST = ["prod-cn-east-a", "prod-cn-east-b", "prod-cn-east-canary"]
NORTH = ["prod-cn-north-a"]


def _identity() -> dict[str, str]:
    return normalize_identity(
        artifact_name="checkout-generic-linux-x86_64-gcc",
        bundle_sha256=CANDIDATE,
        source_sha="c" * 40,
        source_run_id="42",
        release_tag="artifact-v2-" + "d" * 64,
    )


class ArgoPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.strategies, self.clusters, self.promotion = load_release_policy(
            ROOT / "ci" / "release-strategies.json",
            ROOT / "ci" / "clusters.json",
            ROOT / "ci" / "promotion-policy.json",
        )
        self.argo = json.loads((ROOT / "ci" / "argo-rollouts.json").read_text(encoding="utf-8"))

    def test_repository_policy_is_valid(self) -> None:
        self.assertEqual(ar.validate_argo_policy(self.argo, self.strategies), [])

    def test_thresholds_and_steps_have_one_source(self) -> None:
        text = (ROOT / "ci" / "argo-rollouts.json").read_text(encoding="utf-8")
        for forbidden in ("error_rate_max", "latency_p95_max_ms", "min_requests", "canary_weight", "region_order"):
            self.assertNotIn(forbidden, text, forbidden)

    def test_rejects_checks_that_can_never_fail_or_never_pause(self) -> None:
        broken = copy.deepcopy(self.argo)
        broken["analysis"]["error_rate"]["failure_limit"] = broken["analysis"]["error_rate"]["count"]
        errors = ar.validate_argo_policy(broken, self.strategies)
        self.assertTrue(any("can never fail" in item for item in errors), errors)

        broken = copy.deepcopy(self.argo)
        broken["analysis"]["request_volume"]["inconclusive_limit"] = broken["analysis"]["request_volume"]["count"]
        errors = ar.validate_argo_policy(broken, self.strategies)
        self.assertTrue(any("inconclusive_limit must be lower" in item for item in errors), errors)

    def test_rejects_blue_green_rollback_window_shorter_than_post_analysis(self) -> None:
        broken = copy.deepcopy(self.argo)
        broken["blue_green"]["scale_down_delay_seconds"] = 100
        errors = ar.validate_argo_policy(broken, self.strategies)
        self.assertTrue(any("outlive post-promotion analysis" in item for item in errors), errors)

    def test_rejects_unscoped_queries_unknown_fields_and_bad_router(self) -> None:
        broken = copy.deepcopy(self.argo)
        broken["metrics"]["queries"]["error_rate"] = "sum(rate(http_requests_total[2m]))"
        errors = ar.validate_argo_policy(broken, self.strategies)
        self.assertTrue(any("scoped with {{args.host}}" in item for item in errors), errors)

        broken = copy.deepcopy(self.argo)
        broken["controller"]["traffic_router"] = "nginx"
        broken["controller"]["surprise"] = 1
        errors = ar.validate_argo_policy(broken, self.strategies)
        self.assertTrue(any("traffic_router" in item for item in errors), errors)
        self.assertTrue(any("unknown fields: surprise" in item for item in errors), errors)

        broken = copy.deepcopy(self.argo)
        broken["gitops"]["base_path"] = "../outside"
        errors = ar.validate_argo_policy(broken, self.strategies)
        self.assertTrue(any("base_path" in item for item in errors), errors)


class ArgoRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.strategies, self.clusters, self.promotion = load_release_policy(
            ROOT / "ci" / "release-strategies.json",
            ROOT / "ci" / "clusters.json",
            ROOT / "ci" / "promotion-policy.json",
        )
        self.argo = json.loads((ROOT / "ci" / "argo-rollouts.json").read_text(encoding="utf-8"))

    def _plan(self, strategy: str) -> dict:
        return plan_release(
            strategies=self.strategies,
            clusters=self.clusters,
            promotion=self.promotion,
            strategy=strategy,
            environment="production",
            service="checkout",
            identity=_identity(),
            baseline_digest=BASELINE,
            environment_pointer_digest=CANDIDATE,
            accept_excluded=strategy in ("canary", "blue_green"),
        )

    def _render(self, strategy: str, *, argo: dict | None = None, strategies: dict | None = None, gitops_url: str = GITOPS_URL):
        argo = argo or self.argo
        gitops = dict(argo["gitops"], repo_url=gitops_url)
        documents, summary = ar.render_documents(
            self._plan(strategy), strategies or self.strategies, argo, image=IMAGE, gitops=gitops
        )
        return dict(documents), summary

    def test_canary_steps_follow_the_shared_policy(self) -> None:
        documents, summary = self._render("canary")
        steps = documents["checkout-rollout.json"]["spec"]["strategy"]["canary"]["steps"]
        weights = [step["setWeight"] for step in steps if "setWeight" in step]
        self.assertEqual(weights, [1, 5, 25, 50, 100])
        policy_steps = self.strategies["strategies"]["canary"]["steps"]
        self.assertEqual(weights, [step["canary_weight"] for step in policy_steps if "canary_weight" in step])
        for index, step in enumerate(steps):
            if "setWeight" in step:
                self.assertIn("analysis", steps[index + 1], "every weight must be judged before the next one")
        self.assertIn("analysis", steps[-1])
        first = [item["templateName"] for item in steps[1]["analysis"]["templates"]]
        self.assertEqual(
            first,
            ["checkout-smoke", "checkout-error-rate", "checkout-latency-p95", "checkout-request-volume"],
        )
        last = [item["templateName"] for item in steps[-1]["analysis"]["templates"]]
        self.assertEqual(last, ["checkout-error-rate-confirm", "checkout-request-volume"])
        self.assertEqual([item["step"] for item in summary["steps"]], [s["name"] for s in policy_steps])

    def test_every_referenced_template_exists_for_every_strategy(self) -> None:
        for strategy in ("canary", "blue_green", "multi_cluster_canary", "multi_cluster_blue_green"):
            documents, _ = self._render(strategy)
            names = {doc["metadata"]["name"] for doc in documents.values() if doc["kind"] == "AnalysisTemplate"}
            block = documents["checkout-rollout.json"]["spec"]["strategy"]
            referenced: set[str] = set()
            if "canary" in block:
                for step in block["canary"]["steps"]:
                    for item in step.get("analysis", {}).get("templates", []):
                        referenced.add(item["templateName"])
            else:
                for key in ("prePromotionAnalysis", "postPromotionAnalysis"):
                    for item in block["blueGreen"].get(key, {}).get("templates", []):
                        referenced.add(item["templateName"])
            self.assertTrue(referenced, strategy)
            self.assertLessEqual(referenced, names, strategy)

    def test_conditions_come_from_the_policy_thresholds(self) -> None:
        documents, _ = self._render("canary")
        error = documents["checkout-error-rate-analysis.json"]["spec"]["metrics"][0]
        latency = documents["checkout-latency-p95-analysis.json"]["spec"]["metrics"][0]
        volume = documents["checkout-request-volume-analysis.json"]["spec"]["metrics"][0]
        self.assertIn("result[0] <= 0.01", error["successCondition"])
        self.assertIn("result[0] > 0.01", error["failureCondition"])
        self.assertIn("result[0] <= 300", latency["successCondition"])
        self.assertIn("result[0] >= 50", volume["successCondition"])

        changed = copy.deepcopy(self.strategies)
        thresholds = changed["strategies"]["canary"]["analysis_thresholds"]
        thresholds.update({"error_rate_max": 0.02, "latency_p95_max_ms": 450, "min_requests": 70})
        documents, _ = self._render("canary", strategies=changed)
        self.assertIn("result[0] <= 0.02", documents["checkout-error-rate-analysis.json"]["spec"]["metrics"][0]["successCondition"])
        self.assertIn("result[0] <= 450", documents["checkout-latency-p95-analysis.json"]["spec"]["metrics"][0]["successCondition"])
        self.assertIn("result[0] >= 70", documents["checkout-request-volume-analysis.json"]["spec"]["metrics"][0]["successCondition"])

    def test_silence_pauses_instead_of_aborting_or_passing(self) -> None:
        documents, _ = self._render("canary")
        error = documents["checkout-error-rate-analysis.json"]["spec"]["metrics"][0]
        self.assertIn("len(result) == 0", error["successCondition"])
        self.assertIn("isNaN(result[0])", error["successCondition"])
        self.assertTrue(error["failureCondition"].startswith("len(result) > 0"))
        volume = documents["checkout-request-volume-analysis.json"]["spec"]["metrics"][0]
        self.assertEqual(volume["failureCondition"], "len(result) > 1")
        self.assertEqual(volume["inconclusiveLimit"], self.argo["analysis"]["request_volume"]["inconclusive_limit"])
        self.assertIn("or vector(0)", volume["provider"]["prometheus"]["query"])
        for name in ("checkout-error-rate", "checkout-latency-p95", "checkout-request-volume"):
            metric = documents[f"{name}-analysis.json"]["spec"]["metrics"][0]
            self.assertEqual(metric["consecutiveErrorLimit"], 4, name)

    def test_one_metric_per_template_so_merged_runs_have_unique_names(self) -> None:
        for strategy in ("canary", "blue_green"):
            documents, _ = self._render(strategy)
            for doc in documents.values():
                if doc["kind"] == "AnalysisTemplate":
                    self.assertEqual(len(doc["spec"]["metrics"]), 1, doc["metadata"]["name"])
        documents, _ = self._render("canary")
        confirm = documents["checkout-error-rate-confirm-analysis.json"]["spec"]["metrics"][0]
        self.assertEqual(confirm["name"], "error-rate")
        self.assertEqual(confirm["count"], self.argo["analysis"]["confirm_error_rate"]["count"])

    def test_canary_router_abort_and_baseline_weights(self) -> None:
        documents, _ = self._render("canary")
        canary = documents["checkout-rollout.json"]["spec"]["strategy"]["canary"]
        self.assertEqual(canary["stableService"], "checkout-stable")
        self.assertEqual(canary["canaryService"], "checkout-canary")
        self.assertEqual(
            canary["trafficRouting"],
            {"plugins": {"argoproj-labs/gatewayAPI": {"httpRoute": "checkout-route", "namespace": "apps"}}},
        )
        self.assertEqual(canary["abortScaleDownDelaySeconds"], 30)
        route = documents["checkout-route.json"]
        self.assertEqual(route["kind"], "HTTPRoute")
        self.assertEqual(
            route["spec"]["rules"][0]["backendRefs"],
            [
                {"name": "checkout-stable", "port": 80, "weight": 100},
                {"name": "checkout-canary", "port": 80, "weight": 0},
            ],
        )
        self.assertEqual(route["spec"]["parentRefs"], [{"name": "platform-gateway", "namespace": "apps"}])
        smoke = documents["checkout-smoke-analysis.json"]
        self.assertEqual(smoke["spec"]["args"], [{"name": "host", "value": "checkout-canary.apps.svc.cluster.local"}])
        self.assertEqual(smoke["spec"]["metrics"][0]["provider"]["web"]["url"], "http://{{args.host}}:80/healthz")

    def test_blue_green_maps_checks_to_promotion_hooks(self) -> None:
        documents, summary = self._render("blue_green")
        block = documents["checkout-rollout.json"]["spec"]["strategy"]["blueGreen"]
        self.assertEqual(block["activeService"], "checkout-active")
        self.assertEqual(block["previewService"], "checkout-preview")
        self.assertEqual([i["templateName"] for i in block["prePromotionAnalysis"]["templates"]], ["checkout-smoke"])
        self.assertEqual(
            [i["templateName"] for i in block["postPromotionAnalysis"]["templates"]],
            ["checkout-error-rate", "checkout-latency-p95", "checkout-request-volume"],
        )
        self.assertTrue(block["autoPromotionEnabled"])
        post_window = max(
            ar.bake_seconds(self.argo["analysis"][key]) for key in ("error_rate", "latency_p95", "request_volume")
        )
        self.assertGreaterEqual(block["scaleDownDelaySeconds"], post_window + ar.ROLLBACK_MARGIN_SECONDS)
        self.assertEqual(
            documents["checkout-smoke-analysis.json"]["spec"]["args"][0]["value"],
            "checkout-preview.apps.svc.cluster.local",
        )
        self.assertEqual(
            documents["checkout-error-rate-analysis.json"]["spec"]["args"][0]["value"],
            "checkout-active.apps.svc.cluster.local",
        )
        steps = {item["step"]: item for item in summary["steps"]}
        self.assertEqual(steps["deploy_inactive"]["templates"], [])
        self.assertEqual(steps["deploy_inactive"]["argo"], "native readiness")
        self.assertEqual(steps["confirm"]["templates"], [])
        route = documents["checkout-route.json"]
        header_rule, default_rule = route["spec"]["rules"]
        self.assertEqual(header_rule["matches"][0]["headers"], [{"name": "x-release-preview", "value": "true"}])
        self.assertEqual(header_rule["backendRefs"], [{"name": "checkout-preview", "port": 80, "weight": 100}])
        self.assertNotIn("matches", default_rule)
        self.assertEqual(default_rule["backendRefs"], [{"name": "checkout-active", "port": 80, "weight": 100}])

    def test_cross_region_canary_opens_one_region_after_the_previous_is_healthy(self) -> None:
        documents, summary = self._render("multi_cluster_canary")
        appset = documents["checkout-appset.json"]
        spec = appset["spec"]
        self.assertEqual(spec["strategy"]["type"], "RollingSync")
        steps = spec["strategy"]["rollingSync"]["steps"]
        self.assertEqual(
            [step["matchExpressions"][0]["values"] for step in steps],
            [[region] for region in self.strategies["strategies"]["multi_cluster_canary"]["region_order"]],
        )
        self.assertEqual({step["matchExpressions"][0]["key"] for step in steps}, {"region"})
        self.assertEqual(spec["template"]["metadata"]["labels"]["region"], "{{region}}")
        self.assertEqual(spec["template"]["metadata"]["name"], "checkout-{{cluster}}")
        elements = spec["generators"][0]["list"]["elements"]
        self.assertEqual([item["cluster"] for item in elements], EAST + NORTH)
        self.assertEqual([item["region"] for item in elements], ["cn-east"] * 3 + ["cn-north"])
        blob = json.dumps([doc for doc in documents.values()])
        for outside in ("prod-edge-offline", "dev-cn-east-a", "staging-cn-east-a"):
            self.assertNotIn(outside, blob)
        sync_policy = spec["template"]["spec"]["syncPolicy"]
        self.assertNotIn("automated", sync_policy, "RollingSync forces auto-sync off")
        self.assertIn("RespectIgnoreDifferences=true", sync_policy["syncOptions"])
        self.assertEqual(
            spec["template"]["spec"]["ignoreDifferences"],
            [
                {
                    "group": "gateway.networking.k8s.io",
                    "kind": "HTTPRoute",
                    "jqPathExpressions": [".spec.rules[].backendRefs[].weight"],
                }
            ],
        )
        self.assertEqual(spec["template"]["spec"]["source"]["path"], "rollouts/checkout")
        self.assertEqual(summary["region_order"], ["cn-east", "cn-north"])
        self.assertEqual(summary["applicationset"], "rendered")

    def test_cross_region_blue_green_uses_blue_green_rollouts_with_region_order(self) -> None:
        documents, _ = self._render("multi_cluster_blue_green")
        self.assertIn("blueGreen", documents["checkout-rollout.json"]["spec"]["strategy"])
        steps = documents["checkout-appset.json"]["spec"]["strategy"]["rollingSync"]["steps"]
        self.assertEqual([step["matchExpressions"][0]["values"] for step in steps], [["cn-east"], ["cn-north"]])
        elements = documents["checkout-appset.json"]["spec"]["generators"][0]["list"]["elements"]
        self.assertEqual([item["cluster"] for item in elements], EAST + NORTH)

    def test_environment_strategies_sync_all_clusters_together_with_auto_sync(self) -> None:
        documents, _ = self._render("canary")
        spec = documents["checkout-appset.json"]["spec"]
        self.assertNotIn("strategy", spec)
        self.assertEqual(
            spec["template"]["spec"]["syncPolicy"]["automated"],
            {"prune": True, "selfHeal": True},
        )
        self.assertNotIn("region", spec["template"]["metadata"]["labels"])
        elements = spec["generators"][0]["list"]["elements"]
        self.assertEqual([item["cluster"] for item in elements], EAST + NORTH)

    def test_environment_strategy_may_skip_the_applicationset_but_multi_cluster_may_not(self) -> None:
        documents, summary = self._render("canary", gitops_url="")
        self.assertNotIn("checkout-appset.json", documents)
        self.assertTrue(summary["applicationset"].startswith("skipped"))
        with self.assertRaisesRegex(ValueError, "RollingSync"):
            self._render("multi_cluster_canary", gitops_url="")

    def test_istio_router_variant_keeps_the_same_policy(self) -> None:
        argo = copy.deepcopy(self.argo)
        argo["controller"]["traffic_router"] = "istio"
        documents, _ = self._render("canary", argo=argo)
        canary = documents["checkout-rollout.json"]["spec"]["strategy"]["canary"]
        self.assertEqual(
            canary["trafficRouting"],
            {"istio": {"virtualService": {"name": "checkout-vs", "routes": ["primary"]}}},
        )
        virtual = documents["checkout-vs.json"]
        self.assertEqual(virtual["apiVersion"], "networking.istio.io/v1")
        self.assertEqual(
            virtual["spec"]["http"],
            [
                {
                    "name": "primary",
                    "route": [
                        {"destination": {"host": "checkout-stable", "port": {"number": 80}}, "weight": 100},
                        {"destination": {"host": "checkout-canary", "port": {"number": 80}}, "weight": 0},
                    ],
                }
            ],
        )
        steps = canary["steps"]
        self.assertEqual([s["setWeight"] for s in steps if "setWeight" in s], [1, 5, 25, 50, 100])
        appset = documents["checkout-appset.json"]["spec"]["template"]["spec"]["ignoreDifferences"]
        self.assertEqual(appset[0]["kind"], "VirtualService")

        documents, _ = self._render("blue_green", argo=argo)
        http = documents["checkout-vs.json"]["spec"]["http"]
        self.assertEqual(http[0]["match"], [{"headers": {"x-release-preview": {"exact": "true"}}}])
        self.assertEqual(http[0]["route"][0]["destination"]["host"], "checkout-preview")
        self.assertEqual(http[1]["route"][0]["destination"]["host"], "checkout-active")

    def test_image_must_be_pinned_by_digest(self) -> None:
        state = self._plan("canary")
        for image in (
            "ghcr.io/iwacollection/checkout:v1.2.3",
            "ghcr.io/iwacollection/checkout:latest",
            "checkout@sha256:" + "e" * 64,
            "ghcr.io/iwacollection/checkout@sha256:" + "E" * 64,
            "ghcr.io/iwacollection/checkout@sha256:" + "e" * 63,
            "",
        ):
            with self.assertRaisesRegex(ValueError, "pinned by digest"):
                ar.render_documents(state, self.strategies, self.argo, image=image)

    def test_release_identity_travels_with_every_manifest(self) -> None:
        documents, _ = self._render("canary")
        for name, doc in documents.items():
            annotations = doc["metadata"]["annotations"]
            self.assertEqual(annotations["cicd.platform/release-digest"], CANDIDATE, name)
            self.assertEqual(annotations["cicd.platform/baseline-digest"], BASELINE, name)
            self.assertEqual(annotations["cicd.platform/image"], IMAGE, name)
            self.assertEqual(annotations["cicd.platform/evidence"], "analysis-run", name)
        container = documents["checkout-rollout.json"]["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["image"], IMAGE)
        self.assertEqual(container["readinessProbe"]["httpGet"]["path"], "/healthz")

    def test_operator_driven_state_is_not_rendered_in_argo_mode(self) -> None:
        state = self._plan("canary")
        state["history"].append({"action": "advance", "wave": "environment-traffic", "step": "1pct", "status": "in_progress"})
        with self.assertRaisesRegex(ValueError, "fresh plan"):
            ar.render_documents(state, self.strategies, self.argo, image=IMAGE)
        state = self._plan("canary")
        state["status"] = "aborted"
        with self.assertRaisesRegex(ValueError, "fresh plan"):
            ar.render_documents(state, self.strategies, self.argo, image=IMAGE)

    def test_cli_validate_and_render_reuses_the_out_dir_safely(self) -> None:
        validated = subprocess.run(
            [sys.executable, "scripts/ci/argo_rollouts.py", "validate"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertIn("argo rollouts policy validated", validated.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "rendered-argo"
            out.mkdir()
            (out / "keep-me.json").write_text("{}\n", encoding="utf-8")
            first = self._cli_render(root, "multi_cluster_canary", out)
            self.assertEqual(first.returncode, 0, first.stderr)
            names = {path.name for path in out.glob("*.json")}
            self.assertIn("checkout-appset.json", names)
            self.assertIn("checkout-canary-svc.json", names)
            manifest = json.loads((out / ar.MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(manifest["strategy"], "multi_cluster_canary")

            second = self._cli_render(root, "multi_cluster_blue_green", out)
            self.assertEqual(second.returncode, 0, second.stderr)
            names = {path.name for path in out.glob("*.json")}
            self.assertNotIn("checkout-canary-svc.json", names)
            self.assertIn("checkout-active-svc.json", names)
            self.assertIn("checkout-preview-svc.json", names)
            self.assertIn("keep-me.json", names, "files this module did not write stay untouched")

            third = self._cli_render(root, "multi_cluster_canary", out, gitops="")
            self.assertEqual(third.returncode, 1)
            self.assertIn("RollingSync", third.stderr)

    def _cli_render(self, root: Path, strategy: str, out: Path, gitops: str = GITOPS_URL) -> subprocess.CompletedProcess:
        identity = _identity()
        state = root / "release-state.json"
        planned = subprocess.run(
            [
                sys.executable,
                "scripts/ci/release_strategy.py",
                "plan",
                "--strategy",
                strategy,
                "--environment",
                "production",
                "--service",
                "checkout",
                "--artifact-name",
                identity["artifact_name"],
                "--bundle-sha256",
                identity["bundle_sha256"],
                "--source-sha",
                identity["source_sha"],
                "--source-run-id",
                identity["source_run_id"],
                "--release-tag",
                identity["release_tag"],
                "--baseline-digest",
                BASELINE,
                "--environment-pointer-digest",
                CANDIDATE,
                "--out",
                str(state),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(planned.returncode, 0, planned.stderr)
        command = [
            sys.executable,
            "scripts/ci/argo_rollouts.py",
            "render",
            "--state",
            str(state),
            "--image",
            IMAGE,
            "--out-dir",
            str(out),
        ]
        if gitops:
            command += ["--gitops-repo-url", gitops]
        return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)


class ArgoWiringTests(unittest.TestCase):
    def test_docs_index_and_guide_know_the_argo_layer(self) -> None:
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        self.assertIn("argo-rollouts.md", index)
        guide = (ROOT / "docs" / "argo-rollouts.md").read_text(encoding="utf-8")
        for phrase in (
            "RollingSync",
            "AnalysisTemplate",
            "ignoreDifferences",
            "applicationsetcontroller.enable.progressive.syncs",
            "不执行 `kubectl apply`",
            "inconclusive",
            "release_strategy.py plan",
            "argo_rollouts.py render",
        ):
            self.assertIn(phrase, guide, phrase)


if __name__ == "__main__":
    unittest.main()
